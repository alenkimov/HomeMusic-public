"""Backups on Google Drive: connecting by a code, the nightly copy, what is kept, and a restore of the files."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from itertools import count

import httpx
import pytest
import respx

from tests.support import filed_track
from tests.test_flow import drain_jobs
from tests.test_flow import env as env  # the fixture
from tests.test_flow import src_mp3 as src_mp3  # its file


class FakeDrive:
    """Google's sign-in and the part of the Drive API the Hub uses, in memory."""

    def __init__(self) -> None:
        self.files: dict[str, dict] = {}  # id -> {name, parents, mime, data, created}
        self.ids = count(1)
        self.allowed = False
        self.sessions: dict[str, dict] = {}
        self.uploads = 0
        self.revoked = False

    def add(self, name: str, parent: str | None, data: bytes = b"", mime: str = "") -> str:
        fid = f"f{next(self.ids)}"
        self.files[fid] = {"name": name, "parents": [parent or "root"], "mime": mime, "data": data,
                           "created": f"2026-01-01T00:00:{len(self.files):02d}Z"}
        return fid

    def named(self, name: str) -> list[dict]:
        return [f for f in self.files.values() if f["name"] == name]

    def children(self, parent: str) -> list[str]:
        return sorted(f["name"] for f in self.files.values() if parent in f["parents"])

    def folder_id(self, name: str) -> str:
        return next(i for i, f in self.files.items() if f["name"] == name and f["mime"].endswith("folder"))

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, path = request.url, request.url.path
        if path == "/device/code":
            return httpx.Response(200, json={"device_code": "dev-1", "user_code": "ABCD-EFGH",
                                             "verification_url": "https://www.google.com/device",
                                             "interval": 5, "expires_in": 1800})
        if path == "/token":
            if self.revoked:
                return httpx.Response(400, json={"error": "invalid_grant"})
            form = dict(httpx.QueryParams(request.content.decode()))
            if form.get("grant_type") == "refresh_token":
                return httpx.Response(200, json={"access_token": "acc", "expires_in": 3600})
            if not self.allowed:
                return httpx.Response(428, json={"error": "authorization_pending"})
            payload = json.dumps({"email": "me@gmail.com"}).encode()
            import base64

            idt = "x." + base64.urlsafe_b64encode(payload).decode().rstrip("=") + ".y"
            return httpx.Response(200, json={"access_token": "acc", "refresh_token": "refresh-1", "id_token": idt})
        if path == "/revoke":
            return httpx.Response(200)
        if path == "/drive/v3/about":
            used = sum(len(f["data"]) for f in self.files.values())
            return httpx.Response(200, json={"user": {"emailAddress": "me@gmail.com"},
                                             "storageQuota": {"usage": str(used), "limit": str(15 * 2**30)}})
        if path == "/drive/v3/files" and request.method == "GET":
            q = url.params["q"]
            parent = re.search(r"'([^']+)' in parents", q).group(1)
            name = m.group(1) if (m := re.search(r"name = '([^']+)'", q)) else None
            hits = [{"id": i, "name": f["name"], "size": str(len(f["data"])), "createdTime": f["created"]}
                    for i, f in self.files.items()
                    if parent in f["parents"] and (name is None or f["name"] == name)
                    and ("mimeType" not in q or f["mime"].endswith("folder"))]
            return httpx.Response(200, json={"files": hits})
        if path == "/drive/v3/files" and request.method == "POST":
            meta = json.loads(request.content)
            fid = self.add(meta["name"], (meta.get("parents") or [None])[0], mime=meta["mimeType"])
            return httpx.Response(200, json={"id": fid})
        if path == "/upload/drive/v3/files":
            meta = json.loads(request.content)
            sid = f"s{len(self.sessions)}"
            self.sessions[sid] = {"meta": meta, "data": b""}
            return httpx.Response(200, headers={"Location": f"https://www.googleapis.com/upload/session/{sid}"})
        if path.startswith("/upload/session/"):
            sess = self.sessions[path.rsplit("/", 1)[1]]
            sess["data"] += request.content
            total = int(request.headers["Content-Range"].rsplit("/", 1)[1])
            if len(sess["data"]) < total:
                return httpx.Response(308)
            self.uploads += 1
            fid = self.add(sess["meta"]["name"], sess["meta"]["parents"][0], sess["data"])
            return httpx.Response(200, json={"id": fid, "name": sess["meta"]["name"], "size": str(total),
                                             "createdTime": "2026-01-01T00:00:00Z"})
        if path.startswith("/drive/v3/files/"):
            fid = path.rsplit("/", 1)[1]
            if request.method == "DELETE":
                return httpx.Response(204 if self.files.pop(fid, None) else 404)
            if fid not in self.files:
                return httpx.Response(404)
            return httpx.Response(200, content=self.files[fid]["data"])
        return httpx.Response(404)


@pytest.fixture
def gdrive(env, monkeypatch, tmp_path):
    from app import housekeeping
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "google_drive_client_id", "cid")
    monkeypatch.setattr(s, "google_drive_client_secret", "secret")
    fake = FakeDrive()
    dumps = s.backups_dir / "hub"
    dumps.mkdir(parents=True, exist_ok=True)

    def backup_hub_db():  # pg_dump's place: a file of the day
        from datetime import datetime

        p = dumps / f"hub-{datetime.now():%Y%m%d}.dump"
        p.write_bytes(b"PGDMP fake dump")
        return p

    monkeypatch.setattr(housekeeping, "backup_hub_db", backup_hub_db)
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="navidrome.test").pass_through()
        for host in ("oauth2.googleapis.com", "www.googleapis.com"):
            mock.route(host=host).mock(side_effect=fake.handler)
        yield fake


async def _connect(c, fake: FakeDrive) -> dict:
    r = await c.post("/api/backup/connect")
    assert r.status_code == 200 and r.json()["user_code"] == "ABCD-EFGH", r.text
    assert (await c.post("/api/backup/connect/poll")).json()["state"] == "pending"
    fake.allowed = True
    r = (await c.post("/api/backup/connect/poll")).json()
    assert r["state"] == "connected" and r["backup"]["email"] == "me@gmail.com", r
    return r


async def _uploaded_track(name: str = "mine.mp3") -> tuple[int, str]:
    from app.db import session_scope
    from app.library.importer import library_abs

    p = library_abs(f"Me/{name}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"my own recording")
    async with session_scope() as s:
        t = filed_track(artist="Me", title="Mine", fingerprint="x", path=f"Me/{name}", source="upload")
        s.add(t)
        await s.flush()
        return t.id, t.files[0].uid


def test_seven_days_and_four_weeks_are_kept():
    from app.backup.service import keep

    days = [date(2026, 11, 26) - timedelta(days=i) for i in range(60)]
    kept = sorted(keep(days))
    assert kept[-7:] == days[:7][::-1]
    assert kept[:-7] == [date(2026, 10, 26), date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 16)]  # Mondays


async def test_without_a_client_in_env_the_page_says_so(env):
    c = env["client"]
    b = (await c.get("/api/backup")).json()
    assert (b["configured"], b["connected"]) == (False, False)
    assert (await c.post("/api/backup/connect")).status_code == 400


async def test_connected_the_database_and_the_own_files_go_to_the_drive_once(gdrive, env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import BackupFile

    c = env["client"]
    _tid, uid = await _uploaded_track()
    await _connect(c, gdrive)
    await drain_jobs()  # connecting queues the first copy
    b = (await c.get("/api/backup")).json()
    assert b["last_ok"] and not b["error"] and not b["stale"] and b["files_count"] == 1, b
    root = gdrive.folder_id("HomeMusic backups")
    assert gdrive.children(root) == ["config.yaml", "database", "files"] or gdrive.children(root) == ["database", "files"]
    assert gdrive.children(gdrive.folder_id("database")) == [f"hub-{date.today().isoformat()}.dump"]
    assert gdrive.children(gdrive.folder_id("files")) == [f"upload-{uid}.mp3"]
    assert gdrive.named(f"upload-{uid}.mp3")[0]["data"] == b"my own recording"
    async with session_scope() as s:
        assert [x.key for x in (await s.execute(select(BackupFile))).scalars()] == [f"upload:{uid}"]

    # again the same day: the day's copy is replaced, the file is not sent twice
    sent = gdrive.uploads
    r = await c.post("/api/backup/run")
    assert r.status_code == 200 and r.json()["running"]
    await drain_jobs()
    assert gdrive.children(gdrive.folder_id("database")) == [f"hub-{date.today().isoformat()}.dump"]
    assert gdrive.uploads - sent <= 2  # the dump (and config.yaml), not the file


async def test_old_copies_go_by_the_rule(gdrive, env):
    c = env["client"]
    await _connect(c, gdrive)
    await drain_jobs()
    db = gdrive.folder_id("database")
    for i in range(1, 40):
        gdrive.add(f"hub-{(date.today() - timedelta(days=i)).isoformat()}.dump", db, b"old")
    await c.post("/api/backup/run")
    await drain_jobs()
    assert len(gdrive.children(db)) == 11


async def test_a_revoked_access_is_said_and_not_retried(gdrive, env, monkeypatch):
    c = env["client"]
    await _connect(c, gdrive)
    await drain_jobs()
    gdrive.revoked = True
    await c.post("/api/backup/run")
    await drain_jobs()
    b = (await c.get("/api/backup")).json()
    assert "отозван" in b["error"] and not b["running"]


async def test_a_restore_puts_the_own_files_back_and_queues_the_downloaded_ones(gdrive, env):
    from sqlalchemy import select

    from app.backup import drive, restore
    from app.db import session_scope
    from app.library.importer import library_abs
    from app.models import DownloadItem, Job, TrackFile

    c = env["client"]
    mine, _uid = await _uploaded_track()
    async with session_scope() as s:  # a downloaded track, whose file the new server has not
        t = filed_track(artist="Кино", title="Группа крови", fingerprint="x", path="Кино/x.mp3", source="hitmoz",
                        catalog={"ref": "dz:1", "source": "dz", "id": "1", "title": "Группа крови", "artist": "Кино"})
        s.add(t)
        await s.flush()
        downloaded = t.id
    await _connect(c, gdrive)
    await drain_jobs()
    library_abs("Me/mine.mp3").unlink()  # the server died

    async with drive.Drive("refresh-1") as d, session_scope() as s:
        _root, dumps = await restore.find_copies(d)
        assert [f.name for f in dumps] == [f"hub-{date.today().isoformat()}.dump"]
        assert await restore.put_back_files(s, d) == (1, 0)
    assert library_abs("Me/mine.mp3").read_bytes() == b"my own recording"
    async with session_scope() as s:
        assert await restore.queue_missing(s) == 1
    async with session_scope() as s:
        (item,) = (await s.execute(select(DownloadItem))).scalars()
        assert item.query_json["meta"]["track_id"] == downloaded and item.query_json["meta"]["track_ref"] == "dz:1"
        assert (await s.execute(select(Job.priority).where(Job.kind == "download_item"))).scalar_one() < 0
        assert (await s.execute(select(TrackFile.track_id))).scalars().all() == [mine]
