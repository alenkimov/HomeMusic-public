"""Moving a collection over from another music service (app/imports)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import integrations
from app.api.schemas import (
    ImportActivityOut,
    ImportAuthIn,
    ImportDownloadActivityOut,
    ImportIn,
    ImportItemsOut,
    ImportOut,
    ImportPickOut,
    ImportStartIn,
)
from app.auth import current_user
from app.db import get_session
from app.events import bus
from app.imports import apply, core
from app.imports.sources.base import SourceError
from app.jobs.downloads import IN_WORK
from app.jobs.queue import PermanentError, enqueue, notify
from app.models import DownloadItem, Import, ImportCollection, ImportItem, IntegrationConnection, Job, Track, User

router = APIRouter(prefix="/api/imports", tags=["imports"])
ACTIVITY_LIMIT = 30  # running downloads are a few (the workers); saved files wait for one Navidrome scan


async def _own(s: AsyncSession, user: User, import_id: int, *, lock: bool = False) -> Import:
    imp = await s.get(Import, import_id, with_for_update={"key_share": True} if lock else None)
    if imp is None or imp.user_id != user.id:
        raise HTTPException(404, "Импорт не найден")
    return imp


@router.get("", response_model=list[ImportOut])
async def list_imports(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    rows = (await s.execute(select(Import).where(Import.user_id == user.id).order_by(Import.id.desc()))).scalars()
    return [await core.import_out(s, imp) for imp in rows]


@router.get("/accounts/{source}")
async def accounts(source: str, user: User = Depends(current_user),
                   s: AsyncSession = Depends(get_session, scope="function")):
    rows = (await s.scalars(select(IntegrationConnection).where(
        IntegrationConnection.provider == source, IntegrationConnection.owner_user_id == user.id,
        IntegrationConnection.retained.is_(True), IntegrationConnection.state != "reauth",
    ).order_by(IntegrationConnection.id.desc()))).all() if source in {"spotify", "yandex", "vk"} else []
    return [{"id": row.id, "account": row.account} for row in rows]


@router.delete("/accounts/{connection_id:int}")
async def disconnect_vk(connection_id: int, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    row = await s.get(IntegrationConnection, connection_id)
    if row is None or row.provider != "vk" or row.owner_user_id != user.id or not row.retained:
        raise HTTPException(404, "Сохранённый вход не найден")
    await integrations.remove(s, row)
    return {"ok": True}


@router.post("", response_model=ImportOut)
async def create(body: ImportIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    try:
        imp = await core.create(s, user.id, source=body.source, method=body.method, token=body.token, url=body.url,
                                login=body.login, password=body.password, connection_id=body.connection_id)
    except SourceError as e:
        raise HTTPException(400, str(e)) from e
    await s.flush()
    out = await core.import_out(s, imp)
    await s.commit()
    notify()
    return out


@router.get("/{import_id}", response_model=ImportOut)
async def get(import_id: int, user: User = Depends(current_user),
              s: AsyncSession = Depends(get_session, scope="function")):
    return await core.import_out(s, await _own(s, user, import_id))


@router.get("/{import_id}/activity", response_model=ImportActivityOut)
async def activity(import_id: int, user: User = Depends(current_user),
                   s: AsyncSession = Depends(get_session, scope="function")):
    """What the transfer is doing right now, running downloads ahead of saved files: an item is shown while its job
    runs or its file waits for Navidrome, not while it waits in the queue for a retry."""
    imp = await _own(s, user, import_id)
    item = DownloadItem
    query = item.query_json["query"]
    running = select(Job.id).where(
        Job.kind == "download_item", Job.status == "running", Job.payload_json["item_id"].as_integer() == item.id,
    ).exists()
    rows = (await s.execute(select(
        item.id, query["title"].as_string(), query["artist"].as_string(),
        func.coalesce(item.query_json["meta"]["cover_url"].as_string(),
                      item.chosen_candidate_json["cover_url"].as_string()),
        item.status, item.progress, item.provider, item.search_sources_json, item.track_id.is_not(None),
        func.count().over(),
    ).where(
        item.user_id == imp.user_id, item.import_id == imp.id,
        item.status.in_(IN_WORK), running | ((item.status == "importing") & item.track_id.is_not(None)),
    ).order_by(case((item.status == "importing", 1), else_=0), item.id).limit(ACTIVITY_LIMIT))).all()
    return ImportActivityOut(items=[
        ImportDownloadActivityOut(id=id_, title=title or "", artist=artist or "", cover_url=cover,
                                  status=status, progress=progress or 0, provider=provider, file_saved=saved,
                                  searching=[source for source, state in (sources or {}).items() if state == "searching"])
        for id_, title, artist, cover, status, progress, provider, sources, saved, _ in rows
    ], more=(rows[0][-1] - len(rows)) if rows else 0)


@router.post("/{import_id}/auth", response_model=ImportOut)
async def answer_auth(import_id: int, body: ImportAuthIn, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    """The code VK sent, or what its captcha answered: the sign-in goes on with it."""
    imp = await _own(s, user, import_id)
    try:
        await core.answer_auth(s, imp, code=body.code, captcha=body.captcha)
    except SourceError as e:
        raise HTTPException(409, str(e)) from e
    await s.flush()
    out = await core.import_out(s, imp)
    await s.commit()
    notify()
    core.publish(imp)
    return out


@router.get("/{import_id}/items", response_model=ImportItemsOut)
async def items(import_id: int, collection_id: int | None = None, status: str | None = None,
                match: str | None = None, offset: int = 0, limit: int = Query(200, le=1000),
                user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """Rows in the service's order; `status`/`match` — comma-separated filters (the report: `match=not_found`)."""
    imp = await _own(s, user, import_id)
    q = (select(ImportItem, ImportCollection.kind)
         .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
         .where(ImportItem.import_id == imp.id))
    if collection_id is not None:
        q = q.where(ImportItem.collection_id == collection_id)
    if status:
        q = q.where(ImportItem.status.in_([x.strip() for x in status.split(",") if x.strip()]))
    if match:
        q = q.where(ImportItem.match.in_([x.strip() for x in match.split(",") if x.strip()]))
    total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await s.execute(q.order_by(ImportCollection.position, ImportItem.position)
                            .offset(offset).limit(limit))).all()
    track_ids = {it.track_id for it, _ in rows if it.track_id}
    songs = dict((await s.execute(select(Track.id, Track.nd_song_id).where(Track.id.in_(track_ids)))).all()) \
        if track_ids else {}
    dl_ids = {it.download_item_id for it, _ in rows if it.download_item_id}
    dls = {d.id: d for d in (await s.execute(select(DownloadItem).where(DownloadItem.id.in_(dl_ids)))).scalars()} \
        if dl_ids else {}
    return ImportItemsOut(total=total, items=[
        core.item_out(it, kind, songs.get(it.track_id), dls.get(it.download_item_id)) for it, kind in rows])


@router.post("/{import_id}/start", response_model=ImportOut)
async def start(import_id: int, body: ImportStartIn, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    imp = await _own(s, user, import_id)
    try:
        await core.start(s, imp, collection_ids=body.collection_ids, mode=body.mode,
                         download_albums=body.download_albums, keep_token=body.keep_token, copy_likes=body.copy_likes,
                         extra={"star": body.star, "playlist_ids": list(dict.fromkeys(body.playlist_ids)),
                                "tag_ids": list(dict.fromkeys(body.tag_ids))})
    except PermanentError as e:
        raise HTTPException(409, str(e)) from e
    await s.flush()
    out = await core.import_out(s, imp)
    await s.commit()
    notify()
    core.publish(imp)
    return out


@router.post("/{import_id}/download_all", response_model=ImportOut)
async def download_all(import_id: int, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    """Economy → full: what waits for a listen is downloaded now, in the background."""
    imp = await _own(s, user, import_id, lock=True)
    if imp.status not in ("running", "done"):
        raise HTTPException(409, "Импорт ещё не запущен")
    await core.download_all(s, imp)
    await s.flush()
    out = await core.import_out(s, imp)
    await s.commit()
    notify()
    return out


@router.post("/{import_id}/sync", response_model=ImportOut)
async def sync(import_id: int, user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")):
    """«Обновить»: read the collection again; only what is new is matched and acted on."""
    imp = await _own(s, user, import_id, lock=True)
    if imp.status == "paused":
        raise HTTPException(409, "Сначала продолжите перенос")
    if imp.status in core.ACTIVE:
        raise HTTPException(409, "Импорт ещё идёт")
    if imp.started_at is None:
        raise HTTPException(409, "Импорт ещё не запущен")
    if imp.method not in ("profile", "link") and not await integrations.import_connection(s, imp):
        raise HTTPException(409, "Вход в сервис удалён — начните новый перенос")
    imp.status = "fetching"
    await enqueue(s, "import_fetch", {"import_id": imp.id}, priority=core.PRIORITY_ASKED)
    await s.flush()
    out = await core.import_out(s, imp)
    await s.commit()
    notify()
    return out


@router.put("/{import_id}/pause", response_model=ImportOut)
async def set_paused(import_id: int, paused: bool = Body(embed=True), user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    """Hold or release queued transfer work. Claimed downloads can finish."""
    imp = await _own(s, user, import_id, lock=True)
    try:
        await core.set_paused(s, imp, paused)
    except PermanentError as e:
        raise HTTPException(409, str(e)) from e
    await s.flush()
    out = await core.import_out(s, imp)
    await s.commit()
    notify()
    core.publish(imp)
    return out


@router.delete("/{import_id}")
async def remove(import_id: int, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    """Forget the import (and its sign-in). The music, the ♥ and the playlists it made stay."""
    await core.remove(s, await _own(s, user, import_id, lock=True))
    await s.commit()
    bus.publish(user.id, "library", {"kind": "changed", "album_id": None})
    return {"ok": True}


async def _own_item(s: AsyncSession, user: User, item_id: int) -> tuple[ImportItem, ImportCollection, Import]:
    it = await s.get(ImportItem, item_id)
    imp = await s.get(Import, it.import_id) if it else None
    if it is None or imp is None or imp.user_id != user.id:
        raise HTTPException(404, "Строка импорта не найдена")
    coll = await s.get(ImportCollection, it.collection_id)
    assert coll is not None
    return it, coll, imp


@router.post("/items/{item_id}/pick", response_model=ImportPickOut)
async def pick(item_id: int, user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")):
    """A row that was not found (or found wrong): a visible download by its names, without the catalog match. The
    user picks the file on its page (`/api/downloads/{id}`), as for any download that needs review."""
    it, coll, imp = await _own_item(s, user, item_id)
    if coll.kind not in ("likes", "playlist"):
        raise HTTPException(400, "Выбрать файл можно только для трека")
    dl = await apply.pick(s, imp, it)
    await s.commit()
    notify()
    return ImportPickOut(download_item_id=dl.id)


@router.post("/items/{item_id}/skip")
async def skip(item_id: int, user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")):
    """Leave this row out (it is not moved). A track known by its name alone, which this row brought, leaves the
    playlist the row brought it into: there was nothing of it but its place there."""
    from app import playlists
    from app.library.tracks import has_file

    it, coll, _ = await _own_item(s, user, item_id)
    it.status = "skipped"
    track = await s.get(Track, it.track_id) if it.track_id else None
    p = await playlists.own(s, user.id, coll.playlist_id) if coll.playlist_id else None
    if track is not None and p is not None and not track.ref and not await has_file(s, track.id):
        await playlists.remove_tracks(s, p, [track.id])
        await s.commit()
        bus.publish(user.id, "library", {"kind": "playlist", "playlist_id": str(p.id)})
    return {"ok": True}
