"""What the Hub is built of that is out of date: the libraries that break when the services change under them
(yt-dlp the first — YouTube changes its player every few weeks), and the Hub itself.

The worker asks once a day (`loop`): PyPI for the newest of each library (`WATCHED`), GitHub for the newest release
tag of the Hub (`Settings.release_repo`). What it found is written down (`_STATE`) for both processes (one image:
what is installed is the same); «О сервере» lists what is behind, and a count stands by it in the menu. Nothing is
updated here: the versions are pinned in the image (`hub/pyproject.toml`) — a new one comes with a release and
`scripts/update-server.ps1`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from importlib import metadata
from typing import Any

log = logging.getLogger(__name__)

# the PyPI name, the name shown, what is at stake while it is behind
WATCHED: tuple[tuple[str, str, str], ...] = (
    ("yt-dlp", "yt-dlp", "YouTube и другие видеосайты: часть видео может не скачиваться"),
    ("yt-dlp-ejs", "yt-dlp-ejs", "YouTube: ссылки на видео могут не открываться"),
    ("bgutil-ytdlp-pot-provider", "bgutil-ytdlp-pot-provider", "YouTube может просить подтвердить, что вы не робот"),
    ("spotapi", "spotapi", "каталог Spotify: поиск и страницы могут не работать"),
)
HUB = "HomeMusic"
_STATE = "updates"
_TTL_S = 7 * 86400  # an answer a week old is still better than none; the worker rewrites it daily
EVERY_S = 86400


def key(v: str) -> tuple[int, ...]:
    """A version compared by its numbers: «2026.11.23» > «2026.10.26», «v2.0.10» > «2.0.9»."""
    return tuple(int(n) for n in re.findall(r"\d+", v))


def installed(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


async def _newest_on_pypi(client, package: str) -> str | None:
    r = await client.get(f"https://pypi.org/pypi/{package}/json")
    r.raise_for_status()
    return (r.json().get("info") or {}).get("version") or None


async def _newest_hub(client, repo: str) -> str | None:
    r = await client.get(f"https://api.github.com/repos/{repo}/tags", params={"per_page": 30},
                         headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    tags = [t["name"].lstrip("v") for t in r.json() if re.fullmatch(r"v?\d{4}\.\d{1,2}\.\d+", t.get("name") or "")]
    return max(tags, key=key) if tags else None


async def check() -> dict[str, Any]:
    """Ask for the newest versions now and write them down; one that could not be asked keeps what was known."""
    from app import version
    from app.catalog.cache import cache_get, cache_set
    from app.config import get_settings
    from app.net import make_client
    from app.outbound import purpose

    before = {i["name"]: i for i in ((await cache_get(_STATE)) or {}).get("items") or []}
    items: list[dict[str, Any]] = []
    with purpose("updates"):
        async with make_client(timeout=20) as client:
            for package, name, why in WATCHED:
                latest = None
                try:
                    latest = await _newest_on_pypi(client, package)
                except Exception as e:
                    log.info("updates: %s not asked: %r", package, e)
                    latest = (before.get(name) or {}).get("latest")
                items.append({"name": name, "installed": installed(package), "latest": latest, "why": why})
            repo = get_settings().release_repo.strip()
            if repo:
                latest = None
                try:
                    latest = await _newest_hub(client, repo)
                except Exception as e:
                    log.info("updates: the Hub's releases not asked: %r", e)
                    latest = (before.get(HUB) or {}).get("latest")
                items.append({"name": HUB, "installed": version.version(), "latest": latest,
                              "why": "новые исправления и возможности: «Меню» → «Что нового» после обновления"})
    state = {"checked_at": datetime.now(UTC).isoformat(), "items": items}
    await cache_set(_STATE, state, _TTL_S)
    behind = [i["name"] for i in outdated(state)]
    log.info("updates: checked, %s", f"behind: {', '.join(behind)}" if behind else "all up to date")
    return state


def outdated(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    """What is behind of what was found. What is installed is read now, not taken from the record: after an update
    a library is not shown behind until the next check."""
    from app import version

    now = {name: installed(package) for package, name, _ in WATCHED} | {HUB: version.version()}
    out = []
    for i in (state or {}).get("items") or []:
        have, latest = now.get(i["name"], i.get("installed")), i.get("latest")
        if have and latest and key(latest) > key(have):
            out.append({**i, "installed": have})
    return out


async def state() -> dict[str, Any] | None:
    from app.catalog.cache import cache_get

    return await cache_get(_STATE)


async def loop() -> None:
    """The worker asks once a day, the first time a minute after it starts (not to add to a start's load)."""
    await asyncio.sleep(60)
    while True:
        try:
            await check()
        except Exception:
            log.exception("updates: the check failed")
        await asyncio.sleep(EVERY_S)
