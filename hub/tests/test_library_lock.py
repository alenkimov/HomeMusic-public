"""One library edit at a time — held by the transaction that makes it, to its end."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app import db
from app.library import editor
from app.models import Album


@pytest.fixture
async def albums(hub_env):
    db.init_engine()
    try:
        async with db.session_scope() as s:
            rows = [Album(uid=str(uuid.uuid4()), title=f"Альбом {n}", album_artist="Кино") for n in (1, 2)]
            s.add_all(rows)
            await s.flush()
            ids = [a.id for a in rows]
        yield ids
    finally:
        await db.dispose()


async def test_an_edit_that_goes_on_to_the_next_album_is_not_waited_for_forever(albums):
    """An edit (an artist's music written in their name) changes one album and goes on to the next, in one
    transaction; another edit of the first album comes in between. It waits for the first edit's transaction — not
    for a lock the first one let go of while its album row stayed locked, which the first one then waited for."""
    first, second = albums
    took_first = asyncio.Event()

    async def rewrite() -> None:
        async with db.session_scope() as s:
            await editor.update_album(s, await s.get(Album, first), album_artist="КИНО")
            took_first.set()
            await asyncio.sleep(0.5)  # the other edit comes now, and waits
            await editor.update_album(s, await s.get(Album, second), album_artist="КИНО")

    async def rename() -> None:
        await took_first.wait()
        async with db.session_scope() as s:
            await editor.update_album(s, await s.get(Album, first), title="Группа крови")

    await asyncio.wait_for(asyncio.gather(rewrite(), rename()), timeout=10)
    async with db.session_scope() as s:
        a, b = await s.get(Album, first), await s.get(Album, second)
        assert (a.title, a.album_artist, b.album_artist) == ("Группа крови", "КИНО", "КИНО")
