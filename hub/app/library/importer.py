"""Import an audio file into MUSIC_ROOT/library: album row, tags, atomic move, Track row (PLAN 8.4).

Navidrome is not notified here: callers enqueue an `nd_scan` job (coalesced) that scans and links nd ids.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from datetime import date
from pathlib import Path, PurePosixPath

from pydantic import BaseModel
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.library import tagger
from app.library.files import main_of
from app.library.layout import album_dirname, next_version_path, sanitize_component, track_filename, unique_path
from app.library.tracks import bind_refs, by_ref, dz_id, dz_ref
from app.matching.normalize import fingerprint, normalize_artist, normalize_title
from app.models import Album, AlbumName, CatalogRelease, CatalogReleaseRef, Track, TrackFile, utcnow

log = logging.getLogger(__name__)

# one import / library edit at a time: they touch the same folders and album rows
library_lock = asyncio.Lock()


class ImportSpec(BaseModel):
    artist: str
    title: str
    artists: list[str] = []
    artist_refs: list[str | None] = []  # the catalog's records of `artists`, in their order (None: not said)
    producer: str | None = None  # who made the beat (taken out of the name where it carried it)
    album: str | None = None
    album_artist: str | None = None
    album_id: int | None = None  # existing Hub album (uploads, editor)
    catalog_album_ref: str | None = None
    year: int | None = None
    release_date: date | None = None
    release_type: str | None = None
    track_no: int | None = None
    track_total: int | None = None
    disc_no: int | None = None
    genre: str | None = None
    genre_source: str | None = None
    isrc: str | None = None
    explicit: bool | None = None
    deezer_track_id: str | None = None
    ref: str | None = None  # the catalog track the file is of (a Deezer id alone is "dz:<id>")
    track_id: int | None = None  # the track the file is of, when the Hub knows it already
    deezer_album_id: str | None = None
    upc: str | None = None
    cover: bytes | None = None
    source: str | None = None
    source_url: str | None = None
    source_upload_date: date | None = None
    duration_s: int | None = None
    bitrate_kbps: int | None = None
    codec: str | None = None
    warnings: list[str] = []
    added_by: int | None = None
    force: bool = False  # explicit re-download: keep the old file, add a "[vN]" version next to it
    chroma: bytes | None = None  # the picture of the file's sound, when it is drawn already (an upload)
    # what brings the file, for the journal of the track's files (`library.files`): download / upload / picked /
    # upgrade; and the download it came by
    why: str = "download"
    download_item_id: int | None = None


def album_reldir(album: Album) -> PurePosixPath:
    return PurePosixPath(sanitize_component(album.album_artist), album_dirname(album.year, album.title))


def library_abs(rel: str | PurePosixPath) -> Path:
    return get_settings().library_dir / Path(*PurePosixPath(rel).parts)


async def find_album(s: AsyncSession, *, title: str, album_artist: str, deezer_album_id: str | None,
                     upc: str | None = None, year: int | None = None, catalog_refs: tuple[str, ...] = (),
                     track_refs: tuple[str, ...] = ()) -> Album | None:
    from app.library.album_names import catalog_uid
    from app.library.artists import key

    if deezer_album_id:
        a = (
            await s.execute(select(Album).where(Album.deezer_album_id == str(deezer_album_id)).limit(1))
        ).scalar_one_or_none()
        if a:
            return a
    if catalog_refs:
        known = (await s.scalars(select(Album).where(Album.uid.in_([catalog_uid(ref) for ref in catalog_refs])))).all()
        by_uid = {a.uid: a for a in known}
        for ref in catalog_refs:
            if a := by_uid.get(catalog_uid(ref)):
                return a
        album_ids = select(Track.album_id).where(or_(
            Track.ref.in_(track_refs), Track.catalog["album_ref"].as_string().in_(catalog_refs),
        ))
        if a := await s.scalar(select(Album).where(Album.id.in_(album_ids)).order_by(Album.id).limit(1)):
            return a
    nt, na = normalize_title(title), normalize_artist(album_artist).text

    def compatible(a: Album) -> bool:
        return (normalize_artist(a.album_artist).text == na
                and not (deezer_album_id and a.deezer_album_id and a.deezer_album_id != str(deezer_album_id))
                and not (upc and a.upc and a.upc.lstrip("0") != upc.lstrip("0"))
                and not (year and a.year and year != a.year))

    rows = (await s.execute(select(Album).where(Album.title == title))).scalars().all()
    if not rows:
        # same album typed with a different case/ё/punctuation
        rows = (await s.execute(select(Album))).scalars().all()
    for a in rows:
        current = normalize_title(a.title)
        if (current.text, current.version) == (nt.text, nt.version) and compatible(a):
            return a
    if catalog_refs:
        former = (await s.scalars(select(Album).join(AlbumName).where(
            AlbumName.key == key(title), AlbumName.source.in_(["catalog", "renamed"]),
        ))).all()
        matches = [a for a in former if compatible(a)]
        if len(matches) == 1:
            return matches[0]
    return None


async def get_or_create_album(s: AsyncSession, spec: ImportSpec) -> Album:
    from app.catalog import release_matches
    from app.library import album_names

    if spec.album_id:
        a = await s.get(Album, spec.album_id)
        if a is None:
            raise ValueError(f"album {spec.album_id} not found")
        return a
    title = spec.album or spec.title
    album_artist = spec.album_artist or spec.artist
    ref = spec.catalog_album_ref or dz_ref(spec.deezer_album_id)
    group = (await release_matches.groups_for(s, [ref])).get(ref, []) if ref else []
    deezer_id = next((item.id for item in group if item.source == "dz"), spec.deezer_album_id)
    refs = tuple(item.ref for item in group) or ((ref,) if ref else ())
    if group:
        title = group[0].title
    a = await find_album(s, title=title, album_artist=album_artist, deezer_album_id=deezer_id,
                         upc=spec.upc, year=spec.year or (spec.release_date.year if spec.release_date else None),
                         catalog_refs=refs, track_refs=tuple(t.ref for item in group for t in item.tracks or []))
    if a is None:
        a = Album(
            uid=album_names.catalog_uid(refs[0]) if refs else str(uuid.uuid4()),
            title=title,
            album_artist=album_artist,
            year=spec.year or (spec.release_date.year if spec.release_date else None),
            release_type=spec.release_type or ("single" if not spec.album else "album"),
            deezer_album_id=deezer_id,
            upc=spec.upc,
            genre=spec.genre,
            created_by=spec.added_by,
        )
        s.add(a)
        await s.flush()
    elif deezer_id and not a.deezer_album_id:
        a.deezer_album_id = deezer_id
    for item in group:
        await album_names.add(s, a, item.title, "catalog")
    if refs:
        registered = select(CatalogReleaseRef.release_id).where(CatalogReleaseRef.ref.in_(refs))
        await s.execute(update(CatalogRelease).where(
            CatalogRelease.id.in_(registered), CatalogRelease.album_id.is_(None),
        ).values(album_id=a.id))
    return a


def tagdata_for(track: Track, album: Album, file: TrackFile, *, cover: bytes | None = None,
                artists: list[str] | None = None, track_total: int | None = None) -> tagger.TagData:
    return tagger.TagData(
        title=track.title,
        artist=track.artist,
        artists=artists or [],
        album=album.title,
        album_artist=album.album_artist,
        track_no=track.track_no,
        track_total=track_total,
        disc_no=track.disc_no,
        date=str(album.year) if album.year else None,
        genre=track.genre or album.genre,
        isrc=track.isrc,
        explicit=track.explicit,
        release_type=album.release_type,
        track_uid=file.uid,
        album_uid=album.uid,
        source=file.source,
        source_url=file.source_url,
        source_date=file.source_upload_date,
        acquired=file.acquired_at,
        deezer_track_id=dz_id(track.ref),
        deezer_album_id=album.deezer_album_id,
        producer=track.producer,
        cover=cover,
    )


def _move(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dest)
    except OSError:
        shutil.move(str(src), str(dest))


def _write_cover_file(album_dir: Path, cover: bytes) -> None:
    album_dir.mkdir(parents=True, exist_ok=True)
    tmp = album_dir / ".cover.jpg.part"
    tmp.write_bytes(cover)
    os.replace(tmp, album_dir / "cover.jpg")


def is_original(source_date: date | None, explicit: bool | None) -> bool:
    """Pre-censorship upload or explicit version: never replaced automatically (PLAN 8.6)."""
    cutoff = get_settings().cfg.matching.original_before
    return bool(explicit) or (source_date is not None and source_date < cutoff)


class TrackAlreadyFiled(ValueError):
    """The current file changed while a manual file selection was being prepared."""


async def import_file(s: AsyncSession, src: Path, spec: ImportSpec, *, require_missing: bool = False,
                      fallback_cover: bytes | None = None) -> Track:
    """Tag `src` (a file in staging/), move it into the library and make its track and file rows (caller
    commits). `fallback_cover` is a prepared JPEG used when the track and album have no saved cover."""
    from app import tags
    from app.catalog import release_matches
    from app.library import artists, titles

    # the name is the name: a guest written into it is one of the artists, the producer has a field of their own
    spec.title, guests, producers = titles.clean(spec.title)
    if guests:
        spec.artist, spec.artists = titles.with_guests(spec.artist, spec.artists, guests)
    spec.producer = spec.producer or (", ".join(producers) or None)
    file_genres = []
    try:
        file_genres = tags.genre_names(await asyncio.to_thread(tagger.read_genres, src))
    except Exception:
        log.debug("unable to read original file genre: %s", src.name, exc_info=True)
    if spec.genre is None and file_genres:
        spec.genre = file_genres[0]
    ref = spec.ref or dz_ref(spec.deezer_track_id)
    met = await by_ref(s, ref)
    # Take the filesystem lock before acquiring database write locks for the import.
    async with library_lock:
        if require_missing and spec.track_id is not None and await main_of(s, spec.track_id) is not None:
            raise TrackAlreadyFiled()
        # who the music is by — by the catalog's records of them where the catalog said, else by the names — and the
        # lines in their names: a spelling that is somebody's is filed under their name
        refs = artists.beside(spec.artists, spec.artist_refs) or artists.refs_of(met.catalog if met else None)
        spec.artist, by = await artists.credits(s, spec.artist, refs)
        if spec.artists:
            spec.artists = [a.name for a in [await artists.who(s, n, refs.get(artists.key(n))) for n in spec.artists] if a is not None]
        album_by = None
        if spec.album_artist:
            spec.album_artist, album_by = await artists.credits(s, spec.album_artist, refs)
        album = await get_or_create_album(s, spec)
        # decoding, scaling and encoding a picture holds the loop for a while (more so on a weak CPU): not on it
        cover = await asyncio.to_thread(tagger.prepare_cover, spec.cover) if spec.cover else None
        album_dir_rel = album_reldir(album)
        album_dir = library_abs(album_dir_rel)
        if cover is None and (album_dir / "cover.jpg").is_file():
            cover = await asyncio.to_thread((album_dir / "cover.jpg").read_bytes)
        if cover is None:
            cover = fallback_cover

        # a file of a song the Hub knows is one more file of that track: its tags, lyrics and places stay with it
        known = await s.get(Track, spec.track_id) if spec.track_id else met
        track = known or Track(
            album_id=album.id,
            artist=spec.artist,
            title=spec.title,
            album=album.title,
            album_artist=album.album_artist,
            track_no=spec.track_no,
            disc_no=spec.disc_no,
            year=album.year,
            genre=spec.genre or album.genre,
            duration_s=spec.duration_s,
            isrc=spec.isrc.upper() if spec.isrc else None,
            ref=ref,
            explicit=spec.explicit,
            producer=spec.producer,
            fingerprint=fingerprint(spec.artist, spec.title),
            created_by=spec.added_by,
        )
        if known is not None:  # the file is filed under this album: the track follows its main file, this one
            known.album_id, known.album, known.album_artist = album.id, album.title, album.album_artist
            known.track_no, known.disc_no = spec.track_no or known.track_no, spec.disc_no or known.disc_no
            known.year, known.genre = album.year or known.year, spec.genre or album.genre or known.genre
            known.duration_s = spec.duration_s or known.duration_s
            known.explicit = spec.explicit if spec.explicit is not None else known.explicit
            known.producer = known.producer or spec.producer
            if known.title == known.ref or not known.artist:
                # met by its ref alone (a like that said nothing of the song): the file says what the song is
                known.artist, known.title = spec.artist, spec.title
                known.fingerprint = fingerprint(spec.artist, spec.title)
                known.isrc = known.isrc or (spec.isrc.upper() if spec.isrc else None)
        sources = dict(track.genre_sources or {})
        if file_genres:
            sources["file"] = file_genres
        else:
            sources.pop("file", None)
        if spec.genre_source and spec.genre:
            sources[spec.genre_source] = tags.genre_names([spec.genre])
        genres_changed = sources != (track.genre_sources or {})
        if genres_changed:
            track.genre_sources = sources
        track.genres_checked_at = None
        file = TrackFile(
            uid=str(uuid.uuid4()),
            path="",
            bitrate_kbps=spec.bitrate_kbps,
            codec=spec.codec,
            source=spec.source,
            source_url=spec.source_url,
            source_upload_date=spec.source_upload_date,
            acquired_at=utcnow(),
            locked=is_original(spec.source_upload_date, spec.explicit),
            warnings=list(spec.warnings),
            added_by=spec.added_by,
            chroma=spec.chroma,
        )

        await asyncio.to_thread(
            tagger.write_tags, src, tagdata_for(track, album, file, cover=cover, artists=spec.artists,
                                                track_total=spec.track_total)
        )

        ext = src.suffix.lstrip(".").lower() or "mp3"
        name = track_filename(spec.disc_no, spec.track_no, spec.title, ext)
        dest = album_dir / name
        if dest.exists():
            dest = next_version_path(dest) if spec.force else unique_path(album_dir, name)
        await asyncio.to_thread(_move, src, dest)
        if cover and not (album_dir / "cover.jpg").is_file():
            await asyncio.to_thread(_write_cover_file, album_dir, cover)
            album.cover_path = (album_dir_rel / "cover.jpg").as_posix()
        elif not album.cover_path and (album_dir / "cover.jpg").is_file():
            album.cover_path = (album_dir_rel / "cover.jpg").as_posix()  # the folder has its picture: so has the album

        file.path = PurePosixPath(album_dir_rel, dest.name).as_posix()
        if file.chroma is None:  # the picture of its sound: the next file that is this recording is known by it
            from app.library import acoustic

            got = await acoustic.of_file(dest)
            file.chroma = got[0] if got else None
        s.add(track)
        await s.flush()
        await bind_refs(s, track, [ref])
        catalog_album = spec.catalog_album_ref or dz_ref(spec.deezer_album_id) or (track.catalog or {}).get("album_ref")
        if catalog_album:
            group = (await release_matches.groups_for(s, [catalog_album])).get(catalog_album)
            if group:
                versions = release_matches.track_versions(group, ref or track.ref)
                await bind_refs(s, track, [other.ref for other in versions])
                for other in versions:
                    await artists.add_title(s, track, other.title, "catalog")
        await artists.credit_track(s, track, by if track.artist == spec.artist else None)
        if album_by and album.album_artist == spec.album_artist:
            await artists.credit_album(s, album, album_by)
        file.track_id = track.id
        from app.library import files

        # the main file of its track from now on (the one before stays beside it), written in the track's journal
        await files.added(s, file, why=spec.why, user_id=spec.added_by, download_item_id=spec.download_item_id)
        if track.genre or genres_changed:
            await tags.sync(s, [track.id])
        log.info("imported %s", file.path, extra={"user_id": spec.added_by})
        return track


def staging_dir_for(kind: str, key: str | int) -> Path:
    d = get_settings().staging_dir / kind / str(key)
    d.mkdir(parents=True, exist_ok=True)
    return d


def copy_limited(src, dest: Path, limit: int) -> int:
    size = 0
    with dest.open("wb") as output:
        while chunk := src.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise ValueError("too large")
            output.write(chunk)
    return size


def cleanup_dir(d: Path) -> None:
    shutil.rmtree(d, ignore_errors=True)
