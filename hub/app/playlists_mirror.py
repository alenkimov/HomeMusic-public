"""The mirror of the Hub's playlists in Navidrome (ARCHITECTURE.md), for the apps that talk to Navidrome.

A playlist's tracks that have a file, in the Hub's order, are written to a Navidrome playlist of the same name.
Before writing, what Navidrome holds now is compared with what the Hub last gave it (`Playlist.mirrored`): a song
that appeared there was added in another app and is added to the Hub's playlist, one that disappeared is taken out.
Then the Hub's state is written — the Hub wins a conflict, an edit made elsewhere is merged, not lost.

«Микс дня» (`kind` "mix") goes the other way: the mix builder writes that playlist in Navidrome, and the Hub's
row only follows it.

`adopt` is for a library that had playlists before the Hub kept them: it reads the user's Navidrome playlists once
and makes the Hub's of them.
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
from app.models import (
    Import,
    ImportCollection,
    ImportItem,
    Job,
    MixSettings,
    Playlist,
    PlaylistCover,
    PlaylistDate,
    PlaylistItem,
    Track,
    TrackFile,
    User,
)

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


# --- a library that had playlists before the Hub kept them --------------------------------------------------------


def _catalog_of(item: ImportItem):
    """An import's row of a track the library has not got, as a catalog track."""
    from app.catalog.models import CatalogTrack

    raw = item.raw_json or {}
    src, _, ident = (item.ref or "").partition(":")
    if src not in ("dz", "hm", "mb") or not ident or not raw.get("title"):
        return None
    return CatalogTrack(ref=item.ref, source=src, id=ident, title=raw["title"], artist=raw.get("artist") or "",
                        artists=raw.get("artists") or [], album_title=raw.get("album"),
                        duration_s=raw.get("duration_s"), isrc=raw.get("isrc"), cover_url=raw.get("cover_url"),
                        cover_small_url=raw.get("cover_url"))


async def adopt(user_id: int) -> int:
    """Make the Hub's playlists of the user's Navidrome playlists, once: a user who has any of the Hub's is left
    alone. The songs keep their order and the dates the Hub knew; the tracks of an import that were waiting for a
    file come after them, as tracks without one. Returns how many playlists were made."""
    from app.favorites import parse_time
    from app.library import tracks as registry
    from app.playlist_dates import naive

    async with session_scope() as s:
        user = await s.get(User, user_id)
        if user is None or (await s.execute(select(Playlist.id).where(
                Playlist.user_id == user_id, Playlist.kind.in_((playlists.USER, MIX))).limit(1))).first():
            return 0
        mix = (await s.execute(select(MixSettings.nd_playlist_id).where(MixSettings.user_id == user_id))).scalar_one_or_none()
    nd = user_navidrome(user)
    try:
        own = [x for x in await nd.get_playlists() if x.get("owner") in (None, user.nd_username)]
        bodies = [await nd.get_playlist(x["id"]) for x in own]
    finally:
        await nd.aclose()
    made = 0
    async with session_scope() as s:
        for body in bodies:
            nd_id = body.get("id")
            if not nd_id:
                continue
            songs = [e["id"] for e in body.get("entry") or [] if e.get("id")]
            p = Playlist(user_id=user_id, kind=MIX if nd_id == mix else playlists.USER, name=body.get("name") or "",
                         nd_playlist_id=nd_id, mirrored=songs, nd_cover=body.get("coverArt"))
            s.add(p)
            await s.flush()
            ids = await _tracks_of_songs(s, songs)
            dates = dict((await s.execute(select(PlaylistDate.track_id, PlaylistDate.added_at)
                                          .where(PlaylistDate.nd_playlist_id == nd_id))).all())
            acquired = dict((await s.execute(select(TrackFile.track_id, func.min(TrackFile.acquired_at))
                                             .where(TrackFile.track_id.in_(list(ids.values())))
                                             .group_by(TrackFile.track_id))).all()) if ids else {}
            position = 0
            for song in songs:
                if (tid := ids.get(song)) is not None:
                    s.add(PlaylistItem(playlist_id=p.id, track_id=tid, position=position,
                                       added_at=dates.get(tid) or acquired.get(tid) or p.created_at))
                    position += 1
            if p.kind == playlists.USER:
                waiting = (await s.execute(
                    select(ImportItem).join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
                    .join(Import, Import.id == ImportItem.import_id)
                    .where(Import.user_id == user_id, ImportCollection.kind == "playlist",
                           ImportCollection.nd_playlist_id == nd_id, ImportItem.ref.is_not(None),
                           ImportItem.status.in_(("waiting", "queued")))
                    .order_by(ImportItem.position, ImportItem.id))).scalars().all()
                seen: set[int] = set(ids.values())
                for item in waiting:
                    c = _catalog_of(item)
                    if c is None:
                        continue
                    track = await registry.of_catalog(s, c, user_id=user_id)
                    if track.id in seen:
                        continue
                    seen.add(track.id)
                    at = naive(parse_time((item.raw_json or {}).get("liked_at")))
                    s.add(PlaylistItem(playlist_id=p.id, track_id=track.id, position=position, added_at=at or p.created_at))
                    position += 1
            for c in (await s.execute(
                    select(ImportCollection).join(Import, Import.id == ImportCollection.import_id)
                    .where(Import.user_id == user_id, ImportCollection.nd_playlist_id == nd_id))).scalars():
                c.playlist_id = p.id  # the import that made it goes on filling the Hub's playlist
            cover = await s.get(PlaylistCover, nd_id)  # the picture the user gave it follows the playlist
            if cover is not None:
                s.add(PlaylistCover(nd_playlist_id=str(p.id), image=cover.image, key=cover.key))
                await s.delete(cover)
            made += 1
    if made:
        log.info("playlists: %s of user %s taken over from Navidrome", made, user_id)
    return made


async def loop() -> None:
    """At the start: playlists a library had before the Hub kept them. Then, now and then: what other apps did to
    the mirrors."""
    await asyncio.sleep(45)
    while True:
        try:
            async with session_scope() as s:
                users = list((await s.execute(select(User.id).order_by(User.id))).scalars())
            for user_id in users:
                try:
                    await adopt(user_id)
                except Exception:
                    log.exception("playlists of user %s not taken over", user_id)
            async with session_scope() as s:
                ids = list((await s.execute(select(Playlist.id).where(Playlist.kind.in_((playlists.USER, MIX))))).scalars())
                for pid in ids:
                    await enqueue_mirror(s, pid, delay_s=0)
            if ids:
                notify()
        except Exception:
            log.exception("playlists mirror loop failed")
        await asyncio.sleep(LOOP_S)
