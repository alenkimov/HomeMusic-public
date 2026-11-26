"""The Hub as two processes (`Settings.hub_role`): «api» answers the apps, «worker» does the background work — the
downloads of a transfer, ffmpeg, yt-dlp, the scheduled loops. Work of one never holds up the other's event loop.
What still passes between them goes through PostgreSQL's LISTEN/NOTIFY (channel `hm_relay`):

- the events of the apps (`app.events.bus`): published by the worker, delivered by the api to its subscribers;
- a job queued by the api (`app.jobs.queue.notify`): the worker's free hands look for it at once;
- somebody listening (`app.activity`): the worker's background jobs give way;
- a pick to forget (`app.listen.forget`): a download by the worker, the api's memory of the pick.

A message too long for a NOTIFY (8000 bytes) is put into `relay_messages` and its id is sent instead. With
`hub_role` «all» (one process: development, tests) nothing goes through here: everything is delivered at once.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

CHANNEL = "hm_relay"
MAX_NOTIFY = 7000  # bytes of a payload sent as it is; longer ones go through `relay_messages`
RETRY_S = 2.0

_handlers: dict[str, Callable[[dict[str, Any]], None]] = {}
_out: asyncio.Queue[dict[str, Any]] | None = None
_tasks: list[asyncio.Task] = []
listening = asyncio.Event()  # set while the LISTEN is in place


def role() -> str:
    return get_settings().hub_role


def split() -> bool:
    return role() != "all"


def on(kind: str) -> Callable[[Callable[[dict[str, Any]], None]], Callable[[dict[str, Any]], None]]:
    """Handles messages of `kind` sent by the other process."""

    def deco(fn: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
        _handlers[kind] = fn
        return fn

    return deco


def send(kind: str, **data: Any) -> None:
    """To the other process (dropped when the relay is not running: one process, or shutting down)."""
    if _out is None:
        return
    try:
        _out.put_nowait({"t": kind, "r": role(), **data})
    except asyncio.QueueFull:
        log.warning("relay: queue full, %s dropped", kind)


def _conninfo() -> str:
    from app.db import get_database_url

    return get_database_url().set(drivername="postgresql").render_as_string(hide_password=False)


async def _sender() -> None:
    import psycopg

    assert _out is not None
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(_conninfo(), autocommit=True) as conn:
                while True:
                    msg = await _out.get()
                    body = json.dumps(msg, ensure_ascii=False, default=str)
                    if len(body.encode()) > MAX_NOTIFY:
                        row = await (await conn.execute(
                            "INSERT INTO relay_messages (body) VALUES (%s) RETURNING id", (body,))).fetchone()
                        body = json.dumps({"t": "stored", "r": role(), "id": row[0]})
                    await conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, body))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("relay: sending failed (%r), again in %.0f s", e, RETRY_S)
            await asyncio.sleep(RETRY_S)


_stored: list[asyncio.Task] = []


def _handle(msg: dict[str, Any]) -> None:
    if (fn := _handlers.get(msg.get("t", ""))) is not None:
        fn(msg)


async def _read_stored(message_id: int) -> None:
    from sqlalchemy import text

    from app.db import session_scope

    try:
        async with session_scope() as s:
            body = (await s.execute(text("SELECT body FROM relay_messages WHERE id = :id"), {"id": message_id})).scalar()
        if body is not None:
            _handle(json.loads(body))
    except Exception:
        log.exception("relay: a long message was not read")


async def _listener() -> None:
    import psycopg

    while True:
        try:
            async with await psycopg.AsyncConnection.connect(_conninfo(), autocommit=True) as conn:
                await conn.execute(f"LISTEN {CHANNEL}")
                listening.set()
                log.info("relay: listening as %s", role())
                async for n in conn.notifies():
                    try:
                        msg = json.loads(n.payload)
                        if msg.get("r") == role():
                            continue  # its own
                        if msg.get("t") == "stored":
                            # read on a connection of its own: this one is held by the waiting for notifications
                            _stored.append(asyncio.create_task(_read_stored(int(msg["id"]))))
                            _stored[:] = [t for t in _stored if not t.done()]
                            continue
                        _handle(msg)
                    except Exception:
                        log.exception("relay: a message was not handled")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            listening.clear()
            log.warning("relay: listening failed (%r), again in %.0f s", e, RETRY_S)
            await asyncio.sleep(RETRY_S)


async def _sweep() -> None:
    """Long messages are read at once; an hour later they go."""
    from sqlalchemy import text

    from app.db import session_scope

    while True:
        await asyncio.sleep(600)
        try:
            async with session_scope() as s:
                await s.execute(text("DELETE FROM relay_messages WHERE created_at < now() - interval '1 hour'"))
        except Exception as e:
            log.info("relay: sweep failed: %r", e)


def start() -> None:
    global _out
    if not split():
        return
    _out = asyncio.Queue(maxsize=10_000)
    _tasks.extend([asyncio.create_task(_sender(), name="relay-send"),
                   asyncio.create_task(_listener(), name="relay-listen")])
    if role() == "worker":
        _tasks.append(asyncio.create_task(_sweep(), name="relay-sweep"))


async def stop() -> None:
    global _out
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    _tasks.clear()
    listening.clear()
    _out = None
