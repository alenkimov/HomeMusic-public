"""The mirror of the Hub's playlists in Navidrome (ARCHITECTURE.md), for the apps that talk to Navidrome.

A playlist's tracks that have a file, in the Hub's order, are written to a Navidrome playlist of the same name.
Before writing, what Navidrome holds now is compared with what the Hub last gave it (`Playlist.mirrored`): a song
that appeared there was added in another app and is added to the Hub's playlist, one that disappeared is taken out.
Then the Hub's state is written — the Hub wins a conflict, an edit made elsewhere is merged, not lost.

«Микс дня» (`kind` "mix") goes the other way: the mix builder writes that playlist in Navidrome, and the Hub's
row only follows it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import playlists
from app.auth import user_navidrome
from app.db import session_scope
from app.events import bus
from app.jobs.queue import enqueue, handler, notify
from app.library.navidrome import SubsonicError
from app.models import Job, Playlist, PlaylistItem, Track, TrackFile, User

log = logging.getLogger(__name__)

JOB = "playlist_mirror"
MIX = "mix"
LOOP_S = 600
NOT_FOUND = 70


async def songs_of(s: AsyncSession, playlist_id: int) -> list[str]:
    """The Navidrome songs of the playlist's tracks that have one, in the playlist's order."""
    rows = (await s.execute(select(Track.nd_song_id).join(PlaylistItem, PlaylistItem.track_id == Track.id)
                            .where(PlaylistItem.playlist_id == playlist_id)
                            .order_by(PlaylistItem.position, PlaylistItem.id))).scalars()
    return [x for x in rows if x]


async def _tracks_of_songs(s: AsyncSession, songs: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in range(0, len(songs), 500):
        out.update((song, tid) for song, tid in (await s.execute(
            select(TrackFile.nd_song_id, TrackFile.track_id).where(TrackFile.nd_song_id.in_(songs[k:k + 500])))).all())
    return out


async def enqueue_mirror(s: AsyncSession, playlist_id: int, *, delay_s: float = 2) -> None:
    """Ask for the playlist to be mirrored soon (once, however many changes are made meanwhile)."""
    waiting = (await s.execute(select(Job.id).where(
        Job.kind == JOB, Job.status == "queued",
        func.json_extract(Job.payload_json, "$.playlist_id") == playlist_id).limit(1))).first()
    if waiting is None:
        await enqueue(s, JOB, {"playlist_id": playlist_id}, delay_s=delay_s, max_attempts=3)


async def enqueue_stale(s: AsyncSession) -> int:
    """Playlists whose songs are not what the mirror was given (a download brought a file, a scan found it)."""
    n = 0
    for p in (await s.execute(select(Playlist).where(Playlist.kind == playlists.USER))).scalars():
        if await songs_of(s, p.id) != list(p.mirrored or []):
            await enqueue_mirror(s, p.id, delay_s=0)
            n += 1
    return n


@handler(JOB)
async def handle_mirror(job: Job) -> None:
    await mirror(int(job.payload_json["playlist_id"]))


async def mirror(playlist_id: int) -> None:
    async with session_scope() as s:
        p = await s.get(Playlist, playlist_id)
        user = await s.get(User, p.user_id) if p is not None else None
        if p is None or user is None or p.kind not in (playlists.USER, MIX):
            return
        kind, name, nd_id, mirrored = p.kind, p.name, p.nd_playlist_id, list(p.mirrored or [])
    nd = user_navidrome(user)
    try:
        body: dict[str, Any] = {}
        made = False
        if nd_id:
            try:
                body = await nd.get_playlist(nd_id)
            except SubsonicError as e:
                if e.code != NOT_FOUND:
                    raise
                nd_id = None  # deleted in another app: the Hub's playlist stays and gets a new mirror
        if not nd_id:
            if kind == MIX:
                return
            body = await nd.create_playlist(name)
            nd_id, mirrored, made = body.get("id"), [], True
            if not nd_id:
                raise RuntimeError("Navidrome made no playlist")
        now = [e["id"] for e in body.get("entry") or [] if e.get("id")]
        async with session_scope() as s:
            p = await s.get(Playlist, playlist_id)
            if p is None:  # deleted here meanwhile
                await nd.delete_playlist(nd_id)
                return
            changed = False
            if kind == MIX:  # the builder's playlist: the Hub's row follows it
                ids = await _tracks_of_songs(s, now)
                want_tracks = [ids[x] for x in now if x in ids]
                if want_tracks != await playlists.track_ids(s, p.id):
                    await s.execute(delete(PlaylistItem).where(PlaylistItem.playlist_id == p.id))
                    await playlists.add(s, p, want_tracks, again=True)
                    changed = True
                want = now
            else:
                have = set(mirrored)
                came = [x for x in now if x not in have]
                gone = [x for x in mirrored if x not in set(now)]
                if came or gone:
                    ids = await _tracks_of_songs(s, came + gone)
                    changed = bool(await playlists.remove_tracks(s, p, [ids[x] for x in gone if x in ids]))
                    changed = bool(await playlists.add(s, p, [ids[x] for x in came if x in ids])) or changed
                want = await songs_of(s, p.id)
            p.nd_playlist_id, p.nd_cover, name = nd_id, body.get("coverArt") or p.nd_cover, p.name
            user_id = p.user_id
        if kind != MIX:
            if want != now:
                await nd.replace_playlist_songs(nd_id, want)
            if body.get("name") != name:
                await nd.update_playlist(nd_id, name=name)
            if made:  # the picture Navidrome makes of a playlist is known once it has songs
                body = await nd.get_playlist(nd_id)
        async with session_scope() as s:
            p = await s.get(Playlist, playlist_id)
            if p is not None:
                p.mirrored = want
                if made:
                    p.nd_cover = body.get("coverArt")
        if changed:
            bus.publish(user_id, "library", {"kind": "playlist", "playlist_id": str(playlist_id)})
    finally:
        await nd.aclose()


async def drop_mirror(user: User, nd_playlist_id: str | None) -> None:
    """The playlist is deleted here: its mirror goes too."""
    if not nd_playlist_id:
        return
    nd = user_navidrome(user)
    try:
        await nd.delete_playlist(nd_playlist_id)
    except Exception as e:
        log.info("mirror %s not deleted: %r", nd_playlist_id, e)
    finally:
        await nd.aclose()


async def follow_mix(s: AsyncSession, user_id: int, nd_playlist_id: str, name: str) -> None:
    """«Микс дня» is written to Navidrome by its builder (app/mixes/core.py): the Hub's row of it — made here the
    first time — is told where it is, and follows it."""
    p = (await s.execute(select(Playlist).where(Playlist.user_id == user_id, Playlist.kind == MIX)
                         .order_by(Playlist.id).limit(1))).scalar_one_or_none()
    if p is None:
        p = await playlists.create(s, user_id, name, kind=MIX)
    p.nd_playlist_id = nd_playlist_id
    await enqueue_mirror(s, p.id, delay_s=0)


async def loop() -> None:
    """Now and then: what other apps did to the mirrors."""
    await asyncio.sleep(45)
    while True:
        try:
            async with session_scope() as s:
                ids = list((await s.execute(select(Playlist.id).where(Playlist.kind.in_((playlists.USER, MIX))))).scalars())
                for pid in ids:
                    await enqueue_mirror(s, pid, delay_s=0)
            if ids:
                notify()
        except Exception:
            log.exception("playlists mirror loop failed")
        await asyncio.sleep(LOOP_S)
