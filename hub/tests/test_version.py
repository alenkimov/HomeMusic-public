"""The version and «Что нового»: `VERSION` and `CHANGELOG.md` of the repository."""

from __future__ import annotations

import re

from sqlalchemy import select

from app import version
from app.db import session_scope
from app.models import User
from tests.test_flow import env as env
from tests.test_flow import src_mp3 as src_mp3

TEXT = """# Что нового

Вступление файла.

## Не выпущено
- ещё не вышло

## 2026.12.2 — 2026-12-20

Коротко о выпуске.

### Новое
- Первое
- Второе, длинное,
  на две строки

### Исправлено
- Третье

## 2026.12.1
- Без разделов
"""


def test_the_releases_are_read_newest_first_without_the_unreleased():
    rs = version.parse(TEXT)
    assert [(r["version"], r["date"]) for r in rs] == [("2026.12.2", "2026-12-20"), ("2026.12.1", None)]
    assert rs[0]["intro"] == "Коротко о выпуске."
    assert rs[0]["sections"] == [{"title": "Новое", "items": ["Первое", "Второе, длинное, на две строки"]},
                                 {"title": "Исправлено", "items": ["Третье"]}]
    assert rs[1]["sections"] == [{"title": "", "items": ["Без разделов"]}]


def test_the_repository_has_a_version_and_its_release():
    v = version.version()
    assert re.fullmatch(r"\d{4}\.\d{1,2}\.\d+", v)
    assert version.releases()[0]["version"] == v  # the newest release of CHANGELOG.md is the version


async def test_the_api_says_the_version_and_the_admin_more(env):
    client = env["client"]
    body = (await client.get("/api/version")).json()
    assert body["version"] == version.version() and body["releases"]
    async with session_scope() as s:
        u = await s.scalar(select(User).limit(1))
        u.is_admin = False
    assert (await client.get("/api/admin/about")).status_code == 403
    async with session_scope() as s:
        u = await s.scalar(select(User).limit(1))
        u.is_admin = True
    about = (await client.get("/api/admin/about")).json()
    assert about["version"] == version.version()
    names = [c["name"] for c in about["components"]]
    assert names[:3] == ["Navidrome", "PostgreSQL", "yt-dlp"]
    assert next(c for c in about["components"] if c["name"] == "PostgreSQL")["version"]
