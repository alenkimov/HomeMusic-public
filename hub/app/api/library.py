"""Own library: albums, tracks, covers, stars (FR-4 «В коллекцию», FR-5 editor).

The files are looked after by who brought them and by the admins (`app/library/access.py`)."""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AlbumCreateIn,
    AlbumEditOut,
    AlbumMergeIn,
    AlbumPatchIn,
    AlbumRenameIn,
    FileJournalOut,
    FileRowOut,
    LibAlbumOut,
    LibTrackOut,
    StarIn,
    StarredOut,
    TrackEditOut,
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
from app.db import get_session
from app.events import bus
from app.jobs.downloads import enqueue_scan
from app.jobs.queue import notify
from app.library import access, album_names, artists, editor, edits, tagger, trash
from app.library.artwork import album_cover_url
from app.library.importer import album_reldir, get_or_create_album, library_abs, library_lock
from app.library.owned import file_out
from app.library.tracks import current_file, current_files
from app.matching.normalize import normalize_title
from app.models import Album, DownloadItem, Track, TrackFile, User

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
    files = await current_files(s, [t.id for t in tracks]) if with_tracks else {}
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


@router.get("/api/library/albums/split")
async def split_albums(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> list[dict]:
    """Albums that are one album in pieces: the same name, and artists that differ only by who is a guest («Три дня
    дождя» and «Три дня дождя, Роки») — a file that names no artist of its album is filed by its own artists. Each
    group with the one to join the rest into (the one with the most tracks). Among the files the asker looks after."""
    from app.library.credits import main_artist

    rows = (await s.execute(select(Album).where(access.albums_of(user)))).scalars().all()
    counts = dict((await s.execute(select(Track.album_id, func.count(Track.id)).where(Track.album_id.is_not(None))
                                   .group_by(Track.album_id))).all())
    groups: dict[tuple[str, str], list[Album]] = {}
    for a in rows:
        key = (normalize_title(a.title).text, main_artist(a.album_artist).casefold())
        if key[0] and key[1]:
            groups.setdefault(key, []).append(a)
    out = []
    for albums in groups.values():
        if len(albums) < 2:
            continue
        albums.sort(key=lambda a: (-counts.get(a.id, 0), a.id))
        out.append({"title": albums[0].title, "target_id": albums[0].id,
                    "albums": [{"id": a.id, "title": a.title, "album_artist": a.album_artist, "year": a.year,
                                "track_count": counts.get(a.id, 0), "has_cover": _has_cover(a)} for a in albums]})
    return sorted(out, key=lambda g: g["title"].casefold())


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
        async with library_lock:
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


@router.get("/api/library/albums/{album_id}/cover")
async def get_cover(album_id: int, size: int | None = Query(None, ge=32, le=1000), user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    a = await s.get(Album, album_id)  # a picture is shown wherever the album is, to anybody
    if a is None:
        raise HTTPException(404, "Альбом не найден")
    p = library_abs(album_reldir(a)) / "cover.jpg"
    data: bytes | None = await asyncio.to_thread(p.read_bytes) if p.is_file() else None
    if data is None:
        t = (await s.execute(select(Track).where(Track.album_id == a.id, Track.path.is_not(None)).limit(1))).scalar_one_or_none()
        if t is not None and library_abs(t.path).is_file():
            data = await asyncio.to_thread(tagger.read_cover, library_abs(t.path))
    if data is None:
        raise HTTPException(404, "Нет обложки")
    if size:
        data = await asyncio.to_thread(_resize, data, size)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


def _resize(data: bytes, size: int) -> bytes:
    from PIL import Image

    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


@router.post("/api/library/albums/merge", response_model=LibAlbumOut)
async def merge(body: AlbumMergeIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    target = await access.need_own_album(s, user, body.target_id)
    for source_id in body.source_ids:
        await access.need_own_album(s, user, source_id)
    await editor.merge_albums(s, body.source_ids, target)
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
    return track_out(t, await current_file(s, t.id))


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
    data = await edits.track_cover(s, track_id)
    if data is None:
        raise HTTPException(404, "Нет обложки")
    if size:
        data = await asyncio.to_thread(_resize, data, size)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})


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
    return {"can_delete": may, "others": others, "has_file": await current_file(s, t.id) is not None,
            "mine": [{"id": str(p.id), "name": p.name, "liked": p.kind == "liked"} for p in mine]}


@router.get("/api/library/tracks/{track_id}/journal", response_model=FileJournalOut)
async def track_journal(track_id: int, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    """How the track's file came to the library: the journal of the download that brought it (the last one that
    kept a journal). The library is everybody's, so anybody may read how a file of it was picked."""
    t = await s.get(Track, track_id)
    if t is None:
        raise HTTPException(404, "Трек не найден")
    f = await current_file(s, t.id)
    item = (await s.execute(select(DownloadItem).where(
        DownloadItem.track_id == t.id, func.jsonb_array_length(DownloadItem.log_json) > 0)
        .order_by(DownloadItem.id.desc()).limit(1))).scalar_one_or_none()
    file = file_out(f) if f else None
    if file:
        file.journal = item is not None
    return FileJournalOut(track_id=t.id, artist=t.artist, title=t.title, file=file,
                          steps=list(item.log_json or []) if item else [],
                          download_id=item.id if item and item.user_id == user.id and not item.hidden else None)


@router.post("/api/library/tracks/delete", response_model=TracksDeleteOut)
async def delete_tracks(body: TracksDeleteIn, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    """The files of the picked tracks go to the trash together — by the rule of one (library/access.py): a track
    somebody else keeps is left alone, unless an admin asks. A track with no file, or one that is gone, is passed."""
    out = TracksDeleteOut(deleted=0, kept=0, others=0, mine=0)
    for track_id in dict.fromkeys(body.track_ids):
        t = await s.get(Track, track_id)
        if t is None or await current_file(s, t.id) is None:
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
    from app.api.schemas import FavoriteTarget
    from app import playlists
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

