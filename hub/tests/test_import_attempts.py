"""A transfer that has not begun: how its reading goes, its limit, and what failed before it began leaving."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update

from app.db import session_scope
from app.imports import core
from app.models import Import, Job, utcnow
from tests.test_imports import env as env
from tests.test_imports import run_imports


async def test_the_reading_says_where_it_is_and_can_be_cancelled(env):
    c = env["client"]
    imp = (await c.post("/api/imports", json={"method": "profile", "url": "https://music.yandex.ru/users/ivan/playlists/3"})).json()
    got = (await c.get(f"/api/imports/{imp['id']}")).json()
    assert got["status"] == "fetching" and got["fetch"]["state"] == "queued"
    async with session_scope() as s:
        await s.execute(update(Job).where(Job.kind == "import_fetch").values(
            attempts=1, last_error="ConnectionError('reset')\ntraceback", run_after=utcnow() + timedelta(seconds=30)))
    got = (await c.get(f"/api/imports/{imp['id']}")).json()
    assert got["fetch"]["state"] == "retrying" and got["fetch"]["last_error"] == "ConnectionError('reset')"
    assert (await c.delete(f"/api/imports/{imp['id']}")).status_code == 200
    async with session_scope() as s:
        await s.execute(update(Job).where(Job.kind == "import_fetch").values(run_after=utcnow()))
    await run_imports()  # its job finds nothing to read
    assert env["state"]["fetches"] == []


async def test_a_reading_past_its_limit_fails(env, monkeypatch):
    import asyncio

    async def slow(imp):
        await asyncio.sleep(5)

    monkeypatch.setattr(core, "fetch_snapshot", slow)
    monkeypatch.setattr(core, "FETCH_LIMIT_S", 0.05)
    c = env["client"]
    imp = (await c.post("/api/imports", json={"method": "profile", "url": "https://music.yandex.ru/users/ivan/playlists/3"})).json()
    await run_imports()
    got = (await c.get(f"/api/imports/{imp['id']}")).json()
    assert got["status"] == "failed" and "не удалось прочитать" in got["error"]


async def test_what_failed_before_it_began_leaves(env):
    async with session_scope() as s:
        old = Import(user_id=1, source="yandex", method="device", status="failed", error="Code has expired")
        fresh = Import(user_id=1, source="yandex", method="device", status="failed", error="Code has expired")
        began = Import(user_id=1, source="yandex", method="device", status="failed", error="x", started_at=utcnow())
        s.add_all([old, fresh, began])
        await s.flush()
        ids = (old.id, fresh.id, began.id)
        await s.execute(update(Import).where(Import.id.in_((old.id, began.id))).values(
            updated_at=utcnow() - timedelta(hours=1)))
    assert await core.drop_failed_attempts() == 1
    async with session_scope() as s:
        left = set(await s.scalars(select(Import.id).where(Import.id.in_(ids))))
    assert left == {ids[1], ids[2]}  # a fresh one is still shown; one that began keeps what it did
