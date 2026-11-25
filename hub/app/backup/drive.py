"""Google Drive, as much of it as the backups need: signing in by a code, a folder, files in and out.

The Hub signs in the way a TV does (OAuth 2.0 for limited-input devices): it shows a code, the admin types it at
google.com/device and allows access — the Hub never sees the password. It asks for `drive.file` only: the files the
Hub itself made, nothing else of the Drive. What it keeps is a refresh token, from which it takes a short-lived
access token when it works.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from base64 import urlsafe_b64decode
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.net import make_client

log = logging.getLogger(__name__)

DEVICE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive.file openid email"
FOLDER = "application/vnd.google-apps.folder"
CHUNK = 8 * 1024 * 1024  # a multiple of 256 KiB, as a resumable upload wants


class DriveError(Exception):
    """Google answered with an error, or could not be reached."""


class Unauthorized(DriveError):
    """The access was taken back (or the token is no good): the admin connects the Drive again."""


def configured() -> bool:
    s = get_settings()
    return bool(s.google_drive_client_id and s.google_drive_client_secret)


def _client(timeout: float = 60) -> httpx.AsyncClient:
    return make_client(timeout=timeout, http2=False)


def _email(id_token: str | None) -> str | None:
    """The account's address, from the token Google gives with the sign-in (not checked: it is Google's own answer
    to us over TLS, and only names the account on the page)."""
    try:
        body = (id_token or "").split(".")[1]
        return json.loads(urlsafe_b64decode(body + "=" * (-len(body) % 4))).get("email")
    except (IndexError, ValueError):
        return None


# ---------------------------------------------------------------- signing in


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    url: str
    interval: int
    expires_in: int


@dataclass
class Grant:
    refresh_token: str
    email: str | None


async def device_start() -> DeviceCode:
    s = get_settings()
    async with _client(30) as c:
        r = await c.post(DEVICE_URL, data={"client_id": s.google_drive_client_id, "scope": SCOPE})
    if r.status_code != 200:
        raise DriveError(f"Google не выдал код входа: {r.text[:200]}")
    d = r.json()
    return DeviceCode(d["device_code"], d["user_code"], d.get("verification_url") or "https://www.google.com/device",
                      int(d.get("interval") or 5), int(d.get("expires_in") or 1800))


async def device_poll(device_code: str) -> Grant | None:
    """The grant once the admin allowed access; None while they have not yet. `DriveError`: refused or expired."""
    s = get_settings()
    async with _client(30) as c:
        r = await c.post(TOKEN_URL, data={
            "client_id": s.google_drive_client_id, "client_secret": s.google_drive_client_secret,
            "device_code": device_code, "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code == 200 and d.get("refresh_token"):
        return Grant(d["refresh_token"], _email(d.get("id_token")))
    error = d.get("error")
    if error in ("authorization_pending", "slow_down"):
        return None
    if error == "access_denied":
        raise DriveError("Доступ не разрешён")
    if error == "expired_token":
        raise DriveError("Код устарел — начните заново")
    raise DriveError(f"Google не принял вход: {error or r.status_code}")


async def revoke(refresh_token: str) -> None:
    try:
        async with _client(15) as c:
            await c.post(REVOKE_URL, data={"token": refresh_token})
    except httpx.HTTPError:
        pass  # forgotten here anyway; the admin can take it back in their Google account too


# ---------------------------------------------------------------- the Drive


@dataclass
class DriveFile:
    id: str
    name: str
    size: int
    created: str  # RFC 3339


class Drive:
    """The Hub's own files of a Drive (`drive.file`), signed in by a refresh token."""

    def __init__(self, refresh_token: str) -> None:
        self.refresh_token = refresh_token
        self._access: str | None = None
        self._until = 0.0
        self._http = _client(300)

    async def __aenter__(self) -> Drive:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def _token(self) -> str:
        if self._access and time.monotonic() < self._until:
            return self._access
        s = get_settings()
        r = await self._http.post(TOKEN_URL, data={
            "client_id": s.google_drive_client_id, "client_secret": s.google_drive_client_secret,
            "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
        if r.status_code in (400, 401):
            raise Unauthorized("Доступ к Google Drive отозван — подключите его заново")
        if r.status_code != 200:
            raise DriveError(f"Google не выдал доступ: HTTP {r.status_code}")
        d = r.json()
        self._access, self._until = d["access_token"], time.monotonic() + int(d.get("expires_in") or 3600) - 60
        return self._access

    async def _call(self, method: str, url: str, **kw: Any) -> httpx.Response:
        for attempt in range(4):
            headers = {"Authorization": f"Bearer {await self._token()}", **kw.pop("headers", {})}
            try:
                r = await self._http.request(method, url, headers=headers, **kw)
            except httpx.HTTPError as e:
                if attempt == 3:
                    raise DriveError(f"Нет связи с Google Drive: {e!r}") from e
                await _sleep(2 ** attempt)
                continue
            if r.status_code == 401:
                self._access = None
                if attempt:
                    raise Unauthorized("Доступ к Google Drive отозван — подключите его заново")
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                await _sleep(2 ** attempt)
                continue
            if r.status_code >= 400 and r.status_code != 308:
                raise DriveError(f"Google Drive: HTTP {r.status_code} {r.text[:200]}")
            return r
        raise DriveError("Google Drive не отвечает")

    async def about(self) -> dict[str, Any]:
        """The account and its space: {"email", "used", "limit"} (bytes; limit None — without a limit)."""
        d = (await self._call("GET", f"{API}/about", params={"fields": "user(emailAddress),storageQuota(usage,limit)"})).json()
        q = d.get("storageQuota") or {}
        return {"email": (d.get("user") or {}).get("emailAddress"), "used": int(q.get("usage") or 0),
                "limit": int(q["limit"]) if q.get("limit") else None}

    async def folder(self, name: str, parent: str | None = None) -> str:
        """The folder of that name (in `parent`, else at the top), made if there is none."""
        q = f"name = '{_quote(name)}' and mimeType = '{FOLDER}' and trashed = false"
        q += f" and '{parent}' in parents" if parent else " and 'root' in parents"
        found = (await self._call("GET", f"{API}/files", params={"q": q, "fields": "files(id)", "spaces": "drive"})).json()
        if found.get("files"):
            return found["files"][0]["id"]
        meta = {"name": name, "mimeType": FOLDER, **({"parents": [parent]} if parent else {})}
        return (await self._call("POST", f"{API}/files", json=meta, params={"fields": "id"})).json()["id"]

    async def list(self, folder: str) -> list[DriveFile]:
        out: list[DriveFile] = []
        token = None
        while True:
            params = {"q": f"'{folder}' in parents and trashed = false", "pageSize": 1000,
                      "fields": "nextPageToken,files(id,name,size,createdTime)", "spaces": "drive"}
            if token:
                params["pageToken"] = token
            d = (await self._call("GET", f"{API}/files", params=params)).json()
            out += [DriveFile(f["id"], f["name"], int(f.get("size") or 0), f.get("createdTime") or "") for f in d.get("files") or []]
            token = d.get("nextPageToken")
            if not token:
                return out

    async def upload(self, path: Path, name: str, folder: str) -> DriveFile:
        """A file into the folder (a resumable upload, a chunk at a time: a big file survives a dropped chunk)."""
        size = (await asyncio.to_thread(path.stat)).st_size
        r = await self._call("POST", UPLOAD, params={"uploadType": "resumable", "fields": "id,name,size,createdTime"},
                             json={"name": name, "parents": [folder]},
                             headers={"X-Upload-Content-Length": str(size)})
        session = r.headers["Location"]
        f = await asyncio.to_thread(open, path, "rb")
        with f:
            offset = 0
            while True:
                chunk = await asyncio.to_thread(f.read, CHUNK)
                end = offset + len(chunk) - 1
                rng = f"bytes {offset}-{end}/{size}" if chunk else f"bytes */{size}"
                r = await self._call("PUT", session, content=chunk, headers={"Content-Range": rng})
                if r.status_code in (200, 201):
                    d = r.json()
                    return DriveFile(d["id"], d["name"], int(d.get("size") or size), d.get("createdTime") or "")
                offset = end + 1
                if not chunk:
                    raise DriveError("Google Drive не принял файл целиком")

    async def download(self, file_id: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        async for _ in self._stream(file_id, part):
            pass
        part.replace(dest)

    async def _stream(self, file_id: str, dest: Path) -> AsyncIterator[int]:
        headers = {"Authorization": f"Bearer {await self._token()}"}
        async with self._http.stream("GET", f"{API}/files/{file_id}", params={"alt": "media"}, headers=headers) as r:
            if r.status_code >= 400:
                raise DriveError(f"Google Drive не отдал файл: HTTP {r.status_code}")
            f = await asyncio.to_thread(open, dest, "wb")
            try:
                async for chunk in r.aiter_bytes(CHUNK):
                    await asyncio.to_thread(f.write, chunk)
                    yield len(chunk)
            finally:
                f.close()

    async def delete(self, file_id: str) -> None:
        try:
            await self._call("DELETE", f"{API}/files/{file_id}")
        except DriveError as e:
            if "HTTP 404" not in str(e):
                raise


def _quote(name: str) -> str:
    return name.replace("\\", "\\\\").replace("'", "\\'")


async def _sleep(s: float) -> None:
    await asyncio.sleep(s)
