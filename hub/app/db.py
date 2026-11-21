"""PostgreSQL engine, async sessions and Alembic migrations on startup."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


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
