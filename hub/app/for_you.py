"""«Для вас» as playlists of Navidrome: the lists the Hub makes by itself are kept there the way «Микс дня» is, so
every app — the Hub's own, a Subsonic one — finds them where playlists are, and plays them offline.

- «Открытия недели» (`WEEKLY`): written by its builder as a mix of a week (`app/mixes/core.py`: its tracks are
  downloaded quietly, and the ones nobody took go when the next issue comes);
- the lists of a rule (`app.stats.AUTO`: the tops, «Давно не слушал»): songs the library has, rewritten now and then
  (`loop`) — a top of a month does not change by the minute.

One playlist per user and list (`ForYouList`): its songs are replaced, its id stays. The Hub's own pages do not read
these playlists — they ask the builders — and they are no playlists of the user's: nothing counts a song as kept
because it stands in one (`playlist_ids`)."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import stats
from app.db import session_scope
from app.models import ForYouList, User

log = logging.getLogger(__name__)

WEEKLY = "weekly"
NAMES = {WEEKLY: "Открытия недели", **{kind: title for kind, (title, _) in stats.AUTO.items()}}
COMMENTS = {WEEKLY: "Новые имена, каждый понедельник. Составляет HomeMusic",
            **{kind: "Обновляется само. Составляет HomeMusic" for kind in stats.AUTO}}
REFRESH_S = 6 * 3600
START_S = 90


async def row_of(s: AsyncSession, user_id: int, kind: str) -> ForYouList:
    row = await s.get(ForYouList, (user_id, kind))
    if row is None:
        row = ForYouList(user_id=user_id, kind=kind, songs_json=[])
        s.add(row)
        await s.flush()
    return row


async def playlist_ids(s: AsyncSession) -> set[str]:
    """Navidrome's playlists that are lists of «Для вас», of every user."""
    return set((await s.execute(select(ForYouList.nd_playlist_id).where(ForYouList.nd_playlist_id.is_not(None)))).scalars())


async def put(user: User, playlist_id: str | None, kind: str, songs: list[str]) -> str | None:
    """The user's playlist of this list gets these songs (made the first time there are any; its id stays).
    None: there is none yet, or Navidrome did not answer."""
    from app.auth import user_navidrome

    nd = user_navidrome(user)
    try:
        if playlist_id and playlist_id not in {p.get("id") for p in await nd.get_playlists()}:
            playlist_id = None  # deleted in an app: a new one
        if not playlist_id:
            if not songs:
                return None  # an empty list is no playlist
            playlist_id = (await nd.create_playlist(NAMES[kind])).get("id")
            if not playlist_id:
                return None
            await nd.update_playlist(playlist_id, comment=COMMENTS[kind])
        await nd.set_playlist_songs(playlist_id, songs)
        return playlist_id
    except Exception as e:
        log.warning("for you: «%s» of user %s not written: %r", NAMES[kind], user.id, e)
        return None
    finally:
        await nd.aclose()


async def refresh(user_id: int) -> int:
    """The lists of a rule of this user, as they are now. Returns how many playlists were rewritten."""
    written = 0
    for kind in stats.AUTO:
        async with session_scope() as s:
            user = await s.get(User, user_id)
            if user is None:
                return written
            songs = [x["nd_song_id"] for x in await stats.auto(s, user_id, kind)]
            row = await row_of(s, user_id, kind)
            playlist_id, had = row.nd_playlist_id, list(row.songs_json or [])
        if songs == had and (playlist_id or not songs):
            continue
        playlist_id = await put(user, playlist_id, kind, songs)
        if playlist_id is None and songs:
            continue  # Navidrome is away: the next round
        async with session_scope() as s:
            row = await row_of(s, user_id, kind)
            row.nd_playlist_id, row.songs_json = playlist_id, songs
        written += 1
    return written


async def loop() -> None:
    """Now and then: every user's lists of a rule."""
    await asyncio.sleep(START_S)
    while True:
        try:
            async with session_scope() as s:
                users = list((await s.execute(select(User.id))).scalars())
            from app.mixes.discover import ensure_playlist

            for user_id in users:
                await refresh(user_id)
                try:  # an issue of «Открытия недели» built before issues were kept as playlists
                    await ensure_playlist(user_id)
                except Exception:
                    log.exception("for you: the issue of user %s was not made a playlist", user_id)
        except Exception:
            log.exception("for you: the refresh failed")
        await asyncio.sleep(REFRESH_S)
