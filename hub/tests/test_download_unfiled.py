"""A download left mid-way goes back to the queue: a failed filing is downloaded again, an interrupted step waits for
its retry as queued, not as a step under way."""

import pytest
from sqlalchemy import select

from app import db
from app.jobs.downloads import filed, recover_interrupted
from app.jobs.queue import _Result, _save_result
from app.matching.normalize import fingerprint
from app.models import DownloadBatch, DownloadItem, Job, User
from app.security import encrypt
from tests.support import filed_track


@pytest.fixture
async def env(hub_env):
    db.init_engine()
    try:
        async with db.session_scope() as s:
            user = User(nd_username="qa", nd_password_enc=encrypt("qa-password"))
            s.add(user)
            await s.flush()
            batch = DownloadBatch(user_id=user.id, origin="hub", title="t")
            s.add(batch)
            have = filed_track(path="a.mp3", artist="A", title="a", fingerprint=fingerprint("A", "a"))
            s.add(have)
            await s.flush()
            yield_ = (user.id, batch.id, have.id)
        yield yield_
    finally:
        await db.dispose()


async def item(user_id, batch_id, **fields):
    async with db.session_scope() as s:
        it = DownloadItem(batch_id=batch_id, user_id=user_id, query_json={"query": {"artist": "A", "title": "a"}}, **fields)
        s.add(it)
        await s.flush()
        return it.id


async def job(item_id, status="queued"):
    async with db.session_scope() as s:
        j = Job(kind="download_item", payload_json={"item_id": item_id}, status=status, attempts=1)
        s.add(j)
        await s.flush()
        return j.id


async def status_of(item_id):
    async with db.session_scope() as s:
        return (await s.get(DownloadItem, item_id)).status


async def test_an_item_left_importing_with_no_file_goes_back_to_the_queue(env):
    user_id, batch_id, have = env
    lost = await item(user_id, batch_id, status="importing")
    waits = await item(user_id, batch_id, status="importing", track_id=have)  # its file is there: Navidrome is waited for
    async with db.session_scope() as s:
        assert not await filed(s, await s.get(DownloadItem, lost))
        assert await filed(s, await s.get(DownloadItem, waits))
    assert await recover_interrupted() == 1
    assert await recover_interrupted() == 0  # its job is waiting already
    async with db.session_scope() as s:
        assert (await s.get(DownloadItem, lost)).status == "queued"
        assert (await s.get(DownloadItem, waits)).status == "importing"
        jobs = list((await s.execute(select(Job).where(Job.kind == "download_item"))).scalars())
        assert [j.payload_json for j in jobs] == [{"item_id": lost}]


async def test_a_step_cut_short_by_a_restart_waits_for_its_retry_as_queued(env):
    user_id, batch_id, _ = env
    cut = await item(user_id, batch_id, status="tagging", progress=1)
    await job(cut)  # the shutdown put its job back in the queue
    orphan = await item(user_id, batch_id, status="searching")
    assert await recover_interrupted() == 2
    assert (await status_of(cut), await status_of(orphan)) == ("queued", "queued")
    async with db.session_scope() as s:
        jobs = sorted(j.payload_json["item_id"] for j in (await s.execute(select(Job))).scalars())
    assert jobs == sorted([cut, orphan])  # one job each: the one waiting already is not doubled


async def test_a_failed_attempt_leaves_its_item_queued_until_the_retry(env):
    user_id, batch_id, have = env
    failed_step = await item(user_id, batch_id, status="downloading", progress=0.4)
    saved = await item(user_id, batch_id, status="importing", track_id=have)
    for it in (failed_step, saved):
        j = await job(it, status="running")
        async with db.session_scope() as s:
            running = await s.get(Job, j)
        await _save_result(running, _Result(error="TimeoutError()", retry_in=5))
    async with db.session_scope() as s:
        left = await s.get(DownloadItem, failed_step)
        assert (left.status, left.progress) == ("queued", 0)
        assert (await s.get(DownloadItem, saved)).status == "importing"  # filed: only Navidrome is waited for
        assert {j.status for j in (await s.execute(select(Job))).scalars()} == {"queued"}
