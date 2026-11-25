"""Download pipeline (PLAN 8.3, 8.4): catalog meta → dedupe → providers → fetch → postcheck → tag → import.

Job kinds:
- `expand_batch`  — album/artist/track refs → download items
- `download_item` — one track end to end, ends in `importing` (or `done` if already owned)
- `nd_scan`       — coalesced Navidrome scan, links nd_song_id, completes items, fills playlists
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.progress import SourceState
from app.api.schemas import DownloadItemOut
from app.catalog.models import CATALOGS, CatalogAlbum, CatalogNotFound, CatalogTrack, split_ref
from app.catalog.yandex import russian_original
from app.config import get_settings
from app.covers import fetch_cover
from app.db import session_scope
from app.events import bus
from app.jobs.queue import PermanentError, enqueue, handler, notify, on_final_failure, on_retry
from app.library import probe, upgrade
from app.library.importer import ImportSpec, cleanup_dir, import_file, staging_dir_for
from app.library.navidrome import admin_client
from app.library.owned import find_owned, origin, quiet
from app.library.tracks import current_file, has_file
from app.matching.select import (
    classify,
    needs_uncensored,
    rank_candidates,
    trusted_original,
    uploaded_in_time,
)
from app.models import DownloadBatch, DownloadItem, Job, PlayEvent, PlaylistItem, Track, TrackFile, User, utcnow
from app.net import in_background
from app.providers.base import Candidate, Provider, ProviderError, TrackQuery, file_ref
from app.services import svc

log = logging.getLogger(__name__)

TERMINAL = ("done", "failed", "cancelled", "needs_review")
IN_WORK = ("searching", "downloading", "tagging", "importing")  # a step of a download under way
FAILED = ("failed", "needs_review")
USER_FLAGS = ("requested", "playlists")  # what a request that joins a download under way may set on it
MAX_TRIES_PER_PROVIDER = 3
MAX_SHOWN = 15  # candidates kept on the item for the user to pick from
MAX_SHOWN_REUPLOADS = 5
SCAN_PASSES = 3  # more Navidrome scans after one that left tracks unlinked
BACKGROUND_SCAN_DELAY_S = 30
BACKGROUND_SCAN_PRIORITY = -5
SCAN_SCHEDULE_LOCK = 7013426
MAYBE_CENSORED = "Возможна цензура: копий без цензуры не нашлось"


# --- item state + events ------------------------------------------------------------------


async def item_links(s: AsyncSession, items: list[DownloadItem]) -> dict[int, tuple[str | None, str | None]]:
    """item id -> (Navidrome song id, batch target playlist id) for `item_out`."""
    track_ids = {i.track_id for i in items if i.track_id}
    batch_ids = {i.batch_id for i in items}
    songs = dict((await s.execute(select(Track.id, Track.nd_song_id).where(Track.id.in_(track_ids)))).all())         if track_ids else {}
    playlists = dict((await s.execute(
        select(DownloadBatch.id, DownloadBatch.target_playlist_id).where(DownloadBatch.id.in_(batch_ids)))).all())         if batch_ids else {}
    return {i.id: (songs.get(i.track_id), playlists.get(i.batch_id)) for i in items}


def item_out(item: DownloadItem, *, detail: bool = False,
             links: tuple[str | None, str | None] = (None, None)) -> DownloadItemOut:
    q = (item.query_json or {}).get("query", {})
    meta = (item.query_json or {}).get("meta") or {}
    return DownloadItemOut(
        id=item.id,
        batch_id=item.batch_id,
        status=item.status,
        progress=item.progress or 0,
        artist=q.get("artist", ""),
        title=q.get("title", ""),
        album=meta.get("album") or q.get("album"),
        duration_s=q.get("duration_s"),
        provider=item.provider,
        error=item.error,
        warnings=item.warnings or [],
        track_id=item.track_id,
        attach_to_track_id=meta.get("track_id"),
        nd_song_id=links[0],
        playlist_id=links[1],
        track_ref=item_ref(item),
        quiet=quiet(item.query_json),
        origin=origin(item.query_json),
        import_id=item.import_id,
        candidates=[Candidate(**c) for c in item.candidates_json or []] if detail else None,
        log=list(item.log_json or []) if detail else None,
        chosen=Candidate(**item.chosen_candidate_json) if item.chosen_candidate_json else None,
        hidden=bool(item.hidden),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def item_ref(item: DownloadItem) -> str | None:
    """The catalog track the item was made from, or the provider's file it downloads (a `file_ref`)."""
    if ref := ((item.query_json or {}).get("meta") or {}).get("track_ref"):
        return ref
    c = item.chosen_candidate_json
    return file_ref(c["provider"], c["provider_id"]) if c else None


LOG_MAX = 120  # steps a download keeps (a retried one starts no new journal: the old steps say why)
ORIGINS = {"import": " из импорта", "mix": " из микса", "wave": " из волны", "assistant": " от ассистента",
           "upgrade": " — поиск лучшего файла"}


async def step(item_id: int, kind: str, text: str, cand: Candidate | None = None, *,
               failed_provider: str | None = None) -> None:
    """One more line of the download's journal. `kind`: info, ok, warn or bad; `cand`: the file the line is about."""
    line: dict[str, Any] = {"t": utcnow().isoformat(), "kind": kind, "text": text}
    if failed_provider is not None:
        line["failed_provider"] = failed_provider
    if cand is not None:
        line["cand"] = {"artist": cand.artist, "title": cand.title, "provider": cand.provider,
                        "bitrate_kbps": cand.bitrate_kbps, "duration_s": cand.duration_s, "page_url": cand.page_url}
    async with session_scope() as s:
        item = await s.get(DownloadItem, item_id)
        if item is not None:
            item.log_json = [*(item.log_json or []), line][-LOG_MAX:]


def files_word(n: int) -> str:
    form = "файл" if n % 10 == 1 and n % 100 != 11 else "файла" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "файлов"
    return f"{n} {form}" if n else "ничего"


def _mmss(seconds: int | None) -> str:
    return f", {seconds // 60}:{seconds % 60:02d}" if seconds else ""


async def update_item(item_id: int, **fields: Any) -> DownloadItem | None:
    async with session_scope() as s:
        item = await s.get(DownloadItem, item_id)
        if item is None:
            return None
        if "query_json" in fields:
            # an explicit download may have joined this item meanwhile (`api.downloads.join_active`): the job's
            # copy of query_json predates it
            cur = item.query_json or {}
            fields["query_json"] = {**fields["query_json"], **{k: cur[k] for k in USER_FLAGS if k in cur}}
        for k, v in fields.items():
            setattr(item, k, v)
        if fields.get("track_id") and (ref := item_ref(item)):
            # what was listened to or liked under the name the download was asked by — a catalog track the library
            # had under another name, a provider's file — is this track's from now on
            await s.execute(update(PlayEvent).where(
                PlayEvent.track_id.is_(None), PlayEvent.target_type == "track", PlayEvent.ref == ref)
                .values(track_id=item.track_id))
        if fields.get("track_id") and (item.query_json or {}).get("playlists"):
            # a provider's file asked for with playlists: it is a track now, and goes into them
            await into_playlists(s, item.user_id, item.query_json["playlists"], [item.track_id])
        await s.flush()
        await s.refresh(item)
        if "status" in fields and item.status in TERMINAL:
            await mixes_done(s, [item])
        links = (await item_links(s, [item]))[item.id]
    bus.publish(item.user_id, "download", item_out(item, links=links).model_dump(mode="json"))
    return item


# --- catalog meta ----------------------------------------------------------------------------


def meta_from_catalog(t: CatalogTrack, album: CatalogAlbum | None = None) -> dict[str, Any]:
    from app.tags import RELEASE_GENRE_SOURCES

    m: dict[str, Any] = {
        "track_ref": t.ref,
        "album_ref": album.ref if album is not None else t.album_ref,
        "artists": t.artists or [t.artist],
        "artist_refs": (t.artist_refs if t.artists else [t.artist_ref]) or [],
        "album": t.album_title,
        "album_artist": t.album_artist,
        "track_no": t.track_no,
        "disc_no": t.disc_no,
        "isrc": t.isrc,
        "explicit": t.explicit,
        "release_date": t.release_date.isoformat() if t.release_date else None,
        "cover_url": t.cover_url,
        "duration_s": t.duration_s,
        "spotify_id": t.spotify_id,
        "spotify_plays": t.spotify_plays,
    }
    if t.source == "dz":
        m["deezer_track_id"] = t.id
        if t.album_ref:
            m["deezer_album_id"] = split_ref(t.album_ref)[1]
    if album is not None:
        m.update(
            album=album.title,
            album_artist=album.artist,
            album_artists=album.artists or None,
            record_type=album.record_type,
            release_date=album.release_date.isoformat() if album.release_date else m.get("release_date"),
            year=album.year,
            cover_url=album.cover_url or m.get("cover_url"),
            genre=album.genre,
            genre_source=RELEASE_GENRE_SOURCES.get(album.source) if album.genre else None,
            upc=album.upc,
            track_total=album.nb_tracks or (len(album.tracks) if album.tracks else None),
        )
        if album.source == "dz":
            m["deezer_album_id"] = album.id
    return {k: v for k, v in m.items() if v is not None}


async def complete_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Fill album details (track number, cover, release type...) for a Deezer track known only by id."""
    if not meta.get("deezer_track_id") or (meta.get("track_no") and meta.get("record_type")) or svc.deezer is None:
        return meta
    try:
        t = await svc.deezer.get_track(meta["deezer_track_id"])
        album = await svc.deezer.get_album(split_ref(t.album_ref)[1]) if t.album_ref else None
    except Exception as e:
        log.warning("deezer meta for %s failed: %r", meta.get("deezer_track_id"), e)
        return meta
    if album and album.tracks:
        hit = next((x for x in album.tracks if x.id == t.id), None)
        if hit:
            t = t.model_copy(update={"track_no": hit.track_no or t.track_no, "disc_no": hit.disc_no or t.disc_no})
    merged = meta_from_catalog(t, album)
    # who the track is by is the catalog's word of this very track — the names and, beside each, its record: the
    # caller's own list of names (a service's, a search's with the first artist only) would stand beside the records
    # of others
    merged.update({k: v for k, v in meta.items()
                   if v is not None and k not in ("track_no", "disc_no", "artists", "artist_refs")})
    for k in ("track_no", "disc_no"):
        merged[k] = merged.get(k) or meta.get(k)
    return merged


async def resolve_catalog_meta(q: TrackQuery) -> dict[str, Any] | None:
    """Find a confident catalog match for the downloaded file's metadata."""
    t = await resolve_catalog_track(q)
    return meta_from_catalog(t) if t else None


async def resolve_catalog_track(q: TrackQuery) -> CatalogTrack | None:
    from app.catalog import lookup

    try:
        match = await lookup.match_track(q)
    except lookup.CatalogUnavailable:
        log.info("no catalog available to identify %r", q.title)
        return None
    return match.track if match is not None and match.verdict == "auto" else None


# --- candidates ------------------------------------------------------------------------------


async def owe_cover(s: AsyncSession, album_id: int, url: str) -> None:
    """The album is to have the catalog's picture `url`, and the catalog did not give it just now: it is asked for
    again later, and put on the album when it comes (`handle_album_cover`)."""
    await enqueue(s, "album_cover", {"album_id": album_id, "url": url}, max_attempts=12)


@handler("album_cover")
async def handle_album_cover(job: Job) -> None:
    from app.library import editor
    from app.models import Album

    url = str(job.payload_json["url"])
    cover = await fetch_cover(url)
    if cover is None:
        raise RuntimeError("каталог пока не отдаёт обложку")
    async with session_scope() as s:
        album = await s.get(Album, int(job.payload_json["album_id"]))
        if album is None:
            return
        await editor.set_album_cover(s, album, cover)
        await enqueue_scan(s)
    notify()


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# --- handlers ----------------------------------------------------------------------------------


async def filed(s: AsyncSession, item: DownloadItem) -> bool:
    """The download's file is in the library and only Navidrome is waited for (`handle_scan` finishes it). An item
    left `importing` with no file is one whose filing failed: it is downloaded again."""
    return item.status == "importing" and bool(item.track_id) and await has_file(s, item.track_id)


async def interrupted(s: AsyncSession, item: DownloadItem) -> bool:
    """The item shows a step of a download that no longer runs (a filed one only waits for Navidrome)."""
    return item.status in IN_WORK and not await filed(s, item)


async def recover_interrupted() -> int:
    """Called at startup, once no job runs: downloads left mid-way go back to the queue — with a job, when none is
    waiting for them (a failed filing the attempt after it took for filed)."""
    async with session_scope() as s:
        waiting = {int((j.payload_json or {}).get("item_id") or 0) for j in (await s.execute(
            select(Job).where(Job.kind == "download_item", Job.status.in_(("queued", "running"))))).scalars()}
        n = 0
        for item in (await s.execute(select(DownloadItem).where(DownloadItem.status.in_(IN_WORK)))).scalars():
            if not await interrupted(s, item):
                continue
            item.status, item.progress = "queued", 0
            n += 1
            if item.id in waiting:
                continue
            payload = {"item_id": item.id}
            if prepared := (item.query_json or {}).get("attachment_file"):
                payload["prepared_file"] = prepared
            await enqueue(s, "download_item", payload)
        return n


@on_retry("download_item")
async def _download_retried(job: Job) -> None:
    """Until its next attempt the item waits in the queue: the step it failed at is not under way any more."""
    item_id = int(job.payload_json["item_id"])
    async with session_scope() as s:
        item = await s.get(DownloadItem, item_id)
        if item is None or (item.query_json or {}).get("attachment_file") != job.payload_json.get("prepared_file"):
            return
        if not await interrupted(s, item):
            return
    await update_item(item_id, status="queued", progress=0)


@handler("download_item")
async def handle_download_item(job: Job) -> None:
    path = get_settings().staging_dir / job.payload_json["prepared_file"] if job.payload_json.get("prepared_file") else None
    await _handle_download_item(job, path)
    if path is not None:
        cleanup_dir(path.parent)


async def _handle_download_item(job: Job, prepared: Path | None) -> None:
    item_id = int(job.payload_json["item_id"])
    async with session_scope() as s:
        item = await s.get(DownloadItem, item_id)
        if item is None or item.status in ("done", "cancelled") or await filed(s, item):
            return
        qj = dict(item.query_json or {})
        if qj.get("attachment_file") != job.payload_json.get("prepared_file"):
            return  # a retry or a different attachment superseded this job
        chosen = item.chosen_candidate_json
        force = item.force
        user_id = item.user_id
        again = bool(item.log_json)
        batch = await s.get(DownloadBatch, item.batch_id)
        origin = ORIGINS.get(batch.origin if batch else "", "")
    q = TrackQuery(**qj["query"])
    meta: dict[str, Any] = dict(qj.get("meta") or {})
    pick_only = bool(qj.get("pick_only"))
    await step(item_id, "info", "Ещё одна попытка" if again else f"Запрос{origin}: {q.artist} — {q.title}{_mmss(q.duration_s)}")

    source_states: dict[str, SourceState] = {}
    await update_item(item_id, status="searching", progress=0, provider=None, search_sources_json={}, error=None)
    await upgrade.mark(meta, state="searching")

    # 1. catalog record (needed for tags; without it only a user-picked candidate may be downloaded). A picked
    # re-upload is looked up too: names from its title ("Трек – Исполнитель (без цензуры)") make poor tags.
    if not (meta.keys() - {"track_id"}) and not chosen and not pick_only:
        # nothing of the catalog yet (a file wanted for a known track, as after a restore: its `track_id` only)
        meta = {**(await resolve_catalog_meta(q) or {}), **{k: v for k, v in meta.items() if k == "track_id"}}
    elif chosen and not meta and not pick_only and (chosen.get("extra") or {}).get("reupload"):
        if t := await resolve_catalog_track(q):
            q, meta = query_from_track(t), meta_from_catalog(t)
            qj["query"] = q.model_dump()
    meta = await complete_meta(meta) if meta else meta
    if meta:
        q = q.model_copy(update={
            "duration_s": q.duration_s or meta.get("duration_s"),
            "isrc": q.isrc or meta.get("isrc"),
            "explicit": q.explicit if q.explicit is not None else meta.get("explicit"),
        })
        qj["meta"] = meta
        qj["query"] = q.model_dump()
        await update_item(item_id, query_json=qj)
    if not (meta.keys() - {"track_id"}) and not chosen and not pick_only:
        await step(item_id, "warn", "Записи в каталоге нет: без неё файл можно взять только по вашему выбору")

    # 2. already in the library?
    if not force and (not pick_only or chosen):
        async with session_scope() as s:
            target_id = meta.get("track_id")
            existing = await s.get(Track, target_id) if target_id else await find_owned(
                s, artist=q.artist, title=q.title, duration_s=q.duration_s, isrc=q.isrc,
                catalog_ref=meta.get("track_ref"),
            )
            have = await current_file(s, existing.id) if existing else None
            existing_id = existing.id if existing and have else None
            linked = bool(existing_id and existing.nd_song_id)
            old_song = have.nd_song_id if have else None
            other_file = bool(have and chosen and have.source_url != chosen.get("page_url"))
        if other_file:
            # the user picked another file of a song the library has (an uncensored re-upload of a censored copy):
            # it goes next to the old one as one more file of the same track (PLAN 8.6), and the ♥ in Navidrome
            # moves from the old file's song over to the new one's
            force = True
            qj["replaces_song"] = old_song
            await update_item(item_id, query_json=qj)
            await step(item_id, "info", "Трек уже есть в библиотеке, но выбран другой файл: он ляжет рядом со старым")
        elif existing_id:
            await step(item_id, "ok", "Уже есть в библиотеке — скачивать не нужно")
            item = await update_item(item_id, status="done" if linked else "importing", progress=1,
                                     track_id=existing_id, warnings=["Уже есть в библиотеке"])
            if linked and item is not None:
                async with session_scope() as s:
                    await imports_done(s, [item])
            await after_import(user_id, linked)
            return

    # 3. Russian tracks listed in Latin (Deezer translit): Cyrillic originals for the search, Russian source order
    pq = q
    if not chosen:
        q, pq = await russian_original(q, meta.get("upc"))
        if pq is not q:
            qj["query"], qj["provider_query"] = q.model_dump(), pq.model_dump()
            await update_item(item_id, query_json=qj)
            await step(item_id, "info", f"Название в латинице — ищу по русскому оригиналу: {pq.artist} — {pq.title}")

    # 4. providers in order: search → rank → try the best files; the next provider is asked only if needed.
    # An explicit Russian track takes only copies known to be uncensored first (from the sources, then re-uploads
    # by small channels); a possibly censored copy is the last resort.
    stage = staging_dir_for("dl", item_id)
    errors: list[str] = []
    all_cands: list[Candidate] = []
    last_reason = ""
    providers = svc.provider_order(pq, for_download=True)
    mcfg = get_settings().cfg.matching
    guard = bool(meta) and not pick_only and not chosen and needs_uncensored(pq)
    held: list[Candidate] = []
    try:
        if chosen:
            await step(item_id, "info", "Файл выбран вручную", Candidate(**chosen))
            ok, last_reason = await try_candidates(item_id, user_id, q, meta, [Candidate(**chosen)], stage, force,
                                                   prepared=prepared)
            if ok:
                return
        else:
            from app.listen import listened_candidate

            # the file the user has just listened to (GET /api/listen) goes first: they get what they heard
            heard = await listened_candidate(meta.get("track_ref"))
            if heard and meta and guard and not trusted_original(heard, mcfg):
                await step(item_id, "warn", "Файл, который вы слушали, отложен: возможно, это цензурная версия", heard)
            if heard and meta and not pick_only and (not guard or trusted_original(heard, mcfg)):
                await step(item_id, "info", "Сначала файл, который вы слушали", heard)
                ok, last_reason = await try_candidates(item_id, user_id, q, meta, [heard], stage, force)
                if ok:
                    return
            for p in providers:
                [(cands, err)] = await search_download_sources([p], pq, item_id, source_states)
                if err:
                    errors.append(err)
                    await step(item_id, "bad", f"Источник не ответил — {err}", failed_provider=p.name)
                    continue
                await step(item_id, "info", f"Поиск в {p.name}: {files_word(len(cands))}")
                all_cands.extend(cands)
                await update_item(item_id, candidates_json=[c.model_dump(mode="json") for c in all_cands[:MAX_SHOWN]])
                if pick_only or not meta or not cands:
                    continue  # without a catalog record we only collect candidates for the user (PLAN 8.5.3)
                tries = cands[:MAX_TRIES_PER_PROVIDER]
                if guard:
                    doubtful = [c for c in tries if not trusted_original(c, mcfg)]
                    held += doubtful
                    tries = [c for c in tries if trusted_original(c, mcfg)]
                    if doubtful:
                        await step(item_id, "warn", f"Отложено как возможно цензурное: {files_word(len(doubtful))} из {p.name}")
                    if not tries:
                        continue
                ok, reason = await try_candidates(item_id, user_id, q, meta, tries, stage, force)
                if ok:
                    return
                last_reason = reason or last_reason
            if guard:
                if reups := await search_reuploads(pq, item_id, source_states, searched=providers):
                    shown = all_cands[:MAX_SHOWN - min(MAX_SHOWN_REUPLOADS, len(reups))] + reups[:MAX_SHOWN_REUPLOADS]
                    all_cands.extend(reups)
                    await update_item(item_id, candidates_json=[c.model_dump(mode="json") for c in shown])
                    await step(item_id, "info", f"Перезаливы без цензуры: {files_word(len(reups))}")
                    ok, reason = await try_candidates(item_id, user_id, q, meta, reups[:MAX_TRIES_PER_PROVIDER],
                                                      stage, force)
                    if ok:
                        return
                    last_reason = reason or last_reason
                if held:
                    await step(item_id, "warn", "Ничего надёжного не нашлось — беру отложенные файлы")
                    ok, reason = await try_candidates(item_id, user_id, q, meta, held, stage, force,
                                                      warning=MAYBE_CENSORED)
                    if ok:
                        return
                    last_reason = reason or last_reason
    finally:
        cleanup_dir(stage)

    if upgrade.of_meta(meta):  # nothing better than the file there is: it stays, and nobody is asked anything
        await upgrade.mark(meta, state="none", note=last_reason or "в источниках ничего не нашлось")
        await update_item(item_id, status="cancelled", error=None)
        await step(item_id, "info", "Лучше файла не нашлось — остаётся тот, что есть" + (f" ({last_reason})" if last_reason else ""))
        return
    if chosen:
        verdict = f"Файл не подошёл ({last_reason})"
    elif not all_cands:
        if errors and len(errors) >= len(providers):
            raise RuntimeError("все источники недоступны: " + "; ".join(errors))
        verdict = "Не найдено ни в одном источнике"
    elif pick_only:
        await update_item(item_id, status="needs_review", error=None)
        await step(item_id, "info", "Поиск завершён — выберите файл")
        return
    elif not meta:
        sure = classify(all_cands, get_settings().cfg.matching) == "auto"
        verdict = "Нет записи в каталоге — " + ("подтвердите файл" if sure else "выберите файл")
    else:
        verdict = f"Ни один кандидат не подошёл ({last_reason})"
    await update_item(item_id, status="needs_review", error=verdict)
    await step(item_id, "bad", f"Итог: {verdict[0].lower()}{verdict[1:]}. Нужен ваш выбор")


async def search_provider(p, q: TrackQuery, item_id: int, *, limit: int = 15,
                          enrich_top_n: int | None = None) -> tuple[list[Candidate], str | None]:
    """Search one provider, rank its results and enrich the top ones (hitmoz: Explicit, real bitrate).
    Listening asks for fewer results and enrichments (`limit`, `enrich_top_n`): it has to start fast.
    Files uploaded after the source's cutoff (`sources.uploaded_before`) are dropped."""
    cfg = get_settings().cfg.matching
    cutoffs = get_settings().cfg.sources.uploaded_before
    n_enrich = cfg.enrich_top_n if enrich_top_n is None else enrich_top_n
    try:
        raw = await p.search(q, limit=limit)
    except ProviderError as e:
        return [], f"{p.name}: {e}"
    except Exception as e:
        log.warning("provider %s search failed: %r", p.name, e, extra={"item_id": item_id})
        return [], f"{p.name}: {e!r}"
    ranked = rank_candidates(q, [c for c in raw if uploaded_in_time(c, cutoffs)], cfg)
    if ranked and n_enrich:
        top = ranked[:n_enrich]
        enriched = await asyncio.gather(*(p.enrich(c) for c in top), return_exceptions=True)
        top = [e if isinstance(e, Candidate) else c for c, e in zip(top, enriched, strict=True)]
        # the track page may tell the upload date the search didn't
        ranked = rank_candidates(q, [c for c in top if uploaded_in_time(c, cutoffs)] + ranked[n_enrich:], cfg)
    return ranked, None


async def search_download_sources(providers: list[Provider], q: TrackQuery, item_id: int,
                                  states: dict[str, SourceState]) -> list[tuple[list[Candidate], str | None]]:
    if not providers:
        return []
    for provider in providers:
        states[provider.name] = "searching"
    await update_item(item_id, status="searching", provider=None, progress=0, search_sources_json=dict(states))
    lock = asyncio.Lock()

    async def search(provider: Provider) -> tuple[list[Candidate], str | None]:
        candidates, error = await search_provider(provider, q, item_id)
        async with lock:
            states[provider.name] = "error" if error else "ready" if candidates else "empty"
            await update_item(item_id, search_sources_json=dict(states))
        return candidates, error

    return await asyncio.gather(*(search(provider) for provider in providers))


async def search_reuploads(q: TrackQuery, item_id: int, states: dict[str, SourceState], *,
                           searched: list[Provider] | None = None) -> list[Candidate]:
    """Re-uploads by small channels known to be uncensored (titled so, or uploaded before the law), best first.
    Sources in `searched` (the regular order already had them) are not asked again."""
    cfg = get_settings().cfg.matching
    providers = [p for p in svc.reupload_providers() if p not in (searched or [])]
    found = await search_download_sources(providers, q, item_id, states)
    for _, err in found:
        if err:
            log.info("re-uploads: %s", err, extra={"item_id": item_id})
    return rank_candidates(q, [c for cands, _ in found for c in cands if trusted_original(c, cfg)], cfg)


async def try_candidates(item_id: int, user_id: int, q: TrackQuery, meta: dict[str, Any], cands: list[Candidate],
                         stage: Path, force: bool, *, warning: str | None = None,
                         prepared: Path | None = None) -> tuple[bool, str]:
    """Download candidates one by one until one passes the post-download check and is imported. `warning` goes
    to the imported track's warnings."""
    from app.listen import ListenError, stage_playback_file

    pcfg = get_settings().cfg
    last_reason = ""
    # a file that says more than it sounds like (made of a poorer one) waits: another may be the real thing
    spare: tuple[Candidate, Path, Any] | None = None
    upgrade_of = upgrade.of_meta(meta)
    for cand in cands:
        provider = svc.providers.get(cand.provider)
        await update_item(item_id, status="tagging" if prepared else "downloading", provider=cand.provider,
                          chosen_candidate_json=cand.model_dump(mode="json"), progress=1 if prepared else 0)
        last_pub = [0.0]
        loop = asyncio.get_running_loop()

        def on_progress(frac: float, _last=last_pub, _loop=loop) -> None:
            # may be called from a worker thread (yt-dlp)
            if frac - _last[0] >= 0.05 or frac >= 1:
                _last[0] = frac
                _loop.call_soon_threadsafe(
                    lambda f=round(frac, 3): _loop.create_task(update_item(item_id, progress=f))
                )

        check = None
        try:
            if prepared is not None:
                path = stage / prepared.name
                await asyncio.to_thread(shutil.copyfile, prepared, path)
            else:
                path = None
                if not force and not upgrade_of:
                    try:
                        path = await stage_playback_file(cand, stage, meta.get("track_ref"))
                    except ListenError:
                        pass  # the copy expired or could not be prepared; the source can still provide a file
                if path is not None:
                    await update_item(item_id, status="tagging", progress=1)
                    check = await probe.postcheck(path, q.duration_s or cand.duration_s,
                                                  pcfg.matching.duration_tolerance_s, pcfg.postcheck.silence_warn_s)
                    if check.ok:
                        await step(item_id, "info", "Использована временная копия плеера", cand)
                    else:
                        await step(item_id, "warn", f"Временная копия плеера не подошла: {check.reason}", cand)
                        path.unlink(missing_ok=True)
                        path, check = None, None
                        await update_item(item_id, status="downloading", progress=0)
                if path is None:
                    if provider is None:
                        continue
                    path = await provider.fetch(cand, stage, on_progress=on_progress)  # type: ignore[call-arg]
        except Exception as e:
            last_reason = f"{cand.provider}: {e}"
            log.warning("fetch failed %s: %r", cand.page_url, e, extra={"item_id": item_id})
            await step(item_id, "bad", f"Не скачался: {e}"[:300], cand)
            continue
        if check is None:
            await update_item(item_id, status="tagging", progress=1)
            check = await probe.postcheck(path, q.duration_s or cand.duration_s, pcfg.matching.duration_tolerance_s,
                                          pcfg.postcheck.silence_warn_s)
        if not check.ok:
            last_reason = f"{cand.provider}: {check.reason}"
            path.unlink(missing_ok=True)
            await step(item_id, "bad", f"Не подошёл: {check.reason}", cand)
            continue
        if check.blown_from_kbps and upgrade_of and not meta.get("identify"):  # made of a poorer file: no better than what there is
            last_reason = f"{cand.provider}: по звуку около {check.blown_from_kbps} кбит/с"
            path.unlink(missing_ok=True)
            await step(item_id, "bad", f"Не подошёл на замену: по звуку около {check.blown_from_kbps} кбит/с", cand)
            continue
        # (a file fetched to tell what a track is — `identify` — is weighed as any other, below: it is there to be
        # compared by the sound, and is counted for what it sounds like, not for what it says. It is never put aside
        # to be brought in as a download of its own: that made a second track of the song)
        if check.blown_from_kbps and not upgrade_of:
            said = check.info.bitrate_kbps if check.info else cand.bitrate_kbps
            await step(item_id, "warn", f"Отложен: заявлено {said} кбит/с, а по звуку около {check.blown_from_kbps}", cand)
            if spare is None:
                spare = (cand, path, check)
            else:
                path.unlink(missing_ok=True)
            continue
        if upgrade_of:  # a better file in the place of a weak one: weighed against the track's own (library/upgrade.py)
            done, words = await upgrade.take(meta, path, check, cand, user_id)
            if done is None:
                last_reason = f"{cand.provider}: {words}"
                path.unlink(missing_ok=True)
                await step(item_id, "bad", f"Не подошёл на замену: {words}", cand)
                continue
            await update_item(item_id, status="done", progress=1, track_id=upgrade_of, error=None)
            await step(item_id, "ok", ("Файл заменён: " if done == "replaced" else "Файл отложен, решение за слушателем: ") + words, cand)
            bus.publish(user_id, "library", {"kind": "imported", "track_id": upgrade_of})
            if done == "replaced":
                await after_import(user_id, linked=False)
            return True, ""
        if spare is not None:
            spare[1].unlink(missing_ok=True)
        bitrate = f", {cand.bitrate_kbps} кбит/с" if cand.bitrate_kbps else ""
        await step(item_id, "ok", f"Выбран: совпадение {cand.score:.2f}{bitrate}; проверка после скачивания пройдена", cand)
        await import_downloaded(item_id, user_id, q, meta, cand, path, check, force, warning=warning)
        return True, ""
    if spare is not None and not upgrade_of:
        cand, path, check = spare
        said = check.info.bitrate_kbps if check.info else cand.bitrate_kbps
        check.warnings = [*check.warnings, f"Заявлено {said} кбит/с, по звуку около {check.blown_from_kbps}: файл сделан из худшего"]
        await step(item_id, "warn", "Выбран: лучше не нашлось — беру отложенный, с пометкой о качестве", cand)
        await import_downloaded(item_id, user_id, q, meta, cand, path, check, force, warning=warning)
        return True, ""
    return False, last_reason


async def import_downloaded(item_id: int, user_id: int, q: TrackQuery, meta: dict[str, Any], cand: Candidate,
                            path: Path, check: Any, force: bool, *, warning: str | None = None) -> None:
    await update_item(item_id, status="tagging", progress=1)
    cover = await fetch_cover(meta.get("cover_url") or cand.cover_url)
    release_date = _parse_date(meta.get("release_date"))
    single = not meta.get("album")
    spec = ImportSpec(
        artist=q.artist,
        title=q.title,
        artists=meta.get("artists") or [],
        artist_refs=meta.get("artist_refs") or [],
        album=meta.get("album") or q.title,
        album_artist=meta.get("album_artist") or q.artist,
        catalog_album_ref=meta.get("album_ref"),
        year=meta.get("year") or (release_date.year if release_date else cand.year),
        release_date=release_date,
        release_type={"compile": "compilation"}.get(meta.get("record_type", ""), meta.get("record_type"))
        or ("single" if single else "album"),
        track_no=meta.get("track_no") or (1 if single else None),
        track_total=meta.get("track_total"),
        disc_no=meta.get("disc_no") or 1,
        genre=meta.get("genre"),
        genre_source=meta.get("genre_source"),
        isrc=meta.get("isrc") or q.isrc,
        explicit=meta.get("explicit") if meta.get("explicit") is not None else cand.explicit,
        deezer_track_id=meta.get("deezer_track_id"),
        ref=meta.get("track_ref") if not str(meta.get("track_ref") or "").startswith(("nd:", "file:")) else None,
        track_id=meta.get("track_id"),  # a file chosen for a track known by its name alone: it is that track's
        deezer_album_id=meta.get("deezer_album_id"),
        upc=meta.get("upc"),
        cover=cover,
        source=cand.provider,
        source_url=cand.page_url,
        source_upload_date=cand.upload_date,
        duration_s=round(check.info.duration_s) if check.info and check.info.duration_s else q.duration_s,
        bitrate_kbps=check.info.bitrate_kbps if check.info else cand.bitrate_kbps,
        codec=check.info.codec if check.info else None,
        warnings=list(check.warnings or []) + ([warning] if warning else []),
        added_by=user_id,
        force=force,
    )
    await update_item(item_id, status="importing")
    async with session_scope() as s:
        track = await import_file(s, path, spec)
        track_id = track.id
    await update_item(item_id, track_id=track_id, warnings=spec.warnings, error=None)
    await step(item_id, "ok", "Теги и обложка записаны, файл в библиотеке"
               + (f". Замечания: {'; '.join(str(w) for w in spec.warnings)}" if spec.warnings else ""))
    bus.publish(user_id, "library", {"kind": "imported", "track_id": track_id})
    if meta.get("track_ref"):
        from app.listen import forget

        await forget(meta["track_ref"])  # from now on listening plays the library file
    await after_import(user_id, linked=False)


async def after_import(user_id: int, linked: bool) -> None:
    if not linked:
        async with session_scope() as s:
            await enqueue_scan(s)
    notify()


@on_final_failure("download_item")
async def _download_failed(job: Job, exc: Exception) -> None:
    if prepared := job.payload_json.get("prepared_file"):
        cleanup_dir((get_settings().staging_dir / prepared).parent)
    async with session_scope() as s:
        item = await s.get(DownloadItem, int(job.payload_json["item_id"]))
        if item is None or item.status == "cancelled" or (item.query_json or {}).get("attachment_file") != job.payload_json.get("prepared_file"):
            return
        meta = dict(((item.query_json if item else None) or {}).get("meta") or {})
    await upgrade.mark(meta, state="none", note=f"поиск не удался: {exc}"[:300])
    await update_item(int(job.payload_json["item_id"]), status="failed", error=f"{exc}"[:500])
    await step(int(job.payload_json["item_id"]), "bad", f"Итог: загрузка не удалась — {exc}"[:500])


# --- scan + link + playlists ---------------------------------------------------------------------


async def enqueue_scan(s: AsyncSession, *, retry_pass: int = 0, retry_priority: int = 0) -> None:
    """Batch background files from the first arrival and bring the pending scan forward for manual changes."""
    background = in_background()
    priority = retry_priority if retry_pass else BACKGROUND_SCAN_PRIORITY if background else 0
    delay = 10 if retry_pass else BACKGROUND_SCAN_DELAY_S if background else 1

    # Concurrent writers share one pending scan. The worker skips this row while its schedule is being updated.
    await s.execute(select(func.pg_advisory_xact_lock(SCAN_SCHEDULE_LOCK)))
    pending = await s.scalar(select(Job).where(Job.kind == "nd_scan", Job.status == "queued")
                             .order_by(Job.run_after, Job.id).limit(1).with_for_update())
    if pending is None:
        await enqueue(s, "nd_scan", {"pass": retry_pass}, delay_s=delay, priority=priority)
        return

    pending.run_after = min(pending.run_after, utcnow() + timedelta(seconds=delay))
    pending.priority = max(pending.priority, priority)
    if not retry_pass and (pending.payload_json or {}).get("pass"):
        pending.payload_json = {"pass": 0}


@handler("nd_scan")
async def handle_scan(job: Job) -> None:
    nd = admin_client()
    try:
        await nd.scan_and_wait(poll_s=get_settings().nd_scan_poll_s)
        linked = await link_unlinked(nd)
    finally:
        await nd.aclose()
    log.info("navidrome scan done, linked %s tracks", linked)
    from app.library import songs as library_songs
    from app.playlists_mirror import enqueue_stale

    library_songs.forget()  # Navidrome has new songs: lists are drawn from a fresh look at it
    async with session_scope() as s:
        await enqueue_stale(s)  # playlists whose tracks have got a file: their mirrors follow
    async with session_scope() as s:
        # new music: its lyrics are looked for in the background, so the player has them at once
        from app.lyrics.service import enqueue_scan_once

        await enqueue_scan_once(s, delay_s=20)
        # items whose track is now visible in Navidrome are done
        rows = (
            await s.execute(
                select(DownloadItem, Track.nd_song_id)
                .join(Track, Track.id == DownloadItem.track_id)
                .where(DownloadItem.status == "importing")
            )
        ).all()
        done_items = []
        for item, nd_id in rows:
            if nd_id:
                item.status = "done"
                done_items.append(item)
        await s.flush()
        links = await item_links(s, done_items)
        from app.favorites import deliver_pending
        from app.plays import attach_tracks

        await attach_tracks(s)  # what was listened to from the catalog is this track's history now
        await deliver_pending(s)
        await restar_replaced(s, done_items)
        await imports_done(s, done_items)
        await mixes_done(s, done_items)
        still = (
            await s.execute(select(func.count()).select_from(TrackFile).where(TrackFile.nd_song_id.is_(None)))
        ).scalar_one()
    for item in done_items:
        bus.publish(item.user_id, "download", item_out(item, links=links[item.id]).model_dump(mode="json"))
    if not still:
        return
    # Navidrome may need another pass (file just landed while the scan was running) — a few, not forever: each pass is
    # a new job (its `attempts` start at 0), so the passes are counted in the payload. A file landing later enqueues
    # a scan of its own.
    passes = int((job.payload_json or {}).get("pass") or 0)
    if passes < SCAN_PASSES:
        async with session_scope() as s:
            await enqueue_scan(s, retry_pass=passes + 1, retry_priority=job.priority)
        notify()
    else:
        log.warning("navidrome scan: %s tracks still not found in Navidrome", still)


def _same_path(nd_path: str | None, rel: str) -> bool:
    """Navidrome reports the real path with its music folder prefix (e.g. `/music/Artist/...`)."""
    if not nd_path:
        return False
    nd_path, rel = _nfc(nd_path), _nfc(rel)
    return nd_path == rel or nd_path.endswith("/" + rel)


async def link_unlinked(nd) -> int:
    """Find Navidrome song ids for files that don't have one yet (search3 by title, match by real path)."""
    async with session_scope() as s:
        rows = (await s.execute(select(TrackFile, Track).join(Track, Track.id == TrackFile.track_id)
                                .where(TrackFile.nd_song_id.is_(None)).limit(500))).all()
        taken: set[str] = set()
        n = 0
        for f, t in rows:
            hit = None
            # NFC: Navidrome finds nothing by a decomposed title («и» + U+0306 for «й»); its album and artist are
            # asked next for a title it still can't find
            for i, q in enumerate(dict.fromkeys(_nfc(x) for x in (t.title, t.album, t.artist) if x and x.strip())):
                try:
                    res = await nd.search3(q, song_count=100)
                except Exception as e:
                    log.warning("search3 failed for track %s: %r", t.id, e)
                    continue
                songs = res.get("song", [])
                hit = next((x for x in songs if _same_path(x.get("path"), f.path)), None)
                if hit is None and i == 0:
                    hit = next(
                        (
                            x for x in songs
                            if _nfc(x.get("title")) == _nfc(t.title) and _nfc(x.get("album")) == _nfc(t.album)
                            and (t.duration_s is None or abs((x.get("duration") or 0) - t.duration_s) <= 2)
                            and x["id"] not in taken
                        ),
                        None,
                    )
                if hit:
                    break
            if hit:
                f.nd_song_id = hit["id"]
                taken.add(hit["id"])
                n += 1
        return n


def _nfc(text: str | None) -> str:
    return unicodedata.normalize("NFC", text or "")


async def imports_done(s: AsyncSession, items: list[DownloadItem]) -> None:
    if any((i.query_json or {}).get("import") for i in items):
        from app.imports.core import downloads_done

        await downloads_done(s, items)


async def mixes_done(s: AsyncSession, items: list[DownloadItem]) -> None:
    if any((i.query_json or {}).get("mix") for i in items):
        from app.mixes.core import downloads_changed

        await downloads_changed(s, items)


async def restar_replaced(s: AsyncSession, items: list[DownloadItem]) -> None:
    """A picked file imported as one more file of a track (`replaces_song`: the song of the file before it): a ♥
    the user has on the track moves, in Navidrome, from that song to the new file's."""
    from app import playlists
    from app.auth import user_navidrome

    for i in items:
        old = (i.query_json or {}).get("replaces_song")
        row = None
        if old and i.track_id:
            row = (await s.execute(select(Track.nd_song_id, Track.ref).where(Track.id == i.track_id))).first()
        if row is None or not row[0] or row[0] == old:
            continue
        new = row[0]
        liked = await playlists.liked(s, i.user_id)
        user = await s.get(User, i.user_id)
        if user is None or (await s.execute(select(PlaylistItem.id).where(
                PlaylistItem.playlist_id == liked.id, PlaylistItem.track_id == i.track_id).limit(1))).first() is None:
            continue
        nd = user_navidrome(user)
        try:
            await nd.star(song_ids=[new])
            await nd.unstar(song_ids=[old])
            bus.publish(i.user_id, "library", {"kind": "starred", "song_ids": [new]})
        except Exception as e:
            log.warning("♥ not moved to the new file of track %s: %r", i.track_id, e)
        finally:
            await nd.aclose()


async def into_playlists(s: AsyncSession, user_id: int, playlist_ids: list[Any], track_ids: list[int]) -> None:
    """Put the tracks at the end of the user's playlists, in this order (one already there stays where it is)."""
    from app import playlists
    from app.playlists_mirror import enqueue_mirror

    for pid in dict.fromkeys(str(x) for x in playlist_ids):
        p = await playlists.own(s, user_id, int(pid)) if pid.isdigit() else None
        if p is not None and p.kind == playlists.USER and await playlists.add(s, p, track_ids):
            await enqueue_mirror(s, p.id)
            bus.publish(user_id, "library", {"kind": "playlist", "playlist_id": str(p.id)})


async def known_track(s: AsyncSession, q: TrackQuery, meta: dict[str, Any], user_id: int) -> Track | None:
    """The track a download is of, known before there is any file: the library's own if it has the song, else the
    catalog track it was asked for by — a track of the Hub from now on (ARCHITECTURE.md), so it has its place in a
    playlist at once and the file, when it comes, is that track's. None: a provider's file asked for under no
    catalog name — it is a track once imported."""
    from app.library import tracks as registry

    have = await find_owned(s, artist=q.artist, title=q.title, duration_s=q.duration_s, isrc=q.isrc,
                               catalog_ref=meta.get("track_ref"))
    if have is not None:
        return have
    ref = str(meta.get("track_ref") or "")
    src, _, ident = ref.partition(":")
    if src not in CATALOGS or not ident:
        return None
    album = meta.get("deezer_album_id")
    names, refs = meta.get("artists") or [], meta.get("artist_refs") or []
    refs = refs if len(refs) == len(names) else []
    return await registry.of_catalog(s, CatalogTrack(
        ref=ref, source=src, id=ident, title=q.title, artist=q.artist, artists=names, artist_refs=refs,
        artist_ref=refs[0] if refs else None,
        album_title=meta.get("album") or q.album, album_artist=meta.get("album_artist"), track_no=meta.get("track_no"),
        disc_no=meta.get("disc_no"), isrc=q.isrc or meta.get("isrc"), explicit=q.explicit,
        release_date=meta.get("release_date"), duration_s=q.duration_s or meta.get("duration_s"),
        album_ref=meta.get("album_ref") or (f"dz:{album}" if album else None), cover_url=meta.get("cover_url"),
        cover_small_url=meta.get("cover_url")), user_id=user_id)


async def create_items(
    s: AsyncSession,
    batch: DownloadBatch,
    entries: list[tuple[TrackQuery, dict[str, Any], Candidate | None]],
    *,
    force: bool = False,
    also_playlists: list[str] | None = None,
) -> int:
    """`also_playlists`: playlists besides the batch's own that get the tracks as well. A track known already
    (`known_track`) goes into the playlists now, in the order asked for; a file that is no track yet, when it is
    imported."""
    playlist_ids = list(dict.fromkeys(x for x in (batch.target_playlist_id, *(also_playlists or [])) if x))
    known: list[int] = []
    for q, meta, cand in entries:
        query_json: dict[str, Any] = {"query": q.model_dump(), "meta": meta}
        track = await known_track(s, q, meta, batch.user_id)
        if track is not None:
            known.append(track.id)
        elif playlist_ids:
            query_json["playlists"] = playlist_ids
        item = DownloadItem(
            batch_id=batch.id,
            user_id=batch.user_id,
            query_json=query_json,
            chosen_candidate_json=cand.model_dump(mode="json") if cand else None,
            force=force,
        )
        s.add(item)
        await s.flush()
        await enqueue(s, "download_item", {"item_id": item.id})
    if playlist_ids and known:
        await into_playlists(s, batch.user_id, playlist_ids, known)
    return len(entries)


def query_from_track(t: CatalogTrack) -> TrackQuery:
    return TrackQuery(artist=t.artist, title=t.title, album=t.album_title, duration_s=t.duration_s, isrc=t.isrc,
                      explicit=t.explicit)


async def get_catalog_album(ref: str) -> CatalogAlbum:
    from fastapi import HTTPException

    from app.api.catalog import _release_record

    try:
        async with session_scope() as s:
            album, _, _, _ = await _release_record(s, ref, credited=False)
            return album
    except HTTPException as error:
        if error.status_code == 404:
            raise CatalogNotFound(ref) from error
        if error.status_code == 400:
            raise PermanentError(str(error.detail)) from error
        raise


@handler("expand_batch")
async def handle_expand_batch(job: Job) -> None:
    p = job.payload_json
    album_refs: list[str] = list(p.get("album_refs") or [])
    if p.get("artist_ref"):
        from app.api.catalog import whole_artist

        types = set(p.get("record_types") or ["album", "single", "ep"])
        async with session_scope() as s:
            _, _, _, pages = await whole_artist(s, p["artist_ref"], include_sources=False)
        # oldest first, so singles that are later included in an album don't win the album slot
        album_refs += [a.ref for a in sorted((a for page in pages for a in page.albums),
                                            key=lambda a: a.release_date or date.min)
                       if a.record_type in types]
    entries: list[tuple[TrackQuery, dict[str, Any], Candidate | None]] = []
    seen: set[str] = set()
    for ref in album_refs:
        try:
            album = await get_catalog_album(ref)
        except (CatalogNotFound, PermanentError) as e:  # one dead ref must not sink the whole batch
            log.warning("expand_batch %s: skip album %s: %r", p["batch_id"], ref, e)
            continue
        for t in album.tracks or []:
            key = t.isrc or f"{t.artist}|{t.title}".casefold()
            if key in seen:
                continue
            seen.add(key)
            entries.append((query_from_track(t), meta_from_catalog(t, album), None))
    for ref in p.get("track_refs") or []:
        try:
            t = await svc.track(ref)
        except CatalogNotFound as e:
            log.warning("expand_batch %s: skip track %s: %r", p["batch_id"], ref, e)
            continue
        entries.append((query_from_track(t), meta_from_catalog(t), None))
    async with session_scope() as s:
        batch = await s.get(DownloadBatch, int(p["batch_id"]))
        if batch is None:
            return
        await create_items(s, batch, entries, force=bool(p.get("force")), also_playlists=p.get("also_playlists"))
    notify()
    bus.publish(batch.user_id, "batch", {"batch_id": batch.id, "queued": len(entries)})
