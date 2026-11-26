"""The Hub's version and the history of what changed (`VERSION` and `CHANGELOG.md` at the root of the repository).

The version is a date: year, month and the release of that month (`2026.11.2`). `CHANGELOG.md` keeps the history,
in words for the listeners: «## Не выпущено» gathers what is done, a release turns it into «## 2026.11.2 —
2026-11-28» with its «### Новое / Улучшено / Исправлено» lists. The web app shows it («Меню» → «Что нового») and its
own version beside the Hub's; the admin's «О сервере» adds the commit, the build and the versions of what the Hub
depends on (`components`).

In the image both files are copied next to the app (`hub/Dockerfile`), with the commit and the time of the build
(`BUILD_COMMIT`, `BUILD_AT`); in development they are read from the repository, the commit from git.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
UNRELEASED = "Не выпущено"
_RELEASE = re.compile(r"^(\d{4}\.\d{1,2}\.\d+)(?:\s+[—-]\s+(\d{4}-\d{2}-\d{2}))?\s*$")


def _find(name: str) -> Path | None:
    for d in (_HERE.parent, _HERE.parent.parent):  # /app (the image), or hub/.. (the repository)
        if (p := d / name).is_file():
            return p
    return None


@cache
def version() -> str:
    p = _find("VERSION")
    return p.read_text(encoding="utf-8").strip() if p else "dev"


@cache
def build() -> dict[str, str | None]:
    """The commit and the time of the build (the image's), or of the repository's HEAD (development)."""
    commit = built = None
    if p := _find("BUILD_COMMIT"):
        commit = p.read_text(encoding="utf-8").strip() or None
    if p := _find("BUILD_AT"):
        built = p.read_text(encoding="utf-8").strip() or None
    if commit is None:
        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_HERE, capture_output=True,
                                    text=True, timeout=3, check=True).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            commit = None
    return {"commit": commit, "built_at": built}


def parse(text: str) -> list[dict[str, Any]]:
    """The releases of a changelog, newest first: {version, date, intro, sections: [{title, items}]}; «Не выпущено»
    is not among them."""
    releases: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    section: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            head = line[3:].strip()
            m = _RELEASE.match(head)
            current = {"version": m.group(1), "date": m.group(2), "intro": "", "sections": []} if m else None
            section = None
            if current is not None:
                releases.append(current)
        elif current is None:
            continue
        elif line.startswith("### "):
            section = {"title": line[4:].strip(), "items": []}
            current["sections"].append(section)
        elif line.lstrip().startswith(("- ", "* ")):
            item = line.lstrip()[2:].strip()
            if section is None:
                section = {"title": "", "items": []}
                current["sections"].append(section)
            section["items"].append(item)
        elif line.strip() and section is None:
            current["intro"] = f"{current['intro']} {line.strip()}".strip()
        elif line.strip() and section is not None and section["items"]:
            section["items"][-1] += " " + line.strip()  # an item's line wrapped
    return releases


@cache
def releases() -> list[dict[str, Any]]:
    p = _find("CHANGELOG.md")
    return parse(p.read_text(encoding="utf-8")) if p else []


# --- what the Hub depends on, for the admin ---------------------------------------------------------------------

_components: tuple[float, list[dict[str, str | None]]] | None = None
COMPONENTS_TTL_S = 600


async def _first_line(*cmd: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 5)
    except (OSError, TimeoutError):
        return None
    line = out.decode("utf-8", "replace").splitlines()[0] if out else ""
    return line.strip() or None


async def components() -> list[dict[str, str | None]]:
    """Navidrome, PostgreSQL, yt-dlp, ffmpeg, Python, Deno: the ones a failure is first looked for in."""
    import sys
    import time

    global _components
    if _components and time.monotonic() - _components[0] < COMPONENTS_TTL_S:
        return _components[1]
    out: list[dict[str, str | None]] = []

    navidrome = None
    try:
        from app.library.navidrome import admin_client

        async with admin_client() as nd:
            body = await nd.call("ping")
        navidrome = body.get("serverVersion") or body.get("version")
    except Exception as e:
        log.info("version: Navidrome not asked: %r", e)
    out.append({"name": "Navidrome", "version": navidrome})

    postgres = None
    try:
        from sqlalchemy import text

        from app.db import session_scope

        async with session_scope() as s:
            postgres = (await s.execute(text("SHOW server_version"))).scalar()
    except Exception as e:
        log.info("version: PostgreSQL not asked: %r", e)
    out.append({"name": "PostgreSQL", "version": postgres})

    try:
        from yt_dlp.version import __version__ as ytdlp
    except Exception:
        ytdlp = None
    out.append({"name": "yt-dlp", "version": ytdlp})

    ffmpeg = await _first_line("ffmpeg", "-version")
    m = re.match(r"ffmpeg version (\S+)", ffmpeg or "")
    out.append({"name": "ffmpeg", "version": m.group(1) if m else ffmpeg})
    out.append({"name": "Python", "version": sys.version.split()[0]})
    deno = await _first_line("deno", "--version")
    out.append({"name": "Deno", "version": deno.split()[1] if deno and len(deno.split()) > 1 else deno})

    _components = (time.monotonic(), out)
    return out
