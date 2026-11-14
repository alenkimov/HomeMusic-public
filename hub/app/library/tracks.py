"""The registry of tracks (ARCHITECTURE.md): the songs the Hub knows and the files that play them.

A catalog track becomes a track here the moment it matters to someone — it is put into a playlist, tagged,
downloaded, listened to. `of_catalog` is that moment: it gives the track of a catalog track, making it if the Hub
has not met the song yet. The track has no file then; a download adds one later (`library.importer`).
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import CatalogTrack
from app.matching.normalize import fingerprint
from app.models import Track, TrackFile


def _same_length(a: int | None, b: int | None, tol: int = 3) -> bool:
    return a is None or b is None or abs(a - b) <= tol


async def names_of(s: AsyncSession, track_id: int) -> tuple[str | None, str | None] | None:
    """What a track is called outside the Hub: (its catalog ref, the Navidrome song of its newest file). None: no
    such track."""
    row = (await s.execute(select(Track.ref, Track.nd_song_id).where(Track.id == track_id))).first()
    return (row[0], row[1]) if row is not None else None


async def id_of(s: AsyncSession, ref: str | None, nd_song_id: str | None) -> int | None:
    """The track that has this Navidrome song among its files, else the one of this catalog ref."""
    from app.models import TrackFile

    if nd_song_id:
        found = (await s.execute(select(TrackFile.track_id).where(TrackFile.nd_song_id == nd_song_id)
                                 .order_by(TrackFile.id.desc()).limit(1))).scalar_one_or_none()
        if found is not None:
            return found
    track = await by_ref(s, ref) if ref else None
    return track.id if track is not None else None


def _names(c: CatalogTrack) -> dict:
    """What a track says of its song, as the catalog tells it."""
    return dict(
        artist=c.artist, title=c.title, album=c.album_title, album_artist=c.album_artist,
        track_no=c.track_no, disc_no=c.disc_no, year=c.release_date.year if c.release_date else None,
        duration_s=c.duration_s, isrc=c.isrc.upper() if c.isrc else None, explicit=c.explicit,
        fingerprint=fingerprint(c.artist, c.title))


async def of_catalog(s: AsyncSession, c: CatalogTrack, *, user_id: int | None = None) -> Track:
    """The track of this catalog track; made if the Hub does not know the song. A track the Hub has under no
    catalog name yet (an uploaded file, say) that is this very song — the same ISRC, or the same names and length —
    takes the name instead of a second track being made."""
    ref = c.ref if c.source != "nd" else None
    track = await by_ref(s, ref)
    if track is not None:
        if track.title == ref or not track.artist:
            # met by its ref alone (a like that said nothing of the song): now it is known what the song is
            for k, v in _names(c).items():
                setattr(track, k, v)
            track.catalog = c.model_dump(mode="json")
        return track
    unnamed = select(Track).where(Track.ref.is_(None)).order_by(Track.id.desc())
    if c.isrc:
        track = (await s.execute(unnamed.where(Track.isrc == c.isrc.upper()).limit(1))).scalar_one_or_none()
    if track is None:
        for t in (await s.execute(unnamed.where(Track.fingerprint == fingerprint(c.artist, c.title)))).scalars():
            if _same_length(t.duration_s, c.duration_s):
                track = t
                break
    if track is not None:
        if ref:
            track.ref = ref
        track.catalog = track.catalog or c.model_dump(mode="json")
        return track
    track = Track(ref=ref, **_names(c), catalog=c.model_dump(mode="json"), created_by=user_id)
    s.add(track)
    await s.flush()
    await s.refresh(track)  # what is read with a track (its song, its path) is there to be read: none, for now
    return track


async def fill_catalog(s: AsyncSession, tracks: Iterable[Track] | None = None, limit: int = 60) -> int:
    """Tracks the catalog knows (`ref`) that were made before a track kept what the catalog says of it get it now:
    who their artists are there, their album, their cover. `tracks`: these ones (an artist's page needs its few at
    once); none: any that lack it, `limit` at a time. Returns how many were filled."""
    from app.mixes.resolve import catalog_track

    rows = [t for t in tracks if t.catalog is None and t.ref and t.ref.startswith("dz:")] if tracks is not None else list(
        (await s.execute(select(Track).where(Track.catalog.is_(None), Track.ref.like("dz:%")).order_by(Track.id.desc())
                         .limit(limit))).scalars())
    n = 0
    for t in rows[:limit]:
        c = await catalog_track(t.ref)
        if c is not None:
            t.catalog = c.model_dump(mode="json")
            n += 1
    if n:
        await s.flush()
    return n


async def has_file(s: AsyncSession, track_id: int) -> bool:
    return (await s.execute(select(TrackFile.id).where(TrackFile.track_id == track_id).limit(1))).first() is not None


def dz_ref(deezer_id: str | int | None) -> str | None:
    return f"dz:{deezer_id}" if deezer_id else None


def dz_id(ref: str | None) -> str | None:
    """The Deezer id of a catalog ref, if it is Deezer's."""
    return ref[3:] if ref and ref.startswith("dz:") else None


async def by_ref(s: AsyncSession, ref: str | None) -> Track | None:
    """The track the catalog calls `ref`."""
    if not ref:
        return None
    return (await s.execute(select(Track).where(Track.ref == ref))).scalar_one_or_none()


async def current_file(s: AsyncSession, track_id: int) -> TrackFile | None:
    """The file that plays the track: the newest one."""
    return (await s.execute(select(TrackFile).where(TrackFile.track_id == track_id)
                            .order_by(TrackFile.id.desc()).limit(1))).scalar_one_or_none()


async def current_files(s: AsyncSession, track_ids: Iterable[int]) -> dict[int, TrackFile]:
    """track id -> the file that plays it, for the tracks that have one."""
    ids = list(dict.fromkeys(track_ids))
    out: dict[int, TrackFile] = {}
    for i in range(0, len(ids), 500):
        for f in (await s.execute(select(TrackFile).where(TrackFile.track_id.in_(ids[i:i + 500]))
                                  .order_by(TrackFile.id))).scalars():
            out[f.track_id] = f  # ordered by id: the newest stays
    return out


async def files_of(s: AsyncSession, track_id: int) -> list[TrackFile]:
    return list((await s.execute(select(TrackFile).where(TrackFile.track_id == track_id)
                                 .order_by(TrackFile.id))).scalars())
