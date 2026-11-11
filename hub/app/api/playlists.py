import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import playlist_dates
from app.api.medialib import nd_call, songs_out
from app.api.schemas import (
    PlaylistAddIn,
    PlaylistCreateIn,
    PlaylistMembershipIn,
    PlaylistOfSongOut,
    PlaylistOut,
    PlaylistPageOut,
    PlaylistRemoveIn,
)
from app.auth import current_user, user_navidrome
from app.db import get_session
from app.models import Track, User

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def pl_out(p: dict) -> PlaylistOut:
    return PlaylistOut(id=p["id"], name=p.get("name", ""), song_count=p.get("songCount", 0),
                       duration_s=p.get("duration", 0), cover_art=p.get("coverArt"))


@router.get("", response_model=list[PlaylistOut])
async def list_playlists(user: User = Depends(current_user)):
    nd = user_navidrome(user)
    try:
        pls = await nd.get_playlists()
    finally:
        await nd.aclose()
    # getPlaylists also returns other users' public playlists: show only own ones
    return [pl_out(p) for p in pls if p.get("owner") in (None, user.nd_username)]


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
    out = [(str(p.get("changed") or ""), PlaylistOfSongOut(**pl_out(p).model_dump(), has=nd_song_id in ids))
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
    return PlaylistPageOut(**pl_out(p).model_dump(), tracks=tracks)


@router.post("/{playlist_id}/remove")
async def remove(playlist_id: str, body: PlaylistRemoveIn, user: User = Depends(current_user)):
    """Takes songs out by their positions (a song may be in a playlist twice)."""
    await nd_call(user, lambda nd: nd.update_playlist(playlist_id, remove_indexes=sorted(set(body.indexes))),
                  not_found="Плейлист не найден")
    return {"ok": True}


@router.delete("/{playlist_id}")
async def delete(playlist_id: str, user: User = Depends(current_user)):
    await nd_call(user, lambda nd: nd.delete_playlist(playlist_id), not_found="Плейлист не найден")
    return {"ok": True}
