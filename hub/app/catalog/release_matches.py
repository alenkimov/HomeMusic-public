"""Match releases across catalogs and keep their original names and tracklists."""

from __future__ import annotations

import asyncio
import io
import logging
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from itertools import combinations
from urllib.parse import urlsplit

from PIL import Image, ImageOps
from sqlalchemy import or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import registry
from app.catalog.models import CatalogAlbum, CatalogTrack, SearchResults
from app.library import album_names, artists
from app.matching.normalize import normalize_title
from app.models import Album, CatalogReleaseMatch

log = logging.getLogger(__name__)
_ORDER = {"sp": 0, "dz": 1, "ym": 2}
_EDITIONS = re.compile(
    r"\b(?:deluxe|expanded|anniversary|remaster\w*|reissue|bonus|international edition|"
    r"special edition|limited edition|standard edition|mono|stereo|clean|explicit)\b"
    r"|デラックス|リマスター|限定盤|通常盤|初回盤|делюкс|ремастер\w*",
    re.IGNORECASE,
)
_COVER_HOSTS = {
    "cdn-images.dzcdn.net", "e-cdns-images.dzcdn.net", "e-cdn-images.dzcdn.net",
    "i.scdn.co", "image-cdn-ak.spotifycdn.com", "image-cdn-fa.spotifycdn.com",
    "avatars.yandex.net", "avatars.mds.yandex.net",
}


def _order(a: CatalogAlbum) -> tuple[int, str]:
    return _ORDER.get(a.source, len(_ORDER)), a.ref


def alternate_titles(title: str, names: Iterable[str]) -> list[str]:
    unique = {artists.key(title): title}
    for name in names:
        if name.strip():
            unique.setdefault(artists.key(name), name)
    return list(unique.values())[1:]


def _upc(a: CatalogAlbum) -> str | None:
    value = (a.upc or "").strip()
    return (value.lstrip("0") or None) if value.isascii() and value.isdecimal() else None


def _kind(a: CatalogAlbum) -> str:
    return "single" if a.record_type == "ep" else a.record_type


def _count(a: CatalogAlbum) -> int | None:
    return a.nb_tracks or (len(a.tracks) if a.tracks else None)


def compatible(a: CatalogAlbum, b: CatalogAlbum, *, linked_profiles: bool = False) -> bool:
    """Metadata that can rule out a match even when the catalogs use the same title."""
    if (a.source == b.source and not linked_profiles) or _kind(a) != _kind(b):
        return False
    if _count(a) and _count(b) and _count(a) != _count(b):
        return False
    if _upc(a) and _upc(b) and _upc(a) != _upc(b):
        return False
    if a.release_date and b.release_date and a.release_date != b.release_date:
        return False
    year_a, year_b = a.release_date.year if a.release_date else a.year, b.release_date.year if b.release_date else b.year
    if year_a and year_b and year_a != year_b:
        return False
    if a.explicit is not None and b.explicit is not None and a.explicit != b.explicit:
        return False
    if a.tracks and b.tracks and len(a.tracks) == _count(a) and len(b.tracks) == _count(b):
        if _tracklist(a, b) is None:
            return False
    return (set(_EDITIONS.findall(a.title.casefold())) == set(_EDITIONS.findall(b.title.casefold()))
            and normalize_title(a.title).version == normalize_title(b.title).version)


def _same_artist(a: CatalogAlbum, b: CatalogAlbum, people: artists.Reader) -> bool:
    left, right = people.tied.get(a.artist_ref), people.tied.get(b.artist_ref)
    return left[0] == right[0] if left and right else (
        bool(a.artist and b.artist) and artists.key(a.artist) == artists.key(b.artist)
    )


def _priority(a: CatalogAlbum, b: CatalogAlbum) -> int:
    if _upc(a) and _upc(a) == _upc(b):
        return 0
    if a.release_date and a.release_date == b.release_date:
        return 1 if artists.fold(a.title) != artists.fold(b.title) else 2
    if (a.year or a.release_date) and (b.year or b.release_date):
        return 3
    return 4


def _ordered(a: CatalogAlbum) -> list[CatalogTrack]:
    rows = list(a.tracks or [])
    if all(t.track_no for t in rows):
        rows.sort(key=lambda t: (t.disc_no or 1, t.track_no))
    return rows


def _tracklist(a: CatalogAlbum, b: CatalogAlbum) -> tuple[bool, bool] | None:
    """On a complete ordered tracklist, report whether all ISRCs or all titles agree."""
    left, right = _ordered(a), _ordered(b)
    if not left or len(left) != _count(a) or len(right) != _count(b) or len(left) != len(right):
        return None
    if len({t.ref for t in left}) != len(left) or len({t.ref for t in right}) != len(right):
        return None
    codes_equal = titles_equal = True
    for t, u in zip(left, right, strict=True):
        has_codes = bool(t.isrc and u.isrc)
        if has_codes and t.isrc.upper() != u.isrc.upper():
            return None
        if t.explicit is not None and u.explicit is not None and t.explicit != u.explicit:
            return None
        if ((t.disc_no and u.disc_no and t.disc_no != u.disc_no)
                or (t.track_no and u.track_no and t.track_no != u.track_no)):
            return None
        nt, nu = normalize_title(t.title), normalize_title(u.title)
        if nt.version != nu.version:
            return None
        if t.duration_s and u.duration_s:
            if abs(t.duration_s - u.duration_s) > 2:
                return None
        elif not has_codes:
            return None
        codes_equal &= has_codes
        titles_equal &= bool(nt.text and nt.text == nu.text)
    return codes_equal, titles_equal


def _pixels(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 16_000_000:
            raise ValueError("cover dimensions")
        square = ImageOps.fit(image.convert("RGB"), (16, 16), Image.Resampling.LANCZOS)
        return square.tobytes()


async def _cover(album: CatalogAlbum) -> bytes | None:
    from app.jobs.downloads import fetch_cover

    url = album.cover_small_url or album.cover_url or ""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in _COVER_HOSTS:
        return None
    data = await fetch_cover(url, max_bytes=2_000_000)
    return await asyncio.to_thread(_pixels, data) if data else None


async def _confirmed(a: CatalogAlbum, b: CatalogAlbum, cover: Callable[[CatalogAlbum], Awaitable[bytes | None]]) -> bool:
    comparison = _tracklist(a, b)
    if comparison is None:
        return False
    codes_equal, titles_equal = comparison
    if _upc(a) and _upc(a) == _upc(b):
        return True
    if not a.release_date or a.release_date != b.release_date:
        return False
    if codes_equal:
        return True
    # Without product or recording codes, a localized single still supplies a complete
    # one-track signature. A longer release must also agree on all its ordered song names.
    if not titles_equal and (_kind(a) != "single" or _count(a) != 1):
        return False
    left, right = await asyncio.gather(cover(a), cover(b))
    return bool(left and right and sum(abs(x - y) for x, y in zip(left, right, strict=True)) <= 6 * len(left))


async def groups_for(s: AsyncSession, refs: Iterable[str]) -> dict[str, list[CatalogAlbum]]:
    want = list(set(refs))
    if not want:
        return {}
    tr = CatalogReleaseMatch
    ids = select(tr.group_ref).where(tr.ref.in_(want))
    by_group: dict[str, list[CatalogAlbum]] = defaultdict(list)
    for row in (await s.scalars(select(tr).where(tr.group_ref.in_(ids)))).all():
        by_group[row.group_ref].append(CatalogAlbum.model_validate(row.album_json))
    groups = [sorted(group, key=_order) for group in by_group.values()]
    return {album.ref: group for group in groups for album in group}


def combined(group: list[CatalogAlbum]) -> CatalogAlbum:
    """The representative card, carrying the other catalog names at the same track positions."""
    head, *rest = sorted(group, key=_order)
    paired = track_groups(group)
    by_ref = {rows[0].ref: rows for rows in paired}
    return head.model_copy(update={
        "alternate_titles": alternate_titles(head.title, [*head.alternate_titles, *(a.title for a in rest)]),
        "tracks": [combined_track(by_ref[t.ref]) for t in head.tracks] if head.tracks is not None and paired else head.tracks,
    })


def track_groups(group: list[CatalogAlbum]) -> list[list[CatalogTrack]]:
    return [list(tracks) for tracks in zip(*map(_ordered, sorted(group, key=_order)), strict=True)]


def combined_track(group: list[CatalogTrack]) -> CatalogTrack:
    head, *others = group
    spotify = next((t for t in group if t.source == "sp"), None)
    return head.model_copy(update={
        "alternate_titles": alternate_titles(head.title, [*head.alternate_titles, *(t.title for t in others)]),
        "isrc": next((t.isrc for t in group if t.isrc), None),
        **({"spotify_id": spotify.id, "spotify_plays": spotify.spotify_plays} if spotify else {}),
    })


def track_versions(group: list[CatalogAlbum], ref: str | None) -> list[CatalogTrack]:
    return next((rows for rows in track_groups(group) if any(t.ref == ref for t in rows)), [])


def _album_snapshot(album: CatalogAlbum) -> dict:
    snapshot = album.model_dump(mode="json")
    for track in snapshot["tracks"] or []:
        track.pop("spotify_plays", None)  # Counts keep their own expiry via the track audience API.
    return snapshot


async def _copy_names(s: AsyncSession, group: list[CatalogAlbum]) -> None:
    from app.library.tracks import bind_refs, tracks_by_refs

    variants = track_groups(group)
    refs = {a.ref for a in group}
    known = await tracks_by_refs(s, (t.ref for rows in variants for t in rows))
    local = {ref: t for ref, t in known.items() if (t.catalog or {}).get("album_ref") in refs | {None}}
    albums = {t.album_id for t in local.values() if t.album_id}
    deezer = [a.id for a in group if a.source == "dz"]
    for album in (await s.scalars(select(Album).where(or_(Album.id.in_(albums), Album.deezer_album_id.in_(deezer))))).all():
        for item in group:
            await album_names.add(s, album, item.title, "catalog")
    for rows in variants:
        linked = {local[t.ref].id: local[t.ref] for t in rows if t.ref in local}
        if len(linked) == 1:
            await bind_refs(s, next(iter(linked.values())), (t.ref for t in rows))
        for track in linked.values():
            for item in rows:
                await artists.add_title(s, track, item.title, "catalog")


async def _save(s: AsyncSession, pairs: list[tuple[CatalogAlbum, CatalogAlbum]]) -> None:
    if not pairs:
        return
    async with registry.write_lock:
        saved = await groups_for(s, (a.ref for pair in pairs for a in pair))
        verified = {frozenset((a.ref, b.ref)) for a, b in pairs}
        changed: dict[str, list[CatalogAlbum]] = {}
        for a, b in pairs:
            left, right = saved.get(a.ref, [a]), saved.get(b.ref, [b])
            refs_a, refs_b = {x.ref for x in left}, {x.ref for x in right}
            if refs_a & refs_b or any(
                frozenset((x.ref, y.ref)) not in verified for x in left for y in right
            ):
                continue  # require every pair; transitive matches can bridge two editions
            group = sorted([*left, *right], key=_order)
            changed.pop(left[0].ref, None)
            changed.pop(right[0].ref, None)
            changed[group[0].ref] = group
            saved.update((item.ref, group) for item in group)
        for ref, group in changed.items():
            stmt = insert(CatalogReleaseMatch)
            await s.execute(stmt.on_conflict_do_update(
                index_elements=[CatalogReleaseMatch.ref],
                set_={name: getattr(stmt.excluded, name) for name in ("group_ref", "album_json", "search_key")},
            ), [{
                "ref": a.ref, "group_ref": ref, "album_json": _album_snapshot(a),
                "search_key": artists.fold(" ".join([a.title, a.artist, *(t.title for t in a.tracks or [])])),
            } for a in group])
            await _copy_names(s, group)
            await registry.remember(s, combined(group), [a.ref for a in group], create=False)
        await s.commit()


async def discover(
    s: AsyncSession, albums: Iterable[CatalogAlbum], fetch: Callable[[CatalogAlbum], Awaitable[CatalogAlbum]],
) -> dict[str, list[CatalogAlbum]]:
    albums = list({a.ref: a for a in albums if a.source in _ORDER}.values())
    saved = await groups_for(s, (a.ref for a in albums))
    known = {a.ref: a for group in saved.values() for a in group}
    candidates = list({**{a.ref: a for a in albums}, **known}.values())
    people = await artists.reader(s, [], [a.artist_ref for a in candidates])
    pairs = [
        (a, b) for a, b in combinations(candidates, 2)
        if not (a.ref in saved and any(p.ref == b.ref for p in saved[a.ref]))
        and _priority(a, b) < 4 and _same_artist(a, b, people) and compatible(a, b)
    ]
    if not pairs:
        return saved
    gate = asyncio.Semaphore(6)
    details: dict[str, asyncio.Task[CatalogAlbum | None]] = {}
    covers: dict[str, asyncio.Task[bytes | None]] = {}

    async def full(a: CatalogAlbum) -> CatalogAlbum | None:
        try:
            async with gate:
                return a if a.tracks else await fetch(a)
        except Exception as error:
            log.info("Release %s unavailable for comparison: %r", a.ref, error)
            return None

    async def cover(a: CatalogAlbum) -> bytes | None:
        try:
            async with gate:
                return await _cover(a)
        except (OSError, ValueError) as error:
            log.info("Release %s cover unavailable for comparison: %r", a.ref, error)
            return None

    async def check(a: CatalogAlbum, b: CatalogAlbum) -> tuple[CatalogAlbum, CatalogAlbum] | None:
        try:
            left, right = await asyncio.gather(details[a.ref], details[b.ref])
            if (left and right and _same_artist(left, right, people)
                    and compatible(left, right) and await _confirmed(left, right, picture)):
                return left, right
        except Exception as error:
            log.info("Could not compare releases %s and %s: %r", a.ref, b.ref, error)
        return None

    async def picture(a: CatalogAlbum) -> bytes | None:
        if a.ref not in covers:
            covers[a.ref] = group.create_task(cover(a))
        return await covers[a.ref]

    # Keep catalog lookups bounded. A later request can verify releases whose metadata was unavailable.
    pairs = sorted(pairs, key=lambda pair: _priority(*pair))[:24]
    try:
        async with asyncio.timeout(9), asyncio.TaskGroup() as group:
            for a, b in pairs:
                for album in (a, b):
                    if album.ref not in details:
                        details[album.ref] = group.create_task(full(album))
            checks = [group.create_task(check(a, b)) for a, b in pairs]
    except TimeoutError:
        pass
    except ExceptionGroup as error:
        log.info("Release details unavailable: %s", error)
    matches = [result for task in checks if task.done() and not task.cancelled() and task.exception() is None
               if (result := task.result()) is not None]
    if not matches:
        return saved
    # Equal confidence for two records in the same catalog is ambiguous.
    alternatives: dict[tuple[str, str], list[str]] = defaultdict(list)
    for a, b in matches:
        alternatives[a.ref, b.source].append(b.ref)
        alternatives[b.ref, a.source].append(a.ref)
    await _save(s, [(a, b) for a, b in matches
                    if len(alternatives[a.ref, b.source]) == len(alternatives[b.ref, a.source]) == 1])
    return await groups_for(s, (a.ref for a in albums))


async def search(s: AsyncSession, query: str) -> SearchResults:
    words = artists.fold(query).split()
    if not words:
        return SearchResults()
    candidates = (await s.scalars(select(CatalogReleaseMatch).where(*(
        CatalogReleaseMatch.search_key.contains(word, autoescape=True) for word in words
    )).order_by(CatalogReleaseMatch.ref).limit(100))).all()
    results = SearchResults()
    for row in candidates:
        album = CatalogAlbum.model_validate(row.album_json)
        if all(w in artists.fold(f"{album.artist} {album.title}") for w in words):
            results.albums.append(album)
        results.tracks.extend(t for t in album.tracks or []
                              if all(w in artists.fold(f"{t.artist} {t.title} {album.title}") for w in words))
    return results
