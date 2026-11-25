"""A restore from the backups on Google Drive, on a new server (or the same one, wiped).

Run with the Hub stopped and its database up (scripts/restore-server.ps1 does it): `python -m app.backup restore`.

1. Sign in to Google Drive (a code to type at google.com/device, as when the backups were connected).
2. The copy of a day (the newest, or `--day`): the database is wiped and the copy put in its place, then brought to
   the Hub's version of it (the migrations).
3. The uploaded files and the covers set by hand, from the Drive to where the database says they are.
4. The downloaded files are not in the backups: the rows of the files missing are dropped and their tracks queued to
   be downloaded again (in the background, at the pace of a transfer) — each for who brought it.
5. The listeners' Navidrome accounts are made again when the Hub starts (`restore_accounts`): their passwords are in
   the database, encrypted with `HUB_SECRET_KEY` — the `.env` of the old server must be this server's.

Everybody signs in again: the sign-ins are not in the copies.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backup import drive, service
from app.config import get_settings
from app.db import get_database_url
from app.jobs.queue import PRIORITY_IMPORT, enqueue, handler
from app.models import Album, BackupFile, DownloadBatch, DownloadItem, Job, Track, TrackFile, User
from app.security import encrypt

log = logging.getLogger(__name__)

ACCOUNTS_JOB = "restore_accounts"


def say(text: str) -> None:
    print(text, flush=True)


# ---------------------------------------------------------------- the Drive


async def sign_in() -> str:
    """The refresh token of the Drive the copies are on: the admin types the code shown."""
    code = await drive.device_start()
    say(f"\nОткройте {code.url} и введите код:  {code.user_code}\n"
        "Войдите в тот Google-аккаунт, куда сохранялись копии, и разрешите доступ.\n")
    waited = 0
    while waited < code.expires_in:
        await asyncio.sleep(code.interval)
        waited += code.interval
        grant = await drive.device_poll(code.device_code)
        if grant is not None:
            say(f"Вход выполнен: {grant.email or 'аккаунт Google'}")
            return grant.refresh_token
    raise drive.DriveError("Код устарел — запустите восстановление заново")


async def find_copies(d: drive.Drive) -> tuple[str, list[drive.DriveFile]]:
    """The folder of the copies and the database's copies in it, the newest first."""
    root = await _find_folder(d, service.ROOT, None)
    if root is None:
        raise drive.DriveError(f"На этом Google Drive нет папки «{service.ROOT}» с копиями HomeMusic")
    db = await _find_folder(d, service.DB_FOLDER, root)
    dumps = [f for f in (await d.list(db)) if service._dump_day(f.name)] if db else []
    if not dumps:
        raise drive.DriveError("В папке копий нет ни одной копии базы")
    return root, sorted(dumps, key=lambda f: f.name, reverse=True)


async def _find_folder(d: drive.Drive, name: str, parent: str | None) -> str | None:
    q = f"name = '{drive._quote(name)}' and mimeType = '{drive.FOLDER}' and trashed = false"
    q += f" and '{parent}' in parents" if parent else " and 'root' in parents"
    found = (await d._call("GET", f"{drive.API}/files", params={"q": q, "fields": "files(id)", "spaces": "drive"})).json()
    return found["files"][0]["id"] if found.get("files") else None


# ---------------------------------------------------------------- the database


def load_dump(path: Path) -> None:
    """The database wiped and the copy put in its place; then the Hub's migrations bring it to this version."""
    from app.db import run_migrations

    database = get_database_url().set(drivername="postgresql")
    target = database._replace(password=None).render_as_string()
    env = os.environ | {"PGPASSWORD": database.password or ""}
    subprocess.run(["psql", f"--dbname={target}", "-v", "ON_ERROR_STOP=1", "-c",
                    "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"], env=env, check=True, timeout=600,
                   capture_output=True)
    subprocess.run(["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", f"--dbname={target}", str(path)],
                   env=env, check=True, timeout=3600, capture_output=True)
    run_migrations()


# ---------------------------------------------------------------- the files


async def put_back_files(s: AsyncSession, d: drive.Drive) -> tuple[int, int]:
    """The uploaded files and the covers set by hand, from the Drive to where the database says they are.
    Returns (put back, missing on the Drive)."""
    from app.library.importer import album_reldir, library_abs

    done = missing = 0
    for b in (await s.execute(select(BackupFile))).scalars().all():
        kind, _, uid = b.key.partition(":")
        dest: Path | None = None
        if kind == "upload":
            path = await s.scalar(select(TrackFile.path).where(TrackFile.uid == uid))
            dest = library_abs(path) if path else None
        elif kind == "cover":
            album = await s.scalar(select(Album).where(Album.uid == uid))
            if album is not None:
                dest = library_abs(album.cover_path) if album.cover_path else library_abs(album_reldir(album)) / "cover.jpg"
        if dest is None or dest.is_file():
            continue
        try:
            await d.download(b.drive_id, dest)
            done += 1
        except drive.DriveError as e:
            log.warning("restore: %s not on the Drive: %s", b.key, e)
            missing += 1
    return done, missing


async def queue_missing(s: AsyncSession) -> int:
    """The files the database names and the disk has not: their rows go, and the tracks left with no file are
    downloaded again — in the background, each for who brought it (else the first admin). Returns how many."""
    from app.library.importer import library_abs
    from app.providers.base import TrackQuery

    admin = await s.scalar(select(User.id).where(User.is_admin).order_by(User.id).limit(1))
    gone: dict[int, int | None] = {}
    for f in (await s.execute(select(TrackFile))).scalars().all():
        if not library_abs(f.path).is_file():
            gone.setdefault(f.track_id, f.added_by)
            await s.delete(f)
    await s.flush()
    left = set((await s.execute(select(TrackFile.track_id).where(TrackFile.track_id.in_(list(gone))))).scalars()) if gone else set()
    by_user: dict[int, list[Track]] = defaultdict(list)
    for t in (await s.execute(select(Track).where(Track.id.in_([i for i in gone if i not in left])))).scalars():
        who = gone[t.id] or t.created_by or admin
        if who is not None:
            by_user[who].append(t)
    n = 0
    for user_id, tracks in by_user.items():
        batch = DownloadBatch(user_id=user_id, origin="hub", title="Восстановление из резервной копии")
        s.add(batch)
        await s.flush()
        for t in tracks:
            q = TrackQuery(artist=t.artist, title=t.title, album=t.album, duration_s=t.duration_s, isrc=t.isrc,
                           explicit=t.explicit)
            item = DownloadItem(batch_id=batch.id, user_id=user_id,
                                query_json={"query": q.model_dump(), "meta": _meta(t)})
            s.add(item)
            await s.flush()
            await enqueue(s, "download_item", {"item_id": item.id}, priority=PRIORITY_IMPORT)
            n += 1
    return n


def _meta(t: Track) -> dict[str, Any]:
    """What the catalog said of the track, as a download wants it, and the track the file is for."""
    from app.catalog.models import CatalogTrack
    from app.jobs.downloads import meta_from_catalog

    meta: dict[str, Any] = {}
    if t.catalog:
        try:
            meta = meta_from_catalog(CatalogTrack.model_validate(t.catalog))
        except ValueError:
            meta = {}
    return {**meta, "track_id": t.id}


@handler(ACCOUNTS_JOB)
async def handle_accounts(job: Job) -> None:
    """The listeners' Navidrome accounts, made again on a new Navidrome (one there is left as it is)."""
    from app.db import session_scope
    from app.library.navidrome import NavidromeAdmin, NavidromeUserExists
    from app.security import decrypt

    async with session_scope() as s:
        users = [(u.nd_username, u.nd_password_enc) for u in (await s.execute(select(User))).scalars()]
    made = failed = 0
    async with NavidromeAdmin() as nd:
        for name, enc in users:
            if name == get_settings().navidrome_admin_user:
                continue
            try:
                password = decrypt(enc)
            except Exception:
                log.warning("restore: the password of %s cannot be read: is HUB_SECRET_KEY the old server's?", name)
                failed += 1
                continue
            try:
                await nd.create_user(name, password)
                made += 1
            except NavidromeUserExists:
                pass
    log.info("restore: %s Navidrome accounts made, %s could not be", made, failed)


# ---------------------------------------------------------------- the whole of it


async def restore(day: str | None = None, *, yes: bool = False) -> None:
    from app.db import init_engine, session_scope

    if not drive.configured():
        raise SystemExit("В .env нет GOOGLE_DRIVE_CLIENT_ID и GOOGLE_DRIVE_CLIENT_SECRET — "
                         "перенесите .env со старого сервера (README, «Резервные копии»)")
    token = await sign_in()
    staging = get_settings().staging_dir / "restore"
    staging.mkdir(parents=True, exist_ok=True)
    async with drive.Drive(token) as d:
        _root, dumps = await find_copies(d)
        say("Копии базы: " + ", ".join(service._dump_day(f.name).strftime("%d.%m.%Y") for f in dumps))
        chosen = dumps[0]
        if day:
            wanted = date.fromisoformat(day)
            chosen = next((f for f in dumps if service._dump_day(f.name) == wanted), None)
            if chosen is None:
                raise SystemExit(f"Копии за {wanted:%d.%m.%Y} нет")
        when = service._dump_day(chosen.name)
        if not yes:
            answer = await asyncio.to_thread(input, f"\nВосстановить копию за {when:%d.%m.%Y}? Всё, что сейчас в базе этого сервера, "
                           "будет заменено. Введите «да»: ")
            if answer.strip().lower() not in ("да", "yes", "y"):
                raise SystemExit("Отменено")
        say(f"Скачиваю копию за {when:%d.%m.%Y} ({chosen.size / 1e6:.1f} МБ)…")
        dump = staging / chosen.name
        await d.download(chosen.id, dump)
        say("Восстанавливаю базу…")
        try:
            await asyncio.to_thread(load_dump, dump)
        except subprocess.CalledProcessError as e:
            raise SystemExit(f"База не восстановилась: {(e.stderr or b'').decode(errors='replace')[-800:]}") from e
        init_engine()
        say("Возвращаю загруженные файлы и обложки…")
        async with session_scope() as s:
            done, missing = await put_back_files(s, d)
        say(f"  возвращено: {done}" + (f", не нашлось на Drive: {missing}" if missing else ""))
    async with session_scope() as s:
        queued = await queue_missing(s)
        await enqueue(s, ACCOUNTS_JOB, {})
        # the Drive this server goes on sending its copies to
        await s.execute(delete(Job).where(Job.kind == service.JOB))
        await service.save(s, token=encrypt(token), error=None, root=None, db=None, files=None)
    dump.unlink(missing_ok=True)
    say(f"Поставлено на скачивание заново: {queued} треков. Они скачаются в фоне после запуска Хаба.")
    say("Готово. Запустите Хаб; всем пользователям нужно будет войти заново.")


def main(argv: list[str]) -> None:
    import argparse

    p = argparse.ArgumentParser(prog="python -m app.backup", description="Резервные копии HomeMusic в Google Drive")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("restore", help="восстановить сервер из копии")
    r.add_argument("--day", help="дата копии, ГГГГ-ММ-ДД (по умолчанию — последняя)")
    r.add_argument("--yes", action="store_true", help="не спрашивать подтверждение")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(restore(args.day, yes=args.yes))
    except drive.DriveError as e:
        raise SystemExit(str(e)) from e

