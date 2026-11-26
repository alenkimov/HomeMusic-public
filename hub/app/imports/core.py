"""An import's life: sign-in → fetch (a snapshot of the collection) → preview → matching in batches → apply.

Job kinds (below the user's work in the queue, `PRIORITY_IMPORT` — but for the sign-in and the reading of the
collection, which the user waits for on the screen: `PRIORITY_ASKED`):
- `import_auth`      — waits for the user to confirm the sign-in code, then fetches (Yandex); for VK, whose code the
                       user types here (`answer_auth`), only ends a sign-in nobody finished
- `import_fetch`     — reads the collection from the service into collections and items, checks the library
- `import_resolve`   — matches a batch of rows (`resolve.py`) and acts on them (`apply.py`), queues the next batch
- `import_playlists` — settles the Hub's playlists (coalesced: one per import)
- `import_likes`     — mirrors the transfer's likes to Navidrome on the general queue
- `import_finish`    — when the downloads are over: the last rewrite, ★ albums and artists, the token goes

Modes: `economy` — playlists and ♥ at once from what the library has, the rest is listened to from the sources
(the Import page, `playQueue` in the app) and downloaded once listened to (`listened`); `full` — everything is
downloaded in the background.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import timedelta
from typing import Any

from sqlalchemy import case, delete, func, insert, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import integrations, links
from app.api.schemas import (
    ImportAuthOut,
    ImportCollectionOut,
    ImportDestinationOut,
    ImportFetchOut,
    ImportItemOut,
    ImportOut,
    ImportStatsOut,
)
from app.catalog.lookup import CatalogUnavailable
from app.config import get_settings
from app.db import session_scope
from app.events import bus
from app.imports import apply
from app.imports.resolve import Resolution, resolve_album, resolve_artist, resolve_track
from app.imports.sources import spotify, vk, yandex
from app.imports.sources.base import ITEM_KIND, AuthPending, Snapshot, SourceError
from app.jobs.queue import PRIORITY_IMPORT, PermanentError, RetryLater, enqueue, handler, notify, on_final_failure
from app.library.owned import find_owned
from app.models import DownloadItem, Import, ImportCollection, ImportItem, IntegrationConnection, Job, Playlist, utcnow
from app.security import decrypt, encrypt

log = logging.getLogger(__name__)

RESOLVE_BATCH = 50
RESOLVE_CONCURRENCY = 4
# What the user waits for on the screen — a sign-in being confirmed, the collection being read — is no background
# work: it is not queued behind the downloads of a transfer (somebody else's, thousands of them) but goes at once.
PRIORITY_ASKED = 0
PRIORITY_LIKES = PRIORITY_IMPORT + 1  # ahead of the transfer's downloads, still background work
PLAYLISTS_DELAY_S = 30  # playlists are rewritten at most this often while downloads come in
FINISH_RECHECK_S = 120
PLAYLIST_UPDATE_LOCK = 7013425
BYTES_PER_S = 40_000  # 320 kbps
DEFAULT_DURATION_S = 240
# matching and downloading order: playlists, then likes (newest first), albums, artists; dislikes are only kept
KIND_ORDER = {"playlist": 0, "likes": 1, "albums": 2, "artists": 3, "dislikes": 4}
TRACK_KINDS = ("likes", "playlist")
ACTIVE = ("auth", "fetching", "running")


# --- state + events --------------------------------------------------------------------------------------


def _free_bytes() -> int | None:
    try:
        return shutil.disk_usage(get_settings().music_root).free
    except OSError:
        return None


async def stats(s: AsyncSession, imp: Import, colls: list[ImportCollection]) -> ImportStatsOut:
    selected = {c.id for c in colls if c.selected and c.kind in TRACK_KINDS}
    # of what the service said of a track only its length is needed here: not the whole of it, row by row
    rows = (await s.execute(
        select(ImportItem.collection_id, ImportItem.external_id, ImportItem.match, ImportItem.status,
               ImportItem.raw_json["duration_s"].as_float())
        .where(ImportItem.import_id == imp.id))).all()
    out = ImportStatsOut(free_bytes=_free_bytes())
    tracks: dict[str, tuple[str | None, str, float | None]] = {}
    for cid, ext, match, status, duration in rows:
        if cid in selected and status != "skipped":
            prev = tracks.get(ext)
            if prev is None or match == "owned":
                tracks[ext] = (match, status, duration)
    out.tracks = len(tracks)
    for match, status, duration in tracks.values():
        if match == "owned":
            out.owned += 1
            continue
        if status in ("done",):
            continue
        out.to_download += 1
        out.est_bytes += int((duration or DEFAULT_DURATION_S) * BYTES_PER_S)
        if status == "pending":
            out.pending += 1
        elif status == "waiting":
            out.waiting += 1
        if match == "exact":
            out.exact += 1
        elif match == "probable":
            out.probable += 1
        elif match == "not_found":
            out.not_found += 1
    for status, n in (await s.execute(
        select(DownloadItem.status, func.count(DownloadItem.id))
        .where(DownloadItem.import_id == imp.id)
        .group_by(DownloadItem.status))).all():
        if status == "done":
            out.done += n
        elif status in ("failed", "needs_review", "cancelled"):
            out.failed += n
        else:
            out.downloading += n
    return out


async def _destinations(s: AsyncSession, imp: Import, colls: list[ImportCollection]) -> list[ImportDestinationOut]:
    if imp.started_at is None:
        return []
    extra = (imp.options_json or {}).get("extra") or {}
    ids = [c.playlist_id for c in colls if c.selected and c.playlist_id is not None]
    ids.extend(int(pid) for pid in extra.get("playlist_ids") or [] if str(pid).isdigit())
    targets = []
    if ids:
        names = dict((await s.execute(select(Playlist.id, Playlist.name).where(
            Playlist.user_id == imp.user_id, Playlist.kind == "user", Playlist.id.in_(ids)))).all())
        targets.extend(ImportDestinationOut(name=names[pid], playlist_id=str(pid))
                       for pid in dict.fromkeys(ids) if pid in names)
    if extra.get("star") or any(c.selected and c.kind == "likes" for c in colls):
        targets.append(ImportDestinationOut(name="Любимое"))
    return targets


def display_title(imp: Import, colls: list[ImportCollection]) -> str | None:
    """What a transfer is called after its service: the account, or the collection a link or Spotify gave."""
    if imp.method == "link" or (imp.source == "spotify" and imp.method == "saved"):
        return next((c.title for c in sorted(colls, key=lambda c: c.position) if c.kind in TRACK_KINDS and c.title),
                    imp.account)
    return imp.account


async def import_out(s: AsyncSession, imp: Import) -> ImportOut:
    colls = (await s.execute(select(ImportCollection).where(ImportCollection.import_id == imp.id)
                             .order_by(ImportCollection.position))).scalars().all()
    counts: dict[int, tuple[int, int]] = {}
    for cid, n, owned in (await s.execute(
        select(ImportItem.collection_id, func.count(ImportItem.id),
               func.sum(case((ImportItem.match == "owned", 1), else_=0)))
        .where(ImportItem.import_id == imp.id, ImportItem.status != "skipped")
        .group_by(ImportItem.collection_id))).all():
        counts[cid] = (n, int(owned or 0))
    auth = None
    a = imp.auth_json or {}
    if imp.status == "auth" and a.get("user_code"):
        auth = ImportAuthOut(user_code=a["user_code"], verification_url=a["verification_url"],
                             expires_at=a["expires_at"])
    elif imp.status == "auth" and a.get("kind"):
        auth = ImportAuthOut(kind=a["kind"], hint=a.get("hint"), wrong=bool(a.get("wrong")),
                             verification_url=a.get("verification_url") or "", expires_at=a["expires_at"])
    title = display_title(imp, list(colls))
    has_token = imp.connection_id is not None
    fetch = await _fetch_state(s, imp) if imp.status == "fetching" else None
    return ImportOut(
        id=imp.id, source=imp.source, title=title, status=imp.status, mode=imp.mode, account=imp.account,
        has_token=has_token, keep_token=imp.keep_token, can_sync=has_token or imp.method in ("profile", "link"),
        download_albums=bool((imp.options_json or {}).get("download_albums")), auth=auth, fetch=fetch, error=imp.error,
        stats=await stats(s, imp, list(colls)),
        collections=[ImportCollectionOut(id=c.id, kind=c.kind, title=c.title, selected=c.selected,
                                         count=counts.get(c.id, (0, 0))[0], owned=counts.get(c.id, (0, 0))[1],
                                         cover_url=c.cover_url, playlist_id=str(c.playlist_id) if c.playlist_id else None) for c in colls],
        destinations=await _destinations(s, imp, colls),
        created_at=imp.created_at, started_at=imp.started_at, finished_at=imp.finished_at,
    )


async def _fetch_state(s: AsyncSession, imp: Import) -> ImportFetchOut | None:
    """The job reading the collection: waiting, being read, or waiting to try again after a failure."""
    job = await s.scalar(select(Job).where(Job.kind == "import_fetch", Job.status.in_(("queued", "running")),
                                           Job.payload_json["import_id"].as_integer() == imp.id)
                         .order_by(Job.id.desc()).limit(1))
    if job is None:
        return None
    error = (job.last_error or "").split("\n", 1)[0] or None
    if job.status == "running":
        return ImportFetchOut(state="running", attempt=job.attempts, max_attempts=job.max_attempts,
                              since=job.locked_at or job.updated_at, last_error=error)
    return ImportFetchOut(state="retrying" if job.attempts else "queued", attempt=job.attempts,
                          max_attempts=job.max_attempts, since=job.updated_at if job.attempts else job.created_at,
                          last_error=error, next_at=job.run_after if job.attempts else None)


def item_out(it: ImportItem, kind: str, nd_song_id: str | None = None, dl: DownloadItem | None = None) -> ImportItemOut:
    raw = it.raw_json or {}
    return ImportItemOut(
        id=it.id, collection_id=it.collection_id, position=it.position, kind=ITEM_KIND.get(kind, "track"),
        artist=raw.get("artist") or raw.get("name") or "", title=raw.get("title") or raw.get("name") or "",
        album=raw.get("album"), duration_s=raw.get("duration_s"), cover_url=raw.get("cover_url"), ref=it.ref,
        match=it.match, score=it.score, status=it.status, nd_song_id=nd_song_id,
        track_id=it.track_id if ITEM_KIND.get(kind, "track") == "track" else None, download_item_id=it.download_item_id,
        download_status=dl.status if dl else None, error=dl.error if dl else None,
    )


def publish(imp: Import) -> None:
    bus.publish(imp.user_id, "import", {"id": imp.id, "status": imp.status})


async def set_status(import_id: int, status: str, **fields: Any) -> Import | None:
    async with session_scope() as s:
        imp = await s.get(Import, import_id)
        if imp is None:
            return None
        imp.status = status
        for k, v in fields.items():
            setattr(imp, k, v)
    publish(imp)
    return imp


async def enqueue_once(s: AsyncSession, kind: str, import_id: int, *, delay_s: float = 0,
                       priority: int = PRIORITY_IMPORT) -> None:
    """One queued job of this kind per import (a batch of downloads coming in asks for one rewrite)."""
    pending = (await s.execute(
        select(Job).where(Job.kind == kind, Job.status == "queued",
                          Job.payload_json["import_id"].as_integer() == import_id).limit(1)
    )).scalar_one_or_none()
    if pending is None:
        await enqueue(s, kind, {"import_id": import_id}, delay_s=delay_s, priority=priority)
    elif pending.priority < priority:
        pending.priority = priority


def _active_transfer_jobs(import_id=Import.id):
    return select(Job.id).where(Job.kind.in_(("import_fetch", "import_resolve", "import_finish")),
                                Job.status.in_(("queued", "running")),
                                Job.payload_json["import_id"].as_integer() == import_id).exists()


async def set_paused(s: AsyncSession, imp: Import, paused: bool) -> None:
    if imp.status not in ("running", "paused"):
        raise PermanentError("Пауза доступна только для запущенного переноса")
    imp.status = "paused" if paused else "running"
    if not paused and not await s.scalar(select(_active_transfer_jobs(imp.id))):
        await enqueue(s, "import_resolve", {"import_id": imp.id}, priority=PRIORITY_IMPORT)


async def _running_transfer(s: AsyncSession, import_id: int, *, lock: bool = False) -> Import | None:
    imp = await s.get(Import, import_id, with_for_update={"key_share": True} if lock else None)
    if imp is not None and imp.status == "paused":
        # A worker claimed this job before the pause. Put it back for the next resume.
        raise RetryLater(0, "transfer paused")
    return imp if imp is not None and imp.status == "running" else None


async def resume_orphaned() -> int:
    """Repair a running transfer whose matching or finishing chain was interrupted."""
    # Select and insert in one statement so a concurrent worker cannot advance the chain between them.
    orphaned = select(literal("import_resolve"), func.jsonb_build_object("import_id", Import.id), literal(PRIORITY_IMPORT),
                      literal(utcnow()), literal(get_settings().cfg.jobs.max_attempts)).where(
        Import.status == "running", ~_active_transfer_jobs())
    async with session_scope() as s:
        recovered = list(await s.scalars(insert(Job).from_select(
            ["kind", "payload_json", "priority", "run_after", "max_attempts"], orphaned,
        ).returning(Job.id)))
    if recovered:
        notify()
    return len(recovered)


async def recovery_loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            if n := await resume_orphaned():
                log.info("resumed %s interrupted transfers", n)
        except Exception:
            log.exception("checking for interrupted transfers failed")
        try:
            if n := await drop_failed_attempts():
                log.info("let go %s transfers that failed before they started", n)
        except Exception:
            log.exception("letting go failed transfers failed")


# --- creating an import ------------------------------------------------------------------------------------


VK_AUTH_S = 15 * 60  # how long a sign-in to VK waits for its code; the password is kept (encrypted) only so long


def _vk_asks(imp: Import, e: Exception, *, login: str, password: str) -> None:
    """VK wants a code or its captcha passed: the import waits for the user's answer (`answer_auth`)."""
    was = imp.auth_json or {}
    captcha = isinstance(e, vk.NeedCaptcha)
    imp.status = "auth"
    imp.auth_json = {
        "kind": "captcha" if captcha else "code",
        "hint": "ВК просит подтвердить, что вы не робот" if captcha else e.hint,
        "verification_url": e.url if captcha else "",
        "wrong": bool(getattr(e, "wrong", False)),
        "login": encrypt(login), "password": encrypt(password),
        "expires_at": was.get("expires_at") or (utcnow() + timedelta(seconds=VK_AUTH_S)).isoformat(),
    }


async def _create_vk(s: AsyncSession, user_id: int, *, method: str, token: str | None, login: str | None,
                     password: str | None) -> Import:
    imp = Import(user_id=user_id, source="vk", method=method, status="fetching", auth_json={}, options_json={})
    if method == "token":
        if not (token or "").strip():
            raise SourceError("Вставьте токен")
        await integrations.set_import_credential(s, imp, token.strip())
    elif method == "password":
        login, password = (login or "").strip(), password or ""
        if not login or not password:
            raise SourceError("Введите логин и пароль от ВК")
        try:
            await integrations.set_import_credential(s, imp, await vk.sign_in(login, password))
        except (vk.NeedCode, vk.NeedCaptcha) as e:
            _vk_asks(imp, e, login=login, password=password)
    else:
        raise SourceError("Неизвестный способ")
    s.add(imp)
    await s.flush()
    if imp.status == "auth":  # ends the sign-in if its code never comes
        await enqueue(s, "import_auth", {"import_id": imp.id}, delay_s=VK_AUTH_S, priority=PRIORITY_ASKED)
    else:
        await enqueue(s, "import_fetch", {"import_id": imp.id}, priority=PRIORITY_ASKED)
    return imp


async def answer_auth(s: AsyncSession, imp: Import, *, code: str | None, captcha: str | None) -> None:
    """The user's answer to what VK asked at the sign-in: the code, or what its captcha page gave. The sign-in is made
    again with it; VK may ask once more (a wrong code, a captcha after the code)."""
    auth = dict(imp.auth_json or {})
    if imp.source != "vk" or imp.status != "auth" or not auth.get("login"):
        raise SourceError("Этот вход ничего не ждёт")
    if utcnow().isoformat() > auth["expires_at"]:
        imp.status, imp.error, imp.auth_json = "failed", "Вход не завершён вовремя — начните заново", {}
        return
    login, password = decrypt(auth["login"]), decrypt(auth["password"])
    try:
        token = await vk.sign_in(login, password, code=(code or "").strip() or None,
                                 captcha=(captcha or "").strip() or None)
    except (vk.NeedCode, vk.NeedCaptcha) as e:
        _vk_asks(imp, e, login=login, password=password)
        return
    except SourceError as e:
        imp.status, imp.error, imp.auth_json = "failed", str(e), {}
        return
    await integrations.set_import_credential(s, imp, token)
    imp.auth_json, imp.status = {}, "fetching"
    await enqueue(s, "import_fetch", {"import_id": imp.id}, priority=PRIORITY_ASKED)


async def create(s: AsyncSession, user_id: int, *, source: str, method: str, token: str | None = None,
                 url: str | None = None, login: str | None = None, password: str | None = None,
                 connection_id: int | None = None) -> Import:
    if method == "saved":
        row = await s.get(IntegrationConnection, connection_id) if connection_id else None
        if (source not in {"spotify", "yandex", "vk"} or row is None or row.provider != source
                or row.owner_user_id != user_id or not row.retained or row.state == "reauth"):
            raise SourceError("Сохранённый вход не найден")
        data = {"url": spotify.link_target(url.strip())} if source == "spotify" and url and url.strip() else {}
        imp = Import(user_id=user_id, source=source, method=method, status="fetching", account=row.account,
                     connection_id=row.id, keep_token=True, auth_json=data, options_json={})
        s.add(imp)
        await s.flush()
        await enqueue(s, "import_fetch", {"import_id": imp.id}, priority=PRIORITY_ASKED)
        return imp
    if source == "vk":
        return await _create_vk(s, user_id, method=method, token=token, login=login, password=password)
    if source not in ("yandex", "spotify") or (source == "spotify" and method != "link"):
        raise SourceError("Этот сервис пока не поддерживается")
    imp = Import(user_id=user_id, source=source, method=method, status="fetching", auth_json={}, options_json={})
    if method == "device":
        s.add(imp)
        await s.flush()
        code = await yandex.device_start(f"homemusic-{user_id}-{imp.id}")
        expires = utcnow() + timedelta(seconds=int(code.get("expires_in") or 300))
        imp.status = "auth"
        imp.auth_json = {"device_code": encrypt(code["device_code"]), "user_code": code["user_code"],
                         "verification_url": code.get("verification_url") or "https://ya.ru/device",
                         "interval": int(code.get("interval") or 5), "expires_at": expires.isoformat()}
        await enqueue(s, "import_auth", {"import_id": imp.id}, delay_s=imp.auth_json["interval"],
                      priority=PRIORITY_ASKED)
        return imp
    if method == "token":
        if not (token or "").strip():
            raise SourceError("Вставьте токен")
        await integrations.set_import_credential(s, imp, token.strip())
    elif method == "profile":
        imp.account = yandex.profile_login(url or "")
    elif method == "link":  # one open playlist, read by its link: «Подтянуть новое» reads it again
        link = (url or "").strip()
        (spotify if source == "spotify" else yandex).link_target(link)
        imp.auth_json = {"url": link}
    else:
        raise SourceError("Неизвестный способ")
    s.add(imp)
    await s.flush()
    await enqueue(s, "import_fetch", {"import_id": imp.id}, priority=PRIORITY_ASKED)
    return imp


@handler("import_auth")
async def handle_auth(job: Job) -> None:
    import_id = int(job.payload_json["import_id"])
    async with session_scope() as s:
        imp = await s.get(Import, import_id)
        if imp is None or imp.status != "auth":
            return
        auth = dict(imp.auth_json or {})
        source = imp.source
    if source == "vk":  # its code is typed in the Hub: nothing to ask VK about, only the time to watch
        if utcnow().isoformat() > auth.get("expires_at", ""):
            await set_status(import_id, "failed", error="Вход не завершён вовремя — начните заново", auth_json={})
            return
        raise RetryLater(60, "vk sign-in waits for the user")
    try:
        token = await yandex.device_poll(decrypt(auth["device_code"]))
    except AuthPending as e:
        if utcnow().isoformat() > auth["expires_at"]:
            await set_status(import_id, "failed", error="Код входа устарел — получите новый", auth_json={})
            return
        raise RetryLater(auth.get("interval") or 5, str(e)) from e
    except SourceError as e:
        await set_status(import_id, "failed", error=str(e), auth_json={})
        return
    async with session_scope() as s:
        imp = await s.get(Import, import_id)
        if imp is None or imp.status != "auth":
            return
        await integrations.set_import_credential(s, imp, token)
        imp.auth_json = {}
        imp.status = "fetching"
        await enqueue(s, "import_fetch", {"import_id": imp.id}, priority=PRIORITY_ASKED)
    notify()
    publish(imp)


# --- fetching ----------------------------------------------------------------------------------------------


async def fetch_snapshot(imp: Import) -> Snapshot:
    token = None
    if imp.connection_id is not None:
        async with session_scope() as s:
            connection = await integrations.import_connection(s, imp)
            if connection:
                token = await integrations.credential(s, connection.id)
    if imp.source == "vk":
        if not token:
            raise SourceError("Вход в ВК не сохранён — войдите заново")
        return await vk.fetch(token)
    if imp.source == "spotify" and imp.method == "saved":
        if not token:
            raise SourceError("Вход в Spotify не сохранён — войдите заново")
        return await spotify.fetch_account(token, (imp.auth_json or {}).get("url"))
    if imp.method == "link":
        url = (imp.auth_json or {}).get("url") or ""
        if imp.source == "spotify":
            return await spotify.fetch_link(url, fresh=imp.started_at is not None)
        return await asyncio.to_thread(yandex.fetch_link, url)
    login = imp.account if imp.method == "profile" else None
    if login is None and not token:
        raise SourceError("Вход в Яндекс Музыку не сохранён — войдите заново")
    return await asyncio.to_thread(yandex.fetch, token, login)


async def store_snapshot(s: AsyncSession, imp: Import, snap: Snapshot) -> int:
    """Collections and items of the snapshot; a repeated fetch (sync) keeps what it had, adds what is new, follows
    the new order and drops what left the service. Returns the number of new rows."""
    if snap.account and imp.method != "profile":
        imp.account = snap.account
    if connection := await integrations.import_connection(s, imp):
        connection.account = snap.account or connection.account
        connection.plan = snap.plan if snap.plan is not None else connection.plan
        connection.checked_at = utcnow()
        connection.state = "connected"
        if snap.account_subject:
            imp.options_json = {**(imp.options_json or {}), "account_subject": snap.account_subject}
    colls = {c.external_id: c for c in (await s.execute(
        select(ImportCollection).where(ImportCollection.import_id == imp.id))).scalars()}
    fresh = 0
    for pos, sc in enumerate(snap.collections):
        coll = colls.get(sc.external_id)
        if coll is None:
            kind = "playlist" if sc.kind == "likes" and (imp.options_json or {}).get("copy_likes") is False else sc.kind
            coll = ImportCollection(import_id=imp.id, kind=kind, external_id=sc.external_id, title=sc.title,
                                    selected=sc.selected)
            s.add(coll)
            await s.flush()
        coll.title, coll.position, coll.cover_url = sc.title, pos, sc.cover_url
        old = {i.external_id: i for i in (await s.execute(
            select(ImportItem).where(ImportItem.collection_id == coll.id))).scalars()}
        seen: set[str] = set()
        for ipos, si in enumerate(sc.items):
            seen.add(si.external_id)
            it = old.get(si.external_id)
            if it is None:
                it = ImportItem(import_id=imp.id, collection_id=coll.id, external_id=si.external_id, raw_json=si.raw,
                                status="pending")
                s.add(it)
                fresh += 1
                if sc.kind in ("likes", "playlist", "dislikes"):
                    lib = await find_owned(s, artist=si.raw.get("artist") or "", title=si.raw.get("title") or "",
                                              duration_s=si.raw.get("duration_s"))
                    if lib is not None:
                        it.match, it.track_id, it.score = "owned", lib.id, 1.0
            elif it.status == "skipped" and (it.raw_json or {}).get("removed"):
                it.status, it.raw_json = "pending", si.raw
            it.position = ipos
        for ext, it in old.items():
            if ext not in seen and it.status != "skipped":
                it.status, it.raw_json = "skipped", {**(it.raw_json or {}), "removed": True}
    await s.flush()
    return fresh


FETCH_LIMIT_S = 10 * 60
FAILED_KEPT_S = 15 * 60  # a sign-in or a reading that failed before anything was moved: shown so long, then gone


async def drop_failed_attempts() -> int:
    """Imports that failed before they started (a sign-in refused or expired, a collection not read) leave: they
    moved nothing, and «Начать заново» is the service's own page."""
    async with session_scope() as s:
        old = list(await s.scalars(select(Import).where(
            Import.status == "failed", Import.started_at.is_(None),
            Import.updated_at < utcnow() - timedelta(seconds=FAILED_KEPT_S))))
        for imp in old:
            await remove(s, imp)
    return len(old)


@handler("import_fetch")
async def handle_fetch(job: Job) -> None:
    import_id = int(job.payload_json["import_id"])
    async with session_scope() as s:
        imp = await s.get(Import, import_id)
        if imp is None or imp.status not in ("fetching", "running", "done"):
            return
    try:
        # a collection read for longer than this is not waited for: the import says so and can be begun again
        snap = await asyncio.wait_for(fetch_snapshot(imp), FETCH_LIMIT_S)
    except TimeoutError:
        await set_status(import_id, "failed" if imp.started_at is None else imp.status,
                         error=f"Коллекцию не удалось прочитать за {FETCH_LIMIT_S // 60} минут — попробуйте ещё раз")
        return
    except SourceError as e:
        await set_status(import_id, "failed" if imp.started_at is None else imp.status, error=str(e))
        return
    async with session_scope() as s:
        imp = await s.get(Import, import_id)
        if imp is None:
            return
        fresh = await store_snapshot(s, imp, snap)
        imp.error = None
        if imp.started_at is None:
            imp.status = "preview"
        else:  # «Обновить»: the new rows go the way the first ones went
            imp.status, imp.finished_at = "running", None
            await enqueue(s, "import_resolve", {"import_id": imp.id}, priority=PRIORITY_IMPORT)
    log.info("import %s: fetched %s collections, %s new rows", import_id, len(snap.collections), fresh)
    notify()
    publish(imp)


@on_final_failure("import_fetch")
async def _fetch_failed(job: Job, exc: Exception) -> None:
    await set_status(int(job.payload_json["import_id"]), "failed", error=f"Не удалось прочитать коллекцию: {exc}"[:500])


@on_final_failure("import_resolve")
@on_final_failure("import_finish")
async def _transfer_failed(job: Job, exc: Exception) -> None:
    async with session_scope() as s:
        imp = await s.get(Import, int(job.payload_json["import_id"]), with_for_update={"key_share": True})
        if imp is None or imp.status != "running":
            return
        imp.status = "failed"
        imp.error = "Перенос прервался после нескольких попыток"
        imp.finished_at = utcnow()
    publish(imp)


# --- start, matching ----------------------------------------------------------------------------------------


async def start(s: AsyncSession, imp: Import, *, collection_ids: list[int], mode: str, download_albums: bool,
                keep_token: bool, copy_likes: bool, extra: dict[str, Any] | None = None) -> None:
    if imp.status != "preview":
        raise PermanentError("Импорт уже запущен")
    selected = set(collection_ids)
    for c in (await s.execute(select(ImportCollection).where(ImportCollection.import_id == imp.id))).scalars():
        c.selected = c.id in selected
        if c.selected and c.kind == "likes" and not copy_likes:
            c.kind = "playlist"
    imp.mode = mode
    imp.keep_token = keep_token or imp.method == "saved"
    if imp.keep_token:
        await integrations.retain_import(s, imp)
    imp.options_json = {**(imp.options_json or {}), "download_albums": download_albums, "copy_likes": copy_likes,
                        "extra": extra or {}}  # «После переноса»: `apply.apply_extras`
    imp.status = "running"
    imp.started_at = utcnow()
    await enqueue(s, "import_resolve", {"import_id": imp.id}, priority=PRIORITY_IMPORT)


async def _next_batch(s: AsyncSession, imp: Import) -> list[tuple[ImportItem, ImportCollection]]:
    rows = (await s.execute(
        select(ImportItem, ImportCollection)
        .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
        .where(ImportItem.import_id == imp.id, ImportItem.status == "pending", ImportCollection.selected.is_(True))
    )).all()
    # likes from the oldest: each downloaded one then lands on top of «Любимое» (`apply.sync_likes`)
    rows = sorted(rows, key=lambda r: (KIND_ORDER.get(r[1].kind, 9), r[1].position,
                                       -r[0].position if r[1].kind == "likes" else r[0].position))
    return [(it, c) for it, c in rows[:RESOLVE_BATCH]]


@handler("import_resolve")
async def handle_resolve(job: Job) -> None:
    import_id = int(job.payload_json["import_id"])
    async with session_scope() as s:
        imp = await _running_transfer(s, import_id)
        if imp is None:
            return
        batch = await _next_batch(s, imp)
        ids = [(it.id, c.kind) for it, c in batch]
    sem = asyncio.Semaphore(RESOLVE_CONCURRENCY)
    down = False

    async def one(item_id: int, kind: str) -> tuple[bool, Resolution | None]:
        nonlocal down
        async with sem:
            async with session_scope() as s:
                it = await s.get(ImportItem, item_id)
                if it is None or it.status != "pending":
                    return False, None
                raw, external_id, owned = it.raw_json or {}, it.external_id, it.match == "owned" and bool(it.track_id)
            try:
                if kind == "albums":
                    r = await resolve_album(imp.source, external_id, raw)
                elif kind == "artists":
                    r = await resolve_artist(imp.source, external_id, raw)
                elif owned:
                    return True, None
                else:
                    r = await resolve_track(imp.source, external_id, raw)
            except CatalogUnavailable as e:
                log.warning("import %s: catalog down: %s", import_id, e)
                down = True
                return False, None
            return True, r

    resolved = await asyncio.gather(*(one(i, k) for i, k in ids))
    async with session_scope() as s:
        imp = await _running_transfer(s, import_id, lock=True)
        if imp is None:
            return
        for (item_id, kind), (ready, resolution) in zip(ids, resolved, strict=True):
            if ready and (item := await s.get(ImportItem, item_id)) is not None and item.status == "pending":
                await apply.decide(s, imp, item, kind, resolution)
        await enqueue_once(s, "import_playlists", import_id, delay_s=0 if not down else PLAYLISTS_DELAY_S)
        if down:
            await enqueue(s, "import_resolve", {"import_id": import_id}, delay_s=60, priority=PRIORITY_IMPORT)
        elif await _next_batch(s, imp):
            await enqueue(s, "import_resolve", {"import_id": import_id}, priority=PRIORITY_IMPORT)
        else:
            await enqueue_once(s, "import_finish", import_id)
    notify()
    publish(imp)


@handler("import_playlists")
async def handle_playlists(job: Job) -> None:
    import_id = int(job.payload_json["import_id"])
    async with session_scope() as s:
        await s.execute(select(func.pg_advisory_xact_lock(PLAYLIST_UPDATE_LOCK, import_id)))
        imp = await s.get(Import, import_id)
        if imp is None or imp.status in ("failed", "preview"):  # a stopped import keeps what it has done in order
            return
        await apply.refresh_items(s, imp)
        await apply.sync_playlists(s, imp)
        await apply.apply_extras(s, imp)
        if await s.scalar(select(ImportCollection.id).where(
                ImportCollection.import_id == imp.id, ImportCollection.kind == "likes",
                ImportCollection.selected.is_(True)).limit(1)) is not None:
            # the general queue goes by priority, then by time: at the transfer's own priority the ♥ would wait
            # for every download queued before it
            await enqueue_once(s, "import_likes", import_id, priority=PRIORITY_LIKES)
    notify()
    publish(imp)


@handler("import_likes")
async def handle_likes(job: Job) -> None:
    async with session_scope() as s:
        imp = await s.get(Import, int(job.payload_json["import_id"]))
        if imp is None or imp.status in ("failed", "preview"):
            return
        await apply.sync_likes(s, imp)
    publish(imp)


@handler("import_finish")
async def handle_finish(job: Job) -> None:
    import_id = int(job.payload_json["import_id"])
    async with session_scope() as s:
        await s.execute(select(func.pg_advisory_xact_lock(PLAYLIST_UPDATE_LOCK, import_id)))
        imp = await _running_transfer(s, import_id)
        if imp is None:
            return
        await apply.refresh_items(s, imp)
        active = await apply.active_downloads(s, imp)
        if not active:
            await apply.sync_playlists(s, imp)
            await apply.apply_extras(s, imp)
    if active:
        raise RetryLater(FINISH_RECHECK_S, f"{active} downloads under way")
    async with session_scope() as s:
        imp = await _running_transfer(s, import_id)
        if imp is None:
            return
        await apply.sync_likes(s, imp)
        # sync_likes takes its own lock before updating the import, so check the transfer state after it returns.
        status = await s.scalar(select(Import.status).where(Import.id == import_id).with_for_update(key_share=True))
        if status == "running":
            await apply.star_albums_artists(s, imp)
            imp.status = "done"
            imp.finished_at = utcnow()
            if not imp.keep_token:
                await integrations.release_import(s, imp)
    if status == "paused":
        raise RetryLater(0, "transfer paused")
    if status != "running":
        return
    log.info("import %s done", import_id)
    publish(imp)


# --- the user's decisions -----------------------------------------------------------------------------------


async def download_all(s: AsyncSession, imp: Import) -> int:
    """Economy → full: what waits for a listen is downloaded now."""
    imp.mode = "full"
    rows = (await s.execute(
        select(ImportItem, ImportCollection).join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
        .where(ImportItem.import_id == imp.id, ImportItem.status == "waiting"))).all()
    n = 0
    for it, coll in rows:
        if coll.kind == "albums":
            if (imp.options_json or {}).get("download_albums"):
                n += await apply.queue_album(s, imp, it)
            continue
        if await apply.queue_track(s, imp, it, coll.kind):
            n += 1
    if imp.status == "done" and n:
        imp.status, imp.finished_at = "running", None
        await enqueue_once(s, "import_finish", imp.id)
    return n


async def listened(user_id: int, ref: str) -> None:
    """A track of an economy import was listened to: download it (the user's own work, not background)."""
    async with session_scope() as s:
        rows = (await s.execute(
            select(ImportItem, ImportCollection, Import)
            .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
            .join(Import, Import.id == ImportItem.import_id)
            .where(Import.user_id == user_id, ImportItem.ref == ref, ImportItem.status == "waiting")
        )).all()
        for it, coll, imp in rows:
            await apply.queue_track(s, imp, it, coll.kind, priority=0)
    if rows:
        notify()


async def downloads_done(s: AsyncSession, items: list[DownloadItem]) -> None:
    """Downloads of imports have finished: their playlists are rewritten soon (one rewrite for many)."""
    ids = {int(imp["id"]) for i in items if (imp := (i.query_json or {}).get("import"))}
    for import_id in ids:
        await enqueue_once(s, "import_playlists", import_id, delay_s=PLAYLISTS_DELAY_S)


async def remove(s: AsyncSession, imp: Import) -> None:
    """The import and its rows go; the music, the ♥ and the playlists stay. Its queued downloads are cancelled."""
    from app.favorites import project_liked

    has_likes = await s.scalar(select(ImportCollection.id).where(
        ImportCollection.import_id == imp.id, ImportCollection.kind == "likes",
        ImportCollection.selected.is_(True)).limit(1))
    if imp.started_at is not None and has_likes is not None:
        await project_liked(s, imp.user_id, preserve_import_id=imp.id)
    await apply.cancel_downloads(s, imp)
    await integrations.release_import(s, imp)
    await links.collect(s)  # the service ids of its tracks stay with the tracks
    await s.execute(delete(ImportItem).where(ImportItem.import_id == imp.id))
    await s.execute(delete(ImportCollection).where(ImportCollection.import_id == imp.id))
    await s.delete(imp)


__all__ = ["ACTIVE", "create", "download_all", "downloads_done", "import_out", "item_out", "listened",
           "remove", "start"]
