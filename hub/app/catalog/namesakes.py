"""Releases the catalog files under an artist that are not theirs.

The catalog does not tell artists of one name apart unless somebody did it by hand: a «TAK» there is a vocaloid
producer and a few rappers at once, 108 releases under one id. A listener who sees it says so — «это другой TAK» —
and the release leaves that artist for the whole server (`artist_release_exclusions`): it is a fact about the
catalog, not a taste, so it is nobody's dislike and weighs nothing in a profile. The tracks themselves stay
playable; they are only not that artist's any more.

What is excluded is read once at start and kept in memory: the catalog's client drops such releases from everything
it says of an artist (`own_albums`, `own_tracks` in catalog/deezer.py) — the artist's page, the discography, the
popular tracks, the feed of new releases, a download of everything, the mixes.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import CatalogAlbum, CatalogTrack
from app.db import session_scope
from app.models import ArtistRelease, ArtistReleaseExclusion

_by_artist: dict[str, set[str]] = {}


async def load() -> None:
    """Read what is excluded (at start)."""
    async with session_scope() as s:
        rows = (await s.execute(select(ArtistReleaseExclusion.artist_ref, ArtistReleaseExclusion.album_ref))).all()
    _by_artist.clear()
    for artist_ref, album_ref in rows:
        _by_artist.setdefault(artist_ref, set()).add(album_ref)


def forget() -> None:
    _by_artist.clear()


def of(artist_ref: str) -> frozenset[str]:
    return frozenset(_by_artist.get(artist_ref, ()))


def own_albums(artist_ref: str, albums: list[CatalogAlbum]) -> list[CatalogAlbum]:
    gone = _by_artist.get(artist_ref)
    return [a for a in albums if a.ref not in gone] if gone else albums


def own_tracks(artist_ref: str, tracks: list[CatalogTrack]) -> list[CatalogTrack]:
    gone = _by_artist.get(artist_ref)
    return [t for t in tracks if t.album_ref not in gone] if gone else tracks


async def exclude(s: AsyncSession, user_id: int, artist_ref: str, artist: str, albums: Iterable[CatalogAlbum]) -> int:
    """These releases are not this artist's. What the feeds of new releases kept of them goes too. Returns how many
    were not excluded before."""
    have = set((await s.execute(select(ArtistReleaseExclusion.album_ref).where(
        ArtistReleaseExclusion.artist_ref == artist_ref))).scalars())
    new = [a for a in albums if a.ref not in have]
    for a in new:
        s.add(ArtistReleaseExclusion(
            artist_ref=artist_ref, artist=artist, album_ref=a.ref, title=a.title, record_type=a.record_type,
            year=a.year or (a.release_date.year if a.release_date else None),
            cover_url=a.cover_small_url or a.cover_url, created_by=user_id))
    if new:
        await s.execute(delete(ArtistRelease).where(
            ArtistRelease.artist_ref == artist_ref, ArtistRelease.album_ref.in_([a.ref for a in new])))
        await s.flush()
        _by_artist.setdefault(artist_ref, set()).update(a.ref for a in new)
    return len(new)


async def restore(s: AsyncSession, artist_ref: str, album_refs: list[str] | None = None) -> int:
    """They are the artist's after all: these releases, or everything excluded of the artist (`album_refs` None)."""
    q = delete(ArtistReleaseExclusion).where(ArtistReleaseExclusion.artist_ref == artist_ref)
    if album_refs is not None:
        q = q.where(ArtistReleaseExclusion.album_ref.in_(album_refs))
    n = (await s.execute(q)).rowcount or 0
    await s.flush()
    if album_refs is None:
        _by_artist.pop(artist_ref, None)
    elif artist_ref in _by_artist:
        _by_artist[artist_ref] -= set(album_refs)
    return n


async def listed(s: AsyncSession) -> list[dict[str, Any]]:
    rows = (await s.execute(select(ArtistReleaseExclusion).order_by(
        ArtistReleaseExclusion.artist, ArtistReleaseExclusion.year.desc(), ArtistReleaseExclusion.id))).scalars()
    return [{"artist_ref": r.artist_ref, "artist": r.artist, "album_ref": r.album_ref, "title": r.title,
             "record_type": r.record_type, "year": r.year, "cover_url": r.cover_url} for r in rows]
