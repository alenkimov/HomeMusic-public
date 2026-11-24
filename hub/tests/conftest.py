"""Shared PostgreSQL fixtures and the Hub's isolated test environment."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg import sql
from sqlalchemy import URL

from app.db import get_database_url, run_migrations
from tests.support import ND


@contextmanager
def temporary_database(server: URL, template: str | None = None) -> Iterator[URL]:
    """Allocate a database with a unique name that this fixture owns."""
    database = f"hm_test_{uuid4().hex}"
    name = sql.Identifier(database)
    connection = server.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(connection, autocommit=True) as admin:
        create = sql.SQL("CREATE DATABASE {}").format(name)
        if template is not None:
            create += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
        admin.execute(create)
        try:
            yield server.set(database=database)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(name))


@pytest.fixture(scope="session")
def postgres_template() -> Iterator[URL]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("Set TEST_DATABASE_URL to a disposable PostgreSQL server with permission to create databases")
    with temporary_database(get_database_url(url)) as template:
        run_migrations(template)
        yield template


@pytest.fixture
def db_url(postgres_template: URL) -> Iterator[str]:
    server = get_database_url(os.environ["TEST_DATABASE_URL"])
    with temporary_database(server, postgres_template.database) as database:
        yield database.render_as_string(hide_password=False)


@pytest.fixture
def hub_env(db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Settings for create_app(): temp folders, a fake Navidrome, no background workers, no waiting.

    Yields the music root. Module caches that outlive a test are cleared before and after it.
    """
    music = tmp_path / "music"
    from app.catalog.spotify import SpotifyCatalog

    async def offline_spotify(self):
        raise OSError("Spotify guest requests must be mocked in tests using hub_env")

    monkeypatch.setattr(SpotifyCatalog, "_open", offline_spotify)
    monkeypatch.setenv("MUSIC_ROOT", str(music))
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONFIG_FILE", str(tmp_path / "none.yaml"))
    monkeypatch.setenv("NAVIDROME_URL", ND)
    monkeypatch.setenv("NAVIDROME_ADMIN_USER", "admin")
    monkeypatch.setenv("NAVIDROME_ADMIN_PASSWORD", "adminpw")
    monkeypatch.setenv("HUB_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DISABLE_BACKGROUND", "true")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("ND_SCAN_POLL_S", "0")
    monkeypatch.setenv("EMBEDDER_URL", "")  # a test that needs it mocks its own
    monkeypatch.setenv("YANDEX_CATALOG", "false")  # nothing of a test goes out to Yandex; one that needs it sets its own
    monkeypatch.setenv("SMTP_HOST", "")  # no letters; a test that sends them captures its own
    monkeypatch.setenv("MIX_EMBED_WAIT_S", "1")
    monkeypatch.setenv("MIX_POLL_S", "0")
    _clear_caches()
    yield music
    _clear_caches()


def _clear_caches() -> None:
    from app import accounts, config, google_auth, security
    from app.library.navidrome import NavidromeAdmin, NavidromeHistory
    from app.mixes import sources, wave
    from app.providers import ytdlp

    config.get_settings.cache_clear()
    security._fernet.cache_clear()
    accounts._failures.clear()
    google_auth._keys.clear()
    NavidromeAdmin.forget_token()
    NavidromeHistory.forget_tokens()
    ytdlp._media_hosts.clear()
    sources._lastfm_limiter = None
    wave._rt.clear()

