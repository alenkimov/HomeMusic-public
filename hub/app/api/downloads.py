"""Download queue API (FR-3)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import listen
from app.api.schemas import DownloadBatchOut, DownloadIdsIn, DownloadIn, DownloadItemOut, DownloadTrackIn, RetryIn
from app.auth import current_user
from app.catalog.models import split_ref
from app.db import get_session
from app.events import bus
from app.jobs.downloads import create_items, item_links, item_out, item_ref
from app.jobs.queue import enqueue, notify
from app.library.owned import ACTIVE_STATUSES, find_existing, quiet
from app.library.tracks import current_file
from app.models import DownloadBatch, DownloadItem, Job, User
from app.providers.base import Candidate, TrackQuery
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
            func.json_extract(Job.payload_json, "$.item_id").in_([i.id for i in changed])).values(priority=0))
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
        have = None if body.force else await find_existing(
            s, artist=q.artist, title=q.title, duration_s=q.duration_s, isrc=q.isrc,
            deezer_track_id=meta.get("deezer_track_id"))
        # a picked file the library has not got is downloaded next to the song's older version (`jobs.downloads`)
        file = await current_file(s, have.id) if have else None
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


@router.get("", response_model=list[DownloadItemOut])
async def list_items(status: str | None = None, limit: int = Query(100, le=5000), user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    stmt = select(DownloadItem).where(DownloadItem.user_id == user.id, DownloadItem.hidden.is_(False))
    if status:
        stmt = stmt.where(DownloadItem.status.in_([x.strip() for x in status.split(",") if x.strip()]))
    rows = (await s.execute(stmt.order_by(DownloadItem.id.desc()).limit(limit))).scalars().all()
    links = await item_links(s, list(rows))
    return [item_out(r, links=links[r.id]) for r in rows]


async def _own_item(s: AsyncSession, user: User, item_id: int) -> DownloadItem:
    item = await s.get(DownloadItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(404, "Загрузка не найдена")
    return item


@router.get("/{item_id}", response_model=DownloadItemOut)
async def get_item(item_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    item = await _own_item(s, user, item_id)
    return item_out(item, detail=True, links=(await item_links(s, [item]))[item.id])


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


@router.post("/items/{item_id}/retry", response_model=DownloadItemOut)
async def retry(item_id: int, body: RetryIn, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    item = await _own_item(s, user, item_id)
    if item.status in ("searching", "downloading", "tagging", "importing"):
        raise HTTPException(409, "Загрузка ещё идёт")
    if body.candidate_index is not None:
        cands = item.candidates_json or []
        if not 0 <= body.candidate_index < len(cands):
            raise HTTPException(400, "Нет такого варианта")
        item.chosen_candidate_json = cands[body.candidate_index]
    elif body.url:
        q = TrackQuery(**item.query_json["query"])
        item.chosen_candidate_json = (await candidate_from_url(body.url, q)).model_dump(mode="json")
    else:
        item.chosen_candidate_json = None
    item.status = "queued"
    item.error = None
    item.progress = 0
    await enqueue(s, "download_item", {"item_id": item.id})
    await s.commit()
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
        item.chosen_candidate_json, item.status, item.error, item.progress = None, "queued", None, 0
        await enqueue(s, "download_item", {"item_id": item.id})
    await s.commit()
    notify()
    for item in items:
        bus.publish(user.id, "download", item_out(item).model_dump(mode="json"))
    return {"queued": len(items)}


@router.post("/clear")
async def clear(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The finished and the cancelled ones leave the list (the tracks stay in the library)."""
    res = await s.execute(update(DownloadItem).where(
        DownloadItem.user_id == user.id, DownloadItem.hidden.is_(False),
        DownloadItem.status.in_(("done", "cancelled"))).values(hidden=True))
    return {"removed": res.rowcount or 0}
