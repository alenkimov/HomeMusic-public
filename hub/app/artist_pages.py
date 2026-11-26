"""An artist's page kept put together, and kept warm.

An artist's page is put together of what the catalogs say (Spotify, Deezer, Yandex, Last.fm: their records of the
artist, releases, the most listened tracks) — some seconds of their answers. So it is kept (`catalog_cache`, key
`artistpage:<artist>`, as `app.api.catalog.ArtistAssembly`): an opening shows the kept page at once, with what is of
the listener put on it (♥, the library's copies: the database only), and a page older than `FRESH_S` is put
together again behind it — the apps are told (`artist` event) and draw it again without a spinner.

And it is kept warm, in the worker (`loop`), one artist after another, slowly — the catalogs' own limits are far
(Deezer 50 requests in 5 s, Last.fm 5 a second; Spotify's and Yandex's are not said): an artist costs some 15–20
requests, one every `BACKGROUND_GAP_S`:

- first, the ones asked for (`ask`): the artists of what plays now and of what comes next (the web player asks),
  an artist's page opened for the first time or kept too long;
- then, while nothing is asked, the library's artists that have a record in a catalog — the ones of the most tracks
  first — whose page is missing or older than `REFRESH_S`.

A catalog failing (a refusal, its limit: 429) makes the warming of the library wait longer each time
(`BACKOFF_MAX_S` at most); what is asked goes on, as an opening of the page would.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from app import activity, relay
from app.db import session_scope
from app.models import CatalogCache, utcnow

log = logging.getLogger(__name__)

KEY = "artistpage:{}"
KEEP_S = 60 * 86400  # kept this long at most (`catalog_cache` purges it after)
FRESH_S = 6 * 3600  # an opening of a page older than this puts it together again, behind it
REFRESH_S = 3 * 86400  # the warming of the library brings a page up to date this often
HOT_GAP_S = 1.0  # between two artists asked for
BACKGROUND_GAP_S = 15.0  # between two of the library's, nothing being asked
LISTENING_GAP_S = 30.0  # ... while somebody listens
BACKOFF_MAX_S = 30 * 60
CANDIDATES_EVERY_S = 30 * 60  # the library's artists read again this often
MAX_HOT = 200


@dataclass
class Kept:
    data: dict[str, Any]
    at: float  # epoch seconds
    complete: bool

    @property
    def stale(self) -> bool:
        return not self.complete or time.time() - self.at > FRESH_S


async def read(artist_id: int) -> Kept | None:
    async with session_scope() as s:
        row = await s.get(CatalogCache, KEY.format(artist_id))
    if row is None or row.fetched_at + timedelta(seconds=row.ttl_s) < utcnow():
        return None
    v = row.payload_json or {}
    if not isinstance(v.get("page"), dict):
        return None
    from datetime import UTC

    return Kept(data=v["page"], at=row.fetched_at.replace(tzinfo=UTC).timestamp(), complete=bool(v.get("complete")))


async def write(artist_id: int, page: dict[str, Any], *, complete: bool) -> None:
    row = {"key": KEY.format(artist_id), "payload_json": {"page": page, "complete": complete},
           "ttl_s": KEEP_S, "fetched_at": utcnow()}
    stmt = insert(CatalogCache).values(**row)
    stmt = stmt.on_conflict_do_update(index_elements=[CatalogCache.key], set_={k: stmt.excluded[k] for k in row if k != "key"})
    async with session_scope() as s:
        await s.execute(stmt)


async def drop(*artist_ids: int) -> None:
    """A page that is not the artist's any more (their records, releases changed by hand): put together anew."""
    if not artist_ids:
        return
    async with session_scope() as s:
        await s.execute(text("DELETE FROM catalog_cache WHERE key = ANY(:keys)"),
                        {"keys": [KEY.format(i) for i in artist_ids]})


# --- the warming ------------------------------------------------------------------------------------------------

_hot: deque[str] = deque()
_asked = asyncio.Event()  # set when something is asked: the warming's waiting cut short
_running = False


def warming() -> bool:
    """Somebody warms the pages: the worker (the api asks it), or this process's own loop."""
    return relay.role() == "api" or _running


def ask(refs: list[int | str]) -> None:
    """These artists' pages put together soon (an artist of the Hub by id, or a catalog's record)."""
    refs = [str(r) for r in refs if str(r).strip()]
    if not refs:
        return
    if relay.role() == "api":
        relay.send("warm", refs=refs[:50])
        return
    _enqueue(refs)


def _enqueue(refs: list[str]) -> None:
    for ref in refs:
        if ref in _hot:
            continue
        _hot.append(ref)
    while len(_hot) > MAX_HOT:
        _hot.popleft()  # the oldest asked give way
    _asked.set()


@relay.on("warm")
def _relayed(msg: dict[str, Any]) -> None:
    if relay.role() == "worker":
        _enqueue([str(r) for r in msg.get("refs") or []])


async def refresh(ref: str) -> set[str]:
    """Puts the page of `ref` together (the search of the artist's records included), keeps it and tells the apps.
    Returns the catalogs that failed."""
    from app.api.catalog import _SourceTimer, assemble_artist
    from app.events import bus

    timer = _SourceTimer()
    async with session_scope() as s:
        page = await assemble_artist(s, ref, tie=True, timer=timer)
    await write(page.who_id, page.model_dump(mode="json"), complete=True)
    bus.broadcast("artist", {"id": page.who_id, "refs": page.records})
    return timer.errors


async def _wanted(ref: str, fresh_s: float) -> bool:
    """The page of `ref` is to be put together: none kept, an incomplete one, or one older than `fresh_s`."""
    from app.catalog import registry
    from app.library import artists

    async with session_scope() as s:
        ident = registry.numeric_id(ref)
        who = await artists.by_id(s, ident) if ident is not None else await artists.by_ref(s, ref)
    if who is None:
        return ident is None  # a catalog's record the Hub does not know yet: its first page
    kept = await read(who.id)
    return kept is None or not kept.complete or time.time() - kept.at > fresh_s


async def _candidates() -> list[int]:
    """The library's artists with a record in a catalog, the ones of the most tracks first, whose page is missing or
    older than `REFRESH_S`."""
    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT r.artist_id
            FROM (SELECT DISTINCT artist_id FROM artist_refs) r
            LEFT JOIN track_artists ta ON ta.artist_id = r.artist_id
            LEFT JOIN catalog_cache c ON c.key = 'artistpage:' || r.artist_id
            WHERE c.key IS NULL OR c.fetched_at < :old
            GROUP BY r.artist_id, c.key, c.fetched_at
            ORDER BY (c.key IS NOT NULL), count(ta.track_id) DESC, c.fetched_at NULLS FIRST
            LIMIT 5000
        """), {"old": utcnow() - timedelta(seconds=REFRESH_S)})).all()
    return [int(r[0]) for r in rows]


async def loop() -> None:
    global _running
    _running = True
    backlog: deque[int] = deque()
    read_at = 0.0
    backoff = 0.0
    try:
        await asyncio.sleep(30)  # the Hub has just started: what it does first comes first
        while True:
            if _hot:
                ref = _hot.popleft()
                try:
                    if await _wanted(ref, FRESH_S):
                        await refresh(ref)
                        await asyncio.sleep(HOT_GAP_S)
                except Exception as e:
                    log.info("artist page %s not put together: %r", ref, e)
                continue
            if (not backlog or time.monotonic() - read_at > CANDIDATES_EVERY_S) and time.monotonic() - read_at > 60:
                try:
                    backlog = deque(await _candidates())
                except Exception as e:
                    log.warning("artist pages: the library's artists not read: %r", e)
                read_at = time.monotonic()
            if not backlog:
                await _idle(60)
                continue
            artist_id = backlog.popleft()
            if not await _wanted(str(artist_id), REFRESH_S):
                continue
            try:
                failed = await refresh(str(artist_id))
            except Exception as e:
                log.info("artist page %s not put together: %r", artist_id, e)
                failed = {"?"}
            if failed:
                backoff = min(BACKOFF_MAX_S, max(60.0, backoff * 2))
                log.info("artist pages: %s failed, the library's next one in %.0f s", ", ".join(sorted(failed)), backoff)
            else:
                backoff = 0.0
            await _idle(backoff or (LISTENING_GAP_S if activity.listening() else BACKGROUND_GAP_S))
    finally:
        _running = False


async def _idle(seconds: float) -> None:
    """Waits, or less: till something is asked."""
    if _hot:
        return
    _asked.clear()
    try:
        await asyncio.wait_for(_asked.wait(), seconds)
    except TimeoutError:
        pass
