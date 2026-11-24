"""A track as every list shows it — its card — drawn once and kept (`track_cards`), not drawn anew for each list on
each look at it. A list of thousands of tracks took seconds of the Hub's time to draw track by track from a dozen
sources (the track, its files, its album's cover, the artists its credits name, its versions, the last searches of
its recording, Navidrome's song), on every read, and again after every event of a transfer.

A card is what is the same for everybody. What is somebody's of a track — what their transfers say of it, when they
put it in a list — is laid over it by the list (`app.api.playlists.tracks_out`).

Drawing (`draw`) is one: the kept cards are drawn by it and so is a card read before it is kept. What a card was made
of is known to the database, which marks a card stale itself when any of it changes (`migrations`, `track_cards`
triggers): a track, its files and covers, its album, the artists of its names, its versions, its recording's searches,
the transfers' rows of it. A read takes a stale or missing card drawn anew (`read`); the Hub draws them in the
background too (`keep_fresh`)."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import NdSongOut
from app.catalog.models import CATALOGS
from app.models import Track

log = logging.getLogger(__name__)


def track_without_file(t: Track, *, downloading: bool = False) -> NdSongOut:
    """A track the library has no file of, drawn from what the catalog said of it."""
    c = t.catalog or {}
    ref = t.ref or f"lib:{t.id}"
    src, _, ident = ref.partition(":")
    return NdSongOut(
        ref=ref, source=src if src in CATALOGS else "nd", id=ident, title=t.title, artist=t.artist,
        artists=c.get("artists") or [t.artist], artist_refs=c.get("artist_refs") or [], artist_ref=c.get("artist_ref"),
        album_ref=c.get("album_ref"), album_title=t.album, album_artist=t.album_artist, duration_s=t.duration_s,
        track_no=t.track_no, disc_no=t.disc_no, isrc=t.isrc, explicit=t.explicit, cover_url=c.get("cover_url"),
        cover_small_url=c.get("cover_small_url") or c.get("cover_url"), owned=False, library_track_id=t.id,
        spotify_id=c.get("spotify_id"), spotify_plays=c.get("spotify_plays"),
        downloading=downloading, unknown=not t.ref)


def track_with_file(t: Track) -> NdSongOut:
    """A track of the library drawn from the Hub's own record: for when Navidrome has not been asked about its
    song yet, or does not answer."""
    out = track_without_file(t)
    out.owned, out.nd_song_id, out.unknown = True, t.nd_song_id, False
    if not t.ref and t.nd_song_id:  # the catalog does not know it: it is named by its song, as everywhere
        out.ref, out.source, out.id = f"nd:{t.nd_song_id}", "nd", t.nd_song_id
    return out


async def draw_songs(s: AsyncSession, songs: list[dict]) -> list[NdSongOut]:
    """Navidrome songs as tracks: the Hub's own record of the file gives the Deezer ref (mixes, «Моя волна» and
    listening data know the song by it), the rest are `nd:<song>`. The second way to draw a track, beside `draw`
    (which takes it for the tracks with a file): a list of Navidrome's songs reads kept cards (`of_songs`)."""
    from datetime import datetime

    from sqlalchemy import select

    from app.api.medialib import BIG_COVER, cover_url, with_artist_links
    from app.api.schemas import NdSongOut
    from app.library.owned import put_right, with_cover_fallbacks, with_files
    from app.mixes.resolve import library_track
    from app.models import Track, TrackFile

    ids = [x["id"] for x in songs if x.get("id")]
    rows: dict[str, Track] = {}
    acquired: dict[str, datetime] = {}
    for i in range(0, len(ids), 500):
        q = (select(Track, TrackFile.nd_song_id, TrackFile.acquired_at).join(TrackFile, TrackFile.track_id == Track.id)
             .where(TrackFile.nd_song_id.in_(ids[i:i + 500])))
        for t, song, at in (await s.execute(q)).all():
            rows[song], acquired[song] = t, at
    out = []
    for x in songs:
        sid = x.get("id")
        if not sid:
            continue
        t = rows.get(sid)
        ref = library_track(t).ref if t is not None else f"nd:{sid}"
        artist = x.get("artist") or ""
        credited = [a for a in x.get("artists") or [] if a.get("name")]
        names = [a["name"] for a in credited] or ([artist] if artist else [])
        artist_ids = [a.get("id") for a in credited] or ([x.get("artistId")] if artist else [])
        cat = (t.catalog or {}) if t is not None else {}
        out.append(NdSongOut(
            ref=ref, source=ref.split(":", 1)[0], id=ref.split(":", 1)[1], title=x.get("title") or "",
            artist=artist, artists=names, album_title=x.get("album"), album_artist=x.get("displayAlbumArtist"),
            duration_s=x.get("duration"), track_no=x.get("track"), disc_no=x.get("discNumber"),
            isrc=(x.get("isrc") or [None])[0] if isinstance(x.get("isrc"), list) else x.get("isrc"),
            explicit=t.explicit if t is not None else None,
            spotify_id=cat.get("spotify_id"), spotify_plays=cat.get("spotify_plays"),
            cover_url=cover_url(x.get("coverArt"), BIG_COVER), cover_small_url=cover_url(x.get("coverArt")),
            owned=True, library_track_id=t.id if t is not None else None, nd_song_id=sid,
            added_at=acquired.get(sid),
            nd_album_id=x.get("albumId"), nd_artist_id=x.get("artistId"), nd_artist_ids=artist_ids,
        ))
        if t is not None:
            put_right(out[-1], t)  # Navidrome learns a new name at its next scan; the Hub knows it now
    await with_artist_links(s, [(song, rows.get(song.nd_song_id)) for song in out])
    await with_files(s, out)
    await with_cover_fallbacks(s, [(o, rows[o.nd_song_id]) for o in out if o.nd_song_id in rows])
    from app.library import versions

    await versions.mark(s, out)  # how many recordings the song of each has
    return out


async def of_songs(s: AsyncSession, songs: list[dict]) -> list[NdSongOut]:
    """Navidrome's songs (an album of the library, all its songs, a star of a song) as a list shows them, in their
    order: the kept card of the Hub's track of each, drawn here only where the Hub has no track of the song (a file
    put into the library by hand) or its card is of another file of the track."""
    from sqlalchemy import select

    from app.api.schemas import NdSongOut
    from app.models import TrackFile

    ids = [x["id"] for x in songs if x.get("id")]
    owner: dict[str, int] = {}
    for i in range(0, len(ids), 500):
        owner.update((await s.execute(select(TrackFile.nd_song_id, TrackFile.track_id)
                                      .where(TrackFile.nd_song_id.in_(ids[i:i + 500])))).all())
    kept = await read(s, sorted(set(owner.values())))
    carded = {sid: kept[tid] for sid, tid in owner.items() if tid in kept and kept[tid].get("nd_song_id") == sid}
    drawn = {o.nd_song_id: o for o in await draw_songs(s, [x for x in songs if x.get("id") and x["id"] not in carded])}
    return [NdSongOut.model_validate(carded[x["id"]]) if x["id"] in carded else drawn[x["id"]]
            for x in songs if x.get("id")]


async def draw(s: AsyncSession, rows: list[Track], *, songs: dict[str, dict] | None = None) -> list[NdSongOut]:
    """Tracks as a list shows them, in their order: one with a file as Navidrome's song of it (`songs`: Navidrome's
    songs by id, else its kept index), one without as the catalog's."""
    from sqlalchemy import select

    from app.api.medialib import with_artist_links
    from app.library import songs as library_songs
    from app.library.owned import with_cover_fallbacks, with_files, with_recording_availability
    from app.models import ImportItem

    index: dict[str, dict] = songs if songs is not None else {}
    if songs is None and any(t.nd_song_id for t in rows):
        try:
            index = await library_songs.index()
        except Exception as e:  # Navidrome is away: the list is drawn from the Hub's own records
            log.info("cards: songs not read from Navidrome: %r", e)
    known = [index[t.nd_song_id] for t in rows if t.nd_song_id in index]
    drawn = {x.nd_song_id: x for x in await draw_songs(s, known)}
    out = [drawn.get(t.nd_song_id) or (track_with_file(t) if t.path else track_without_file(t)) for t in rows]
    fallback = [(o, t) for o, t in zip(out, rows, strict=True) if t.nd_song_id not in drawn]
    await with_artist_links(s, fallback)
    await with_files(s, out)
    await with_cover_fallbacks(s, fallback)
    await with_recording_availability(s, out)
    # A transfer's image is still useful after a previously missing track has been given a file.
    without_cover = [x.library_track_id for x in out if not x.cover_url]
    if without_cover:
        covers = {track_id: (raw or {}).get("cover_url") for track_id, raw in (await s.execute(
            select(ImportItem.track_id, ImportItem.raw_json).where(ImportItem.track_id.in_(without_cover)))).all()}
        for x in out:
            if not x.cover_url and covers.get(x.library_track_id):
                x.cover_url = x.cover_small_url = covers[x.library_track_id]
    return out


# --- kept cards ----------------------------------------------------------------------------------------------------

# what is somebody's of a track, not its card's: a list lays it over the card
NOT_CARD = {"import_review"}
BATCH = 400


def _digest(song: dict | None) -> str | None:
    import hashlib
    import json

    return hashlib.md5(json.dumps(song, sort_keys=True, default=str).encode()).hexdigest() if song else None


async def build(track_ids: list[int]) -> dict[int, dict]:
    """Draw the cards of these tracks and keep them; returns them by track. In a session of its own: a card is kept
    whatever becomes of the request that needed it. A card the database marked stale again while it was being drawn
    stays marked (`gen`): it is drawn again."""
    from datetime import timedelta

    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from app.db import session_scope
    from app.library import artists as artist_names
    from app.library import songs as library_songs
    from app.library.owned import _track_variants
    from app.listen import _key as check_key
    from app.models import CatalogCache, TrackCard, utcnow

    out: dict[int, dict] = {}
    changed: list[int] = []
    ids = sorted(set(track_ids))
    try:
        index: dict[str, dict] | None = await library_songs.index()
    except Exception as e:  # Navidrome is away: the cards are drawn from the Hub's own records, and drawn again later
        log.info("cards: songs not read from Navidrome: %r", e)
        index = None
    for k in range(0, len(ids), BATCH):
        part = ids[k:k + BATCH]
        async with session_scope() as s:
            before = {i: (gen, card) for i, gen, card in (await s.execute(
                select(TrackCard.track_id, TrackCard.gen, TrackCard.card).where(TrackCard.track_id.in_(part)))).all()}
            gens = {i: gen for i, (gen, _) in before.items()}
            rows = list((await s.execute(select(Track).where(Track.id.in_(part)))).scalars())
            drawn = await draw(s, rows, songs=index or {})
            pending = [o for o in drawn if not o.owned and not o.unknown]
            variants = await _track_variants(s, pending)
            checks = {o.library_track_id: [check_key(r) for r in (o.ref, *variants.get(o.ref, []), f"lib:{o.library_track_id}")]
                      for o in pending}
            wanted = [c for cs in checks.values() for c in cs]
            ends = {key: at + timedelta(seconds=ttl) for key, at, ttl in (await s.execute(
                select(CatalogCache.key, CatalogCache.fetched_at, CatalogCache.ttl_s)
                .where(CatalogCache.key.in_(wanted)))).all()} if wanted else {}
            now = utcnow()
            values = []
            for t, o in zip(rows, drawn, strict=True):
                deps = {f"k:{artist_names.key(n)}" for n in (o.artist, *o.artists) if n}
                deps |= {f"r:{r}" for r in artist_names.refs_of(t.catalog).values()}
                deps |= {f"a:{i}" for i in (o.hub_artist_id, *o.hub_artist_ids) if i}
                deps |= {f"c:{c}" for c in checks.get(t.id, ())}
                if o.album_ref:
                    deps.add(f"g:{o.album_ref}")
                live = [ends[c] for c in checks.get(t.id, ()) if c in ends and ends[c] >= now]
                card = o.model_dump(mode="json", exclude=NOT_CARD)
                out[t.id] = card
                if t.id in before and before[t.id][1] != card:
                    changed.append(t.id)
                values.append({"track_id": t.id, "card": card, "deps": sorted(deps),
                               "nd_digest": _digest(index.get(t.nd_song_id)) if index and t.nd_song_id else None,
                               "valid_until": min(live) if live else None,
                               # drawn without Navidrome (no digest): the next look at its songs draws it again
                               "dirty": False, "gen": gens.get(t.id, 0), "built_at": now})
            if values:
                q = insert(TrackCard).values(values)
                await s.execute(q.on_conflict_do_update(index_elements=[TrackCard.track_id], set_={
                    "card": q.excluded.card, "deps": q.excluded.deps, "nd_digest": q.excluded.nd_digest,
                    "valid_until": q.excluded.valid_until, "built_at": q.excluded.built_at,
                    # marked again while it was drawn: it stays to be drawn again
                    "dirty": TrackCard.gen != q.excluded.gen,
                    "gen": TrackCard.gen,
                }))
        # kept now (the session is over): whoever shows these tracks takes their new cards (`POST /api/tracks/cards`)
        if changed:
            from app.events import bus

            bus.broadcast("cards", {"ids": changed})
            changed = []
    return out


async def read(s: AsyncSession, track_ids: list[int]) -> dict[int, dict]:
    """The cards of these tracks by track: the kept ones, and those missing, marked stale or run out drawn now."""
    from sqlalchemy import or_, select

    from app.models import TrackCard, utcnow

    ids = list(dict.fromkeys(i for i in track_ids if i))
    out: dict[int, dict] = {}
    now = utcnow()
    for k in range(0, len(ids), 5000):
        part = ids[k:k + 5000]
        out.update((await s.execute(select(TrackCard.track_id, TrackCard.card).where(
            TrackCard.track_id.in_(part), TrackCard.dirty.is_(False),
            or_(TrackCard.valid_until.is_(None), TrackCard.valid_until >= now)))).all())
    if stale := [i for i in ids if i not in out]:
        out.update(await build(stale))
    return out


async def ensure(s: AsyncSession, track_ids: list[int]) -> None:
    """The cards of these tracks kept and fresh: those missing, marked stale or run out drawn now (the fresh ones
    are not read — a list that the database draws from them, `list_json`, needs them there, not here)."""
    from sqlalchemy import or_, select

    from app.models import TrackCard, utcnow

    ids = list(dict.fromkeys(i for i in track_ids if i))
    fresh: set[int] = set()
    now = utcnow()
    for k in range(0, len(ids), 5000):
        fresh.update((await s.execute(select(TrackCard.track_id).where(
            TrackCard.track_id.in_(ids[k:k + 5000]), TrackCard.dirty.is_(False),
            or_(TrackCard.valid_until.is_(None), TrackCard.valid_until >= now)))).scalars())
    if stale := [i for i in ids if i not in fresh]:
        await build(stale)


async def list_json(s: AsyncSession, playlist_id: int, user_id: int, *, extra: list[dict] | None = None) -> str:
    """A playlist's tracks as a list gives them — a JSON array, made by the database of the kept cards in the
    playlist's order: none of thousands of cards is parsed or written out here. Over each card: when it was put in
    the list, and what the user's transfers say of it (`imports.review.word`). `extra`: tracks the list has beside
    the playlist (drawn already, each with its `added_at`); with them the list is ordered by when each was added."""
    import json

    from sqlalchemy import text

    from app import playlists
    from app.imports.review import word

    ids = await playlists.track_ids(s, playlist_id)
    await ensure(s, ids)
    reviews, failed = await word(s, user_id, set(ids))
    said = json.dumps({str(i): {"import_review": r.model_dump(mode="json")} for i, r in reviews.items()})
    rows = """
        SELECT c.card || jsonb_build_object('added_at', to_jsonb(pi.added_at), 'import_review', NULL::jsonb)
                      || coalesce(CAST(:said AS jsonb) -> pi.track_id::text, '{}'::jsonb)
                      || CASE WHEN pi.track_id = ANY(:failed) AND NOT (c.card->>'owned')::boolean
                              THEN '{"unavailable": true}'::jsonb ELSE '{}'::jsonb END AS song,
               pi.added_at AS at, pi.position AS pos, pi.id AS id
        FROM playlist_items pi JOIN track_cards c ON c.track_id = pi.track_id
        WHERE pi.playlist_id = :pid"""
    if extra:
        q = f"""
            WITH rows AS ({rows}
                UNION ALL
                SELECT e.value, (e.value->>'added_at')::timestamp, 2147483647, e.ordinality
                FROM jsonb_array_elements(CAST(:extra AS jsonb)) WITH ORDINALITY e)
            SELECT coalesce(json_agg(song ORDER BY at DESC NULLS LAST, pos, id), '[]')::text FROM rows"""
    else:
        q = f"SELECT coalesce(json_agg(song ORDER BY pos, id), '[]')::text FROM ({rows}) rows"
    params = {"pid": playlist_id, "said": said, "failed": sorted(failed)}
    if extra:
        params["extra"] = json.dumps(extra)
    return (await s.execute(text(q), params)).scalar_one()


async def list_tag(s: AsyncSession, playlist_id: int, user_id: int, *more: str) -> str:
    """The version of what `list_json` gives for the playlist (an ETag): its rows and their cards as kept, and what
    the user's transfers say of them — told by one aggregate, without making the list. `more`: what else the answer
    holds (the tracks beside the playlist, Navidrome's stars)."""
    import hashlib
    import json

    from sqlalchemy import text

    from app import playlists
    from app.imports.review import word

    ids = await playlists.track_ids(s, playlist_id)
    await ensure(s, ids)
    reviews, failed = await word(s, user_id, set(ids))
    rows = (await s.execute(text("""
        SELECT md5(coalesce(string_agg(concat_ws(':', pi.track_id, pi.position, pi.added_at, c.built_at),
                                       ',' ORDER BY pi.position, pi.id), ''))
        FROM playlist_items pi JOIN track_cards c ON c.track_id = pi.track_id WHERE pi.playlist_id = :pid"""),
        {"pid": playlist_id})).scalar_one()
    said = json.dumps([sorted((i, r.model_dump(mode="json")["id"]) for i, r in reviews.items()), sorted(failed), *more])
    return '"' + hashlib.md5(f"{rows}|{said}".encode()).hexdigest() + '"'


def unchanged(if_none_match: str | None, tag: str) -> bool:
    """Whether a request's `If-None-Match` names this version. What the browser sends back is the tag as it got it,
    and a compressing proxy (Caddy's `encode`) gives it changed: weak (`W/"…"`) or with the encoding added
    (`"…-gzip"`)."""
    if not if_none_match:
        return False
    core = tag.strip('"')
    for part in if_none_match.split(","):
        part = part.strip().removeprefix("W/").strip('"')
        if part == "*" or part == core or part.rsplit("-", 1)[0] == core:
            return True
    return False


# --- in the background ---------------------------------------------------------------------------------------------

# Navidrome's songs as the cards were last compared with them (`songs.loaded_at`): read anew — after a scan the Hub
# made, or once they are ten minutes old — they are compared again
_compared_at = -1.0


async def _compare_songs() -> int:
    """Mark the cards whose Navidrome song is not what they were drawn from (one it has now, or has no more), once
    for each reading of its songs."""
    global _compared_at
    from sqlalchemy import select, update

    from app.db import session_scope
    from app.library import songs as library_songs
    from app.models import TrackCard, TrackFile

    index = await library_songs.index()
    if library_songs.loaded_at() == _compared_at:
        return 0
    _compared_at = library_songs.loaded_at()
    async with session_scope() as s:
        rows = (await s.execute(select(TrackCard.track_id, TrackCard.nd_digest, TrackFile.nd_song_id)
                                .outerjoin(TrackFile, TrackFile.track_id == TrackCard.track_id))).all()
        drawn: dict[int, str | None] = {}
        now: dict[int, set[str | None]] = {}
        for track_id, digest, song in rows:
            drawn[track_id] = digest
            now.setdefault(track_id, set()).add(_digest(index.get(song)) if song else None)
        stale = [i for i, digest in drawn.items() if digest not in now[i]]
        for k in range(0, len(stale), 5000):
            await s.execute(update(TrackCard).where(TrackCard.track_id.in_(stale[k:k + 5000]))
                            .values(dirty=True, gen=TrackCard.gen + 1))
    return len(stale)


async def refresh_some(limit: int = BATCH) -> int:
    """Draw some of the cards that are stale, run out or missing; returns how many."""
    from sqlalchemy import or_, select

    from app.db import session_scope
    from app.models import TrackCard, utcnow

    async with session_scope() as s:
        due = list((await s.execute(select(TrackCard.track_id).where(
            or_(TrackCard.dirty.is_(True), TrackCard.valid_until < utcnow())).limit(limit))).scalars())
        if len(due) < limit:
            due += list((await s.execute(select(Track.id).where(
                ~select(TrackCard.track_id).where(TrackCard.track_id == Track.id).exists())
                .limit(limit - len(due)))).scalars())
    if due:
        await build(due)
    return len(due)


async def keep_fresh() -> None:
    """The Hub's loop: stale and missing cards drawn as they come, Navidrome's songs compared after its scans."""
    import asyncio

    while True:
        try:
            if n := await _compare_songs():
                log.info("cards: %d to be drawn from songs Navidrome has changed", n)
            busy = await refresh_some()
        except Exception as e:  # Navidrome or the database away: tried again in a while
            log.warning("cards: could not be kept fresh: %r", e)
            await asyncio.sleep(30)
            continue
        await asyncio.sleep(0.2 if busy else 2)
