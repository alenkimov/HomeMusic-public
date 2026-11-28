"""Own library: albums, tracks, covers, stars (FR-4 «В коллекцию», FR-5 editor).

The files are looked after by who brought them and by the admins (`app/library/access.py`)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import covers
from app.api.schemas import (
    AlbumCreateIn,
    AlbumEditOut,
    AlbumMergeIn,
    AlbumPatchIn,
    AlbumRenameIn,
    DownloadStepsOut,
    FileEventOut,
    FileOut,
    FileRowOut,
    LibAlbumOut,
    LibTrackOut,
    StarIn,
    StarredOut,
    TrackEditOut,
    TrackFileOut,
    TrackFilesOut,
    TrackPatchIn,
    TracksDeleteIn,
    TracksDeleteOut,
    TracksMoveIn,
    TrackSourceOut,
    VersionLabelOut,
    VersionOut,
    VersionsOut,
)
from app.auth import current_user, user_navidrome
from app.catalog.models import CatalogAlbum, split_ref
from app.config import get_settings
from app.db import get_session
from app.events import bus
from app.jobs.downloads import enqueue_scan
from app.jobs.queue import notify
from app.library import access, album_names, artists, editor, edits, tagger, trash
from app.library.artwork import album_cover_url
from app.library.files import main_of, mains
from app.library.importer import album_reldir, get_or_create_album, library_abs, lock_library
from app.library.owned import file_out
from app.matching.normalize import normalize_title
from app.models import Album, DownloadItem, Track, TrackFile, TrackFileEvent, User

router = APIRouter(tags=["library"])
log = logging.getLogger(__name__)


def track_out(t: Track, f: TrackFile | None) -> LibTrackOut:
    """A track of the library with what its file is (`f`: the file that plays it)."""
    return LibTrackOut(
        id=t.id, nd_song_id=t.nd_song_id, album_id=t.album_id, artist=t.artist, title=t.title, album=t.album,
        album_artist=t.album_artist, track_no=t.track_no, disc_no=t.disc_no, year=t.year, duration_s=t.duration_s,
        bitrate_kbps=f.bitrate_kbps if f else None, codec=f.codec if f else None, explicit=t.explicit,
        source=f.source if f else None, locked=bool(f and f.locked), warnings=(f.warnings if f else None) or [],
        path=f.path if f else "",
    )


def _has_cover(a: Album) -> bool:
    return (library_abs(album_reldir(a)) / "cover.jpg").is_file()


async def album_out(s: AsyncSession, a: Album, user: User, *, with_tracks: bool = False) -> LibAlbumOut:
    """The album as `user` sees it among their files: their own tracks of it (an admin: all)."""
    tracks = (
        await s.execute(select(Track).where(Track.album_id == a.id, access.tracks_of(user))
                        .order_by(Track.disc_no, Track.track_no, Track.id))
    ).scalars().all()
    files = await mains(s, [t.id for t in tracks]) if with_tracks else {}
    return LibAlbumOut(
        id=a.id, uid=a.uid, title=a.title, album_artist=a.album_artist, year=a.year, release_type=a.release_type,
        genre=a.genre, deezer_album_id=a.deezer_album_id, track_count=len(tracks), has_cover=_has_cover(a),
        cover_url=album_cover_url(a),
        can_edit=await access.owns_album(s, user, a),
        tracks=[track_out(t, files.get(t.id)) for t in tracks] if with_tracks else None,
    )


async def _track(s: AsyncSession, track_id: int) -> Track:
    """A track to put right: anybody signed in may (app/library/edits.py) — the library is everybody's."""
    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "Трек не найден")
    return t


async def _album(s: AsyncSession, album_id: int) -> Album:
    a = await s.get(Album, album_id)
    if a is None:
        raise HTTPException(404, "Альбом не найден")
    return a


async def _after_edit(s: AsyncSession, user: User, album_id: int | None) -> None:
    await enqueue_scan(s)
    await s.commit()
    notify()
    bus.publish(user.id, "library", {"kind": "changed", "album_id": album_id})


@router.get("/api/library/albums", response_model=list[LibAlbumOut])
async def list_albums(q: str | None = None, offset: int = 0, limit: int = Query(50, le=200),
                      user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    rows = (await s.execute(select(Album).where(access.albums_of(user)).order_by(Album.updated_at.desc()))).scalars().all()
    if q:
        titles = await album_names.listed(s)
        words = artists.fold(q).split()
        rows = [a for a in rows if all(w in artists.fold(" ".join(
            [a.album_artist, a.title, *(row.name for row in titles.get(a.id, []))]
        )) for w in words)]
    rows = rows[offset: offset + limit]
    counts = dict((await s.execute(
        select(Track.album_id, func.count(Track.id)).where(Track.album_id.in_([a.id for a in rows]), access.tracks_of(user))
        .group_by(Track.album_id)
    )).all())
    return [
        LibAlbumOut(id=a.id, uid=a.uid, title=a.title, album_artist=a.album_artist, year=a.year,
                    release_type=a.release_type, genre=a.genre, deezer_album_id=a.deezer_album_id,
                    track_count=counts.get(a.id, 0), has_cover=_has_cover(a),
                    can_edit=await access.owns_album(s, user, a))
        for a in rows
    ]


@router.get("/api/library/files")
async def list_files(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> list[FileRowOut]:
    """«Все файлы» / «Мои файлы»: every track with a file that the asker looks after, the newest file first."""
    from app import playlists
    from app.api.playlists import tracks_out

    rows = (await s.execute(
        select(Track, func.max(TrackFile.acquired_at)).join(TrackFile, TrackFile.track_id == Track.id)
        .where(access.tracks_of(user)).group_by(Track.id).order_by(func.max(TrackFile.acquired_at).desc(), Track.id.desc())
    )).all()
    names = dict((await s.execute(select(User.id, User.nd_username))).all())
    liked, listed = await playlists.kept(s, user.id)
    drawn = await tracks_out(s, user, [t for t, _ in rows])
    out = []
    for (t, at), song in zip(rows, drawn, strict=True):
        out.append(FileRowOut(**{**song.model_dump(), "added_at": at}, added_by=names.get(t.created_by),
                              mine=t.created_by == user.id, kept=t.id in liked or t.id in listed,
                              no_album=t.album_id is None))
    return out


@router.get("/api/library/files/doubles")
async def file_doubles(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> list[list[int]]:
    """The tracks that are one recording twice (told by the sound): groups of track ids, of the asker's files."""
    from app.library import acoustic

    groups = await acoustic.doubles(s)
    if user.is_admin:
        return groups
    own = set((await s.execute(select(Track.id).where(access.tracks_of(user)))).scalars())
    return [g for g in groups if any(t in own for t in g)]


def _split_key(ids) -> str:
    return ",".join(str(i) for i in sorted(set(ids)))


async def split_groups(s: AsyncSession, user: User) -> list[list[Album]]:
    """The albums in pieces among the files `user` looks after: groups of one name and one main artist."""
    from app.library.credits import main_artist

    rows = (await s.execute(select(Album).where(access.albums_of(user)))).scalars().all()
    groups: dict[tuple[str, str], list[Album]] = {}
    for a in rows:
        key = (normalize_title(a.title).text, main_artist(a.album_artist).casefold())
        if key[0] and key[1]:
            groups.setdefault(key, []).append(a)
    return [albums for albums in groups.values() if len(albums) >= 2]


async def split_waiting(s: AsyncSession, user: User) -> int:
    """How many of those groups wait for an answer (none called separate releases)."""
    from app.models import AlbumSplitDismissal

    dismissed = set((await s.execute(select(AlbumSplitDismissal.key))).scalars().all())
    return sum(_split_key(a.id for a in g) not in dismissed for g in await split_groups(s, user))


@router.get("/api/library/albums/split")
async def split_albums(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> list[dict]:
    """Albums that are one album in pieces: the same name, and artists that differ only by who is a guest («Три дня
    дождя» and «Три дня дождя, Роки») — a file that names no artist of its album is filed by its own artists. Each
    group with the one to join the rest into (the one with the most tracks), each album with its tracks in order
    (their cards: `POST /api/tracks/cards`). Among the files the asker looks after. A group someone called separate
    releases («Это разные альбомы») is `dismissed`: «Объединение альбомов» shows it apart and does not count it."""
    from app.models import AlbumSplitDismissal

    split = await split_groups(s, user)
    ids = [a.id for albums in split for a in albums]
    tracks: dict[int, list[int]] = {}
    if ids:
        for album_id, track_id in (await s.execute(
                select(Track.album_id, Track.id).where(Track.album_id.in_(ids))
                .order_by(Track.album_id, Track.disc_no.nulls_last(), Track.track_no.nulls_last(), Track.id))).all():
            tracks.setdefault(album_id, []).append(track_id)
    dismissed = set((await s.execute(select(AlbumSplitDismissal.key))).scalars().all())
    # each credit of a part with its page, as on the album's own page
    from app.library.credits import split_artists

    credits = {a.id: split_artists(a.album_artist) or [a.album_artist] for albums in split for a in albums}
    names = await artists.reader(s, (n for ns in credits.values() for n in ns))

    # where each part came from: a transfer of which service, the sources its files were downloaded from, an upload
    from app.models import Import, ImportItem

    came: dict[int, list[dict]] = {}
    if ids:
        for album_id, service, n in (await s.execute(
                select(Track.album_id, Import.source, func.count(func.distinct(Track.id)))
                .join(ImportItem, ImportItem.track_id == Track.id).join(Import, Import.id == ImportItem.import_id)
                .where(Track.album_id.in_(ids)).group_by(Track.album_id, Import.source)
                .order_by(func.count(func.distinct(Track.id)).desc()))).all():
            came.setdefault(album_id, []).append({"kind": "import", "service": service, "count": n})
        for album_id, service, n in (await s.execute(
                select(Track.album_id, TrackFile.source, func.count(func.distinct(Track.id)))
                .join(TrackFile, TrackFile.track_id == Track.id)
                .where(Track.album_id.in_(ids), TrackFile.source.is_not(None)).group_by(Track.album_id, TrackFile.source)
                .order_by(func.count(func.distinct(Track.id)).desc()))).all():
            came.setdefault(album_id, []).append({"kind": "file", "service": service, "count": n})

    def linked(album_id: int) -> list[dict]:
        return [{"name": n, "id": hit[0] if (hit := names.artist(n)) else None} for n in credits[album_id]]

    out = []
    for albums in split:
        albums.sort(key=lambda a: (-len(tracks.get(a.id, [])), a.id))
        out.append({"title": albums[0].title, "target_id": albums[0].id, "key": _split_key(a.id for a in albums),
                    "dismissed": _split_key(a.id for a in albums) in dismissed,
                    "albums": [{"id": a.id, "title": a.title, "album_artist": a.album_artist, "year": a.year,
                                "track_count": len(tracks.get(a.id, [])), "track_ids": tracks.get(a.id, []),
                                "has_cover": _has_cover(a), "artists": linked(a.id),
                                "sources": came.get(a.id, [])} for a in albums]})
    return sorted(out, key=lambda g: g["title"].casefold())


class SplitIn(BaseModel):
    album_ids: list[int] = Field(min_length=2, max_length=100)


@router.post("/api/library/albums/split/dismiss")
async def dismiss_split(body: SplitIn, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")) -> dict:
    """«Это разные альбомы»: this group is not offered for joining again (until it gains or loses an album)."""
    from app.models import AlbumSplitDismissal

    for album_id in body.album_ids:
        await access.need_album(s, user, album_id)
    key = _split_key(body.album_ids)
    if (await s.execute(select(AlbumSplitDismissal.id).where(AlbumSplitDismissal.key == key))).first() is None:
        s.add(AlbumSplitDismissal(key=key, album_ids=sorted(set(body.album_ids)), user_id=user.id))
    await s.commit()
    return {"ok": True}


@router.post("/api/library/albums/split/restore")
async def restore_split(body: SplitIn, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")) -> dict:
    """The group called separate releases is offered for joining again."""
    from sqlalchemy import delete

    from app.models import AlbumSplitDismissal

    for album_id in body.album_ids:
        await access.need_album(s, user, album_id)
    await s.execute(delete(AlbumSplitDismissal).where(AlbumSplitDismissal.key == _split_key(body.album_ids)))
    await s.commit()
    return {"ok": True}


@router.post("/api/library/albums", response_model=LibAlbumOut)
async def create_album(body: AlbumCreateIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    a = Album(uid=str(uuid.uuid4()), title=body.title.strip(), album_artist=body.album_artist.strip(), year=body.year,
              genre=body.genre, release_type=body.release_type, created_by=user.id)
    s.add(a)
    await s.flush()
    return await album_out(s, a, user, with_tracks=True)


@router.get("/api/library/track-source", response_model=TrackSourceOut)
async def track_source(nd_song_id: str, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    f = (await s.execute(select(TrackFile).where(TrackFile.nd_song_id == nd_song_id).limit(1))).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, "Трек не найден")
    return TrackSourceOut(source=f.source, source_url=f.source_url, acquired_at=f.acquired_at)


@router.get("/api/library/albums/{album_id}", response_model=LibAlbumOut)
async def get_album(album_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await album_out(s, await access.need_album(s, user, album_id), user, with_tracks=True)


@router.patch("/api/library/albums/{album_id}", response_model=LibAlbumOut)
async def patch_album(album_id: int, body: AlbumPatchIn, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    a = await _album(s, album_id)
    await edits.update_album(s, user, a, **body.model_dump(exclude_unset=True))
    await _after_edit(s, user, a.id)
    return await album_out(s, a, user, with_tracks=True)


class AlternateNameIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


async def _album_edit_target(
    s: AsyncSession, album_id: int | None, ref: str | None,
) -> tuple[Album | None, CatalogAlbum | None]:
    if (album_id is None) == (ref is None):
        raise HTTPException(422, "Укажи альбом или ссылку на каталог")
    if album_id is not None:
        return await _album(s, album_id), None
    from app.api.catalog import editable_release

    assert ref is not None
    release, local = await editable_release(s, ref)
    return local, release


async def _album_edit_info(
    s: AsyncSession, user: User, local: Album | None, release: CatalogAlbum | None,
) -> AlbumEditOut:
    assert local is not None or release is not None
    title = local.title if local is not None else release.title
    artist = local.album_artist if local is not None else release.artist
    known = (await album_names.listed(s, [local.id])).get(local.id, []) if local is not None else []
    names = {row.key: {"id": row.id if row.source != "catalog" else None, "name": row.name, "source": row.source}
             for row in known if row.key != artists.key(title)}
    if release is not None:
        for name in [release.title, *release.alternate_titles]:
            if (key := artists.key(name)) and key != artists.key(title):
                names[key] = {"id": None, "name": name, "source": "catalog"}
    return AlbumEditOut(album_id=local.id if local is not None else None, title=title, album_artist=artist,
                        names=list(names.values()), can_edit=local is None or await access.owns_album(s, user, local))


async def _ensure_album_edit(
    s: AsyncSession, user: User, local: Album | None, release: CatalogAlbum | None,
) -> Album:
    if local is None:
        from app.api.catalog import release_spec

        assert release is not None
        await lock_library(s)
        local = await get_or_create_album(s, release_spec(release, user))
        await s.commit()
    if not await access.owns_album(s, user, local):
        raise HTTPException(403, "Изменить названия этого альбома может администратор")
    return local


async def _keep_catalog_album_names(s: AsyncSession, local: Album, release: CatalogAlbum | None) -> None:
    if release is not None:
        from app.catalog import registry

        for name in [release.title, *release.alternate_titles]:
            await album_names.add(s, local, name, "catalog")
        row = await registry.by_address(s, release.ref)
        if row is not None and row.album_id is None:
            row.album_id = local.id


@router.get("/api/library/album-edit", response_model=AlbumEditOut)
async def get_album_edit(album_id: int | None = None, ref: str | None = None, user: User = Depends(current_user),
                         s: AsyncSession = Depends(get_session, scope="function")):
    local, release = await _album_edit_target(s, album_id, ref)
    return await _album_edit_info(s, user, local, release)


@router.patch("/api/library/album-edit", response_model=AlbumEditOut)
async def rename_album(body: AlbumRenameIn, album_id: int | None = None, ref: str | None = None,
                       user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    local, release = await _album_edit_target(s, album_id, ref)
    local = await _ensure_album_edit(s, user, local, release)
    await edits.update_album(s, user, local, **body.model_dump(exclude_unset=True))
    await _keep_catalog_album_names(s, local, release)
    await _after_edit(s, user, local.id)
    return await _album_edit_info(s, user, local, release)


@router.post("/api/library/album-edit/names", response_model=AlbumEditOut)
async def add_album_name(body: AlternateNameIn, album_id: int | None = None, ref: str | None = None,
                         user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    local, release = await _album_edit_target(s, album_id, ref)
    local = await _ensure_album_edit(s, user, local, release)
    await _keep_catalog_album_names(s, local, release)
    await album_names.add(s, local, body.name, "manual")
    return await _album_edit_info(s, user, local, release)


@router.delete("/api/library/album-edit/names/{name_id}", response_model=AlbumEditOut)
async def drop_album_name(name_id: int, album_id: int | None = None, ref: str | None = None,
                          user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    local, release = await _album_edit_target(s, album_id, ref)
    if local is None:
        raise HTTPException(404, "Название не найдено")
    local = await _ensure_album_edit(s, user, local, release)
    await album_names.drop(s, local, name_id)
    return await _album_edit_info(s, user, local, release)


@router.put("/api/library/albums/{album_id}/cover", response_model=LibAlbumOut)
async def put_cover(album_id: int, file: UploadFile = File(...), user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    a = await _album(s, album_id)
    return await save_album_cover(s, user, a, await read_cover_upload(file))


async def read_cover_upload(file: UploadFile) -> bytes:
    data = await file.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Картинка слишком большая")
    return data


async def save_album_cover(s: AsyncSession, user: User, a: Album, data: bytes) -> LibAlbumOut:
    try:
        await edits.set_album_cover(s, user, a, data)
    except Exception as e:
        raise HTTPException(400, f"Не удалось прочитать картинку: {e}") from e
    await _after_edit(s, user, a.id)
    return await album_out(s, a, user, with_tracks=True)


async def _cover_bytes(s: AsyncSession, a: Album | None) -> bytes | None:
    """An album's picture: its cover.jpg, else the one in its first file."""
    if a is None:
        return None
    p = library_abs(album_reldir(a)) / "cover.jpg"
    if p.is_file():
        return await asyncio.to_thread(p.read_bytes)
    t = (await s.execute(select(Track).where(Track.album_id == a.id, Track.path.is_not(None)).limit(1))).scalar_one_or_none()
    f = library_abs(t.path) if t is not None else None
    return await asyncio.to_thread(tagger.read_cover, f) if f is not None and f.is_file() else None


@router.get("/api/library/albums/{album_id}/cover")
async def get_cover(album_id: int, size: int | None = Query(None, ge=32, le=1000), v: str | None = None,
                    user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The album's picture (`size`: a copy for a list, made once and kept). Its address names its version (`v`, the
    album's last change): such an address is the same picture for good, and a browser keeps it so."""
    a = await s.get(Album, album_id)  # a picture is shown wherever the album is, to anybody
    if a is None:
        raise HTTPException(404, "Альбом не найден")
    p = library_abs(album_reldir(a)) / "cover.jpg"
    if p.is_file():
        key = f"{p}:{p.stat().st_mtime_ns}"

        async def load() -> bytes | None:
            return await asyncio.to_thread(p.read_bytes)
    else:
        t = (await s.execute(select(Track).where(Track.album_id == a.id, Track.path.is_not(None)).limit(1))).scalar_one_or_none()
        f = library_abs(t.path) if t is not None else None
        if f is None or not f.is_file():
            raise HTTPException(404, "Нет обложки")
        key = f"{f}:{f.stat().st_mtime_ns}"

        async def load() -> bytes | None:
            return await asyncio.to_thread(tagger.read_cover, f)
    data = await (covers.sized(key, size, load) if size else load())
    if data is None:
        raise HTTPException(404, "Нет обложки")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": _FOR_GOOD if v else "private, max-age=3600"})


_FOR_GOOD = "private, max-age=31536000, immutable"


@router.post("/api/library/albums/merge", response_model=LibAlbumOut)
async def merge(body: AlbumMergeIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    target = await access.need_own_album(s, user, body.target_id)
    for source_id in body.source_ids:
        await access.need_own_album(s, user, source_id)
    # «Объединение альбомов» chooses the name, the artist and the picture of what comes out; the picture is read
    # before its part is gone
    cover = None
    if body.cover_from is not None and body.cover_from != target.id:
        if body.cover_from not in body.source_ids:
            raise HTTPException(400, "Обложка — только одной из объединяемых частей")
        cover = await _cover_bytes(s, await s.get(Album, body.cover_from))
    await editor.merge_albums(s, body.source_ids, target)
    if body.title or body.album_artist:
        await edits.update_album(s, user, target, title=body.title, album_artist=body.album_artist)
    if cover:
        await edits.set_album_cover(s, user, target, cover)
    await _after_edit(s, user, target.id)
    return await album_out(s, target, user, with_tracks=True)


@router.post("/api/library/tracks/move", response_model=LibAlbumOut)
async def move(body: TracksMoveIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    target = await access.need_album(s, user, body.album_id)
    for track_id in body.track_ids:
        await access.need_own_track(s, user, track_id)
    await editor.move_tracks(s, body.track_ids, target)
    await _after_edit(s, user, target.id)
    return await album_out(s, target, user, with_tracks=True)


@router.patch("/api/library/tracks/{track_id}", response_model=LibTrackOut)
async def patch_track(track_id: int, body: TrackPatchIn, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    t = await _track(s, track_id)
    try:
        await edits.update_track(s, user, t, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    await _after_edit(s, user, t.album_id)
    await s.refresh(t)
    return track_out(t, await main_of(s, t.id))


@router.get("/api/library/track-edit", response_model=TrackEditOut)
async def track_edit(track_id: int | None = None, ref: str | None = None, nd_song_id: str | None = None,
                     user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """A track's names as they are, what the catalog calls it and who changed it last — for the window that changes
    them. A song of the catalog the Hub has not met yet becomes a track here: it can be renamed as any other."""
    from app.lyrics.service import track_for

    t = await s.get(Track, track_id) if track_id else await track_for(s, nd_song_id=nd_song_id, ref=ref, user_id=user.id)
    if t is None:
        raise HTTPException(404, "Трек не найден")
    out = await edits.info(s, t)
    await s.commit()
    return out


@router.put("/api/library/tracks/{track_id}/cover", response_model=TrackEditOut)
async def put_track_cover(track_id: int, file: UploadFile = File(...), user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session, scope="function")):
    """The track's own cover, for everyone: where the album's picture is not its."""
    t = await _track(s, track_id)
    data = await read_cover_upload(file)
    try:
        await edits.set_track_cover(s, user, t, data)
    except Exception as e:
        raise HTTPException(400, f"Не удалось прочитать картинку: {e}") from e
    await _after_edit(s, user, t.album_id)
    return await edits.info(s, t)


@router.delete("/api/library/tracks/{track_id}/cover", response_model=TrackEditOut)
async def delete_track_cover(track_id: int, user: User = Depends(current_user),
                             s: AsyncSession = Depends(get_session, scope="function")):
    """No cover of its own any more: the album's again."""
    t = await _track(s, track_id)
    await edits.set_track_cover(s, user, t, None)
    await _after_edit(s, user, t.album_id)
    return await edits.info(s, t)


@router.get("/api/library/tracks/{track_id}/cover")
async def get_track_cover(track_id: int, size: int | None = Query(None, ge=32, le=1000), user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session, scope="function")):
    key = (await s.execute(select(Track.cover_key).where(Track.id == track_id))).scalar_one_or_none()
    if not key:
        raise HTTPException(404, "Нет обложки")

    async def load() -> bytes | None:
        return await edits.track_cover(s, track_id)
    data = await (covers.sized(f"track:{track_id}:{key}", size, load) if size else load())
    if data is None:
        raise HTTPException(404, "Нет обложки")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": _FOR_GOOD})


@router.get("/api/library/tracks/{track_id}/edits")
async def track_edits(track_id: int, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")) -> list[dict]:
    """The journal of what was changed of the track, the latest first: any of it can be gone back to."""
    return await edits.history(s, (await _track(s, track_id)).id)


@router.get("/api/library/trash")
async def trash_list(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> list[dict]:
    """«Корзина»: the tracks deleted lately, whose files are still kept — anybody may bring one back."""
    return await trash.listed(s)


@router.post("/api/library/trash/{trashed_id}/restore")
async def trash_restore(trashed_id: int, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")) -> dict:
    from app.models import TrashedTrack

    row = await s.get(TrashedTrack, trashed_id)
    if row is None:
        raise HTTPException(404, "В корзине такого нет")
    try:
        t = await trash.restore(s, row)
    except ValueError as e:
        raise HTTPException(410, str(e)) from e
    await _after_edit(s, user, t.album_id)
    return {"ok": True, "track_id": t.id}


@router.post("/api/library/tracks/{track_id}/find-file")
async def find_file(track_id: int, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    """A track there is nothing to play of — known by its name alone, no file: a visible download by its names. The
    user picks the file on its page (`/api/downloads/{id}`), and the file is this track's: it stays where it stands
    and plays from then on."""
    from app.imports import apply
    from app.jobs.queue import enqueue
    from app.library.tracks import has_file
    from app.models import DownloadBatch, Import, ImportItem
    from app.providers.base import TrackQuery

    track = await s.get(Track, track_id)
    if track is None:
        raise HTTPException(404, "Хаб не знает этот трек")
    if await has_file(s, track.id):
        raise HTTPException(400, "У трека уже есть файл")
    # a transfer brought it: the transfer's row follows the download, as when the file is picked on its page
    row = (await s.execute(
        select(ImportItem, Import).join(Import, Import.id == ImportItem.import_id)
        .where(Import.user_id == user.id, ImportItem.track_id == track.id, ImportItem.match == "not_found",
               ImportItem.status.in_(("not_found", "failed")))
        .order_by(ImportItem.id.desc()).limit(1))).first()
    if row is not None:
        dl = await apply.pick(s, row[1], row[0])
    else:
        batch = DownloadBatch(user_id=user.id, origin="hub", title=f"{track.artist} — {track.title}")
        s.add(batch)
        await s.flush()
        q = TrackQuery(artist=track.artist, title=track.title, album=track.album, duration_s=track.duration_s,
                       isrc=track.isrc)
        dl = DownloadItem(batch_id=batch.id, user_id=user.id,
                          query_json={"query": q.model_dump(), "meta": {"track_id": track.id}, "requested": True,
                                      "pick_only": True})
        s.add(dl)
        await s.flush()
        await enqueue(s, "download_item", {"item_id": dl.id})
    await s.commit()
    notify()
    return {"download_item_id": dl.id}


@router.get("/api/library/tracks/{track_id}/keeping")
async def track_keeping(track_id: int, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")) -> dict:
    """Whether the asker may delete the track's file (library/access.py): nobody else keeps it — and what keeps it
    on their own side, to take it out of there instead."""
    t = await _track(s, track_id)
    may, others, mine = await access.keeping(s, user, t.id)
    return {"can_delete": may, "others": others, "has_file": await main_of(s, t.id) is not None,
            "mine": [{"id": str(p.id), "name": p.name, "liked": p.kind == "liked"} for p in mine]}


async def _files_out(s: AsyncSession, t: Track) -> TrackFilesOut:
    from app.library import files as library_files

    have = await library_files.of_track(s, t.id)
    events = await library_files.history(s, t.id)
    came = {e.file_id: e.download_item_id for e in events if e.kind == "added" and e.file_id and e.download_item_id}
    told = set((await s.execute(select(DownloadItem.id).where(
        DownloadItem.id.in_(set(came.values())), func.jsonb_array_length(DownloadItem.log_json) > 0))).scalars()) if came else set()
    people = dict((await s.execute(select(User.id, User.nd_username).where(
        User.id.in_({e.user_id for e in events if e.user_id})))).all()) if events else {}
    out = []
    for f in have:
        row = file_out(f)
        out.append(TrackFileOut(**row.model_dump(), id=f.id, main=f.main, name=PurePosixPath(f.path).name,
                                acquired_at=f.acquired_at, source_url=f.source_url,
                                download_id=came.get(f.id) if came.get(f.id) in told else None))
    lines = []
    for e in events:
        data = e.file or {}
        lines.append(FileEventOut(
            id=e.id, kind=e.kind, why=e.why, at=e.at, who=people.get(e.user_id), file_id=e.file_id,
            file=FileOut(source=data.get("source"), codec=data.get("codec"), bitrate_kbps=data.get("bitrate_kbps"),
                         uid=data.get("uid")),
            name=PurePosixPath(data["path"]).name if data.get("path") else None,
            can_restore=library_files.kept(e) is not None))
    return TrackFilesOut(track_id=t.id, artist=t.artist, title=t.title, files=out, events=lines,
                         trash_days=get_settings().cfg.library.trash_days)


@router.get("/api/library/tracks/{track_id}/files", response_model=TrackFilesOut)
async def track_files(track_id: int, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    """The track's files and their journal (`library.files`). The library is everybody's: anybody may read it."""
    return await _files_out(s, await _track(s, track_id))


@router.get("/api/library/tracks/{track_id}/files/{file_id}/steps", response_model=DownloadStepsOut)
async def file_steps(track_id: int, file_id: int, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    """How a file of the track came: the journal of the download that brought it."""
    item = (await s.execute(select(DownloadItem).join(TrackFileEvent, TrackFileEvent.download_item_id == DownloadItem.id)
                            .where(TrackFileEvent.track_id == track_id, TrackFileEvent.file_id == file_id,
                                   TrackFileEvent.kind == "added")
                            .order_by(TrackFileEvent.id.desc()).limit(1))).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Журнала загрузки нет")
    return DownloadStepsOut(steps=list(item.log_json or []),
                            download_id=item.id if item.user_id == user.id and not item.hidden else None)


async def _after_files(s: AsyncSession, user: User, t: Track) -> TrackFilesOut:
    from app.listen import forget

    await enqueue_scan(s)
    await s.commit()
    notify()
    await forget(f"lib:{t.id}")  # what was ready to play of it was the old main file
    bus.publish(user.id, "library", {"kind": "imported", "track_id": t.id})
    return await _files_out(s, t)


@router.post("/api/library/tracks/{track_id}/files/{file_id}/main", response_model=TrackFilesOut)
async def make_file_main(track_id: int, file_id: int, user: User = Depends(current_user),
                         s: AsyncSession = Depends(get_session, scope="function")):
    """This file of the track plays from now on; the one that did stays beside it."""
    from app.library import files as library_files

    t = await _track(s, track_id)
    f = await s.get(TrackFile, file_id)
    if f is None or f.track_id != t.id:
        raise HTTPException(404, "Файла у трека уже нет")
    await library_files.make_main(s, f, why="listener", user_id=user.id)
    return await _after_files(s, user, t)


@router.post("/api/library/tracks/{track_id}/files/{file_id}/trash", response_model=TrackFilesOut)
async def trash_file(track_id: int, file_id: int, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    """A file kept beside the main one goes to the trash folder (it is brought back from the journal while it is
    kept there). The main one is not: another is made the main one first, or the track is deleted."""
    from app.library import files as library_files

    t = await _track(s, track_id)
    f = await s.get(TrackFile, file_id)
    if f is None or f.track_id != t.id:
        raise HTTPException(404, "Файла у трека уже нет")
    if f.main:
        raise HTTPException(409, "Это главный файл трека: сначала сделайте главным другой")
    await library_files.trash(s, f, why="listener", user_id=user.id)
    return await _after_files(s, user, t)


@router.post("/api/library/tracks/{track_id}/files/restore/{event_id}", response_model=TrackFilesOut)
async def restore_file(track_id: int, event_id: int, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    """A file of the track that went to the trash folder comes back as its main file."""
    from app.library import files as library_files

    t = await _track(s, track_id)
    event = await s.get(TrackFileEvent, event_id)
    if event is None or event.track_id != t.id:
        raise HTTPException(404, "Записи нет")
    try:
        await library_files.restore(s, event, why="listener", user_id=user.id)
    except library_files.FileGone as e:
        raise HTTPException(410, str(e)) from e
    return await _after_files(s, user, t)


@router.post("/api/library/tracks/delete", response_model=TracksDeleteOut)
async def delete_tracks(body: TracksDeleteIn, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    """The files of the picked tracks go to the trash together — by the rule of one (library/access.py): a track
    somebody else keeps is left alone, unless an admin asks. A track with no file, or one that is gone, is passed."""
    out = TracksDeleteOut(deleted=0, kept=0, others=0, mine=0)
    for track_id in dict.fromkeys(body.track_ids):
        t = await s.get(Track, track_id)
        if t is None or await main_of(s, t.id) is None:
            continue
        may, others, mine = await access.keeping(s, user, t.id)
        if not may:
            out.kept += 1
            continue
        out.deleted += 1
        out.others += bool(others)
        out.mine += bool(mine)
        if not body.dry:
            await editor.trash_track(s, t, user.id)
    if out.deleted and not body.dry:
        await _after_edit(s, user, None)
    return out


@router.delete("/api/library/tracks/{track_id}")
async def delete_track(track_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    t = await access.need_deletable_track(s, user, track_id)
    album_id = t.album_id
    await editor.trash_track(s, t, user.id)
    await _after_edit(s, user, album_id)
    return {"ok": True}


@router.get("/api/starred", response_model=StarredOut)
async def starred(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    from app import playlists
    from app.api.schemas import FavoriteTarget
    from app.favorites import ND_FIELDS, AliasIndex, imported_targets, project_liked, snapshot, subjects_of

    # what the buttons of tracks go by is the Hub's own list; Navidrome is asked only about albums and artists
    nd = user_navidrome(user)
    try:
        body = await nd.get_starred2()
    except Exception as e:
        log.info("starred: Navidrome did not say what is starred: %r", e)
        body = None
    finally:
        await nd.aclose()
    await project_liked(s, user.id, body.get("song") or [] if body is not None else None)
    await s.commit()
    body = body or {}
    liked = [t for _, t in await playlists.items(s, (await playlists.liked(s, user.id)).id)]
    states = await snapshot(s, user.id)
    subjects = [dict(target_type=x.target_type, key=x.key, ref=x.ref, nd_id=x.nd_id,
                     liked=x.liked, liked_since=x.since, target_data=x.data,
                     event_id=x.event.event_id, aliases=x.aliases, provenance="own") for x in states.values()]
    index = AliasIndex([x.event for x in states.values()], await subjects_of(s))  # albums and artists
    for state in states.values():
        index.join(state.target_type, set(state.aliases))
    imported = {index.root(k) for k in await imported_targets(s, user.id)}
    for typ, field in (("album", "album"), ("artist", "artist")):
        for obj in body.get(field, []):
            if not obj.get("id"):
                continue
            target = FavoriteTarget(target_type=typ, **{ND_FIELDS[typ]: obj["id"]})
            key = index.key(target)
            if key in imported and key not in states:
                subjects.append(dict(target_type=typ, key=key[1], nd_id=obj["id"], liked=True,
                                     aliases=[f"nd:{obj['id']}"], provenance="import"))
    refs = {t.ref for t in liked if t.ref and not t.nd_song_id}  # liked tracks the library has no song of yet
    unliked = {a for x in states.values() if x.target_type == "track" and not x.liked for a in x.aliases}
    foreign = [x["id"] for x in body.get("song", []) if x.get("id") and f"nd:{x['id']}" not in unliked]
    return StarredOut(song_ids=list(dict.fromkeys([*(t.nd_song_id for t in liked if t.nd_song_id), *foreign])),
                      album_ids=[x["id"] for x in body.get("album", []) if x.get("id")],
                      artist_ids=[x["id"] for x in body.get("artist", []) if x.get("id")],
                      refs=sorted(refs), subjects=subjects)


@router.post("/api/star")
async def star(body: StarIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    from app.api.schemas import FavoriteTarget
    from app.favorites import command

    targets = list(body.targets)
    targets += [FavoriteTarget(nd_song_id=x) for x in body.song_ids]
    targets += [FavoriteTarget(target_type="album", nd_album_id=x) for x in body.album_ids]
    targets += [FavoriteTarget(target_type="artist", nd_artist_id=x) for x in body.artist_ids]
    if body.refs and not body.unstar:
        raise HTTPException(400, "Чтобы отметить трек каталога, поставьте его в загрузку с ♥")
    targets += [FavoriteTarget(ref=x) for x in body.refs]
    nd = user_navidrome(user)
    try:
        duplicate_unlike = False
        if body.unstar and body.event_id and len(targets) == 1:
            from app.models import PlayEvent
            duplicate_unlike = (await s.execute(select(PlayEvent.id).where(
                PlayEvent.user_id == user.id, PlayEvent.event_id == body.event_id))).first() is not None
        if body.catalog_album_ref:
            src, id_ = split_ref(body.catalog_album_ref)
            q = select(Track.nd_song_id).join(Album, Album.id == Track.album_id).where(Track.nd_song_id.is_not(None))
            q = q.where(Album.deezer_album_id == id_) if src == "dz" else q.where(Album.id == -1)
            song_id = (await s.execute(q.limit(1))).scalar_one_or_none()
            if song_id is None:
                raise HTTPException(409, "Этого альбома ещё нет в библиотеке — сначала скачайте его")
            song = await nd.get_song(song_id)
            if not song.get("albumId"):
                raise HTTPException(409, "Альбом ещё не появился в Navidrome")
            targets.append(FavoriteTarget(target_type="album", ref=body.catalog_album_ref,
                                          nd_album_id=song["albumId"], target_data={"artist": song.get("artist")}))
        events = await command(s, user, targets, liked=not body.unstar, operation=body.event_id,
                               ts=body.ts, client=body.client, context=body.context, nd=nd)
        # Telemetry may have stored the same decision before the command. Pending downloads
        # still need cancellation, but an older unlike must not cancel a newer like.
        if body.unstar and body.event_id:
            from app.favorites import clear_pending, snapshot
            states = await snapshot(s, user.id)
            for target in targets:
                if target.target_type != "track" or not target.ref:
                    continue
                state = next((x for x in states.values() if x.target_type == "track" and x.ref == target.ref), None)
                if state and not state.liked:
                    await clear_pending(s, user.id, target.ref)
                    if duplicate_unlike and state.nd_id:
                        await nd.unstar(song_ids=[state.nd_id])
    except Exception:
        # Preserve successful chunks of a bulk request even if a later chunk fails.
        await s.commit()
        raise
    finally:
        await nd.aclose()
    from app.favorites import project_liked

    await project_liked(s, user.id)
    await s.commit()
    bus.publish(user.id, "library", {"kind": "unstarred" if body.unstar else "starred"})
    return {"ok": True, "event_ids": [e.event_id for e in events]}


@router.post("/api/library/tracks/{track_id}/names", response_model=TrackEditOut)
async def add_track_name(track_id: int, body: AlternateNameIn, user: User = Depends(current_user),
                         s: AsyncSession = Depends(get_session, scope="function")):
    """One more name the track is known by («Марафоны» for «Спорт»): the search finds it by that too."""
    from app.library import artists

    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "Трек не найден")
    await artists.add_title(s, t, body.name)
    out = await edits.info(s, t)
    await s.commit()
    return out


@router.delete("/api/library/tracks/{track_id}/names/{name_id}", response_model=TrackEditOut)
async def drop_track_name(track_id: int, name_id: int, user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session, scope="function")):
    from app.library import artists

    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "Трек не найден")
    await artists.drop_title(s, t, name_id)
    out = await edits.info(s, t)
    await s.commit()
    return out


# --- a song and its versions (app/library/versions.py) -----------------------------------------------------------


class VersionIn(BaseModel):
    """The track to join as a version: one of the Hub's (`track_id`), or a song of the catalog (`ref`) — which
    becomes a track here, without a file until it is downloaded."""

    track_id: int | None = None
    ref: str | None = Field(default=None, max_length=64)


class VersionPatchIn(BaseModel):
    """What to say of a version: a word for what it is (`label`; "" takes the word away), or that it is the
    preferred one."""

    label: str | None = Field(default=None, max_length=64)
    preferred: bool | None = None


async def _version_track(s: AsyncSession, track_id: int) -> Track:
    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "Трек не найден")
    return t


async def _versions(s: AsyncSession, user: User, t: Track) -> dict:
    from app.library import versions

    out = await versions.card(s, t)
    await s.commit()
    bus.publish(user.id, "library", {"kind": "changed", "album_id": t.album_id})
    return out


@router.get("/api/library/version-labels", response_model=list[VersionLabelOut])
async def version_labels(user: User = Depends(current_user),
                         s: AsyncSession = Depends(get_session, scope="function")):
    from app.library import versions

    return await versions.label_index(s)


@router.get("/api/library/versions", response_model=VersionsOut)
async def get_versions(track_id: int | None = None, ref: str | None = None, nd_song_id: str | None = None,
                       user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The song a track is, with every version of it. The track is named the way a row knows it; a song of the
    catalog the Hub has not met yet becomes a track here."""
    from app.library import versions
    from app.lyrics.service import track_for

    t = await s.get(Track, track_id) if track_id else await track_for(s, nd_song_id=nd_song_id, ref=ref, user_id=user.id)
    if t is None:
        raise HTTPException(404, "Трек не найден")
    out = await versions.card(s, t)
    await s.commit()
    return out


@router.get("/api/library/tracks/{track_id}/versions/find", response_model=list[VersionOut])
async def find_versions(track_id: int, q: str = Query(min_length=1), user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    from app.library import versions

    return await versions.candidates(s, await _version_track(s, track_id), q)


@router.post("/api/library/tracks/{track_id}/versions", response_model=VersionsOut)
async def add_version(track_id: int, body: VersionIn, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    """Another track is a recording of the same song."""
    from app.library import versions
    from app.lyrics.service import track_for

    t = await _version_track(s, track_id)
    other = await _version_track(s, body.track_id) if body.track_id else (await track_for(s, ref=body.ref, user_id=user.id) if body.ref else None)
    if other is None:
        raise HTTPException(404, "Трек не найден")
    try:
        await versions.link(s, user.id, t, other)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return await _versions(s, user, t)


@router.patch("/api/library/tracks/{track_id}/version", response_model=VersionsOut)
async def patch_version(track_id: int, body: VersionPatchIn, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    from app.library import versions

    t = await _version_track(s, track_id)
    try:
        if body.label is not None:
            await versions.label(s, user.id, t, body.label)
        if body.preferred:
            await versions.prefer(s, user.id, t)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return await _versions(s, user, t)


@router.delete("/api/library/tracks/{track_id}/version", response_model=VersionsOut)
async def drop_version(track_id: int, of: int | None = None, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    """The track is not a version of that song after all. `of`: the track whose window it was dropped in — the
    answer is that track's song."""
    from app.library import versions

    t = await _version_track(s, track_id)
    await versions.unlink(s, user.id, t)
    return await _versions(s, user, await _version_track(s, of) if of else t)



@router.get("/api/covers")
async def cover(url: str, user: User = Depends(current_user)):
    """A picture of a catalog or a service, by its address there: got once and kept on the Hub's disk (`app.covers`).
    Only the image CDNs it knows: the Hub is no way out to whatever a page names."""
    if not covers.allowed(url):
        raise HTTPException(400, "Не картинка каталога")
    data = await covers.fetch_cover(url)
    if data is None:
        raise HTTPException(404, "Каталог не отдал картинку", headers={"Cache-Control": "no-store"})
    return Response(data, media_type=covers.image_type(data), headers={"Cache-Control": _FOR_GOOD})


class CardsIn(BaseModel):
    ids: list[int] = Field(max_length=2000)


@router.post("/api/tracks/cards")
async def track_cards(body: CardsIn, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    """The cards of these tracks as a list shows them (`app.library.cards`), with what the user's transfers say of
    each — what a page takes in place of the rows it shows when the Hub says their cards changed (event `cards`),
    instead of reading its whole list again. A track the Hub has no more is left out."""
    import json

    from app.imports.review import unfound
    from app.library import cards

    ids = list(dict.fromkeys(body.ids))
    kept = await cards.read(s, ids)
    failed = await unfound(s, user.id, set(kept))
    out = []
    for i in ids:
        if (card := kept.get(i)) is None:
            continue
        out.append({**card, **({"unavailable": True} if i in failed and not card.get("owned") else {})})
    return Response(json.dumps(out, ensure_ascii=False), media_type="application/json")
