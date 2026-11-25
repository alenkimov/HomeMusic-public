"""The connection to Google Drive, the nightly copy and what the admin's page shows of it."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.backup import drive
from app.config import get_settings
from app.db import session_scope
from app.jobs.queue import handler
from app.models import Album, AppSetting, BackupFile, Edit, Job, TrackFile, utcnow
from app.security import decrypt, encrypt

log = logging.getLogger(__name__)

KEY = "backup"
JOB = "backup_drive"
ROOT = "HomeMusic backups"
DB_FOLDER, FILES_FOLDER = "database", "files"
KEEP_DAILY, KEEP_WEEKLY = 7, 4
STALE_AFTER = timedelta(hours=48)


# ---------------------------------------------------------------- what is kept of the connection and the runs


async def state(s: AsyncSession) -> dict[str, Any]:
    row = await s.get(AppSetting, KEY)
    return dict(row.value_json) if row is not None else {}


async def save(s: AsyncSession, **changes: Any) -> dict[str, Any]:
    row = await s.get(AppSetting, KEY)
    value = {**(row.value_json if row is not None else {}), **changes}
    value = {k: v for k, v in value.items() if v is not None}
    if row is None:
        s.add(AppSetting(key=KEY, value_json=value))
    else:
        row.value_json = value
    await s.flush()
    return value


def refresh_token(st: dict[str, Any]) -> str | None:
    enc = st.get("token")
    return decrypt(enc) if enc else None


# ---------------------------------------------------------------- connecting


async def connect_start(s: AsyncSession) -> dict[str, Any]:
    """A code for the admin to type at google.com/device; the Hub waits for it (`connect_poll`)."""
    code = await drive.device_start()
    pending = {"code": encrypt(code.device_code), "user_code": code.user_code, "url": code.url,
               "interval": code.interval, "until": (utcnow() + timedelta(seconds=code.expires_in)).isoformat()}
    await save(s, pending=pending)
    return {"user_code": code.user_code, "url": code.url, "interval": code.interval}


async def connect_poll(s: AsyncSession) -> str:
    """pending / connected / failed:<why>. Connected: the folders are made and the first copy is queued."""
    st = await state(s)
    pending = st.get("pending")
    if not pending:
        return "connected" if st.get("token") else "failed:Вход не начат"
    if datetime.fromisoformat(pending["until"]) < utcnow():
        await _forget_pending(s)
        return "failed:Код устарел — начните заново"
    try:
        grant = await drive.device_poll(decrypt(pending["code"]))
    except drive.DriveError as e:
        await _forget_pending(s)
        return f"failed:{e}"
    if grant is None:
        return "pending"
    old = refresh_token(st)
    if old and old != grant.refresh_token:
        await drive.revoke(old)
    await _forget_pending(s)
    await save(s, token=encrypt(grant.refresh_token), email=grant.email, error=None, root=None, db=None, files=None)
    if st.get("email") and grant.email and st["email"] != grant.email:
        # another account: the files sent to the old one are not on this Drive
        from sqlalchemy import delete

        await s.execute(delete(BackupFile))
    await request(s)
    return "connected"


async def _forget_pending(s: AsyncSession) -> None:
    row = await s.get(AppSetting, KEY)
    if row is not None and "pending" in row.value_json:
        row.value_json = {k: v for k, v in row.value_json.items() if k != "pending"}
        await s.flush()


async def disconnect(s: AsyncSession) -> None:
    """The Hub forgets the Drive (and gives the access back to Google). The copies stay on the Drive."""
    st = await state(s)
    if token := refresh_token(st):
        await drive.revoke(token)
    row = await s.get(AppSetting, KEY)
    if row is not None:
        row.value_json = {k: v for k, v in row.value_json.items() if k in ("last_ok", "copies")}
    await s.flush()


# ---------------------------------------------------------------- the copy


async def running(s: AsyncSession) -> bool:
    return (await s.scalar(select(func.count(Job.id)).where(Job.kind == JOB, Job.status.in_(("queued", "running"))))) > 0


async def request(s: AsyncSession, *, dump: bool = False) -> bool:
    """Queue a copy unless one is on its way. `dump`: make a fresh dump of the database first (a copy asked for by
    hand; the nightly one sends the dump the night made)."""
    from app.jobs.queue import enqueue

    if await running(s):
        return False
    await enqueue(s, JOB, {"dump": dump}, max_attempts=3)
    return True


def keep(days: list[date]) -> set[date]:
    """Which daily copies to keep: the newest 7, and the first day of each of the 4 newest weeks besides them."""
    days = sorted(set(days), reverse=True)
    daily = days[:KEEP_DAILY]
    first: dict[tuple[int, int], date] = {}
    for d in days:  # back in time: the last one a week gets is its earliest
        first[d.isocalendar()[:2]] = d
    weekly = [first[w] for w in sorted(first, reverse=True) if first[w] not in daily][:KEEP_WEEKLY]
    return set(daily) | set(weekly)


def _dump_day(name: str) -> date | None:
    try:
        return date.fromisoformat(name.removeprefix("hub-").removesuffix(".dump"))
    except ValueError:
        return None


def _latest_dump() -> Path | None:
    dumps = sorted((get_settings().backups_dir / "hub").glob("hub-*.dump"))
    return dumps[-1] if dumps else None


async def _folders(d: drive.Drive, s: AsyncSession, st: dict[str, Any]) -> tuple[str, str, str]:
    root = st.get("root") or await d.folder(ROOT)
    db = st.get("db") or await d.folder(DB_FOLDER, root)
    files = st.get("files") or await d.folder(FILES_FOLDER, root)
    if (root, db, files) != (st.get("root"), st.get("db"), st.get("files")):
        await save(s, root=root, db=db, files=files)
    return root, db, files


async def _wanted(s: AsyncSession) -> dict[str, tuple[Path, str]]:
    """What of the library goes to the Drive: key -> (the file, its stamp). The uploaded tracks' files, and the
    covers of the albums whose picture somebody set."""
    from app.library.importer import album_reldir, library_abs

    out: dict[str, tuple[Path, str]] = {}
    for uid, path in (await s.execute(select(TrackFile.uid, TrackFile.path).where(TrackFile.source == "upload"))).all():
        p = library_abs(path)
        if p.is_file():
            st = p.stat()
            out[f"upload:{uid}"] = (p, f"{st.st_size}:{st.st_mtime_ns}")
    covered = select(Edit.target_id).where(Edit.target_type == "album", Edit.changes_json.has_key("cover"))
    for album in (await s.execute(select(Album).where(Album.id.in_(covered)))).scalars():
        p = library_abs(album.cover_path) if album.cover_path else library_abs(album_reldir(album)) / "cover.jpg"
        if p.is_file():
            st = p.stat()
            out[f"cover:{album.uid}"] = (p, f"{st.st_size}:{st.st_mtime_ns}")
    return out


async def run(*, dump: bool = False) -> dict[str, Any]:
    """Send the copy: the database's dump of the day, the files not sent yet, config.yaml; then drop the copies
    and the files no longer kept. Returns what the page shows."""
    from app.housekeeping import backup_hub_db

    async with session_scope() as s:
        st = await state(s)
        token = refresh_token(st)
        await save(s, last_try=utcnow().isoformat())
    if not token or not drive.configured():
        raise drive.DriveError("Google Drive не подключён")
    path = await asyncio.to_thread(backup_hub_db) if dump else _latest_dump()
    if path is None:
        path = await asyncio.to_thread(backup_hub_db)
    day = datetime.strptime(path.stem.removeprefix("hub-"), "%Y%m%d").date()
    try:
        async with drive.Drive(token) as d, session_scope() as s:
            root, db, files = await _folders(d, s, st)
            # the database: the day's copy replaces one of the same day
            name = f"hub-{day.isoformat()}.dump"
            sent = await d.upload(path, name, db)
            dumps = await d.list(db)
            for f in dumps:
                if f.name == name and f.id != sent.id:
                    await d.delete(f.id)
            kept_days = keep([x for f in dumps if (x := _dump_day(f.name))])
            for f in dumps:
                if (x := _dump_day(f.name)) is not None and x not in kept_days:
                    await d.delete(f.id)
            # config.yaml: one, the newest
            cfg = get_settings().config_file
            if cfg.is_file():
                tops = await d.list(root)
                new = await d.upload(cfg, "config.yaml", root)
                for f in tops:
                    if f.name == "config.yaml" and f.id != new.id:
                        await d.delete(f.id)
            # the files: each sent once, again when it changed
            wanted = await _wanted(s)
            have = {b.key: b for b in (await s.execute(select(BackupFile))).scalars()}
            for key, (p, stamp) in wanted.items():
                old = have.get(key)
                if old is not None and old.stamp == stamp:
                    if old.gone_at is not None:
                        old.gone_at = None
                    continue
                up = await d.upload(p, key.replace(":", "-") + p.suffix, files)
                if old is not None:
                    await d.delete(old.drive_id)
                await s.execute(insert(BackupFile).values(key=key, drive_id=up.id, size=up.size, stamp=stamp,
                                                          uploaded_at=utcnow(), gone_at=None)
                                .on_conflict_do_update(index_elements=[BackupFile.key], set_={
                                    "drive_id": up.id, "size": up.size, "stamp": stamp, "uploaded_at": utcnow(),
                                    "gone_at": None}))
                await s.commit()  # what was sent is known even if a later file fails
            # a file gone from the library is kept while the trash keeps things: an older copy may still name it
            now = utcnow()
            await s.execute(update(BackupFile).where(BackupFile.key.not_in(list(wanted) or [""]), BackupFile.gone_at.is_(None))
                            .values(gone_at=now))
            keep_for = timedelta(days=get_settings().cfg.library.trash_days)
            for b in (await s.execute(select(BackupFile).where(BackupFile.gone_at < now - keep_for))).scalars().all():
                await d.delete(b.drive_id)
                await s.delete(b)
            about = await d.about()
            dumps = await d.list(db)
            copies = sorted(({"day": x.isoformat(), "size": f.size} for f in dumps if (x := _dump_day(f.name))),
                            key=lambda c: c["day"], reverse=True)
            stored = await s.scalar(select(func.coalesce(func.sum(BackupFile.size), 0)))
            return await save(s, last_ok=utcnow().isoformat(), error=None, copies=copies,
                              files_count=await s.scalar(select(func.count()).select_from(BackupFile)),
                              files_size=int(stored or 0), drive_used=about["used"], drive_limit=about["limit"],
                              email=about["email"] or st.get("email"))
    except drive.DriveError as e:
        async with session_scope() as s:
            await save(s, error=str(e))
        raise


@handler(JOB)
async def handle_backup(job: Job) -> None:
    from app.jobs.queue import PermanentError

    try:
        await run(dump=bool(job.payload_json.get("dump")))
    except drive.Unauthorized as e:
        raise PermanentError(str(e)) from e
    log.info("backup: sent to Google Drive")


# ---------------------------------------------------------------- the admin's page


async def status(s: AsyncSession) -> dict[str, Any]:
    st = await state(s)
    last_ok = datetime.fromisoformat(st["last_ok"]) if st.get("last_ok") else None
    connected = bool(st.get("token"))
    return {
        "configured": drive.configured(),
        "connected": connected,
        "email": st.get("email") if connected else None,
        "running": await running(s),
        "last_ok": last_ok,
        "last_try": datetime.fromisoformat(st["last_try"]) if st.get("last_try") else None,
        "error": st.get("error"),
        # nothing sent for two days, or never since connecting: the page says so in red
        "stale": connected and (last_ok is None or utcnow() - last_ok > STALE_AFTER),
        "copies": st.get("copies") or [],
        "files_count": int(st.get("files_count") or 0),
        "files_size": int(st.get("files_size") or 0),
        "drive_used": st.get("drive_used"),
        "drive_limit": st.get("drive_limit"),
        "pending": ({"user_code": st["pending"]["user_code"], "url": st["pending"]["url"]}
                    if st.get("pending") else None),
    }
