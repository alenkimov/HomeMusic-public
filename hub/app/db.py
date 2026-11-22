"""PostgreSQL engine, async sessions and Alembic migrations on startup."""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import greenlet
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, event, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, QueuePool

from app.config import get_settings

log = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

_APP = Path(__file__).resolve().parent
POOL_REPORT_FREE = 2  # at this few free connections the holders are logged
POOL_REPORT_EVERY_S = 60
_holders: dict[int, tuple[float, str]] = {}
_reported_at = 0.0


def get_database_url(url: str | URL | None = None) -> URL:
    if url is None:
        settings = get_settings()
        url = settings.database_url
        if not url:
            if not settings.database_password:
                raise RuntimeError("Set DATABASE_URL or DATABASE_PASSWORD for the Hub's PostgreSQL database")
            return URL.create("postgresql+psycopg", username="homemusic", password=settings.database_password,
                              host="postgres", database="homemusic")
    database = make_url(url)
    if database.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("The Hub requires a PostgreSQL database")
    return database.set(drivername="postgresql+psycopg")


def init_engine(url: str | URL | None = None) -> AsyncEngine:
    global _engine, _sessionmaker
    _engine = create_async_engine(get_database_url(url), pool_pre_ping=True)
    _watch_pool(_engine)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def _holder() -> str:
    """The task and the Hub's own frames that took a connection. Under the async engine the checkout runs in a
    greenlet of its own: the caller's frames are those of the greenlet it switched from."""
    g = greenlet.getcurrent()
    frame = g.parent.gr_frame if g.parent is not None and g.parent.gr_frame is not None else None
    frames = [f"{Path(f.f_code.co_filename).stem}:{line} {f.f_code.co_name}"
              for f, line in traceback.walk_stack(frame)
              if f.f_code.co_filename.startswith(str(_APP)) and f.f_code.co_filename != __file__] if frame else []
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return f"[{task.get_name() if task else '-'}] {' < '.join(frames[:6]) or '?'}"


def _watch_pool(eng: AsyncEngine) -> None:
    """When the pool is nearly used up, log who holds its connections and for how long: a pool that runs out makes
    every query wait and then fail with a timeout, far from the code that holds them."""
    pool = eng.sync_engine.pool
    if not isinstance(pool, QueuePool):
        return

    @event.listens_for(pool, "checkout")
    def _out(_dbapi, record, _proxy):
        global _reported_at
        now = time.monotonic()
        _holders[id(record)] = (now, _holder())
        limit = pool.size() + max(0, getattr(pool, "_max_overflow", 0))
        if pool.checkedout() < limit - POOL_REPORT_FREE or now - _reported_at < POOL_REPORT_EVERY_S:
            return
        _reported_at = now
        held = sorted(_holders.values())
        log.warning("database pool nearly used up: %s of %s connections held\n%s", pool.checkedout(), limit,
                    "\n".join(f"  {now - since:7.1f}s {where}" for since, where in held))

    @event.listens_for(pool, "checkin")
    def _in(_dbapi, record):
        _holders.pop(id(record), None)


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
    """FastAPI dependency. Use scope="function" to commit before sending the response."""
    async with session_scope() as s:
        yield s


def migration_config() -> Config:
    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.attributes["configure_logger"] = False
    return config


def run_migrations(url: str | URL | None = None) -> None:
    config = migration_config()
    migration_engine = create_engine(get_database_url(url), poolclass=NullPool)
    try:
        with migration_engine.begin() as connection:
            # One initializer at a time, including on a fresh database with no tables.
            connection.execute(text("SELECT pg_advisory_xact_lock(7013421)"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        migration_engine.dispose()


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
