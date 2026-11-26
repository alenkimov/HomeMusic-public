"""Yandex Music's API (`api.music.yandex.net`), asynchronous, through the Hub's own HTTP (`app.net.make_client`).

It stands for the `yandex-music` library, which is synchronous (`requests`): every call held a thread of the Hub's
pool for its second or more, beside ffmpeg and the files' work, and Yandex had no limit of its own here. Now a call
is a coroutine, goes through the proxy of the account or of Yandex and into the journal (`app.outbound`), at most
`RATE_PER_S` a second, with a timeout, retried on a refusal of the moment (429, 5xx).

The answers are Yandex's JSON as it is, read as objects (`Obj`): `track.duration_ms`, `album.track_position` — the
names of the JSON in snake case, a missing one is None; nothing is refused for a field of a shape not expected (the
code reads what it needs). The methods are named as the library's were, so the code that read its objects reads
these the same way.

The errors (`YandexError`): `kind` — «not_found» (404, 400 of an id it does not know), «auth» (401/403: the token is
not accepted, or a closed library), «unavailable» (451: not served to this country — a proxy of Russia helps;
network trouble, 5xx after the retries), «limited» (429 after the retries), «bad_answer».
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import httpx

from app.net import RateLimiter, make_client, request_with_backoff

log = logging.getLogger(__name__)

BASE = "https://api.music.yandex.net"
HEADERS = {"X-Yandex-Music-Client": "YandexMusicAndroid/24023621", "Accept-Language": "ru", "User-Agent": "Yandex-Music-API"}
# the file of a track is signed by the desktop app's key, checked together with its own client header
FILE_SIGN_KEY = "kzqU4XhfCaY6B6JTHODeq5"
FILE_CLIENT = "YandexMusicDesktopAppWindows/5.95.0"
TIMEOUT_S = 20
RATE_PER_S = 5.0

_limiter = RateLimiter(RATE_PER_S)
_http: httpx.AsyncClient | None = None


class YandexError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind, self.status = kind, status


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = make_client(timeout=TIMEOUT_S, http2=False)
    return _http


async def aclose() -> None:
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None


# --- the answers as objects -------------------------------------------------------------------------------------

_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def _snake(key: str) -> str:
    return _CAMEL.sub("_", key).replace("-", "_").lower()


class Obj:
    """A JSON object of Yandex, read by attributes in snake case (`durationMs` → `duration_ms`); a missing one is
    None, nested objects and lists are read so too. `raw`: the JSON itself."""

    __slots__ = ("_d", "raw")

    def __init__(self, data: dict[str, Any]):
        self.raw = data
        self._d = {_snake(k): v for k, v in data.items()}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return wrap(self._d.get(name))

    def __repr__(self) -> str:
        return f"Obj({self.raw!r:.200})"


def wrap(value: Any) -> Any:
    if isinstance(value, dict):
        return Obj(value)
    if isinstance(value, list):
        return [wrap(v) for v in value]
    return value


def short_id(s: Any) -> str:
    """The id a list of tracks gives a track by (`id:albumId` where the album is said), as `tracks` takes it."""
    return f"{s.id}:{s.album_id}" if getattr(s, "album_id", None) else str(s.id)


# --- the client -------------------------------------------------------------------------------------------------

class YandexMusic:
    """Yandex Music for one token (None: without signing in). `connection_id`: the Hub's account the token is of —
    its requests are its (its proxy, its rows of the journal)."""

    def __init__(self, token: str | None = None, connection_id: int | None = None):
        self.token, self.connection_id = token, connection_id
        self.me: Any = None

    @property
    def account_uid(self) -> Any:
        return getattr(getattr(self.me, "account", None), "uid", None)

    async def _request(self, method: str, path: str, *, params: Any = None, data: Any = None,
                       headers: dict[str, str] | None = None) -> Any:
        from app.outbound import account

        h = {**HEADERS, **(headers or {})}
        if self.token:
            h["Authorization"] = f"OAuth {self.token}"
        with account(self.connection_id) if self.connection_id is not None else nullcontext():
            try:
                r = await request_with_backoff(_client(), method, BASE + path, limiter=_limiter, retries=2,
                                               base_delay=1.0, retry_statuses={429, 500, 502, 503, 504},
                                               params=params, data=data, headers=h)
            except httpx.HTTPError as e:
                raise YandexError("unavailable", f"Яндекс Музыка не ответила: {type(e).__name__}") from e
        return self._answer(r)

    @staticmethod
    def _answer(r: httpx.Response) -> Any:
        try:
            body = r.json()
        except ValueError:
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        said = (error.get("message") or error.get("name")) if isinstance(error, dict) else error
        if r.status_code >= 400 or error:
            status = r.status_code
            kind = ("not_found" if status in (400, 404) else "auth" if status in (401, 403)
                    else "limited" if status == 429 else "unavailable" if status == 451 or status >= 500
                    else "bad_answer")
            if status < 400:  # an error in a 200
                kind = "auth" if "auth" in str(said).lower() or "owner" in str(said).lower() else "bad_answer"
            raise YandexError(kind, f"Яндекс Музыка: {said or f'HTTP {status}'}", status)
        if not isinstance(body, dict):
            raise YandexError("bad_answer", "Яндекс Музыка ответила не JSON", r.status_code)
        return body["result"] if body.get("result") is not None else body

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return wrap(await self._request("GET", path, params=params))

    async def post(self, path: str, data: dict[str, Any]) -> Any:
        return wrap(await self._request("POST", path, data=data))

    # --- the account --------------------------------------------------------------------------------------------

    async def account_status(self) -> Any:
        return await self.get("/account/status")

    async def init(self) -> YandexMusic:
        """Who the token is of (`me`): what «the signed-in user» stands for in the methods that take a user."""
        self.me = await self.account_status()
        return self

    def _user(self, user_id: Any) -> Any:
        return user_id if user_id is not None else self.account_uid

    # --- the catalog --------------------------------------------------------------------------------------------

    async def search(self, text: str, type_: str = "all", page: int = 0, nocorrect: bool = False) -> Any:
        return await self.get("/search", {"text": text, "nocorrect": str(nocorrect), "type": type_, "page": page,
                                          "playlist-in-best": "True"})

    async def tracks(self, ids: list[str | int]) -> list[Any]:
        if not ids:
            return []
        return await self.post("/tracks", {"track-ids": [str(i) for i in ids], "with-positions": "True"}) or []

    async def albums_with_tracks(self, album_id: str | int) -> Any:
        return await self.get(f"/albums/{album_id}/with-tracks")

    async def artists_brief_info(self, artist_id: str | int) -> Any:
        return await self.get(f"/artists/{artist_id}/brief-info")

    async def artists_direct_albums(self, artist_id: str | int, page: int = 0, page_size: int = 20,
                                    sort_by: str = "year") -> Any:
        return await self.get(f"/artists/{artist_id}/direct-albums", {"sort-by": sort_by, "page": page,
                                                                       "page-size": page_size})

    async def tracks_similar(self, track_id: str | int) -> Any:
        return await self.get(f"/tracks/{track_id}/similar")

    async def playlist(self, playlist_uuid: str) -> Any:
        return await self.get(f"/playlist/{playlist_uuid}")

    async def rotor_station_tracks(self, station: str, queue: str | None = None) -> Any:
        return await self.get(f"/rotor/station/{station}/tracks", {"queue": queue} if queue else {"settings2": "True"})

    # --- a user's -----------------------------------------------------------------------------------------------

    async def users_playlists_list(self, user_id: Any = None) -> list[Any]:
        return await self.get(f"/users/{self._user(user_id)}/playlists/list") or []

    async def users_playlists(self, kind: str | int, user_id: Any = None) -> Any:
        return await self.get(f"/users/{self._user(user_id)}/playlists/{kind}")

    async def users_likes_tracks(self, user_id: Any = None) -> Any:
        got = await self.get(f"/users/{self._user(user_id)}/likes/tracks", {"if-modified-since-revision": 0})
        return getattr(got, "library", None)

    async def users_dislikes_tracks(self, user_id: Any = None) -> Any:
        got = await self.get(f"/users/{self._user(user_id)}/dislikes/tracks", {"if_modified_since_revision": 0})
        return getattr(got, "library", None)

    async def users_likes_albums(self, user_id: Any = None) -> list[Any]:
        return await self.get(f"/users/{self._user(user_id)}/likes/albums", {"rich": "True"}) or []

    async def users_likes_artists(self, user_id: Any = None) -> list[Any]:
        """Each with `.artist` (Yandex gives a like with its time, or — some accounts — the artist alone)."""
        rows = await self._request("GET", f"/users/{self._user(user_id)}/likes/artists", params={"with-timestamps": "True"})
        return [wrap(x if "artist" in x else {"artist": x}) for x in rows or [] if isinstance(x, dict)]

    # --- the file of a track (an account with Plus) -------------------------------------------------------------

    async def tracks_file_info(self, track_id: str | int, quality: str = "lossless", codecs: list[str] | None = None,
                               transport: str = "raw") -> Any:
        codecs = codecs or ["flac", "flac-mp4", "aac-mp4", "he-aac-mp4", "aac", "he-aac", "mp3"]
        ts = int(time.time())
        message = f"{ts}{track_id}{quality}{''.join(codecs)}{transport}"
        sign = base64.b64encode(hmac.new(FILE_SIGN_KEY.encode(), message.encode(), hashlib.sha256).digest()).decode()[:-1]
        return wrap(await self._request("GET", "/get-file-info", params={
            "ts": ts, "trackId": track_id, "quality": quality, "codecs": ",".join(codecs), "transports": transport,
            "sign": sign}, headers={"X-Yandex-Music-Client": FILE_CLIENT}))

    async def download(self, url: str, dest: Path) -> int:
        """The file at `url` (of `tracks_file_info`, «raw»: not encrypted) into `dest`, streamed; its size."""
        from app.outbound import account

        size = 0
        with account(self.connection_id) if self.connection_id is not None else nullcontext():
            try:
                async with _client().stream("GET", url, timeout=httpx.Timeout(TIMEOUT_S, read=60)) as r:
                    if r.status_code != 200:
                        raise YandexError("unavailable", f"Яндекс Музыка: файл не отдан (HTTP {r.status_code})", r.status_code)
                    with dest.open("wb") as f:
                        async for chunk in r.aiter_bytes(256 * 1024):
                            f.write(chunk)
                            size += len(chunk)
            except httpx.HTTPError as e:
                raise YandexError("unavailable", f"Яндекс Музыка: файл не скачан: {type(e).__name__}") from e
        return size
