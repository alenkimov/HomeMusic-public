"""«Продолжить»: the lists the user last played from, each once, the latest first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.test_plays import env as env  # the fixture
from tests.test_plays import ev, post


async def test_the_lists_last_played_from(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Playlist, User

    c = env["client"]
    async with session_scope() as s:
        uid = (await s.execute(select(User.id))).scalar_one()
        mine = Playlist(user_id=uid, name="JAPAN", kind="user")
        s.add(mine)
        await s.flush()
        pid = mine.id
    assert (await c.get("/api/plays/places")).json() == []

    now = datetime.now(UTC)

    def start(minutes_ago: int, **kw):
        return ev("start", at=now - timedelta(minutes=minutes_ago), ref=f"dz:{100 + minutes_ago}", **kw)

    await post(
        c,
        start(50, context="playlist", page=f"/playlist/{pid}"),
        start(40, context="liked"),  # before the player said the page: the kind of list is enough for «Любимое»
        start(30, context="search", page="/search"),  # not a list to go back to
        start(20, context="playlist", page=f"/playlist/{pid}"),  # the same playlist again: named once, by this time
        start(15, context="playlist", page="/playlist/999999"),  # deleted since
        start(10, context="auto", page="/auto/top-month"),
        start(5, context="wave:abc"),
    )
    got = (await c.get("/api/plays/places")).json()
    assert [(p["to"], p["kind"], p["title"]) for p in got] == [
        ("/auto/top-month", "for-you", "Топ месяца"),
        (f"/playlist/{pid}", "playlist", "JAPAN"),
        ("/liked", "liked", "Любимое"),
    ]
