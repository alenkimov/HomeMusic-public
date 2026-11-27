"""Download queue API (FR-3)."""

from __future__ import annotations

import re
from pathlib import Path
from tempfile import mkdtemp

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, not_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import listen
from app.api.schemas import (
    DownloadBatchOut,
    DownloadGroupIn,
    DownloadGroupOut,
    DownloadIdsIn,
    DownloadIn,
    DownloadItemOut,
    DownloadLinkIn,
    DownloadsOverviewOut,
    DownloadTrackIn,
    RetryIn,
)
from app.auth import current_user
from app.catalog.models import split_ref
from app.config import get_settings
from app.db import get_session
from app.events import bus
from app.jobs.downloads import create_items, item_links, item_out, item_ref
from app.jobs.queue import enqueue, notify
from app.library.files import main_of
from app.library.importer import cleanup_dir
from app.library.owned import ACTIVE_STATUSES, ORIGINS, find_owned, quiet
from app.models import DownloadBatch, DownloadItem, Import, ImportCollection, Job, User
from app.providers.base import Candidate, TrackQuery, file_ref
from app.services import svc

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


async def resolve_playlist(s: AsyncSession, user: User, playlist_id: str | None, new_name: str | None) -> str | None:
    """The playlist a download's tracks go into: the one named, or a new one of this name."""
    if playlist_id:
        return playlist_id
    if not new_name or not new_name.strip():
        return None
    from app import playlists

    return str((await playlists.create(s, user.id, new_name)).id)


async def join_active(s: AsyncSession, user: User, refs: list[str], *, playlists: list[str] | None = None) -> set[str]:
    """Catalog tracks this user is downloading already: no second download. An explicit download makes a quiet one
    (an import's, a mix's) a visible download that goes first in the queue from then on. `playlists`: the track goes
    into them too. Returns the joined refs."""
    if not refs:
        return set()
    from app.jobs.downloads import into_playlists
    from app.library import tracks as registry

    items = (await s.execute(
        select(DownloadItem).where(DownloadItem.user_id == user.id, DownloadItem.status.in_(ACTIVE_STATUSES))
    )).scalars().all()
    joined: set[str] = set()
    changed: list[DownloadItem] = []
    for item in items:
        qj = dict(item.query_json or {})
        ref = item_ref(item)
        if ref not in refs or ref in joined:
            continue
        joined.add(ref)
        if playlists:
            track = await registry.by_ref(s, ref)
            if track is not None:
                await into_playlists(s, user.id, playlists, [track.id])
            else:  # a provider's file: it is a track once imported
                qj["playlists"] = list(dict.fromkeys([*(qj.get("playlists") or []), *playlists]))
                item.query_json = qj
        if not quiet(qj):
            continue
        qj["requested"] = True
        item.query_json = qj
        changed.append(item)
    if changed:
        await s.execute(update(Job).where(
            Job.kind == "download_item", Job.status == "queued", Job.priority < 0,
            Job.payload_json["item_id"].as_integer().in_([i.id for i in changed])).values(priority=0))
        await s.flush()
        links = await item_links(s, changed)
        for item in changed:
            bus.publish(user.id, "download", item_out(item, links=links[item.id]).model_dump(mode="json"))
    return joined


async def create_batch(
    s: AsyncSession, user: User, body: DownloadIn, *, origin: str, title: str | None = None
) -> DownloadBatchOut:
    playlist_id = await resolve_playlist(s, user, body.playlist_id, body.new_playlist_name)
    # every playlist asked for: the first is the batch's own, the rest only get the tracks
    playlists = list(dict.fromkeys(x for x in (playlist_id, *body.playlist_ids) if x))
    playlist_id = playlists[0] if playlists else None
    also = playlists[1:]
    track_refs = list(body.track_refs)
    joined = set() if body.force else await join_active(s, user, track_refs, playlists=playlists)
    track_refs = [r for r in track_refs if r not in joined]
    items = list(body.items)
    # a provider's file listened to in the player (Files tab of the search): the file itself, as if picked there
    for ref in [r for r in track_refs if listen.is_file_ref(r)]:
        c = await listen.file_candidate(ref)
        if c is None:
            raise HTTPException(404, "Файл не найден: повторите поиск")
        items.append(DownloadTrackIn(artist=c.artist, title=c.title, duration_s=c.duration_s, explicit=c.explicit,
                                     candidate=c))
    track_refs = [r for r in track_refs if not listen.is_file_ref(r)]
    batch = DownloadBatch(user_id=user.id, origin=origin, title=title or body.new_playlist_name,
                          target_playlist_id=playlist_id)
    s.add(batch)
    await s.flush()

    entries = []
    owned = 0
    for it in items:
        q = TrackQuery(artist=it.artist, title=it.title, album=it.album, duration_s=it.duration_s, isrc=it.isrc,
                       explicit=it.explicit, version=it.version)
        meta: dict = {}
        if it.catalog_ref:
            src, id_ = split_ref(it.catalog_ref)
            meta["track_ref"] = it.catalog_ref
            if src == "dz":
                meta["deezer_track_id"] = id_
        have = None if body.force else await find_owned(
            s, artist=q.artist, title=q.title, duration_s=q.duration_s, isrc=q.isrc,
            catalog_ref=meta.get("track_ref"))
        # a picked file the library has not got is downloaded next to the song's older version (`jobs.downloads`)
        file = await main_of(s, have.id) if have else None
        if have and (it.candidate is None or (file is not None and file.source_url == it.candidate.page_url)):
            owned += 1
        entries.append((q, meta, it.candidate))
    queued = await create_items(s, batch, entries, force=body.force, also_playlists=also)

    expanding = bool(track_refs or body.album_ref or body.artist_ref)
    if expanding:
        await enqueue(s, "expand_batch", {
            "batch_id": batch.id,
            "track_refs": track_refs,
            "album_refs": [body.album_ref] if body.album_ref else [],
            "artist_ref": body.artist_ref,
            "record_types": body.record_types,
            "force": body.force,
            "also_playlists": also,
        })
    return DownloadBatchOut(batch_id=batch.id, queued=queued + len(joined), already_owned=owned, playlist_id=playlist_id,
                            expanding=expanding)


@router.post("", response_model=DownloadBatchOut)
async def create(body: DownloadIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    if not (body.items or body.track_refs or body.album_ref or body.artist_ref):
        raise HTTPException(400, "Нечего скачивать")
    origin = "search" if body.items and all(i.candidate for i in body.items) else "catalog"
    out = await create_batch(s, user, body, origin=origin)
    await s.commit()
    notify()
    return out


TAB_STATUSES = {"now": ACTIVE_STATUSES, "review": ("failed", "needs_review"), "done": ("done", "cancelled")}
# the Hub's own groups that are only shown while under way: a mix that missed a track or the wave's done ones are
# nobody's business
UNDER_WAY_ONLY = ("mix", "wave", "upgrade")


def _group_where(group: str):
    """The downloads of a group of «Загрузки» (`DownloadGroupOut.key`): the order of `owned.origin`."""
    qj = DownloadItem.query_json
    asked = func.coalesce(qj["requested"].as_boolean(), False)
    if group == "own":
        return or_(asked, and_(DownloadItem.import_id.is_(None), *(not_(qj.has_key(k)) for k in ORIGINS)))
    if group.startswith("import:") and group[7:].isdigit():
        return and_(not_(asked), DownloadItem.import_id == int(group[7:]))
    if group in UNDER_WAY_ONLY:
        before = ORIGINS[1:ORIGINS.index(group)]
        return and_(not_(asked), DownloadItem.import_id.is_(None), qj.has_key(group), *(not_(qj.has_key(k)) for k in before))
    raise HTTPException(400, "Нет такой группы загрузок")


@router.get("", response_model=list[DownloadItemOut])
async def list_items(status: str | None = None, limit: int = Query(100, le=5000), quiet: bool = True,
                     group: str | None = None, before: int | None = None,
                     user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The user's downloads, the newest first. `quiet` false: without the ones nobody asked for (`owned.quiet`: a
    transfer's, a mix's, the wave's, a better file's) — what «Загрузки» shows; a transfer alone has thousands.
    `group`: only one group of the overview; `before`: the ones older than this id (the next page)."""
    stmt = select(DownloadItem).where(DownloadItem.user_id == user.id, DownloadItem.hidden.is_(False))
    if not quiet:
        stmt = stmt.where(_group_where("own"))
    if group:
        stmt = stmt.where(_group_where(group))
    if before is not None:
        stmt = stmt.where(DownloadItem.id < before)
    if status:
        stmt = stmt.where(DownloadItem.status.in_([x.strip() for x in status.split(",") if x.strip()]))
    rows = (await s.execute(stmt.order_by(DownloadItem.id.desc()).limit(limit))).scalars().all()
    links = await item_links(s, list(rows))
    return [item_out(r, links=links[r.id]) for r in rows]


@router.get("/overview", response_model=DownloadsOverviewOut)
async def overview(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """«Загрузки» by group and section: how many of each are under way, need the user, are done. A transfer, a mix,
    the wave and the better files are groups of their own; the counts of a section leave out what is shown only
    while under way (`UNDER_WAY_ONLY`)."""
    qj = DownloadItem.query_json
    keys = (DownloadItem.status, DownloadItem.import_id, func.coalesce(qj["requested"].as_boolean(), False),
            *(qj.has_key(k) for k in ORIGINS[1:]))
    rows = (await s.execute(select(*keys, func.count()).where(
        DownloadItem.user_id == user.id, DownloadItem.hidden.is_(False)).group_by(*keys))).all()
    tab_of = {st: tab for tab, sts in TAB_STATUSES.items() for st in sts}
    groups: dict[str, DownloadGroupOut] = {}
    for status, import_id, asked, *flags, n in rows:
        tab = tab_of.get(status)
        if tab is None:
            continue
        if asked:
            key = "own"
        elif import_id is not None:
            key = f"import:{import_id}"
        else:
            key = next((k for k, on in zip(ORIGINS[1:], flags, strict=True) if on), "own")
        if key in UNDER_WAY_ONLY and tab != "now":
            continue
        g = groups.setdefault(key, DownloadGroupOut(key=key, origin=None if key == "own" else key.split(":")[0],
                                                    import_id=import_id if key.startswith("import:") else None))
        setattr(g, tab, getattr(g, tab) + n)
    ids = [g.import_id for g in groups.values() if g.import_id is not None]
    if ids:
        colls: dict[int, list[ImportCollection]] = {}
        for c in (await s.execute(select(ImportCollection).where(ImportCollection.import_id.in_(ids)))).scalars():
            colls.setdefault(c.import_id, []).append(c)
        from app.imports.core import display_title

        for imp in (await s.execute(select(Import).where(Import.id.in_(ids)))).scalars():
            g = groups[f"import:{imp.id}"]
            g.source, g.title, g.import_status = imp.source, display_title(imp, colls.get(imp.id, [])), imp.status
    order = {"mix": 1, "wave": 2, "upgrade": 3, "own": 4}
    out = sorted(groups.values(), key=lambda g: (order.get(g.key, 0), -(g.import_id or 0)))
    return DownloadsOverviewOut(now=sum(g.now for g in out), review=sum(g.review for g in out),
                                done=sum(g.done for g in out), groups=out)


async def _own_item(s: AsyncSession, user: User, item_id: int) -> DownloadItem:
    item = await s.get(DownloadItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(404, "Загрузка не найдена")
    return item


@router.get("/{item_id}", response_model=DownloadItemOut)
async def get_item(item_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    item = await _own_item(s, user, item_id)
    out = item_out(item, detail=True, links=(await item_links(s, [item]))[item.id])
    await listen.remember_files([*(out.candidates or []), *([out.chosen] if out.chosen else [])])
    return out


HITMOZ_SONG = re.compile(r"https?://[^/]*hitm[a-z]*\.[a-z]+/song/(\d+)", re.I)
HITMOS_FM_SONG = re.compile(r"https?://[^/]*hitmos\.fm/song/([\w-]+)", re.I)
YOUTUBE = re.compile(r"(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/|music\.youtube\.com/watch\?v=)([\w-]{11})", re.I)
SOUNDCLOUD = re.compile(r"^https?://(?:www\.|m\.)?soundcloud\.com/([\w-]+/[\w-]+)/?(?:[?#].*)?$", re.I)


async def candidate_from_url(url: str, q: TrackQuery) -> Candidate:
    url = url.strip()
    if m := HITMOS_FM_SONG.search(url):
        cand = Candidate(provider="hitmos_fm", provider_id=m.group(1), artist=q.artist, title=q.title, page_url=url)
    elif m := HITMOZ_SONG.search(url):
        cand = Candidate(provider="hitmoz", provider_id=m.group(1), artist=q.artist, title=q.title, page_url=url)
    elif m := YOUTUBE.search(url):
        cand = Candidate(provider="ytdlp", provider_id=m.group(1), artist=q.artist, title=q.title,
                         page_url=f"https://www.youtube.com/watch?v={m.group(1)}")
    elif m := SOUNDCLOUD.search(url):
        # the numeric track id comes with `enrich`; the id is a part of the downloaded file's name
        cand = Candidate(provider="soundcloud", provider_id=m.group(1).replace("/", "-"), artist=q.artist, title=q.title,
                         page_url=f"https://soundcloud.com/{m.group(1)}")
    else:
        raise HTTPException(400, "Ссылка не поддерживается: нужна страница трека hitmoz / hitmos.fm, YouTube "
                                 "или SoundCloud")
    provider = svc.providers.get(cand.provider)
    if provider is None:
        raise HTTPException(400, "Источник отключён")
    try:
        enriched = await provider.enrich(cand)
        if cand.provider == "hitmos_fm" and not enriched.download_url:
            # the song page carries the file link in its RSC payload
            from app.providers.hitmos_fm import parse_rsc_tracks

            html, _ = await provider.fetch_page(f"/song/{cand.provider_id}")  # type: ignore[attr-defined]
            obj = parse_rsc_tracks(html).get(cand.provider_id) or {}
            enriched = enriched.model_copy(update={
                "download_url": obj.get("download"),
                "artist": obj.get("artist") or enriched.artist,
                "title": obj.get("title") or enriched.title,
            })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Не удалось открыть ссылку: {e}") from e
    if cand.provider not in ("ytdlp", "soundcloud") and not enriched.download_url:
        raise HTTPException(400, "На странице не нашлась ссылка на файл")
    return enriched


@router.post("/items/{item_id}/check-link", response_model=Candidate)
async def check_link(item_id: int, body: DownloadLinkIn, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    item = await _own_item(s, user, item_id)
    q = TrackQuery(**item.query_json["query"])
    candidate = await candidate_from_url(body.url, q)
    await listen.remember_files([candidate])
    return candidate


async def _selected_candidate(item: DownloadItem, body: RetryIn) -> Candidate | None:
    if body.candidate_index is not None:
        cands = item.candidates_json or []
        if not 0 <= body.candidate_index < len(cands):
            raise HTTPException(400, "Нет такого варианта")
        return Candidate(**cands[body.candidate_index])
    if body.candidate_ref:
        for data in item.candidates_json or []:
            if file_ref(data["provider"], data["provider_id"]) == body.candidate_ref:
                return Candidate(**data)
        if listen.is_file_ref(body.candidate_ref) and (candidate := await listen.file_candidate(body.candidate_ref)):
            return candidate
        raise HTTPException(404, "Файл не найден: повторите поиск или проверку ссылки")
    if body.url:
        q = TrackQuery(**item.query_json["query"])
        return await candidate_from_url(body.url, q)
    return None


@router.get("/items/{item_id}/prepared", response_model=list[str])
async def prepared(item_id: int, ref: str | None = Query(default=None, max_length=300),
                   user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    item = await _own_item(s, user, item_id)
    refs = [file_ref(c["provider"], c["provider_id"]) for c in item.candidates_json or []]
    if ref and listen.is_file_ref(ref):
        refs.append(ref)
    return [key for key in dict.fromkeys(refs) if listen.prepared_file(key)]


@router.post("/items/{item_id}/prepare", response_model=str)
async def prepare(item_id: int, body: RetryIn, user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session, scope="function")):
    item = await _own_item(s, user, item_id)
    candidate = await _selected_candidate(item, body)
    if candidate is None:
        raise HTTPException(400, "Не указан файл")
    # Release the database transaction while waiting for the source.
    await s.commit()
    try:
        await listen.prepare_file(candidate)
    except listen.ListenError as e:
        raise HTTPException(e.status, str(e)) from e
    return file_ref(candidate.provider, candidate.provider_id)


@router.post("/items/{item_id}/retry", response_model=DownloadItemOut)
async def retry(item_id: int, body: RetryIn, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    item = await _own_item(s, user, item_id)
    if item.status in ("searching", "downloading", "tagging", "importing"):
        raise HTTPException(409, "Загрузка ещё идёт")
    status = item.status
    candidate = await _selected_candidate(item, body)
    chosen = candidate.model_dump(mode="json") if candidate else None
    query = dict(item.query_json or {})
    query.pop("attachment_file", None)
    payload: dict = {"item_id": item.id}
    folder: Path | None = None
    try:
        if candidate and (query.get("meta") or {}).get("track_id"):
            if status in ACTIVE_STATUSES or status == "done":
                raise HTTPException(409, "Привязка файла уже выполняется или завершена")
            ref = file_ref(candidate.provider, candidate.provider_id)
            root = get_settings().staging_dir
            root.mkdir(parents=True, exist_ok=True)
            folder = Path(mkdtemp(prefix="attach-", dir=root))
            path = await listen.stage_prepared_file(ref, folder)
            payload["prepared_file"] = path.relative_to(root).as_posix()
            query["attachment_file"] = payload["prepared_file"]
            claim = await s.execute(update(DownloadItem).where(DownloadItem.id == item.id, DownloadItem.status == status)
                                    .values(status="queued", error=None, progress=0, chosen_candidate_json=chosen,
                                            query_json=query))
            if not claim.rowcount:
                raise HTTPException(409, "Загрузка уже изменилась")
        else:
            item.query_json = query
            item.chosen_candidate_json = chosen
            item.status, item.error, item.progress = "queued", None, 0
        await enqueue(s, "download_item", payload)
        await s.commit()
    except BaseException as e:
        if folder is not None:
            cleanup_dir(folder)
        if isinstance(e, listen.ListenError):
            raise HTTPException(e.status, str(e)) from e
        raise
    notify()
    out = item_out(item)
    bus.publish(user.id, "download", out.model_dump(mode="json"))
    return out


@router.post("/items/{item_id}/cancel", response_model=DownloadItemOut)
async def cancel(item_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    item = await _own_item(s, user, item_id)
    if item.status != "done":
        item.status = "cancelled"
    await s.flush()
    out = item_out(item)
    bus.publish(user.id, "download", out.model_dump(mode="json"))
    return out


RETRIABLE = ("failed", "needs_review", "cancelled")


async def _own_items(s: AsyncSession, user: User, ids: list[int]) -> list[DownloadItem]:
    out: list[DownloadItem] = []
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 400):
        out += list((await s.execute(select(DownloadItem).where(
            DownloadItem.user_id == user.id, DownloadItem.hidden.is_(False), DownloadItem.id.in_(ids[i:i + 400])))).scalars())
    return out


@router.post("/remove")
async def remove(body: DownloadIdsIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    """«Убрать»: the downloads leave the list. One that is not finished is cancelled first — it will not be
    downloaded, and a playlist that waited for it goes on without it; a finished one's track stays in the library.
    The rows are kept: a playlist's order, an import and a mix lean on them."""
    items = await _own_items(s, user, body.ids)
    for item in items:
        if item.status != "done" and item.status != "cancelled":
            item.status = "cancelled"
        item.hidden = True
    await s.commit()
    for item in items:
        bus.publish(user.id, "download", item_out(item).model_dump(mode="json"))
    return {"removed": len(items)}


@router.post("/retry")
async def retry_many(body: DownloadIdsIn, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    """«Повторить» for several downloads at once: each is looked for again, as with its own ↻."""
    items = [x for x in await _own_items(s, user, body.ids) if x.status in RETRIABLE]
    for item in items:
        query = dict(item.query_json or {})
        query.pop("attachment_file", None)
        item.query_json = query
        item.chosen_candidate_json, item.status, item.error, item.progress = None, "queued", None, 0
        await enqueue(s, "download_item", {"item_id": item.id})
    await s.commit()
    notify()
    for item in items:
        bus.publish(user.id, "download", item_out(item).model_dump(mode="json"))
    return {"queued": len(items)}


@router.post("/retry-group")
async def retry_group(body: DownloadGroupIn, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    """«Повторить все» of a group: each of its downloads that failed or waits for a file is looked for again."""
    ids = list((await s.execute(select(DownloadItem.id).where(
        DownloadItem.user_id == user.id, DownloadItem.hidden.is_(False), DownloadItem.status.in_(RETRIABLE[:2]),
        _group_where(body.group)))).scalars())
    if not ids:
        return {"queued": 0}
    return await retry_many(DownloadIdsIn(ids=ids[:5000]), user, s)


@router.post("/clear")
async def clear(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The finished and the cancelled ones leave the list (the tracks stay in the library)."""
    res = await s.execute(update(DownloadItem).where(
        DownloadItem.user_id == user.id, DownloadItem.hidden.is_(False),
        DownloadItem.status.in_(("done", "cancelled"))).values(hidden=True))
    return {"removed": res.rowcount or 0}
