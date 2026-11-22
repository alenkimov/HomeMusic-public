"""What Navidrome says of the library's songs — their covers, their albums and artists there — kept for a while,
so a list of tracks is drawn without asking Navidrome about each song. Forgotten when a scan has run."""

from __future__ import annotations

import asyncio
import time

PAGE = 500
MAX = 50_000
TTL_S = 600

_songs: dict[str, dict] | None = None
_at = 0.0
_lock = asyncio.Lock()


async def load(nd) -> list[dict]:
    """Every song Navidrome has."""
    out: list[dict] = []
    while len(out) < MAX:
        page = (await nd.search3("", song_count=PAGE, song_offset=len(out))).get("song") or []
        out += page
        if len(page) < PAGE:
            break
    return out


async def index() -> dict[str, dict]:
    """Navidrome song id -> the song as Navidrome gives it. The library is one for all: it is asked as the admin."""
    global _songs, _at
    if _songs is not None and time.monotonic() - _at < TTL_S:
        return _songs
    async with _lock:
        if _songs is not None and time.monotonic() - _at < TTL_S:
            return _songs
        from app.library.navidrome import admin_client

        nd = admin_client()
        try:
            songs = await load(nd)
        finally:
            await nd.aclose()
        _songs, _at = {x["id"]: x for x in songs if x.get("id")}, time.monotonic()
        return _songs


def loaded_at() -> float:
    """When the index was read from Navidrome last (0: not yet): a new value — Navidrome's songs read anew."""
    return _at if _songs is not None else 0.0


def forget() -> None:
    global _songs
    _songs = None
