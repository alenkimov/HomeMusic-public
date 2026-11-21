"""The registry of tracks (ARCHITECTURE.md): the songs the Hub knows and the files that play them.

A catalog track becomes a track here the moment it matters to someone — it is put into a playlist, tagged,
downloaded, listened to. `of_catalog` is that moment: it gives the track of a catalog track, making it if the Hub
has not met the song yet. The track has no file then; a download adds one later (`library.importer`).
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.identity import same_recording
from app.catalog.models import CATALOGS, CatalogTrack
from app.matching.normalize import fingerprint
from app.models import Track, TrackCatalogRef, TrackFile
from app.providers.base import TrackQuery


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
    """What a track says of its song, as the catalog tells it — the name without the credits written into it
    (app/library/titles.py): the catalog's own word is kept whole in `Track.catalog`."""
    from app.library import titles

    title, _, producers = titles.clean(c.title)
    return dict(
        producer=", ".join(producers) or None,
        artist=c.artist, title=title, album=c.album_title, album_artist=c.album_artist,
        track_no=c.track_no, disc_no=c.disc_no, year=c.release_date.year if c.release_date else None,
        duration_s=c.duration_s, isrc=c.isrc.upper() if c.isrc else None, explicit=c.explicit,
        fingerprint=fingerprint(c.artist, title))


async def _unnamed(s: AsyncSession, *, artist: str, title: str, duration_s: int | None, isrc: str | None) -> Track | None:
    """A track the Hub has under no catalog name that is this very song: the same ISRC, or the same names and
    length."""
    unnamed = select(Track).where(Track.ref.is_(None)).order_by(Track.id.desc())
    if isrc:
        wanted = TrackQuery(artist=artist, title=title, duration_s=duration_s, isrc=isrc)
        for track in await s.scalars(unnamed.where(Track.isrc == isrc.upper())):
            if same_recording(wanted, track):
                return track
    for t in (await s.execute(unnamed.where(Track.fingerprint == fingerprint(artist, title)))).scalars():
        if _same_length(t.duration_s, duration_s) and (not (isrc and t.isrc) or t.isrc == isrc.upper()):
            return t
    return None


async def by_name(s: AsyncSession, *, artist: str, title: str, album: str | None = None, duration_s: int | None = None,
                  isrc: str | None = None, user_id: int | None = None) -> Track:
    """The track of a song known by its name alone — a row of a transfer the catalog has no record of: the one the
    Hub has under no catalog name that is this song, else a new one. With no file and no record of the catalog there
    is nothing of it to play, yet it is a track like any other: it has its places in playlists, its ♥ and its tags.
    It plays once it is given a file (`ImportSpec.track_id`), and takes the catalog's name once it is matched with
    the catalog (`of_catalog`)."""
    from app.library import artists

    track = await _unnamed(s, artist=artist, title=title, duration_s=duration_s, isrc=isrc)
    if track is not None:
        return track
    track = Track(artist=artist, title=title, album=album, duration_s=duration_s, isrc=isrc.upper() if isrc else None,
                  fingerprint=fingerprint(artist, title), created_by=user_id)
    s.add(track)
    await s.flush()
    await s.refresh(track)
    await artists.credit_track(s, track)
    return track


async def of_catalog(s: AsyncSession, c: CatalogTrack, *, user_id: int | None = None, word: bool = True) -> Track:
    """The track of this catalog track; made if the Hub does not know the song. A track the Hub has under no
    catalog name yet (an uploaded file, say) that is this very song — the same ISRC, or the same names and length —
    takes the name instead of a second track being made. `word` False: `c` is what somebody else called the song
    (a music service's row matched with the catalog's track), not the catalog's record of it — the track is named
    by it, and the catalog's own word is asked for later (`fill_catalog`)."""
    from app.catalog import release_matches
    from app.library import artists

    ref = c.ref if c.source != "nd" else None
    variants = []
    if c.album_ref and ref:
        group = (await release_matches.groups_for(s, [c.album_ref])).get(c.album_ref)
        if group:
            variants = release_matches.track_versions(group, ref)
    track = await by_ref(s, ref)
    if track is None and variants:
        known = await tracks_by_refs(s, [t.ref for t in variants])
        track = min(known.values(), key=lambda t: (t.path is None, t.id), default=None)
        if track is None:
            c = release_matches.combined_track(variants)
            ref = c.ref
    said = c.model_dump(mode="json") if word else None
    if track is not None:
        await bind_refs(s, track, [ref, *(t.ref for t in variants)])
        if (track.title == ref or not track.artist) and track.ref == ref:
            # met by its ref alone (a like that said nothing of the song): now it is known what the song is
            for k, v in _names(c).items():
                setattr(track, k, v)
            track.catalog = said or track.catalog
            await artists.credit_track(s, track)
        for other in variants:
            await artists.add_title(s, track, other.title, "catalog")
        return track
    track = await _unnamed(s, artist=c.artist, title=c.title, duration_s=c.duration_s, isrc=c.isrc)
    if track is not None:
        if ref:
            track.ref = ref
        track.catalog = track.catalog or said
        await bind_refs(s, track, [ref, *(t.ref for t in variants)])
        return track
    track = Track(ref=ref, **_names(c), catalog=said, created_by=user_id)
    s.add(track)
    await s.flush()
    await s.refresh(track)  # what is read with a track (its song, its path) is there to be read: none, for now
    await bind_refs(s, track, [ref, *(t.ref for t in variants)])
    await artists.credit_track(s, track)
    for other in variants:
        await artists.add_title(s, track, other.title, "catalog")
    return track


async def fill_catalog(s: AsyncSession, tracks: Iterable[Track] | None = None, limit: int = 60) -> int:
    """Tracks the catalog knows (`ref`) that do not keep what the catalog says of them get it now: who their artists
    are there, their album, their cover. Such are the tracks made before a track kept it, and the ones made of what
    somebody else called the song — a music service's row, a like that knew only the name: what they keep does not
    say whose record the artist is. A track with no file yet is by whom the catalog says from then on (one with a
    file was filed by the catalog's word already). `tracks`: these ones (an artist's page needs its few at once);
    none: any that lack it, `limit` at a time. Returns how many were filled."""
    from app.library import artists
    from app.mixes.resolve import catalog_track

    def lacks(t: Track) -> bool:
        return bool(t.ref and t.ref.partition(":")[0] in CATALOGS) and not (t.catalog or {}).get("artist_ref")

    rows = [t for t in tracks if lacks(t)] if tracks is not None else list(
        (await s.execute(select(Track).where(
            or_(*(Track.ref.like(f"{source}:%") for source in CATALOGS)),
            or_(Track.catalog.is_(None), func.json_extract(Track.catalog, "$.artist_ref").is_(None)))
            .order_by(Track.id.desc()).limit(limit))).scalars())
    filled: list[Track] = []
    for t in rows[:limit]:
        c = await catalog_track(t.ref)
        if c is not None and c.artist_ref:
            t.catalog = c.model_dump(mode="json")
            filled.append(t)
    for t in filled:
        if not await has_file(s, t.id):
            await artists.credit_track(s, t)
    if filled:
        await s.flush()
    return len(filled)


async def has_file(s: AsyncSession, track_id: int) -> bool:
    return (await s.execute(select(TrackFile.id).where(TrackFile.track_id == track_id).limit(1))).first() is not None


def dz_ref(deezer_id: str | int | None) -> str | None:
    return f"dz:{deezer_id}" if deezer_id else None


def dz_id(ref: str | None) -> str | None:
    """The Deezer id of a catalog ref, if it is Deezer's."""
    return ref[3:] if ref and ref.startswith("dz:") else None


async def by_ref(s: AsyncSession, ref: str | None) -> Track | None:
    """Resolve a Hub track address or its known catalog reference."""
    if not ref:
        return None
    from app.catalog.registry import numeric_id

    if ref.startswith("lib:") and (ident := numeric_id(ref[4:])) is not None:
        return await s.get(Track, ident)
    return (await tracks_by_refs(s, [ref])).get(ref)


async def tracks_by_refs(s: AsyncSession, refs: Iterable[str]) -> dict[str, Track]:
    refs = set(refs)
    if not refs:
        return {}
    linked = await s.execute(select(TrackCatalogRef.ref, Track).join(Track).where(TrackCatalogRef.ref.in_(refs)))
    out = {ref: track for ref, track in linked}
    missing = refs - out.keys()
    if missing:
        out.update((t.ref, t) for t in await s.scalars(select(Track).where(Track.ref.in_(missing))))
    return out


async def bind_refs(s: AsyncSession, track: Track, refs: Iterable[str | None]) -> None:
    external = {ref for ref in refs if ref and ref.partition(":")[0] in CATALOGS}
    if not external:
        return
    existing = await tracks_by_refs(s, external)
    rows = [{"ref": ref, "track_id": track.id} for ref in external
            if ref not in existing or existing[ref].id == track.id]
    if rows:
        await s.execute(insert(TrackCatalogRef).on_conflict_do_nothing(), rows)


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
