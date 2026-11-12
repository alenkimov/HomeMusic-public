"""Playlists (app/playlists.py): the Hub's own. A playlist's `id` here is the Hub's; Navidrome has a mirror of it
(app/playlists_mirror.py) and is not asked about anything on these pages except what its songs look like."""

import asyncio
import io
import logging
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import playlists
from app.api.medialib import songs_out
from app.api.schemas import (
    NdSongOut,
    PlaylistAddIn,
    PlaylistCreateIn,
    PlaylistMembershipIn,
    PlaylistMembershipManyIn,
    PlaylistOfSongOut,
    PlaylistOfSongsOut,
    PlaylistOut,
    PlaylistPageOut,
    PlaylistRemoveIn,
    PlaylistRenameIn,
    PlaylistSongsIn,
)
from app.auth import current_user
from app.db import get_session
from app.jobs.queue import notify
from app.library import songs as library_songs
from app.models import Playlist, PlaylistCover, Track, TrackFile, User
from app.playlists_mirror import MIX, drop_mirror, enqueue_mirror

router = APIRouter(prefix="/api/playlists", tags=["playlists"])
log = logging.getLogger(__name__)


COVER_PX = 600
COVER_MAX_BYTES = 20 * 1024 * 1024
LISTED = (playlists.USER, MIX)  # what the list of playlists shows («Любимое» has a page of its own)


def _id(playlist_id: str) -> int:
    if not playlist_id.isdigit():
        raise HTTPException(404, "Плейлист не найден")
    return int(playlist_id)


async def _own(s: AsyncSession, user: User, playlist_id: str, *, change: bool = False) -> Playlist:
    """The user's playlist; `change`: one that is theirs to change («Микс дня» is the Hub's)."""
    p = await playlists.own(s, user.id, _id(playlist_id))
    if p is None or p.kind not in LISTED or (change and p.kind != playlists.USER):
        raise HTTPException(404, "Плейлист не найден")
    return p


async def _outs(s: AsyncSession, rows: list[Playlist]) -> list[PlaylistOut]:
    numbers = await playlists.counts(s, [p.id for p in rows])
    keys = dict((await s.execute(select(PlaylistCover.nd_playlist_id, PlaylistCover.key).where(
        PlaylistCover.nd_playlist_id.in_([str(p.id) for p in rows])))).all()) if rows else {}
    out = []
    for p in rows:
        total, filed, seconds = numbers.get(p.id, (0, 0, 0))
        if key := keys.get(str(p.id)):
            cover = f"/api/playlists/{p.id}/cover?v={key}"
        elif p.nd_cover and filed:
            cover = f"/api/nd/cover/{quote(p.nd_cover, safe='')}?v={filed}"  # it changes with the songs
        else:
            cover = None
        out.append(PlaylistOut(id=str(p.id), name=p.name, song_count=filed, pending_count=total - filed,
                               duration_s=seconds, cover_art=p.nd_cover, cover_url=cover))
    return out


async def _out(s: AsyncSession, p: Playlist) -> PlaylistOut:
    return (await _outs(s, [p]))[0]


async def _changed(s: AsyncSession, *rows: Playlist) -> None:
    """Commit, and have the mirrors of these playlists follow."""
    for p in rows:
        if p.kind == playlists.USER:
            await enqueue_mirror(s, p.id)
    await s.commit()
    notify()


def track_without_file(t: Track, *, downloading: bool = False) -> NdSongOut:
    """A track the library has no file of, drawn from what the catalog said of it."""
    c = t.catalog or {}
    ref = t.ref or f"lib:{t.id}"
    src, _, ident = ref.partition(":")
    return NdSongOut(
        ref=ref, source=src if src in ("dz", "hm", "mb") else "nd", id=ident, title=t.title, artist=t.artist,
        artists=c.get("artists") or [t.artist], artist_refs=c.get("artist_refs") or [], artist_ref=c.get("artist_ref"),
        album_ref=c.get("album_ref"), album_title=t.album, album_artist=t.album_artist, duration_s=t.duration_s,
        track_no=t.track_no, disc_no=t.disc_no, isrc=t.isrc, explicit=t.explicit, cover_url=c.get("cover_url"),
        cover_small_url=c.get("cover_small_url") or c.get("cover_url"), owned=False, library_track_id=t.id,
        downloading=downloading)


def track_with_file(t: Track) -> NdSongOut:
    """A track of the library drawn from the Hub's own record: for when Navidrome has not been asked about its
    song yet, or does not answer."""
    out = track_without_file(t)
    out.owned, out.nd_song_id = True, t.nd_song_id
    if not t.ref and t.nd_song_id:  # the catalog does not know it: it is named by its song, as everywhere
        out.ref, out.source, out.id = f"nd:{t.nd_song_id}", "nd", t.nd_song_id
    if t.album_id:
        out.cover_url = out.cover_small_url = f"/api/library/albums/{t.album_id}/cover"
    return out


async def tracks_out(s: AsyncSession, user: User, rows: list[Track]) -> list[NdSongOut]:
    """Tracks as a list shows them: one with a file as Navidrome's song of it, one without as the catalog's."""
    index: dict[str, dict] = {}
    if any(t.nd_song_id for t in rows):
        try:
            index = await library_songs.index(user)
        except Exception as e:  # Navidrome is away: the list is drawn from the Hub's own records
            log.info("playlists: songs not read from Navidrome: %r", e)
    known = [index[t.nd_song_id] for t in rows if t.nd_song_id in index]
    drawn = {x.nd_song_id: x for x in await songs_out(s, known)}
    return [drawn.get(t.nd_song_id) or (track_with_file(t) if t.path else track_without_file(t)) for t in rows]


async def _tracks_of_songs(s: AsyncSession, songs: list[str]) -> dict[str, int]:
    """What a page calls a track -> the track: the Navidrome song of its file, or — for a track with no file —
    the Hub's own id, as "t:<id>" (`tags.key_of`)."""
    from app.tags import TRACK_KEY

    out: dict[str, int] = {}
    own = {k: int(k[len(TRACK_KEY):]) for k in songs if k.startswith(TRACK_KEY) and k[len(TRACK_KEY):].isdigit()}
    if own:
        known = set((await s.execute(select(Track.id).where(Track.id.in_(list(own.values()))))).scalars())
        out.update((k, tid) for k, tid in own.items() if tid in known)
    songs = [k for k in songs if not k.startswith(TRACK_KEY)]
    for k in range(0, len(songs), 500):
        out.update((song, tid) for song, tid in (await s.execute(
            select(TrackFile.nd_song_id, TrackFile.track_id).where(TrackFile.nd_song_id.in_(songs[k:k + 500])))).all())
    return out


@router.get("", response_model=list[PlaylistOut])
async def list_playlists(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await _outs(s, await playlists.of_user(s, user.id, kinds=LISTED))


@router.get("/of-song", response_model=list[PlaylistOfSongOut])
async def of_song(nd_song_id: str, user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session, scope="function")):
    """The user's playlists for a song's «in playlists» menu: which of them have the song, the ones changed last
    first."""
    track = (await _tracks_of_songs(s, [nd_song_id])).get(nd_song_id)
    rows = await playlists.of_user(s, user.id)
    has = await playlists.having(s, user.id, [track], kinds=(playlists.USER,)) if track is not None else {}
    return [PlaylistOfSongOut(**o.model_dump(), has=p.id in has) for p, o in zip(rows, await _outs(s, rows), strict=True)]


@router.get("/members")
async def members(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The tracks that are in some playlist of the user's — what the «+ / ✓» of a track row goes by — named as
    tags name a track (`tags.key_of`): by the Navidrome song of its file, else by the Hub's id. «Микс дня» is not
    counted: the Hub fills it, the user did not put the tracks there."""
    from app.tags import key_of

    _liked, listed = await playlists.kept(s, user.id)
    ids = list(listed)
    keys: set[str] = set()
    for k in range(0, len(ids), 500):
        keys.update(key_of(tid, song) for tid, song in (await s.execute(
            select(Track.id, Track.nd_song_id).where(Track.id.in_(ids[k:k + 500])))).all())
    return {"song_ids": sorted(keys)}


async def _set_membership(s: AsyncSession, user: User, track_ids: list[int], add: list[str], remove: list[str]) -> None:
    changed = []
    for pid in dict.fromkeys(add):
        p = await _own(s, user, pid, change=True)
        if await playlists.add(s, p, track_ids):
            changed.append(p)
    for pid in dict.fromkeys(remove):
        p = await _own(s, user, pid, change=True)
        if await playlists.remove_tracks(s, p, track_ids):
            changed.append(p)
    await _changed(s, *changed)


@router.post("/membership", response_model=list[PlaylistOfSongOut])
async def membership(body: PlaylistMembershipIn, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    """Put a song into some playlists and take it out of others, in one go (every place it has in a playlist it is
    taken out of)."""
    track = (await _tracks_of_songs(s, [body.nd_song_id])).get(body.nd_song_id)
    if track is None:
        raise HTTPException(404, "Этой песни нет в библиотеке")
    await _set_membership(s, user, [track], body.add, body.remove)
    return await of_song(body.nd_song_id, user, s)


async def _of_songs(s: AsyncSession, user: User, song_ids: list[str]) -> list[PlaylistOfSongsOut]:
    ids = await _tracks_of_songs(s, song_ids)
    rows = await playlists.of_user(s, user.id)
    has = await playlists.having(s, user.id, list(ids.values()), kinds=(playlists.USER,))
    return [PlaylistOfSongsOut(**o.model_dump(), count=len(has.get(p.id, ())))
            for p, o in zip(rows, await _outs(s, rows), strict=True)]


@router.post("/of-songs", response_model=list[PlaylistOfSongsOut])
async def of_songs(body: PlaylistSongsIn, user: User = Depends(current_user),
                   s: AsyncSession = Depends(get_session, scope="function")):
    """The user's playlists for several picked songs: how many of them each has, the ones changed last first."""
    return await _of_songs(s, user, list(dict.fromkeys(body.nd_song_ids)))


@router.post("/membership-many", response_model=list[PlaylistOfSongsOut])
async def membership_many(body: PlaylistMembershipManyIn, user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session, scope="function")):
    """Put several songs into some playlists (the ones each lacks, in the order given) and take them all out of
    others."""
    songs = list(dict.fromkeys(body.nd_song_ids))
    ids = await _tracks_of_songs(s, songs)
    await _set_membership(s, user, [ids[x] for x in songs if x in ids], body.add, body.remove)
    return await _of_songs(s, user, songs)


@router.post("", response_model=PlaylistOut)
async def create_playlist(body: PlaylistCreateIn, user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session, scope="function")):
    if not body.name.strip():
        raise HTTPException(400, "Введите название")
    p = await playlists.create(s, user.id, body.name)
    await _changed(s, p)
    return await _out(s, p)


@router.post("/{playlist_id}/add")
async def add(playlist_id: str, body: PlaylistAddIn, user: User = Depends(current_user),
              s: AsyncSession = Depends(get_session, scope="function")):
    p = await _own(s, user, playlist_id, change=True)
    by_song = await _tracks_of_songs(s, list(body.song_ids))
    known = set((await s.execute(select(Track.id).where(Track.id.in_(body.library_track_ids)))).scalars()) \
        if body.library_track_ids else set()
    ids = [by_song[x] for x in body.song_ids if x in by_song] + [i for i in body.library_track_ids if i in known]
    if not ids:
        raise HTTPException(400, "Этих треков нет в библиотеке")
    added = await playlists.add(s, p, ids)
    await _changed(s, p)
    return {"ok": True, "added": len(added)}


@router.get("/{playlist_id}", response_model=PlaylistPageOut)
async def get_playlist(playlist_id: str, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    p = await _own(s, user, playlist_id)
    rows = await playlists.items(s, p.id)
    tracks = await tracks_out(s, user, [t for _, t in rows])
    for (item, _), t in zip(rows, tracks, strict=True):
        t.added_at = item.added_at
    return PlaylistPageOut(**(await _out(s, p)).model_dump(), tracks=tracks)


@router.post("/{playlist_id}/remove")
async def remove(playlist_id: str, body: PlaylistRemoveIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    """Takes tracks out by their positions (a track may be in a playlist twice)."""
    p = await _own(s, user, playlist_id, change=True)
    rows = await playlists.items(s, p.id)
    await playlists.remove_items(s, p, [rows[i][0].id for i in set(body.indexes) if 0 <= i < len(rows)])
    await _changed(s, p)
    return {"ok": True}


@router.delete("/{playlist_id}")
async def delete(playlist_id: str, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    p = await _own(s, user, playlist_id, change=True)
    nd_id = p.nd_playlist_id
    row = await s.get(PlaylistCover, str(p.id))
    if row is not None:
        await s.delete(row)
    await s.delete(p)
    await s.commit()
    await drop_mirror(user, nd_id)
    return {"ok": True}


@router.put("/{playlist_id}", response_model=PlaylistOut)
async def rename(playlist_id: str, body: PlaylistRenameIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(400, "Введите название")
    p = await _own(s, user, playlist_id, change=True)
    p.name = name[:200]
    await _changed(s, p)
    return await _out(s, p)


def _square_jpeg(data: bytes) -> bytes:
    from PIL import Image, ImageOps

    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    side = min(im.size)
    left, top = (im.width - side) // 2, (im.height - side) // 2
    im = im.crop((left, top, left + side, top + side))
    if side > COVER_PX:
        im = im.resize((COVER_PX, COVER_PX), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88)
    return out.getvalue()


@router.put("/{playlist_id}/cover", response_model=PlaylistOut)
async def put_cover(playlist_id: str, file: UploadFile = File(...), user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    """The user's own picture for the playlist: cut to a square, kept in the Hub."""
    p = await _own(s, user, playlist_id, change=True)
    data = await file.read()
    if len(data) > COVER_MAX_BYTES:
        raise HTTPException(413, "Картинка слишком большая")
    try:
        image = await asyncio.to_thread(_square_jpeg, data)
    except Exception as e:
        raise HTTPException(400, "Не удалось прочитать картинку — нужен JPEG, PNG или WebP") from e
    row = await s.get(PlaylistCover, str(p.id))
    if row is None:
        row = PlaylistCover(nd_playlist_id=str(p.id), image=image, key="")
        s.add(row)
    row.image, row.key = image, uuid.uuid4().hex[:12]
    await s.commit()
    return await _out(s, p)


@router.delete("/{playlist_id}/cover", response_model=PlaylistOut)
async def drop_cover(playlist_id: str, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    """Back to the picture Navidrome makes of the playlist's albums."""
    p = await _own(s, user, playlist_id, change=True)
    row = await s.get(PlaylistCover, str(p.id))
    if row is not None:
        await s.delete(row)
        await s.commit()
    return await _out(s, p)


@router.get("/{playlist_id}/cover")
async def get_cover(playlist_id: str, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    p = await _own(s, user, playlist_id)
    row = await s.get(PlaylistCover, str(p.id))
    if row is None:
        raise HTTPException(404, "У плейлиста нет своей обложки")
    return Response(row.image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})
