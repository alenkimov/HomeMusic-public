"""What a matched import row does in HomeMusic: a track of the Hub, its place in the playlists, its ♥, its download.

- A row is a track of the Hub at once (`track_of`): a matched one is named as the catalog names it — whether the
  library has its file yet or not; one the catalog does not know is a track known by its name alone
  (`tracks.by_name`) — there is nothing of it to play until the user gives it a file (`pick`), yet it is liked, put
  into playlists and tagged like any other.
- Playlists: a playlist of the service is a playlist of the Hub (`sync_playlists`), whole: every track in the
  service's order and with the date it was added there; it is settled at most every `core.PLAYLISTS_DELAY_S` while
  downloads come in — never on every track.
- ♥: the liked tracks are in «Любимое» in the service's order (`favorites.project_liked`); in Navidrome the Hub stars
  the liked songs itself, in that order (`sync_likes`): one by one from the oldest like, and a song downloaded later
  gets its place by starring the likes newer than it again. Likes are downloaded oldest first, so a new song mostly
  lands on top and costs one star.
- Downloads are the Hub's own business (`DownloadItem.query_json["import"]`): low priority, not shown as downloads;
  each is of the catalog's track the row was matched with (`queue_track`), as a download asked for from the search.
- «После переноса» (`apply_extras`): what the user asked for every transferred track — tags, other playlists, a ♥.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import CATALOGS, CatalogTrack, split_ref
from app.favorites import parse_time
from app.imports.resolve import Resolution
from app.jobs.queue import PRIORITY_IMPORT, enqueue
from app.library.owned import ACTIVE_STATUSES, find_owned
from app.matching.normalize import normalize_artist
from app.mixes.resolve import remembered_track
from app.models import (
    Album,
    DownloadBatch,
    DownloadItem,
    Import,
    ImportCollection,
    ImportItem,
    Track,
    User,
    utcnow,
)
from app.providers.base import TrackQuery

log = logging.getLogger(__name__)

STAR_CHUNK = 200


def _nd(user: User):
    from app.auth import user_navidrome

    return user_navidrome(user)


# --- decisions ---------------------------------------------------------------------------------------------


async def decide(s: AsyncSession, imp: Import, it: ImportItem, kind: str, r: Resolution | None) -> None:
    """A matched row (`r`; None: owned since the fetch) → its status and, by the import's mode, a download."""
    if r is not None:
        it.match, it.ref, it.score = r.match, r.ref, r.score
        if r.track is not None:
            it.track_id = r.track.id
    if kind == "dislikes":
        it.status = "skipped"  # never downloaded, never ♥: «Не нравится» for mixes and «Моя волна»
        track = await s.get(Track, it.track_id) if it.track_id else None
        if it.match != "not_found" and (it.ref or (track and track.nd_song_id)):
            from app.plays import add_dislike

            raw = it.raw_json or {}
            await add_dislike(s, imp.user_id, ref=it.ref, nd_song_id=track.nd_song_id if track else None,
                              artist=raw.get("artist"), title=raw.get("title"), source="yandex_import")
        return
    if kind == "artists":
        it.status = "done" if it.match != "not_found" else "not_found"  # ★ at the end, if the library has them
        return
    if kind == "albums":
        if it.match == "not_found":
            it.status = "not_found"
        elif await album_owned(s, it.ref):
            it.status = "done"
        elif imp.mode == "full" and (imp.options_json or {}).get("download_albums"):
            await queue_album(s, imp, it)
        else:
            it.status = "waiting"
        return
    if it.match == "owned":
        it.status = "done"
    elif it.match == "not_found":
        it.status = "not_found"
    elif imp.mode == "full":
        await queue_track(s, imp, it, kind)
    else:
        it.status = "waiting"


async def _batch(s: AsyncSession, imp: Import) -> DownloadBatch:
    opts = dict(imp.options_json or {})
    batch = await s.get(DownloadBatch, opts["batch_id"]) if opts.get("batch_id") else None
    if batch is None:
        batch = DownloadBatch(user_id=imp.user_id, origin="import", title=f"Импорт: {imp.account or imp.source}")
        s.add(batch)
        await s.flush()
        imp.options_json = {**opts, "batch_id": batch.id}
    return batch


def track_query(raw: dict[str, Any]) -> TrackQuery:
    main = (raw.get("artists") or [raw.get("artist") or ""])[0]
    return TrackQuery(artist=main or raw.get("artist") or "", title=raw.get("title") or "", album=raw.get("album"),
                      duration_s=raw.get("duration_s"), isrc=raw.get("isrc"))


async def pick(s: AsyncSession, imp: Import, it: ImportItem) -> DownloadItem:
    """A row that was not found (or found wrong): a visible download by its names, without the catalog match — the
    user picks the file on its page, as for any download that needs review. The file of a row the catalog does not
    know is the file of the row's track (`tracks.by_name`): the track stays where it stands — in its playlists, in
    «Любимое» — and plays from then on."""
    previous = await s.get(DownloadItem, it.download_item_id) if it.download_item_id else None
    if previous is not None:
        manual = (previous.query_json or {}).get("pick_only")
        if manual and previous.status in (*ACTIVE_STATUSES, "needs_review"):
            return previous
        if it.match != "probable" and previous.status == "needs_review" and previous.candidates_json:
            return previous

    meta: dict[str, Any] = {}
    if it.match == "not_found" and (track := await track_of(s, it, imp.user_id)) is not None:
        it.track_id = track.id
        meta["track_id"] = track.id
    qj = {"query": track_query(it.raw_json or {}).model_dump(), "meta": meta, "import": {"id": imp.id, "item": it.id},
          "requested": True, "pick_only": True}  # a like gets its ♥ from the import, in order (`sync_likes`)
    dl = await new_download(s, imp, qj, 0)
    it.download_item_id, it.status, it.ref = dl.id, "queued", None
    return dl


async def new_download(s: AsyncSession, imp: Import, query_json: dict[str, Any], priority: int) -> DownloadItem:
    batch = await _batch(s, imp)
    item = DownloadItem(batch_id=batch.id, user_id=imp.user_id, query_json=query_json)
    s.add(item)
    await s.flush()
    await enqueue(s, "download_item", {"item_id": item.id}, priority=priority)
    return item


async def queue_track(s: AsyncSession, imp: Import, it: ImportItem, kind: str, *,
                      priority: int = PRIORITY_IMPORT) -> bool:
    """Download a matched track (once per import: a song in likes and in a playlist is one download)."""
    if not it.ref:
        return False
    twin = (await s.execute(
        select(ImportItem.download_item_id).where(ImportItem.import_id == imp.id, ImportItem.ref == it.ref,
                                                  ImportItem.download_item_id.is_not(None)).limit(1)
    )).scalar_one_or_none()
    if twin is not None:
        it.download_item_id, it.status = twin, "queued"
        return True
    from app.jobs.downloads import meta_from_catalog, query_from_track

    raw = it.raw_json or {}
    src, id_ = split_ref(it.ref)
    cat = await remembered_track(it.ref) if src in ("dz", "ym", "sp") else None
    if cat is not None:
        # the download is of the catalog's track the row was matched with — its names, its artists with their
        # records — as one asked for from the search is: what the service called the song found it, no more
        q, meta = query_from_track(cat), meta_from_catalog(cat)
        q = q.model_copy(update={"isrc": q.isrc or raw.get("isrc"), "duration_s": q.duration_s or raw.get("duration_s")})
    else:
        q, meta = track_query(raw), {"track_ref": it.ref}
        if src == "dz":
            meta["deezer_track_id"] = id_  # who it is by, the catalog says when the download asks it (`complete_meta`)
        elif raw.get("artists"):
            meta["artists"] = raw["artists"]  # no catalog keeps a record of it: the service's names are all there is
    if (track := await track_of(s, it, imp.user_id)) is not None:
        it.track_id = track.id
        meta["track_id"] = track.id
    qj: dict[str, Any] = {"query": q.model_dump(), "meta": meta, "import": {"id": imp.id, "item": it.id}}
    dl = await new_download(s, imp, qj, priority)
    it.download_item_id, it.status = dl.id, "queued"
    return True


async def queue_album(s: AsyncSession, imp: Import, it: ImportItem) -> int:
    """A liked album, downloaded whole (its downloads point back to the row: `import.album`)."""
    from app.catalog.models import CatalogNotFound
    from app.jobs.downloads import get_catalog_album, meta_from_catalog, query_from_track
    from app.jobs.queue import PermanentError

    try:
        album = await get_catalog_album(it.ref or "")
    except (CatalogNotFound, PermanentError) as e:
        log.warning("import %s: album %s: %r", imp.id, it.ref, e)
        it.status = "failed"
        return 0
    n = 0
    for t in album.tracks or []:
        if await find_owned(s, artist=t.artist, title=t.title, duration_s=t.duration_s, isrc=t.isrc,
                            catalog_ref=t.ref):
            continue
        qj = {"query": query_from_track(t).model_dump(), "meta": meta_from_catalog(t, album),
              "import": {"id": imp.id, "album": it.id}}
        await new_download(s, imp, qj, PRIORITY_IMPORT)
        n += 1
    it.status = "queued" if n else "done"
    return n


# --- state of the downloads ----------------------------------------------------------------------------------


def _import_downloads(imp: Import):
    return select(DownloadItem).where(DownloadItem.query_json["import"]["id"].as_integer() == imp.id)


async def active_downloads(s: AsyncSession, imp: Import) -> int:
    return (await s.execute(
        select(func.count()).select_from(
            _import_downloads(imp).where(DownloadItem.status.in_(ACTIVE_STATUSES)).subquery())
    )).scalar_one()


async def refresh_items(s: AsyncSession, imp: Import) -> None:
    """Rows follow their downloads; a waiting row whose track got into the library (♥, a search) is done."""
    rows = (await s.execute(
        select(ImportItem, DownloadItem).join(DownloadItem, DownloadItem.id == ImportItem.download_item_id)
        .where(ImportItem.import_id == imp.id, ImportItem.status.in_(("queued", "failed"))))).all()
    for it, dl in rows:
        if dl.status == "done":
            it.status, it.track_id = "done", dl.track_id or it.track_id
        elif dl.status in ("failed", "needs_review", "cancelled"):
            it.status = "failed"
        elif dl.status in ACTIVE_STATUSES:
            it.status = "queued"
    albums = (await s.execute(select(ImportItem).join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
                              .where(ImportItem.import_id == imp.id, ImportItem.status == "queued",
                                     ImportCollection.kind == "albums"))).scalars().all()
    for it in albums:
        left = (await s.execute(select(func.count()).select_from(
            select(DownloadItem.id).where(DownloadItem.query_json["import"]["album"].as_integer() == it.id,
                                          DownloadItem.status.in_(ACTIVE_STATUSES)).subquery()))).scalar_one()
        if not left:
            it.status = "done"
    waiting = (await s.execute(
        select(ImportItem).join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
        .where(ImportItem.import_id == imp.id, ImportItem.status == "waiting",
               ImportCollection.kind.in_(("likes", "playlist"))))).scalars().all()
    for it in waiting:
        raw = it.raw_json or {}
        lib = await find_owned(s, artist=raw.get("artist") or "", title=raw.get("title") or "",
                               duration_s=raw.get("duration_s"), catalog_ref=it.ref)
        if lib is not None:
            it.status, it.track_id = "done", lib.id
    await s.flush()


# --- Navidrome ------------------------------------------------------------------------------------------------


async def _song_ids(s: AsyncSession, items: list[ImportItem]) -> dict[int, str]:
    """import item id -> Navidrome song id of its library track."""
    track_ids = {i.track_id for i in items if i.track_id}
    if not track_ids:
        return {}
    songs = dict((await s.execute(
        select(Track.id, Track.nd_song_id).where(Track.id.in_(track_ids), Track.nd_song_id.is_not(None)))).all())
    return {i.id: songs[i.track_id] for i in items if i.track_id in songs}


def playlist_order(items: list[ImportItem], songs: dict[int, str]) -> list[str]:
    """Songs of a playlist in the service's order: what the library has, without the rest."""
    return [songs[i.id] for i in sorted(items, key=lambda i: i.position)
            if i.id in songs and i.status not in ("skipped",)]


def like_order(items: list[ImportItem], songs: dict[int, str]) -> list[str]:
    """Liked songs from the oldest like to the newest (the service lists the newest first), each once."""
    out: dict[str, None] = {}
    for i in sorted(items, key=lambda i: -i.position):
        if i.id in songs and i.status == "done":
            out.setdefault(songs[i.id], None)
    return list(out)


async def track_of(s: AsyncSession, it: ImportItem, user_id: int) -> Track | None:
    """The Hub's track of an import's row: the track of the catalog track the row was matched with, made if the Hub
    has none. It is named as the catalog names it, by the record kept when the row was matched. Where no record was
    kept (a match of older days), the track is named as the service called the song until the catalog is asked
    (`fill_catalog`): the service's words are not the catalog's and are not kept as such. The catalog is not asked
    here — rows are gone through by the hundred, inside a transaction. A row the catalog does not know is the track
    known by its name alone (`tracks.by_name`). None: a row not matched yet, or one that says nothing of its song."""
    from app.library import tracks as registry

    if it.track_id and (track := await s.get(Track, it.track_id)) is not None:
        return track
    src, _, ident = (it.ref or "").partition(":")
    if src not in CATALOGS or not ident:
        raw = it.raw_json or {}
        if it.match != "not_found" or not raw.get("title"):
            return None
        q = track_query(raw)
        return await registry.by_name(s, artist=q.artist, title=q.title, album=q.album, duration_s=q.duration_s,
                                      isrc=q.isrc, user_id=user_id)
    if (track := await registry.by_ref(s, it.ref)) is not None:
        return track
    if src in ("dz", "ym", "sp") and (cat := await remembered_track(it.ref)) is not None:
        return await registry.of_catalog(s, cat, user_id=user_id)
    raw = it.raw_json or {}
    if not raw.get("title"):
        return None
    said = CatalogTrack(ref=it.ref, source=src, id=ident, title=raw["title"], artist=raw.get("artist") or "",
                        artists=raw.get("artists") or [], album_title=raw.get("album"),
                        duration_s=raw.get("duration_s"), isrc=raw.get("isrc"), cover_url=raw.get("cover_url"),
                        cover_small_url=raw.get("cover_url"))
    return await registry.of_catalog(s, said, user_id=user_id, word=src != "dz")


async def sync_playlists(s: AsyncSession, imp: Import) -> None:
    """The playlists of the service become playlists of the Hub: every row is its track, in its place, in the
    service's order and with the date it was added there — whether the library has its file yet or not, and whether
    the catalog knows the song or not (`track_of`). A row not matched yet waits; one the user left out is not
    there."""
    from app import playlists
    from app.models import naive
    from app.playlists_mirror import enqueue_mirror

    colls = (await s.execute(select(ImportCollection).where(
        ImportCollection.import_id == imp.id, ImportCollection.kind == "playlist",
        ImportCollection.selected.is_(True)))).scalars().all()
    for c in colls:
        given = list(c.given or [])
        already_given = set(given)
        items = list((await s.execute(select(ImportItem).where(ImportItem.collection_id == c.id)
                                      .order_by(ImportItem.position, ImportItem.id))).scalars())
        want: list[int] = []
        dates: dict[int, datetime] = {}
        for it in items:
            if it.status in ("skipped", "pending"):
                continue
            if it.track_id is None and (track := await track_of(s, it, imp.user_id)) is not None:
                it.track_id = track.id
            if it.track_id is None or it.track_id in dates:
                continue
            want.append(it.track_id)
            dates[it.track_id] = naive(parse_time((it.raw_json or {}).get("liked_at"))) or utcnow()
        p = await playlists.own(s, imp.user_id, c.playlist_id) if c.playlist_id else None
        if p is None:
            if c.given:
                # the playlist was made of this collection and is gone: the user deleted it. It is not made again —
                # the collection is let go, its tracks stay in the library
                c.selected, c.playlist_id = False, None
                continue
            if not want:
                continue
            p = await playlists.create(s, imp.user_id, c.title)
            c.playlist_id = p.id
        # Previously offered tracks can be absent because the listener removed them. An update of another
        # row must never make those tracks new again.
        fresh = [tid for tid in want if tid not in already_given]
        if not fresh:
            continue
        for track_id in fresh:
            await playlists.add(s, p, [track_id], at=dates[track_id])
        # the service's order for its tracks; what the user put in by hand stays after them
        rows = await playlists.items(s, p.id)
        first = {t.id: i.id for i, t in reversed(rows)}
        await playlists.reorder(s, p, [first[t] for t in want if t in first])
        c.given = [*given, *fresh]
        await enqueue_mirror(s, p.id)
    if colls:
        from app.lyrics.service import enqueue_scan_once

        await enqueue_scan_once(s, delay_s=30)
    await s.flush()


async def _likes(s: AsyncSession, imp: Import) -> list[ImportItem]:
    return list((await s.execute(
        select(ImportItem).join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
        .where(ImportItem.import_id == imp.id, ImportCollection.kind == "likes",
               ImportCollection.selected.is_(True)))).scalars())


def restar_plan(starred: list[str], want: list[str]) -> list[str]:
    """Songs to ♥ again, in this order, so that the import's ♥ follow `want` (oldest like first): what the import starred
    before (`starred`) stays as long as it matches `want` from the start; the rest is unstarred and starred anew. A
    newer like that arrives costs one star; an older one, the likes after it."""
    k = 0
    while k < min(len(starred), len(want)) and starred[k] == want[k]:
        k += 1
    return want[k:]


_likes_lock = asyncio.Lock()


async def sync_likes(s: AsyncSession, imp: Import) -> None:
    """♥ of the import's likes in the service's order. Navidrome lists «Любимое» by the time of the star, newest
    first, so the songs are starred one by one from the oldest like; a song that arrives later (a download) is starred
    with the likes newer than it again (`restar_plan`). A song the user has unstarred since is left alone."""
    user = await s.get(User, imp.user_id)
    if user is None:
        return
    async with _likes_lock:  # one rewrite at a time: two would interleave their stars
        await s.refresh(imp, ["options_json"])
        items = await _likes(s, imp)
        songs = await _song_ids(s, items)
        opts = dict(imp.options_json or {})
        prev = list(opts.get("liked") or [])
        nd = _nd(user)
        try:
            if prev:
                have = {x.get("id") for x in (await nd.get_starred2()).get("song", [])}
                gone = set(prev) - have
                if gone:
                    for i in items:
                        if i.status == "done" and songs.get(i.id) in gone:
                            i.status, i.raw_json = "skipped", {**(i.raw_json or {}), "unliked": True}
                    prev = [x for x in prev if x not in gone]
            want = like_order(items, songs)
            again = restar_plan(prev, want)
            for k in range(0, len(again), STAR_CHUNK):
                await nd.unstar(song_ids=again[k:k + STAR_CHUNK])
            for song_id in again:
                await nd.star(song_ids=[song_id])
        except Exception as e:
            log.warning("import %s: likes: %r", imp.id, e)
            return
        finally:
            await nd.aclose()
        # Matching can update other options while this job is talking to Navidrome.
        await s.execute(update(Import).where(Import.id == imp.id)
                        .values(options_json=Import.options_json.concat({"liked": want})))
        await s.refresh(imp, ["options_json"])
    if again:
        from app.events import bus

        bus.publish(imp.user_id, "library", {"kind": "starred", "song_ids": again})


async def apply_extras(s: AsyncSession, imp: Import) -> None:
    """Give resolved tracks their selected destinations. Apply tags when the transfer item is done."""
    extra = (imp.options_json or {}).get("extra") or {}
    star, playlist_ids, tag_ids = bool(extra.get("star")), list(extra.get("playlist_ids") or []), list(extra.get("tag_ids") or [])
    has_destinations = star or bool(playlist_ids)
    if not (has_destinations or tag_ids):
        return
    user = await s.get(User, imp.user_id)
    if user is None:
        return
    items = (await s.scalars(
        select(ImportItem).join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
        .where(ImportItem.import_id == imp.id, ImportItem.status.not_in(("pending", "skipped")),
               ImportCollection.kind.in_(("likes", "playlist")), ImportCollection.selected.is_(True))
        .order_by(ImportCollection.id, ImportItem.position))).all()
    placed_ids = {i.track_id for i in items if (i.raw_json or {}).get("extra") or (i.raw_json or {}).get("extra_placed")}
    to_place = []
    to_tag = []
    for item in items:
        raw = item.raw_json or {}
        if raw.get("extra"):
            continue
        place_now = has_destinations and not raw.get("extra_placed")
        tag_now = bool(tag_ids) and item.status == "done"
        if not (place_now or tag_now):
            continue
        if item.track_id is None:
            track = await track_of(s, item, imp.user_id)
            if track is None:
                continue
            item.track_id = track.id
        if place_now:
            to_place.append(item)
        if tag_now:
            to_tag.append(item)

    track_ids = list(dict.fromkeys(i.track_id for i in to_place if i.track_id not in placed_ids))
    try:
        if playlist_ids and track_ids:
            from app.jobs.downloads import into_playlists

            await into_playlists(s, imp.user_id, playlist_ids, track_ids)
        if star and track_ids:
            from app.api.schemas import FavoriteTarget
            from app.favorites import command

            # through the favorites journal, as a ♥ set by hand: «Любимое» and mixes see it the same way. The like is
            # the track's own: Navidrome gets the ♥ once its scan has found the file (`deliver_pending`)
            await command(s, user, [FavoriteTarget(track_id=t) for t in track_ids], liked=True, client="hub",
                          context="import")
        # Keep track of placement separately while tags are waiting on a file.
        marker = "extra_placed" if tag_ids else "extra"
        for item in to_place:
            item.raw_json = {**(item.raw_json or {}), marker: True}
        if to_tag:
            from app import tags

            await tags.assign_tracks(s, imp.user_id, list(dict.fromkeys(i.track_id for i in to_tag)), tag_ids)
            for item in to_tag:
                item.raw_json = {**(item.raw_json or {}), "extra": True}
    except Exception as e:  # the next settling of the import tries these tracks again
        log.warning("import %s: extras: %r", imp.id, e)
        return
    if track_ids:
        await s.flush()
        from app.events import bus

        bus.publish(imp.user_id, "library", {"kind": "playlist", "playlist_id": playlist_ids[0] if playlist_ids else None,
                                             "star": star})


async def album_owned(s: AsyncSession, ref: str | None) -> bool:
    album_id = await _library_album_id(s, ref)
    return bool(album_id and await s.scalar(select(Track.id).where(Track.album_id == album_id).limit(1)))


async def _library_album_id(s: AsyncSession, ref: str | None) -> int | None:
    from app.catalog import registry
    from app.library.album_names import catalog_uid

    if not ref:
        return None
    record = await registry.by_address(s, ref)
    if record and record.album_id is not None:
        return record.album_id
    if registry.numeric_id(ref) is not None:
        return None
    source, ident = split_ref(ref)
    clauses = [Album.uid == catalog_uid(ref)]
    if source == "dz":
        clauses.append(Album.deezer_album_id == ident)
    return await s.scalar(select(Album.id).where(or_(*clauses)).order_by(Album.id).limit(1))


async def star_albums_artists(s: AsyncSession, imp: Import) -> None:
    """★ liked albums and artists the library has (Navidrome stars only what it knows)."""
    rows = (await s.execute(
        select(ImportItem, ImportCollection.kind).join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
        .where(ImportItem.import_id == imp.id, ImportCollection.kind.in_(("albums", "artists")),
               ImportCollection.selected.is_(True), ImportItem.status.in_(("done", "waiting", "queued"))))).all()
    user = await s.get(User, imp.user_id)
    if not rows or user is None:
        return
    nd = _nd(user)
    album_ids: list[str] = []
    artist_ids: list[str] = []
    try:
        for it, kind in rows:
            if kind == "albums" and (album_id := await _library_album_id(s, it.ref)) is not None:
                song = await s.scalar(select(Track.nd_song_id).where(
                    Track.album_id == album_id, Track.nd_song_id.is_not(None)).limit(1))
                if song and (nd_album := (await nd.get_song(song)).get("albumId")):
                    album_ids.append(nd_album)
                    from app.api.schemas import FavoriteTarget
                    from app.favorites import remember
                    await remember(s, FavoriteTarget(target_type="album", ref=it.ref, nd_album_id=nd_album,
                                                      target_data=it.raw_json))
            elif kind == "artists":
                name = (it.raw_json or {}).get("name") or ""
                want = normalize_artist(name).text
                found = (await nd.search3(name, song_count=0, artist_count=5)).get("artist", [])
                hit = next((a for a in found if normalize_artist(a.get("name")).text == want), None)
                if hit:
                    artist_ids.append(hit["id"])
                    from app.api.schemas import FavoriteTarget
                    from app.favorites import remember
                    await remember(s, FavoriteTarget(target_type="artist", ref=it.ref, nd_artist_id=hit["id"],
                                                      target_data={"name": name}))
        if album_ids or artist_ids:
            await nd.star(album_ids=album_ids, artist_ids=artist_ids)
            opts = imp.options_json or {}
            imp.options_json = {**opts,
                "liked_album_ids": list(dict.fromkeys([*(opts.get("liked_album_ids") or []), *album_ids])),
                "liked_artist_ids": list(dict.fromkeys([*(opts.get("liked_artist_ids") or []), *artist_ids]))}
    except Exception as e:
        log.warning("import %s: star albums/artists: %r", imp.id, e)
    finally:
        await nd.aclose()


async def cancel_downloads(s: AsyncSession, imp: Import) -> int:
    """The import's own downloads that have not started (one the user asked for explicitly stays)."""
    n = 0
    for dl in (await s.execute(_import_downloads(imp).where(DownloadItem.status == "queued"))).scalars():
        if not (dl.query_json or {}).get("requested"):
            dl.status = "cancelled"
            n += 1
    await s.flush()
    return n


# --- ♥ of tracks the library does not have yet -----------------------------------------------------------------


def _user_likes(user_id: int):
    return (select(ImportItem).join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
            .join(Import, Import.id == ImportItem.import_id)
            .where(Import.user_id == user_id, ImportCollection.kind == "likes", ImportCollection.selected.is_(True),
                   Import.status.in_(("running", "paused", "done")), ImportItem.ref.is_not(None)))


async def unlike_refs(s: AsyncSession, user_id: int, refs: list[str]) -> None:
    """♥ taken off a track liked in another service: it is no longer liked nor downloaded for that."""
    for it in (await s.execute(_user_likes(user_id).where(ImportItem.ref.in_(refs),
                                                          ImportItem.status.in_(("waiting", "queued"))))).scalars():
        if it.download_item_id:
            dl = await s.get(DownloadItem, it.download_item_id)
            if dl is not None and dl.status == "queued" and not (dl.query_json or {}).get("requested"):
                dl.status = "cancelled"
        it.status = "skipped"
    await s.flush()
