"""Whether somebody is listening now: a track being played or fetched to a device. Background work (a transfer's
downloads, `app.jobs.queue`) takes fewer workers meanwhile — the network, the CPU and the disk of a home server
go to the listener first, and the transfer goes on at full speed when nobody listens."""

from __future__ import annotations

import time

WINDOW_S = 120.0  # a listener is taken to be there this long after their last request for audio
_last = float("-inf")


def note() -> None:
    global _last
    _last = time.monotonic()


def listening() -> bool:
    return time.monotonic() - _last < WINDOW_S
