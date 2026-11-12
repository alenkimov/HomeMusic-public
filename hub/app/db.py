"""SQLite (WAL) engine, async sessions and Alembic migrations on startup."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

log = logging.getLogger(__name__)

SLOW_WRITE_S = 2.0  # SQLite has one writer: a transaction that writes this long keeps every other write waiting

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _set_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=60000")  # a writer waits for another one (a library scan settling a big import) rather than fail
    cur.close()


def _write_began(conn, cursor, statement, parameters, context, executemany) -> None:
    """The first statement of a transaction that changes data: from here on the database is this transaction's."""
    if "write_at" not in conn.info and statement.lstrip()[:6].upper() in ("INSERT", "UPDATE", "DELETE"):
        conn.info["write_at"] = (time.monotonic(), " ".join(statement.split())[:120])


def _write_ended(conn) -> None:
    began = conn.info.pop("write_at", None)
    if began and time.monotonic() - began[0] >= SLOW_WRITE_S:
        log.warning("slow write: the database was held for %.1f s by a transaction that began with: %s",
                    time.monotonic() - began[0], began[1])


def init_engine(db_path: Path | None = None) -> AsyncEngine:
    global _engine, _sessionmaker
    path = db_path or get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}", connect_args={"timeout": 30})
    event.listen(_engine.sync_engine, "connect", _set_pragmas)
    event.listen(_engine.sync_engine, "after_cursor_execute", _write_began)  # after: its own wait for the lock is not counted
    event.listen(_engine.sync_engine, "commit", _write_ended)
    event.listen(_engine.sync_engine, "rollback", _write_ended)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def engine() -> AsyncEngine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session that commits on success and rolls back on error."""
    async with sessionmaker()() as s:
        try:
            yield s
            await s.commit()
        except BaseException:
            await s.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Use with `scope="function"`: the commit then happens before the response is sent
    (with the default request scope a client could see a stale state right after a write, e.g. 401 after login),
    and long responses (SSE) don't hold a connection."""
    async with session_scope() as s:
        yield s


def run_migrations(db_path: Path | None = None) -> None:
    """alembic upgrade head (sync, called once at startup before the event loop work starts)."""
    from alembic import command
    from alembic.config import Config
    from alembic.util import CommandError

    path = db_path or get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    # keep the app's JSON logging (env.py would otherwise apply alembic.ini's logging config)
    cfg.attributes["configure_logger"] = False
    try:
        command.upgrade(cfg, "head")
    except CommandError as e:
        if "Can't locate revision" not in str(e):
            raise
        raise RuntimeError(f"{path} is a database of HomeMusic 1.x, or of another build: 2.0 starts with an empty "
                           "one — move the file away and import the library again") from e


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
