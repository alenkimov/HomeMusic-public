"""The loop held up is said in the log, with where; background work gives way to a listener."""

from __future__ import annotations

import asyncio
import logging
import time


async def test_a_held_up_loop_is_logged_with_where(caplog, monkeypatch):
    from app import watchdog as wd

    monkeypatch.setattr(wd, "STALL_S", 0.3)
    dog = wd.Watchdog()
    with caplog.at_level(logging.WARNING, logger="app.watchdog"):
        dog.start()
        await asyncio.sleep(0.3)

        def hold_the_loop():
            time.sleep(0.9)

        hold_the_loop()  # what a blocking call on the loop does
        await asyncio.sleep(0.4)
        await dog.close()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "event loop held up" in text and "hold_the_loop" in text
    assert "event loop was held up for" in text


def test_background_work_takes_fewer_workers_while_somebody_listens(monkeypatch):
    from app import activity
    from app.jobs import queue

    monkeypatch.setattr(activity, "_last", float("-inf"))
    full = queue.background_slots()
    activity.note()
    assert queue.background_slots() == min(full, queue.LISTENING_BACKGROUND_SLOTS)
    monkeypatch.setattr(activity, "_last", time.monotonic() - activity.WINDOW_S - 1)
    assert queue.background_slots() == full
