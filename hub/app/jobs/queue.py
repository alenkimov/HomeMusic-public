"""PostgreSQL-backed job queue with an in-process asyncio worker pool.

- Jobs survive restarts: on startup every `running` job is put back to `queued`.
- Claiming is atomic (single UPDATE ... RETURNING), so several workers never take the same job.
- Failures retry with exponential backoff up to `max_attempts`; `PermanentError` fails at once.
- Higher `priority` goes first. Background work (imports: `PRIORITY_IMPORT`) is below 0: it never takes the last
  worker (`background_slots`), so a tap or a manual download starts at once, and it runs with the background network
  priority (`app.net.Priority`): its catalog and source requests yield to the user's.
- Import matching and local playlist updates have one worker of their own.
"""

from __future__ import annotations

import asyncio
import logging
import os
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_scope
from app.models import Job, utcnow
from app.net import Priority, _priority

log = logging.getLogger(__name__)

Handler = Callable[[Job], Awaitable[None]]
_handlers: dict[str, Handler] = {}
_wakeup = asyncio.Event()

PRIORITY_IMPORT = -10
RECOVERY_INTERVAL_S = 5


class PermanentError(Exception):
    """Do not retry this job."""


class RetryLater(Exception):
    """Retry after `delay_s` without counting it as a failure-worthy error message."""

    def __init__(self, delay_s: float, reason: str = ""):
        super().__init__(reason or f"retry in {delay_s}s")
        self.delay_s = delay_s


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _handlers[kind] = fn
        return fn

    return deco


async def enqueue(
    s: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    delay_s: float = 0,
    max_attempts: int | None = None,
    priority: int = 0,
) -> Job:
    """Add a job inside the caller's transaction. Workers are woken after the commit by `notify()`."""
    job = Job(
        kind=kind,
        payload_json=payload or {},
        priority=priority,
        run_after=utcnow() + timedelta(seconds=delay_s),
        max_attempts=max_attempts or get_settings().cfg.jobs.max_attempts,
    )
    s.add(job)
    await s.flush()
    return job


def notify() -> None:
    _wakeup.set()


async def recover_stale() -> int:
    """Called at startup: jobs left `running` by a crashed/stopped process go back to the queue."""
    async with session_scope() as s:
        res = await s.execute(
            update(Job).where(Job.status == "running").values(status="queued", locked_by=None, locked_at=None)
        )
        return res.rowcount or 0


def background_slots() -> int:
    """Workers background jobs may hold at once: all but one."""
    return max(1, get_settings().cfg.jobs.workers - 1)


_IMPORT_KINDS = "('import_resolve', 'import_playlists')"
_IMPORT_NOT_PAUSED = (
    "NOT EXISTS (SELECT 1 FROM imports WHERE imports.id = (jobs.payload_json ->> 'import_id')::integer "
    "AND imports.status='paused')"
)
_DOWNLOAD_NOT_PAUSED = (
    "NOT EXISTS (SELECT 1 FROM download_items JOIN imports "
    "ON imports.id = (download_items.query_json #>> '{import,id}')::integer "
    "WHERE download_items.id = (jobs.payload_json ->> 'item_id')::integer AND imports.status='paused')"
)

# Both checks use the same running jobs as the scheduler, including after a restart.
_GENERAL_CLAIMABLE = (
    f"status='queued' AND kind NOT IN {_IMPORT_KINDS} "
    "AND (kind <> 'nd_scan' OR NOT EXISTS (SELECT 1 FROM jobs WHERE kind='nd_scan' AND status='running')) "
    f"AND (kind NOT IN ('import_fetch', 'import_finish') OR {_IMPORT_NOT_PAUSED}) "
    f"AND (kind <> 'download_item' OR priority >= 0 OR {_DOWNLOAD_NOT_PAUSED}) "
    "AND (priority >= 0 OR "
    f"(SELECT COUNT(*) FROM jobs WHERE status='running' AND priority < 0 AND kind NOT IN {_IMPORT_KINDS}) < :bg)"
)
_IMPORT_CLAIMABLE = (
    f"status='queued' AND kind IN {_IMPORT_KINDS} "
    f"AND (kind <> 'import_resolve' OR {_IMPORT_NOT_PAUSED}) AND NOT EXISTS "
    f"(SELECT 1 FROM jobs WHERE status='running' AND kind IN {_IMPORT_KINDS})"
)


async def _claim(worker_id: str, *, imports: bool = False) -> Job | None:
    claimable = _IMPORT_CLAIMABLE if imports else _GENERAL_CLAIMABLE
    async with session_scope() as s:
        # Take the lock in a separate statement so the slot count sees the previous claim.
        await s.execute(text("SELECT pg_advisory_xact_lock(7013422)"))
        now = utcnow()
        row = (
            await s.execute(
                text(
                    "UPDATE jobs SET status='running', locked_by=:w, locked_at=:now, attempts=attempts+1, updated_at=:now "
                    f"WHERE id = (SELECT id FROM jobs WHERE {claimable} AND run_after <= :now "
                    "ORDER BY priority DESC, run_after, id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id"
                ),
                {"w": worker_id, "now": now, "bg": background_slots()},
            )
        ).first()
        if row is None:
            return None
        return await s.get(Job, row[0])


async def _finish(job_id: int, *, ok: bool, error: str | None = None, retry_in: float | None = None) -> None:
    async with session_scope() as s:
        job = await s.get(Job, job_id)
        if job is None:
            return
        job.locked_by = None
        job.locked_at = None
        if ok:
            job.status = "done"
            job.last_error = None
        elif retry_in is not None:
            job.status = "queued"
            job.run_after = utcnow() + timedelta(seconds=retry_in)
            job.last_error = error
        else:
            job.status = "failed"
            job.last_error = error


async def _next_wakeup_in(*, imports: bool = False) -> float:
    claimable = _IMPORT_CLAIMABLE if imports else _GENERAL_CLAIMABLE
    async with session_scope() as s:
        row = (
            await s.execute(text(f"SELECT MIN(run_after) FROM jobs WHERE {claimable}"), {"bg": background_slots()})
        ).first()
    if not row or row[0] is None:
        return 30.0
    nxt = row[0]
    return max(0.05, min(30.0, (nxt - utcnow()).total_seconds()))


@dataclass(frozen=True)
class _Result:
    ok: bool = False
    error: str | None = None
    retry_in: float | None = None
    failure: Exception | None = None


def _retryable_write(exc: OperationalError) -> bool:
    return getattr(exc.orig, "sqlstate", None) in {"40001", "40P01", "55P03"}


async def _execute(job: Job) -> _Result:
    fn = _handlers.get(job.kind)
    if fn is None:
        return _Result(error=f"no handler for {job.kind}")
    token = _priority.set(Priority(background=True)) if (job.priority or 0) < 0 else None
    try:
        await fn(job)
    except RetryLater as e:
        return _Result(error=str(e), retry_in=e.delay_s)
    except PermanentError as e:
        log.warning("job %s (%s) failed permanently: %s", job.id, job.kind, e, extra={"job_id": job.id})
        return _Result(error=str(e))
    except asyncio.CancelledError:
        await asyncio.shield(_finish(job.id, ok=False, error="cancelled (shutdown)", retry_in=0))
        raise
    except Exception as e:
        tb = traceback.format_exc(limit=8)
        if job.attempts < job.max_attempts:
            delay = min(600.0, 5 * 2 ** (job.attempts - 1))
            log.warning("job %s (%s) attempt %s failed: %r, retry in %ss", job.id, job.kind, job.attempts, e, delay,
                        extra={"job_id": job.id})
            return _Result(error=f"{e!r}\n{tb}", retry_in=delay)
        log.error("job %s (%s) failed after %s attempts: %r", job.id, job.kind, job.attempts, e,
                  extra={"job_id": job.id})
        return _Result(error=f"{e!r}\n{tb}", failure=e)
    else:
        return _Result(ok=True)
    finally:
        if token is not None:
            _priority.reset(token)


async def _save_result(job: Job, result: _Result) -> None:
    if result.retry_in is not None and (on_retry := _retry_hooks.get(job.kind)):
        # while the job is still ours: once queued, another worker may take it and its hook would undo that run
        try:
            await on_retry(job)
        except Exception:
            log.exception("retry hook for job %s crashed", job.id)
    for attempt in range(3):
        try:
            await _finish(job.id, ok=result.ok, error=result.error, retry_in=result.retry_in)
            break
        except OperationalError as exc:
            if attempt == 2 or not _retryable_write(exc):
                raise
            await asyncio.sleep(0.2 * 2**attempt)
    _wakeup.set()  # a background slot may be free now
    if result.failure is not None and (on_fail := _failure_hooks.get(job.kind)):
        try:
            await on_fail(job, result.failure)
        except Exception:
            log.exception("failure hook for job %s crashed", job.id)


async def run_job(job: Job) -> None:
    await _save_result(job, await _execute(job))


_failure_hooks: dict[str, Callable[[Job, Exception], Awaitable[None]]] = {}
_retry_hooks: dict[str, Callable[[Job], Awaitable[None]]] = {}


def on_final_failure(kind: str):
    """Register a hook called when a job of this kind exhausted its attempts (e.g. mark the item failed)."""

    def deco(fn):
        _failure_hooks[kind] = fn
        return fn

    return deco


def on_retry(kind: str):
    """Register a hook called when a job of this kind goes back to the queue to be tried again later."""

    def deco(fn):
        _retry_hooks[kind] = fn
        return fn

    return deco


class WorkerPool:
    def __init__(self, n: int):
        self.n = n
        self._worker_count = n + 1
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        self._pending_results: dict[int, tuple[Job, _Result]] = {}
        self._claimed: dict[int, Job] = {}

    async def _worker(self, idx: int) -> None:
        wid = f"{os.getpid()}-{idx}"
        imports = idx == self.n
        while not self._stopping:
            try:
                job = await _claim(wid, imports=imports)
            except Exception:
                log.exception("claim failed")
                await asyncio.sleep(2)
                continue
            if job is None:
                _wakeup.clear()
                try:
                    await asyncio.wait_for(_wakeup.wait(), timeout=await _next_wakeup_in(imports=imports))
                except TimeoutError:
                    pass
                except Exception:
                    log.exception("looking for the next job failed")
                    await asyncio.sleep(2)
                continue
            # let other idle workers look for more work too
            _wakeup.set()
            self._claimed[idx] = job
            result = await _execute(job)
            try:
                await _save_result(job, result)
            except Exception:
                # The handler returned. Retrying its result avoids re-running side effects or reclaiming a live job.
                self._pending_results[job.id] = (job, result)
                log.exception("job %s (%s): could not store its result yet", job.id, job.kind)
            del self._claimed[idx]

    async def _recover(self) -> None:
        while not self._stopping:
            await asyncio.sleep(RECOVERY_INTERVAL_S)
            for idx, task in enumerate(self._tasks[:self._worker_count]):
                if not task.done():
                    continue
                if not task.cancelled():
                    log.error("worker %s stopped", idx, exc_info=task.exception())
                if job := self._claimed.get(idx):
                    try:
                        async with session_scope() as s:
                            await s.execute(update(Job).where(
                                Job.id == job.id, Job.status == "running", Job.locked_by == job.locked_by,
                                Job.locked_at == job.locked_at,
                            ).values(status="queued", locked_by=None, locked_at=None))
                    except Exception:
                        log.exception("could not re-queue job %s from a stopped worker", job.id)
                        continue
                    del self._claimed[idx]
                self._tasks[idx] = asyncio.create_task(self._worker(idx), name=f"job-worker-{idx}")
            for job, result in list(self._pending_results.values()):
                try:
                    await _save_result(job, result)
                except Exception:
                    log.exception("job %s (%s): result still pending", job.id, job.kind)
                else:
                    del self._pending_results[job.id]

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._worker(i), name=f"job-worker-{i}") for i in range(self._worker_count)]
        self._tasks.append(asyncio.create_task(self._recover(), name="job-recovery"))

    async def stop(self) -> None:
        self._stopping = True
        _wakeup.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
