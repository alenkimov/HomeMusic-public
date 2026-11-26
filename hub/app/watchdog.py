"""The event loop held up: said in the log, with where it was held.

The whole Hub — every request, the events the apps listen to, the jobs — runs on one asyncio loop. Work that does
not give it back (a long computation, a blocking call, a query answered in Python row by row) stops all of it at
once: a track does not start, the apps lose the connection for some seconds. A thread watches a beat the loop
gives four times a second; the beat late by `STALL_S`, the loop's own stack is written to the log, once a stall,
and how long the stall lasted when the loop is back. The log is the place to read it.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

log = logging.getLogger(__name__)

BEAT_S = 0.25
STALL_S = 1.0  # the loop not back for this long: a stall


class Watchdog:
    def __init__(self) -> None:
        self.beat = time.monotonic()
        self.loop_thread = threading.get_ident()
        self.stalled_at: float | None = None
        self.stop = threading.Event()
        self.task: asyncio.Task | None = None
        self.thread: threading.Thread | None = None

    async def _beat(self) -> None:
        while True:
            self.beat = time.monotonic()
            if self.stalled_at is not None:
                log.warning("event loop was held up for %.1f s", self.beat - self.stalled_at)
                self.stalled_at = None
            await asyncio.sleep(BEAT_S)

    def _watch(self) -> None:
        while not self.stop.wait(BEAT_S):
            late = time.monotonic() - self.beat
            if late < STALL_S or self.stalled_at is not None:
                continue
            self.stalled_at = self.beat
            frame = sys._current_frames().get(self.loop_thread)
            where = "".join(traceback.format_stack(frame)[-12:]) if frame else "(no stack)"
            log.warning("event loop held up for %.1f s so far, at:\n%s", late, where)

    def start(self) -> None:
        self.loop_thread = threading.get_ident()
        self.task = asyncio.create_task(self._beat(), name="watchdog")
        self.thread = threading.Thread(target=self._watch, name="loop-watchdog", daemon=True)
        self.thread.start()

    async def close(self) -> None:
        self.stop.set()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


watchdog = Watchdog()
