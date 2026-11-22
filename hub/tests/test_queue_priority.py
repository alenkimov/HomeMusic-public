"""Job priority: the user's work goes before background work (imports), which never takes the last worker."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from psycopg.errors import DeadlockDetected
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.db import dispose, init_engine, session_scope
from app.jobs import queue
from app.models import Job


@pytest.fixture
async def db(db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app import config

    monkeypatch.setenv("CONFIG_FILE", str(tmp_path / "none.yaml"))
    config.get_settings.cache_clear()
    config.get_settings().cfg.jobs.workers = 3
    init_engine(db_url)
    yield
    await dispose()
    config.get_settings.cache_clear()


async def _add(kind: str, priority: int = 0) -> int:
    async with session_scope() as s:
        return (await queue.enqueue(s, kind, {}, priority=priority)).id


async def _claimed() -> str | None:
    job = await queue._claim("test")
    return job.kind if job else None


async def test_user_work_goes_before_older_background_work(db):
    await _add("download_item", queue.PRIORITY_IMPORT)
    await _add("listen")  # a tap, queued last
    assert [await _claimed() for _ in range(2)] == ["listen", "download_item"]


async def test_a_transfers_likes_go_before_its_queued_downloads(db):
    from app.imports.core import enqueue_once

    for _ in range(2):
        await _add("download_item", queue.PRIORITY_IMPORT)
    async with session_scope() as s:  # a ♥ rewrite queued at the transfer's priority, as before
        await queue.enqueue(s, "import_likes", {"import_id": 7}, priority=queue.PRIORITY_IMPORT)
    assert await _claimed() == "download_item"  # older downloads went first
    async with session_scope() as s:  # the next downloads that finish ask for the rewrite again
        await enqueue_once(s, "import_likes", 7, priority=queue.PRIORITY_IMPORT + 1)
        await enqueue_once(s, "import_likes", 7)  # a lower ask leaves it where it is
    async with session_scope() as s:
        assert [j.priority for j in (await s.execute(select(Job).where(Job.kind == "import_likes"))).scalars()] == [
            queue.PRIORITY_IMPORT + 1]
    assert await _claimed() == "import_likes"


async def test_background_work_leaves_a_worker_for_the_user(db):
    for _ in range(3):
        await _add("download_item", queue.PRIORITY_IMPORT)
    assert queue.background_slots() == 2
    assert [await _claimed() for _ in range(3)] == ["download_item", "download_item", None]
    # the next idle worker would sleep until something it may take is due, not spin on the held-back job
    assert await queue._next_wakeup_in() == 30.0
    await _add("listen")
    assert await _claimed() == "listen"


async def test_parallel_claims_keep_a_slot_for_urgent_work(db):
    for _ in range(5):
        await _add("background", queue.PRIORITY_IMPORT)
    urgent = await _add("urgent")
    claimed = await asyncio.gather(*(queue._claim(f"worker-{i}") for i in range(5)))
    ids = [job.id for job in claimed if job is not None]
    assert len(set(ids)) == len(ids) == 3
    assert urgent in ids


async def test_matching_and_playlist_updates_run_while_download_slots_are_full(db):
    for _ in range(3):
        await _add("download_item", queue.PRIORITY_IMPORT)
    assert [await _claimed() for _ in range(2)] == ["download_item", "download_item"]
    resolve = await _add("import_resolve", queue.PRIORITY_IMPORT)
    place = await _add("import_playlists", queue.PRIORITY_IMPORT)
    assert await _claimed() is None
    assert await queue._next_wakeup_in() == 30

    matches = await asyncio.gather(*(queue._claim(f"import-{i}", imports=True) for i in range(2)))
    assert [job.id for job in matches if job is not None] == [resolve]
    await _add("listen")
    assert await _claimed() == "listen"
    await queue._finish(resolve, ok=True)
    assert (await queue._claim("import", imports=True)).id == place


async def test_background_job_runs_with_background_network_priority(db):
    from app.net import in_background

    seen: list[tuple[int, bool]] = []

    @queue.handler("test_probe")
    async def probe(job: Job) -> None:
        seen.append((job.priority, in_background()))

    try:
        await _add("test_probe", queue.PRIORITY_IMPORT)
        await _add("test_probe")
        for _ in range(2):
            job = await queue._claim("test")
            assert job is not None
            await queue.run_job(job)
    finally:
        queue._handlers.pop("test_probe", None)
    assert seen == [(0, False), (queue.PRIORITY_IMPORT, True)]
    assert not in_background()  # the worker itself is back to the foreground


async def test_background_scans_share_a_deadline_and_manual_changes_expedite_them(db):
    from app.jobs.downloads import BACKGROUND_SCAN_DELAY_S, BACKGROUND_SCAN_PRIORITY, enqueue_scan
    from app.models import utcnow
    from app.net import Priority, priority_context

    async def request():
        async with session_scope() as s:
            await enqueue_scan(s)

    started_at = utcnow()
    await asyncio.gather(*(
        asyncio.create_task(request(), context=priority_context(Priority(background=True))) for _ in range(6)
    ))
    async with session_scope() as s:
        [pending] = list(await s.scalars(select(Job).where(Job.kind == "nd_scan")))
        assert pending.priority == BACKGROUND_SCAN_PRIORITY
        assert started_at + timedelta(seconds=BACKGROUND_SCAN_DELAY_S) <= pending.run_after
        assert pending.run_after <= utcnow() + timedelta(seconds=BACKGROUND_SCAN_DELAY_S)
    await asyncio.create_task(request(), context=priority_context(Priority(background=True)))
    async with session_scope() as s:
        assert (await s.get(Job, pending.id)).run_after == pending.run_after

    await request()
    async with session_scope() as s:
        [expedited] = list(await s.scalars(select(Job).where(Job.kind == "nd_scan")))
        assert expedited.id == pending.id
        assert expedited.priority == 0
        assert expedited.run_after < pending.run_after
        expedited.run_after = utcnow() - timedelta(seconds=1)
    assert await _claimed() == "nd_scan"

    await asyncio.create_task(request(), context=priority_context(Priority(background=True)))
    async with session_scope() as s:
        await enqueue_scan(s, retry_pass=1)
        [followup] = list(await s.scalars(select(Job).where(Job.kind == "nd_scan", Job.status == "queued")))
        assert followup.payload_json["pass"] == 0
        assert followup.priority == 0
        followup.run_after = utcnow() - timedelta(seconds=1)
    assert await _claimed() is None
    assert await queue._next_wakeup_in() == 30.0
    await _add("listen")
    assert await _claimed() == "listen"
    await queue._finish(pending.id, ok=True)
    assert await _claimed() == "nd_scan"


async def test_worker_keeps_going_when_it_cannot_save_a_result(db, monkeypatch):
    handled: list[int] = []
    second_started = asyncio.Event()
    first_saved = asyncio.Event()

    async def handle(job):
        handled.append(job.id)
        if job.id == second:
            second_started.set()

    monkeypatch.setitem(queue._handlers, "recover_test", handle)
    monkeypatch.setattr(queue, "RECOVERY_INTERVAL_S", 0.05)
    first = await _add("recover_test")
    second = await _add("recover_test")
    real_finish = queue._finish

    async def locked_until_worker_moves_on(job_id, **kwargs):
        if job_id == first and not second_started.is_set():
            raise OperationalError("UPDATE jobs", {}, DeadlockDetected())
        await real_finish(job_id, **kwargs)
        if job_id == first:
            first_saved.set()

    monkeypatch.setattr(queue, "_finish", locked_until_worker_moves_on)
    pool = queue.WorkerPool(1)
    pool.start()
    try:
        await asyncio.wait_for(first_saved.wait(), timeout=5)
    finally:
        await pool.stop()
    assert handled == [first, second]
    async with session_scope() as s:
        job = await s.get(Job, first)
        assert (job.status, job.attempts, job.locked_by) == ("done", 1, None)


async def test_only_transfers_with_a_broken_job_chain_are_resumed(db):
    from app.imports.core import resume_orphaned
    from app.models import Import, User

    async with session_scope() as s:
        user = User(nd_username="test", nd_password_enc="unused")
        s.add(user)
        await s.flush()
        orphan, active, stopped = (
            Import(user_id=user.id, source="spotify", method="profile", status=status)
            for status in ("running", "running", "cancelled")
        )
        s.add_all([orphan, active, stopped])
        await s.flush()
        await queue.enqueue(s, "import_playlists", {"import_id": orphan.id})  # projection is not the transfer chain
        await queue.enqueue(s, "import_finish", {"import_id": active.id})

    assert sorted(await asyncio.gather(resume_orphaned(), resume_orphaned())) == [0, 1]
    async with session_scope() as s:
        [resumed] = list(await s.scalars(select(Job).where(Job.kind == "import_resolve")))
        assert resumed.payload_json == {"import_id": orphan.id}
        assert resumed.priority == queue.PRIORITY_IMPORT


async def test_job_from_a_stopped_worker_is_reclaimed_while_other_workers_keep_their_claims(db, monkeypatch):
    from collections import Counter

    monkeypatch.setattr(queue, "RECOVERY_INTERVAL_S", 0.05)
    interrupted = await _add("recover_test")
    still_running = await _add("recover_test")
    attempts: Counter[int] = Counter()
    reattempted = asyncio.Event()
    active = asyncio.Event()

    async def execute(job):
        attempts[job.id] += 1
        if job.id == still_running:
            active.set()
            await asyncio.Future()
        await active.wait()
        if attempts[job.id] == 1:
            raise RuntimeError("worker stopped unexpectedly")
        reattempted.set()
        return queue._Result(ok=True)

    monkeypatch.setattr(queue, "_execute", execute)
    pool = queue.WorkerPool(2)
    pool.start()
    try:
        await asyncio.wait_for(reattempted.wait(), timeout=5)
        assert attempts == {interrupted: 2, still_running: 1}
        async with session_scope() as s:
            job = await s.get(Job, still_running)
            assert job.status == "running"
    finally:
        await pool.stop()
