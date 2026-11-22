"""«Релизы»: what the artists a user follows have put out lately.

Following an artist is «Подписаться» on the artist's page (a typed favorite, app/favorites.py) — a liked track does
not make its artist followed. Twice a day the Hub reads each followed artist's releases in the catalog and keeps the
ones of the last `WINDOW_DAYS` it has not kept yet (`artist_releases`), each with its tracks; the feed shows them
newest first, the ones the user has not seen marked, and the artists themselves with what they released last. An
artist that is no longer followed leaves the feed, its rows stay.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import lookup, registry, release_matches
from app.catalog.models import CatalogAlbum, CatalogTrack, split_ref
from app.db import session_scope
from app.events import bus
from app.models import ArtistRelease, ArtistReleaseCheck, User, utcnow
from app.services import svc

log = logging.getLogger(__name__)

WINDOW_DAYS = 90  # a release older than this is not news
CHECK_EVERY = timedelta(hours=12)
LOOP_S = 3600
FEED_LIMIT = 300
TYPES = ("album", "ep", "single", "compilation")
TRACKS_PARALLEL = 4  # releases whose tracks are asked of the catalog at once
FILL_AT_ONCE = 24  # releases kept without tracks that one opening of the feed waits for; the loop does the rest

_lock = asyncio.Lock()  # one refresh at a time: the feed's own lookup and the loop do not add a release twice


async def albums_of(ref: str, name: str) -> list[CatalogAlbum]:
    src, id_ = split_ref(ref)
    if src == "dz" and svc.deezer is not None:
        return await svc.deezer.artist_albums_now(id_, name)
    return (await svc.artist_page(ref)).albums


async def picture_of(ref: str) -> str | None:
    """The artist's photo in the catalog (a small one: it is shown as a circle in a list)."""
    src, id_ = split_ref(ref)
    a = await svc.deezer.get_artist(id_) if src == "dz" and svc.deezer is not None else (await svc.artist_page(ref)).artist
    return a.picture_small_url or a.picture_url


async def tracks_of(album_ref: str) -> list[CatalogTrack]:
    return (await svc.album(album_ref, credited=True)).tracks or []


async def _tracks(refs: list[str]) -> dict[str, list[dict[str, Any]]]:
    """The tracks of these releases as they are kept; a release the catalog did not answer for is not in it."""
    gate = asyncio.Semaphore(TRACKS_PARALLEL)
    out: dict[str, list[dict[str, Any]]] = {}

    async def one(ref: str) -> None:
        async with gate:
            try:
                out[ref] = [t.model_dump(mode="json") for t in await tracks_of(ref)]
            except Exception as e:
                log.info("releases: no tracks of %s: %r", ref, e)

    await asyncio.gather(*(one(r) for r in refs))
    return out


@dataclass
class Follow:
    """An artist the user follows: `ref` in the catalog; `like_ref`/`nd_id` — what the follow itself is kept by
    (an artist followed in the library has no catalog ref of their own), to unfollow by."""

    ref: str
    name: str
    like_ref: str | None
    nd_id: str | None
    since: datetime | None


async def follows(s: AsyncSession, user_id: int) -> list[Follow]:
    """The artists the user follows, the latest followed first. One followed in the library (no catalog ref) is
    looked up in the catalog by its name."""
    from app.favorites import snapshot
    from app.mixes.build import artist_by_name

    out: dict[str, Follow] = {}
    for st in (await snapshot(s, user_id, only="artist")).values():
        if st.target_type != "artist" or not st.liked:
            continue
        name = str((st.data or {}).get("name") or "")
        ref = st.ref if st.ref and st.ref.startswith(("dz:", "sp:", "ym:")) else None
        if ref is None:
            from app.library import artists

            holder = await artists.by_ref(s, st.ref) if st.ref else None
            if holder is not None:
                refs = await artists.profiles(s, holder.id)
                ref = next(iter(lookup.preferred_refs(refs)), None) or next(iter(refs), None)
        if ref is None and name:
            try:
                ref = await artist_by_name(name)
            except Exception as e:  # the catalog is away: this artist waits for the next look
                log.info("releases: artist %r not found in the catalog: %r", name, e)
        if ref:
            out.setdefault(ref, Follow(ref=ref, name=name, like_ref=st.ref, nd_id=st.nd_id, since=st.since))
    return sorted(out.values(), key=lambda f: f.since.replace(tzinfo=None) if f.since else datetime.min,
                  reverse=True)


async def _records(s: AsyncSession, whom: list[Follow]) -> dict[str, Follow]:
    """Every record the catalog keeps of the followed artists -> the follow it belongs to (one artist may have
    several, and a new release may come out under any — app/library/artists.py)."""
    from app.library import artists

    out: dict[str, Follow] = {}
    for f in whom:
        out.setdefault(f.ref, f)
        who = await artists.by_ref(s, f.ref)
        for ref in await artists.profiles(s, who.id) if who is not None else []:
            out.setdefault(ref, f)
    return out


async def followed(s: AsyncSession, user_id: int) -> dict[str, str]:
    """catalog ref -> name of the artists the user follows: every record the catalog keeps of each of them."""
    return {ref: f.name for ref, f in (await _records(s, await follows(s, user_id))).items()}


async def refresh(user_id: int, *, only_new: bool = False) -> int:
    """Look up the releases of the followed artists that are due (`only_new`: only the ones never looked up — an
    artist followed a minute ago). Returns how many releases were added."""
    async with _lock:
        async with session_scope() as s:
            artists = await followed(s, user_id)
            from app.library import artists as artist_names

            unlinked = {}
            for ref in artists:
                if not ref.startswith("sp:") and (who := await artist_names.by_ref(s, ref)) is not None:
                    if not any(r.startswith("sp:") for r in await artist_names.profiles(s, who.id)):
                        unlinked[ref] = who.id
            checks = {c.artist_ref: c.checked_at for c in (await s.execute(
                select(ArtistReleaseCheck).where(ArtistReleaseCheck.user_id == user_id))).scalars()}
            known_releases = list((await s.execute(select(ArtistRelease).where(
                ArtistRelease.user_id == user_id, ArtistRelease.release_date >= date.today() - timedelta(days=WINDOW_DAYS)))).scalars())
        now = utcnow()
        due = [ref for ref in artists
               if ref not in checks or (not only_new and now - checks[ref] >= CHECK_EVERY)]
        found: dict[str, list[CatalogAlbum]] = {}
        pictures: dict[str, str | None] = {}
        profiles: dict[str, int] = {}
        found_profiles: set[int] = set()
        for ref in due:  # the network, with no transaction open
            if not lookup.source_available(ref):
                continue
            try:
                found[ref] = await albums_of(ref, artists[ref])
            except Exception as e:
                log.info("releases: %s not read: %r", ref, e)
                continue
            try:
                pictures[ref] = await picture_of(ref)
            except Exception as e:  # the photo it had stays
                log.info("releases: no photo of %s: %r", ref, e)
            if ref in unlinked and unlinked[ref] not in found_profiles and svc.spotify is not None:
                from app.api.catalog import spotify_profile

                try:
                    page = await svc.artist_page(ref)
                    if profile := await spotify_profile(page):
                        found_profiles.add(unlinked[ref])
                        sp = profile.artist.ref
                        profiles[sp] = unlinked[ref]
                        artists[sp] = artists[ref]
                        found[sp] = profile.albums
                        pictures[sp] = profile.artist.picture_small_url or profile.artist.picture_url
                except Exception as error:
                    log.info("releases: Spotify profile for %s unavailable: %r", ref, error)
        if not found:
            return 0
        today = date.today()
        since = today - timedelta(days=WINDOW_DAYS)
        candidates: dict[str, tuple[str, CatalogAlbum]] = {}
        for ref, albums in found.items():
            for a in albums:
                if a.release_date is not None and since <= a.release_date <= today:
                    candidates.setdefault(a.ref, (ref, a))

        async with session_scope() as s:
            for ref, artist_id in profiles.items():
                await artist_names.tie_profile(s, artist_id, ref)
        async with session_scope() as s:
            groups = await release_matches.discover(s, (a for _, a in candidates.values()), lambda a: svc.album(a.ref))
        known = {r.album_ref for r in known_releases}
        fresh: list[tuple[str, CatalogAlbum]] = []
        for album_ref in lookup.preferred_refs(candidates):
            ref, album = candidates[album_ref]
            related = {a.ref for a in groups.get(album_ref, [album])}
            if not (known & related):
                fresh.append((ref, album))
                known.update(related)
        tracks = await _tracks([a.ref for _, a in fresh])
        added = len(fresh)
        async with registry.write_lock, session_scope() as s:
            for _, a in fresh:
                related = groups.get(a.ref, [a])
                record = release_matches.combined(related) if len(related) > 1 else a
                if a.ref in tracks and record.ref == a.ref:
                    record = record.model_copy(update={"tracks": [CatalogTrack.model_validate(t) for t in tracks[a.ref]]})
                await registry.remember(s, record, [r.ref for r in related])
        async with session_scope() as s:
            for ref, a in fresh:
                s.add(ArtistRelease(
                    user_id=user_id, artist_ref=ref, artist=a.artist or artists[ref], album_ref=a.ref,
                    title=a.title, record_type=a.record_type, release_date=a.release_date,
                    cover_url=a.cover_small_url or a.cover_url, nb_tracks=a.nb_tracks, found_at=now,
                    tracks_json=tracks.get(a.ref)))
            for ref, albums in found.items():
                out = [a for a in albums if a.release_date is not None and a.release_date <= today]
                last = max(out, key=lambda a: a.release_date, default=None)
                seen = {"checked_at": now, "last_release_date": last.release_date if last else None,
                        "last_release_title": last.title if last else None}
                if pictures.get(ref):
                    seen["picture_url"] = pictures[ref]
                if ref in checks:
                    await s.execute(update(ArtistReleaseCheck).where(
                        ArtistReleaseCheck.user_id == user_id, ArtistReleaseCheck.artist_ref == ref).values(**seen))
                else:
                    s.add(ArtistReleaseCheck(user_id=user_id, artist_ref=ref, **seen))
        if added:
            bus.publish(user_id, "library", {"kind": "releases", "added": added})
        return added


async def unseen(s: AsyncSession, user_id: int, artists: dict[str, str] | None = None) -> int:
    artists = await followed(s, user_id) if artists is None else artists
    if not artists:
        return 0
    refs = list(await s.scalars(select(ArtistRelease.album_ref).where(
        ArtistRelease.user_id == user_id, ArtistRelease.seen_at.is_(None),
        ArtistRelease.artist_ref.in_(artists))))
    ids, _ = await registry.addresses(s, refs)
    return len({ids.get(ref, ref) for ref in refs})


async def fill_tracks(user_id: int, limit: int = FILL_AT_ONCE, *, repair_credits: bool = False) -> int:
    """Fill missing track lists and, in the background, revisit Deezer releases with incomplete credits."""
    async with _lock:
        previous: dict[str, list[dict[str, Any]]] = {}
        async with session_scope() as s:
            q = select(ArtistRelease.album_ref, ArtistRelease.tracks_json).where(
                ArtistRelease.user_id == user_id, ArtistRelease.release_date >= date.today() - timedelta(days=WINDOW_DAYS),
            ).order_by(ArtistRelease.release_date.desc(), ArtistRelease.id.desc())
            if repair_credits and svc.deezer is not None:
                rows = await s.execute(q)
                selected = [(ref, tracks) for ref, tracks in rows if tracks is None or (
                    ref.startswith("dz:") and any(not t.get("credits_complete") for t in tracks))][:limit]
                refs = [ref for ref, _ in selected]
                previous = {ref: tracks for ref, tracks in selected if tracks is not None}
            else:
                refs = list((await s.scalars(q.with_only_columns(ArtistRelease.album_ref)
                             .where(ArtistRelease.tracks_json.is_(None)).limit(limit))).all())
        if not refs:
            return 0
        tracks = await _tracks(refs)  # the network, with no transaction open
        changed = 0
        async with session_scope() as s:
            for ref, rows in tracks.items():
                if old := previous.get(ref):
                    credited = {track["ref"]: track for track in old if track.get("credits_complete")}
                    rows = [track if track.get("credits_complete") else credited.get(track["ref"], track) for track in rows]
                    if not rows or rows == old:
                        continue
                await s.execute(update(ArtistRelease).where(
                    ArtistRelease.user_id == user_id, ArtistRelease.album_ref == ref).values(tracks_json=rows))
                changed += 1
        return changed


async def feed(s: AsyncSession, user_id: int) -> dict[str, Any]:
    """«Подписки»: the followed artists (the ones with something new first, then by their latest release), and what
    they released in the window, newest first, each release with its tracks as the user has them."""
    from app.library.owned import annotate_tracks

    whom = await follows(s, user_id)
    artists = await _records(s, whom)  # every record of theirs: a release is the artist's under whichever it came out
    rows = list((await s.execute(
        select(ArtistRelease).where(ArtistRelease.user_id == user_id, ArtistRelease.artist_ref.in_(artists))
        .order_by(ArtistRelease.release_date.desc(), ArtistRelease.id.desc()).limit(FEED_LIMIT))).scalars()) if artists else []
    addresses, _ = await registry.addresses(s, (r.album_ref for r in rows))
    by_release: dict[int | str, ArtistRelease] = {}
    for row in rows:
        by_release.setdefault(addresses.get(row.album_ref, row.album_ref), row)
    rows = list(by_release.values())
    looked = {c.artist_ref: c for c in (await s.execute(
        select(ArtistReleaseCheck).where(ArtistReleaseCheck.user_id == user_id))).scalars()} if artists else {}
    # what was last seen of an artist: of their records, the one with the latest release (the follow's own first)
    checks: dict[str, ArtistReleaseCheck] = {}
    for ref, f in sorted(artists.items(), key=lambda x: x[0] != x[1].ref):
        c = looked.get(ref)
        if c is not None and (f.ref not in checks or (c.last_release_date or date.min) > (checks[f.ref].last_release_date or date.min)):
            checks[f.ref] = c
    kept = [[CatalogTrack.model_validate(t) for t in (r.tracks_json or [])] for r in rows]
    told = iter(await annotate_tracks(s, [t for ts in kept for t in ts], user_id))
    fresh: dict[str, int] = {}
    for r in rows:
        if r.seen_at is None:
            fresh[artists[r.artist_ref].ref] = fresh.get(artists[r.artist_ref].ref, 0) + 1

    def last(f: Follow) -> date | None:
        c = checks.get(f.ref)
        return c.last_release_date if c else None

    whom.sort(key=lambda f: (fresh.get(f.ref, 0) > 0, last(f) or date.min), reverse=True)
    return {
        "artists": len(whom),
        "unseen": sum(fresh.values()),
        "following": [{
            "ref": f.ref, "name": f.name, "like_ref": f.like_ref, "nd_id": f.nd_id, "picture_url": checks[f.ref].picture_url if f.ref in checks else None,
            "last_release_date": last(f).isoformat() if last(f) else None,
            "last_release_title": checks[f.ref].last_release_title if f.ref in checks else None,
            "new": fresh.get(f.ref, 0),
        } for f in whom],
        "items": [{
            "album_ref": str(addresses.get(r.album_ref, r.album_ref)), "title": r.title, "artist": r.artist,
            "artist_ref": artists[r.artist_ref].ref,
            "record_type": r.record_type, "release_date": r.release_date.isoformat(), "cover_url": r.cover_url,
            "nb_tracks": r.nb_tracks, "new": r.seen_at is None,
            "tracks": [next(told) for _ in ts] if r.tracks_json is not None else None,
        } for r, ts in zip(rows, kept, strict=True)],
    }


async def mark_seen(s: AsyncSession, user_id: int, now: datetime | None = None) -> None:
    await s.execute(update(ArtistRelease).where(ArtistRelease.user_id == user_id, ArtistRelease.seen_at.is_(None))
                    .values(seen_at=now or utcnow()))


async def loop() -> None:
    await asyncio.sleep(300)
    while True:
        try:
            async with session_scope() as s:
                users = list((await s.execute(select(User.id).order_by(User.id))).scalars())
            for user_id in users:
                added = await refresh(user_id)
                if added:
                    log.info("releases: %s new for user %s", added, user_id)
                if await fill_tracks(user_id, repair_credits=True):
                    bus.publish(user_id, "library", {"kind": "releases"})
        except Exception:
            log.exception("releases check failed")
        await asyncio.sleep(LOOP_S)
