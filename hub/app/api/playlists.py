import asyncio
import io
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import playlist_dates
from app.api.medialib import nd_call, songs_out
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
from app.auth import current_user, user_navidrome
from app.db import get_session, session_scope
from app.models import PlaylistCover, Track, User

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


COVER_PX = 600
COVER_MAX_BYTES = 20 * 1024 * 1024


async def _custom() -> dict[str, str]:
    """playlist id -> the key of the picture the user gave it."""
    async with session_scope() as s:
        return dict((await s.execute(select(PlaylistCover.nd_playlist_id, PlaylistCover.key))).all())


def pl_out(p: dict, custom: dict[str, str] | None = None) -> PlaylistOut:
    key = (custom or {}).get(p["id"])
    count = p.get("songCount", 0)
    if key:
        cover = f"/api/playlists/{quote(p['id'], safe='')}/cover?v={key}"
    elif p.get("coverArt") and count:
        cover = f"/api/nd/cover/{quote(p['coverArt'], safe='')}?v={count}"  # it changes with the songs
    else:
        cover = None
    return PlaylistOut(id=p["id"], name=p.get("name", ""), song_count=count,
                       duration_s=p.get("duration", 0), cover_art=p.get("coverArt"), cover_url=cover)


@router.get("", response_model=list[PlaylistOut])
async def list_playlists(user: User = Depends(current_user)):
    nd = user_navidrome(user)
    try:
        pls = await nd.get_playlists()
    finally:
        await nd.aclose()
    custom = await _custom()
    # getPlaylists also returns other users' public playlists: show only own ones
    return [pl_out(p, custom) for p in pls if p.get("owner") in (None, user.nd_username)]


# playlist id -> (what it looked like, its songs): asked of Navidrome again only when the playlist has changed
_songs: dict[str, tuple[str, list[str]]] = {}


async def _songs_of(nd, p: dict) -> list[str]:
    stamp = f"{p.get('changed')}:{p.get('songCount')}"
    hit = _songs.get(p["id"])
    if hit is not None and hit[0] == stamp:
        return hit[1]
    ids = [e["id"] for e in (await nd.get_playlist(p["id"])).get("entry") or [] if e.get("id")]
    _songs[p["id"]] = (stamp, ids)
    return ids


@router.get("/of-song", response_model=list[PlaylistOfSongOut])
async def of_song(nd_song_id: str, user: User = Depends(current_user)):
    """The user's playlists for a song's «in playlists» menu: which of them have the song, the ones changed last
    first."""
    async def load(nd):
        pls = [p for p in await nd.get_playlists() if p.get("owner") in (None, user.nd_username)]
        return pls, await asyncio.gather(*(_songs_of(nd, p) for p in pls))

    pls, songs = await nd_call(user, load)
    custom = await _custom()
    out = [(str(p.get("changed") or ""), PlaylistOfSongOut(**pl_out(p, custom).model_dump(), has=nd_song_id in ids))
           for p, ids in zip(pls, songs, strict=True)]
    return [x for _, x in sorted(out, key=lambda pair: pair[0], reverse=True)]


@router.post("/membership", response_model=list[PlaylistOfSongOut])
async def membership(body: PlaylistMembershipIn, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    """Put a song into some playlists and take it out of others, in one go (every place it has in a playlist it is
    taken out of)."""
    async def change(nd):
        for pid in dict.fromkeys(body.add):
            if body.nd_song_id not in await _songs_of(nd, {"id": pid}):
                await nd.update_playlist(pid, add=[body.nd_song_id])
            _songs.pop(pid, None)
        for pid in dict.fromkeys(body.remove):
            _songs.pop(pid, None)
            at = [i for i, sid in enumerate(await _songs_of(nd, {"id": pid})) if sid == body.nd_song_id]
            if at:
                await nd.update_playlist(pid, remove_indexes=at)
            _songs.pop(pid, None)

    await nd_call(user, change, not_found="Плейлист не найден")
    track_ids = list((await s.execute(select(Track.id).where(Track.nd_song_id == body.nd_song_id))).scalars())
    for pid in dict.fromkeys(body.add):
        await playlist_dates.stamp(s, pid, track_ids)
    await s.commit()
    return await of_song(body.nd_song_id, user)


async def _of_songs(user: User, song_ids: list[str]) -> list[PlaylistOfSongsOut]:
    async def load(nd):
        pls = [p for p in await nd.get_playlists() if p.get("owner") in (None, user.nd_username)]
        return pls, await asyncio.gather(*(_songs_of(nd, p) for p in pls))

    want = set(song_ids)
    pls, songs = await nd_call(user, load)
    custom = await _custom()
    out = [(str(p.get("changed") or ""), PlaylistOfSongsOut(**pl_out(p, custom).model_dump(), count=len(want.intersection(ids))))
           for p, ids in zip(pls, songs, strict=True)]
    return [x for _, x in sorted(out, key=lambda pair: pair[0], reverse=True)]


@router.post("/of-songs", response_model=list[PlaylistOfSongsOut])
async def of_songs(body: PlaylistSongsIn, user: User = Depends(current_user)):
    """The user's playlists for several picked songs: how many of them each has, the ones changed last first."""
    return await _of_songs(user, list(dict.fromkeys(body.nd_song_ids)))


@router.post("/membership-many", response_model=list[PlaylistOfSongsOut])
async def membership_many(body: PlaylistMembershipManyIn, user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session, scope="function")):
    """Several songs at once: into some playlists (the ones a playlist lacks, in the order given) and out of
    others (every place of every one of them)."""
    song_ids = list(dict.fromkeys(body.nd_song_ids))
    want = set(song_ids)
    added: dict[str, list[str]] = {}

    async def change(nd):
        for pid in dict.fromkeys(body.add):
            have = set(await _songs_of(nd, {"id": pid}))
            _songs.pop(pid, None)
            missing = [x for x in song_ids if x not in have]
            if missing:
                await nd.update_playlist(pid, add=missing)
                added[pid] = missing
        for pid in dict.fromkeys(body.remove):
            _songs.pop(pid, None)
            at = [i for i, sid in enumerate(await _songs_of(nd, {"id": pid})) if sid in want]
            _songs.pop(pid, None)
            if at:
                await nd.update_playlist(pid, remove_indexes=at)

    await nd_call(user, change, not_found="Плейлист не найден")
    for pid, ids in added.items():
        track_ids = list((await s.execute(select(Track.id).where(Track.nd_song_id.in_(ids)))).scalars())
        await playlist_dates.stamp(s, pid, track_ids)
    await s.commit()
    return await _of_songs(user, song_ids)


@router.post("", response_model=PlaylistOut)
async def create_playlist(body: PlaylistCreateIn, user: User = Depends(current_user)):
    if not body.name.strip():
        raise HTTPException(400, "Введите название")
    nd = user_navidrome(user)
    try:
        p = await nd.create_playlist(body.name.strip())
    finally:
        await nd.aclose()
    return pl_out(p)


@router.post("/{playlist_id}/add")
async def add(playlist_id: str, body: PlaylistAddIn, user: User = Depends(current_user),
              s: AsyncSession = Depends(get_session, scope="function")):
    ids = list(body.song_ids)
    if body.library_track_ids:
        rows = (await s.execute(select(Track.id, Track.nd_song_id).where(Track.id.in_(body.library_track_ids)))).all()
        by_id = dict(rows)
        ids += [by_id[i] for i in body.library_track_ids if by_id.get(i)]
    dated = list((await s.execute(select(Track.id).where(Track.nd_song_id.in_(ids)))).scalars()) if ids else []
    if not ids:
        raise HTTPException(400, "Эти треки ещё не появились в Navidrome")
    nd = user_navidrome(user)
    try:
        await nd.update_playlist(playlist_id, add=ids)
    finally:
        await nd.aclose()
    await playlist_dates.stamp(s, playlist_id, dated)
    await s.commit()
    return {"ok": True, "added": len(ids)}


@router.get("/{playlist_id}", response_model=PlaylistPageOut)
async def get_playlist(playlist_id: str, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    p = await nd_call(user, lambda nd: nd.get_playlist(playlist_id), not_found="Плейлист не найден")
    if not p.get("id"):
        raise HTTPException(404, "Плейлист не найден")
    tracks = await songs_out(s, p.get("entry") or [])
    await playlist_dates.collect(s, user.id, playlist_id, p.get("name") or "")
    await s.commit()
    dates = await playlist_dates.of(s, playlist_id)
    for t in tracks:  # a track without a date of its own keeps the day it came into the library
        if t.library_track_id in dates:
            t.added_at = dates[t.library_track_id]
    return PlaylistPageOut(**pl_out(p, await _custom()).model_dump(), tracks=tracks,
                           pending=await _pending(s, user, playlist_id))


async def _pending(s: AsyncSession, user: User, playlist_id: str) -> list[NdSongOut]:
    """The playlist's tracks that wait for a listen or a download (`apply.pending_of_playlist`) as catalog tracks:
    their names from the import's own row, the cover from what the Hub has cached of the catalog."""
    from app.favorites import parse_time
    from app.imports.apply import pending_of_playlist
    from app.models import CatalogCache

    items = await pending_of_playlist(s, user.id, playlist_id)
    if not items:
        return []
    keys = sorted({k for it in items for k in (f"listen:{it.ref}", f"mix:track:{it.ref}",
                                               f"dz:track:{it.ref[3:]}" if it.ref.startswith("dz:") else "") if k})
    cached: dict[str, dict] = {}
    for i in range(0, len(keys), 500):
        for row in (await s.execute(select(CatalogCache).where(CatalogCache.key.in_(keys[i:i + 500])))).scalars():
            cached[row.key] = (row.payload_json or {}).get("v") or {}
    out = []
    for it in items:
        ref = it.ref
        src, _, ident = ref.partition(":")
        if src not in ("dz", "hm", "mb", "file") or not ident:
            continue
        raw = it.raw_json or {}
        meta = {**cached.get(f"listen:{ref}", {}), **cached.get(f"mix:track:{ref}", {}), **cached.get(f"dz:track:{ref[3:]}", {})}
        out.append(NdSongOut(
            ref=ref, source=src, id=ident, title=raw.get("title") or meta.get("title") or ref,
            artist=raw.get("artist") or meta.get("artist") or "", artists=raw.get("artists") or [],
            duration_s=raw.get("duration_s") or meta.get("duration_s"), album_title=raw.get("album") or meta.get("album_title"),
            artist_ref=meta.get("artist_ref"), album_ref=meta.get("album_ref"), cover_url=meta.get("cover_url") or raw.get("cover_url"),
            cover_small_url=meta.get("cover_small_url") or raw.get("cover_url"), owned=False,
            downloading=it.status == "queued",
            added_at=playlist_dates.naive(parse_time(raw.get("liked_at")))))
    return out


@router.post("/{playlist_id}/remove")
async def remove(playlist_id: str, body: PlaylistRemoveIn, user: User = Depends(current_user)):
    """Takes songs out by their positions (a song may be in a playlist twice)."""
    await nd_call(user, lambda nd: nd.update_playlist(playlist_id, remove_indexes=sorted(set(body.indexes))),
                  not_found="Плейлист не найден")
    return {"ok": True}


@router.delete("/{playlist_id}")
async def delete(playlist_id: str, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    await nd_call(user, lambda nd: nd.delete_playlist(playlist_id), not_found="Плейлист не найден")
    row = await s.get(PlaylistCover, playlist_id)
    if row is not None:
        await s.delete(row)
        await s.commit()
    return {"ok": True}


async def _own(user: User, playlist_id: str) -> dict:
    """The playlist, if it is this user's to change."""
    p = await nd_call(user, lambda nd: nd.get_playlist(playlist_id), not_found="Плейлист не найден")
    if not p.get("id") or p.get("owner") not in (None, user.nd_username):
        raise HTTPException(404, "Плейлист не найден")
    return p


@router.put("/{playlist_id}", response_model=PlaylistOut)
async def rename(playlist_id: str, body: PlaylistRenameIn, user: User = Depends(current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Введите название")
    await _own(user, playlist_id)
    await nd_call(user, lambda nd: nd.update_playlist(playlist_id, name=name), not_found="Плейлист не найден")
    _songs.pop(playlist_id, None)
    return pl_out({**await _own(user, playlist_id), "name": name}, await _custom())


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
    p = await _own(user, playlist_id)
    data = await file.read()
    if len(data) > COVER_MAX_BYTES:
        raise HTTPException(413, "Картинка слишком большая")
    try:
        image = await asyncio.to_thread(_square_jpeg, data)
    except Exception as e:
        raise HTTPException(400, "Не удалось прочитать картинку — нужен JPEG, PNG или WebP") from e
    row = await s.get(PlaylistCover, playlist_id)
    if row is None:
        row = PlaylistCover(nd_playlist_id=playlist_id, image=image, key="")
        s.add(row)
    row.image, row.key = image, uuid.uuid4().hex[:12]
    await s.commit()
    return pl_out(p, {playlist_id: row.key})


@router.delete("/{playlist_id}/cover", response_model=PlaylistOut)
async def drop_cover(playlist_id: str, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    """Back to the picture Navidrome makes of the playlist's albums."""
    p = await _own(user, playlist_id)
    row = await s.get(PlaylistCover, playlist_id)
    if row is not None:
        await s.delete(row)
        await s.commit()
    return pl_out(p)


@router.get("/{playlist_id}/cover")
async def get_cover(playlist_id: str, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    row = await s.get(PlaylistCover, playlist_id)
    if row is None:
        raise HTTPException(404, "У плейлиста нет своей обложки")
    return Response(row.image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})
