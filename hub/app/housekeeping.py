"""Periodic maintenance: DB backup, trash/staging cleanup, cache purge, provider smoke test (NFR-5, NFR-6)."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from tempfile import NamedTemporaryFile
from datetime import datetime, timedelta
from pathlib import Path

from app.config import get_settings
from app.db import get_database_url

log = logging.getLogger(__name__)

KEEP_BACKUPS = 7


def backup_hub_db() -> Path:
    dest_dir = get_settings().backups_dir / "hub"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"hub-{datetime.now():%Y%m%d}.dump"
    database = get_database_url().set(drivername="postgresql")
    target = database._replace(password=None).render_as_string()
    env = os.environ | {"PGPASSWORD": database.password or ""}
    with NamedTemporaryFile(dir=dest_dir, prefix=".hub-backup-", suffix=".part", delete=False) as file:
        tmp = Path(file.name)
    try:
        subprocess.run(["pg_dump", "--format=custom", f"--dbname={target}", f"--file={tmp}"],
                       env=env, check=True, timeout=3600)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    for old in sorted(dest_dir.glob("hub-*.dump"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return dest


async def smoke_test_providers() -> dict[str, str]:
    """Search a well-known track on every provider; a parser that returns nothing means the layout changed."""
    from app.providers.base import TrackQuery
    from app.services import svc

    q = TrackQuery(artist="Кино", title="Группа крови")
    out: dict[str, str] = {}
    for name, p in svc.providers.items():
        try:
            res = await p.search(q, limit=5)
            out[name] = f"ok ({len(res)})" if res else "EMPTY — возможно, сменилась вёрстка"
        except Exception as e:
            out[name] = f"error: {e!r}"[:200]
    bad = {k: v for k, v in out.items() if not v.startswith("ok")}
    (log.warning if bad else log.info)("provider smoke test: %s", out)
    return out


async def run_daily() -> None:
    from app.accounts import purge_email_codes
    from app.api.uploads import purge_stale_uploads
    from app.catalog.cache import cache_purge_expired
    from app.library.editor import purge_trash

    cfg = get_settings().cfg
    try:
        path = await asyncio.to_thread(backup_hub_db)
        log.info("hub db backup: %s", path)
    except Exception:
        log.exception("hub db backup failed")
    try:
        n = await asyncio.to_thread(purge_trash, cfg.library.trash_days)
        from app.db import session_scope
        from app.library import trash

        async with session_scope() as s:  # what was written down of the tracks stirred out with those files
            await trash.purge(s, cfg.library.trash_days)
        from app.library import tracks as registry

        async with session_scope() as s:  # tracks made before a track kept the catalog's record of it
            await registry.fill_catalog(s, limit=200)
        from app.library import acoustic

        async with session_scope() as s:  # files that were there before a file kept the picture of its sound
            await acoustic.fill(s, limit=400)
        async with session_scope() as s:  # files found for a weak one that nobody said anything about
            from app.library import upgrade

            await upgrade.purge(s)
        m = await asyncio.to_thread(purge_stale_uploads)
        c = await cache_purge_expired()
        e = await purge_email_codes()
        log.info("housekeeping: trash dirs %s, stale uploads %s, cache rows %s, email codes %s removed", n, m, c, e)
    except Exception:
        log.exception("housekeeping failed")
    await smoke_test_providers()


async def scheduler_loop() -> None:
    """Runs `run_daily` at cfg.library.backup_hour local time every day."""
    hour = get_settings().cfg.library.backup_hour
    while True:
        now = datetime.now()
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        await run_daily()
