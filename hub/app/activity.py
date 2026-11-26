"""Whether somebody is listening now: a track being played or fetched to a device. Background work (a transfer's
downloads, `app.jobs.queue`) takes fewer workers meanwhile — the network, the CPU and the disk of a home server
go to the listener first, and the transfer goes on at full speed when nobody listens."""

from __future__ import annotations

import time

from app import relay

WINDOW_S = 120.0  # a listener is taken to be there this long after their last request for audio
_last = float("-inf")


RELAY_EVERY_S = 20.0  # the api tells the worker no more often than this
_told = float("-inf")


def note() -> None:
    global _last, _told
    _last = time.monotonic()
    if relay.role() == "api" and _last - _told > RELAY_EVERY_S:
        _told = _last
        relay.send("activity")  # the background jobs are the worker's


@relay.on("activity")
def _relayed(_msg: dict) -> None:
    global _last
    _last = time.monotonic()


def listening() -> bool:
    return time.monotonic() - _last < WINDOW_S
