"""Is a song gone from Navidrome because the listener took it out — or because its file is not there just now?

Navidrome's lists (a playlist, the stars) are read back by the Hub, and a song missing from them was read as the
listener's word: taken out of the playlist, unliked — in another app. But Navidrome stops listing a song whose file
it does not find (a music drive that went away for a while, a folder being moved, a rescan half done): the Hub
then took out of its playlists, and unliked for good, everything on that drive — and wrote the empty lists back.

So a song gone from such a list is the listener's word only while its file is there (`away` names the others), and
never so many of them at once as a person does not do by hand (`mass`): then nothing is taken out this time, and
the log says why."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

MASS_MIN = 10  # songs gone at once…
MASS_SHARE = 0.25  # …and this share of the list: not a person's doing


def mass(gone: int, total: int) -> bool:
    """So many gone at once that it is not a person's doing (a drive away, a rescan)."""
    return gone > MASS_MIN and gone > total * MASS_SHARE


async def away(s: AsyncSession, song_ids: Iterable[str]) -> set[str]:
    """The Navidrome songs whose file the Hub has, but not on the disk now (a drive away shows so: none of its files
    is there). A song the Hub has no file row of (put into the library by hand) is not said to be away: nothing is
    known of it — the many at once of a drive away are `mass`."""
    from app.library.importer import library_abs
    from app.models import TrackFile

    ids = [x for x in dict.fromkeys(song_ids) if x]
    if not ids:
        return set()
    paths: dict[str, str] = {}
    for k in range(0, len(ids), 500):
        paths.update((await s.execute(select(TrackFile.nd_song_id, TrackFile.path)
                                      .where(TrackFile.nd_song_id.in_(ids[k:k + 500])))).all())

    def missing() -> set[str]:
        out = set()
        for song, rel in paths.items():
            try:
                if not library_abs(rel).is_file():
                    out.add(song)
            except OSError:
                out.add(song)
        return out

    return await asyncio.to_thread(missing)
