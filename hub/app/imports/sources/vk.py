"""VK: the user's music — «Моя музыка» (the whole list of what they added) and their playlists, their own and the
ones they follow.

VK has no public API for music: its `audio.*` methods answer only to VK's own apps. So the sign-in is made as the
official VK app for Android (its public client id, the way `vkpymusic` by issamansur does it): the login and the
password go to `oauth.vk.com/token` once and a token comes back. On the way VK may ask for a code (an SMS, a call,
the authenticator) — `NeedCode` — or for a captcha, a page of VK's the user has to pass — `NeedCaptcha`; the sign-in
is then repeated with the answer. A token got elsewhere can be pasted instead.

Only lists are read, never audio. A track is {artist, title, duration}: VK knows neither albums nor ISRC, so the
matching goes by the names. «Моя музыка» becomes a playlist «ВКонтакте», not ♥: it is everything ever added there.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.imports.sources.base import SnapCollection, SnapItem, Snapshot, SourceError
from app.net import make_client

OAUTH = "https://oauth.vk.com/token"
API = "https://api.vk.com/method/"
VERSION = "5.131"
# VK's own app for Android (public: shipped in the app, as vkpymusic's `VkMobileClient`)
CLIENT_ID = "2274003"
CLIENT_SECRET = "hHbZxrka2uZ6jB1inYsH"
USER_AGENT = "VKAndroidApp/4.13.1-1206 (Android 4.4.3; SDK 19; armeabi; ; ru)"
PAGE = 100  # what `audio.get` and `audio.getPlaylists` give at once
MAX_TRACKS = 10000  # of one list
MAX_PLAYLISTS = 500
PAUSE_S = 0.35  # VK lets a user's token three calls a second
MY_MUSIC = "ВКонтакте"


class NeedCode(Exception):
    """VK waits for a code: `hint` says where it was sent, `wrong` — the code just given did not fit."""

    def __init__(self, hint: str, *, wrong: bool = False) -> None:
        super().__init__(hint)
        self.hint = hint
        self.wrong = wrong


class NeedCaptcha(Exception):
    """VK wants its captcha passed: `url` is its page; what the page answers with (`success_token`) lets in."""

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.url = url


_CODE_HINTS = {
    "2fa_sms": "ВК отправил код в SMS",
    "sms": "ВК отправил код в SMS",
    "2fa_app": "Введите код из приложения для подтверждения входа",
    "2fa_callreset": "ВК позвонит: введите последние 4 цифры номера, с которого придёт звонок",
}


def _client():
    return make_client(http2=False, headers={"User-Agent": USER_AGENT})


async def sign_in(login: str, password: str, *, code: str | None = None, captcha: str | None = None) -> str:
    """The token of the account. Raises `NeedCode` / `NeedCaptcha` when VK asks for more, `SourceError` when it refuses."""
    params: dict[str, Any] = {
        "grant_type": "password", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "username": login,
        "password": password, "scope": "audio,offline", "2fa_supported": 1, "force_sms": 1, "v": VERSION,
    }
    if code:
        params["code"] = code
    if captcha:
        params["success_token"] = captcha
    try:
        async with _client() as c:
            body = (await c.post(OAUTH, params=params)).json()
            if body.get("error") == "need_validation" and body.get("validation_sid"):
                # asks VK to send the code (an SMS); what it answers does not matter here
                try:
                    await c.get(API + "auth.validatePhone", params={"sid": body["validation_sid"], "v": VERSION})
                except Exception:
                    pass
    except Exception as e:
        raise SourceError(f"ВК не ответил: {e}") from e
    if not isinstance(body, dict):
        raise SourceError("ВК ответил непонятно — попробуйте ещё раз")
    if body.get("access_token"):
        return str(body["access_token"])
    error, kind = str(body.get("error") or ""), str(body.get("error_type") or "")
    said = str(body.get("error_description") or "").strip()
    if error == "need_captcha":
        if not body.get("redirect_uri"):
            raise SourceError("ВК просит капчу, но не дал её страницу — попробуйте позже")
        raise NeedCaptcha(str(body["redirect_uri"]))
    if error == "need_validation":
        hint = _CODE_HINTS.get(str(body.get("validation_type") or ""), said or "ВК ждёт код подтверждения")
        if body.get("phone_mask"):
            hint += f" на {body['phone_mask']}"
        raise NeedCode(hint)
    if error == "invalid_request" and code:
        raise NeedCode("Код не подошёл — введите его ещё раз", wrong=True)
    if error == "invalid_client":
        raise SourceError("ВК не принял логин или пароль")
    if "flood" in error.lower() or kind == "password_bruteforce_attempt":
        raise SourceError("ВК временно закрыл вход: слишком много попыток. Подождите несколько часов")
    raise SourceError(f"ВК отказал во входе: {said or error or 'причина не названа'}")


async def _call(c, token: str, method: str, **params: Any) -> Any:
    """`response` of a method; a call VK held back for haste is made again."""
    for attempt in range(4):
        try:
            body = (await c.get(API + method, params={**params, "access_token": token, "v": VERSION})).json()
        except Exception as e:
            raise SourceError(f"ВК не ответил: {e}") from e
        err = body.get("error") if isinstance(body, dict) else None
        if not err:
            return body.get("response") if isinstance(body, dict) else None
        code = int(err.get("error_code") or 0) if isinstance(err, dict) else 0
        if code == 6 and attempt < 3:  # too many requests per second
            await asyncio.sleep(1 + attempt)
            continue
        if code == 5:
            raise SourceError("ВК больше не принимает этот вход — войдите заново")
        if code == 14:
            raise SourceError("ВК просит капчу — попробуйте позже")
        if code in (15, 201, 203):
            raise PermissionError(method)  # this list is closed to the account: left out
        said = err.get("error_msg") if isinstance(err, dict) else err
        raise SourceError(f"ВК отказал ({method}): {said}")
    raise SourceError(f"ВК не отвечает на {method}")


def track_row(a: dict[str, Any]) -> dict[str, Any] | None:
    """A track of VK as an import's row. VK's `subtitle` is the grey part of the name: «Remix», «prod. X»."""
    base = str(a.get("title") or "").strip()
    artist = str(a.get("artist") or "").strip()
    if not base or not artist:
        return None
    version = str(a.get("subtitle") or "").strip()
    named = [str(x.get("name") or "").strip() for x in (a.get("main_artists") or []) + (a.get("featured_artists") or [])
             if isinstance(x, dict)]
    artists = [n for n in dict.fromkeys(named) if n] or [artist]
    duration = a.get("duration")
    return {"artist": ", ".join(artists), "artists": artists, "title": f"{base} ({version})" if version else base,
            "base": base, "duration_s": int(duration) if duration else None}


async def _tracks(c, token: str, owner: int, *, playlist: int | None = None, key: str | None = None) -> list[SnapItem]:
    out: list[SnapItem] = []
    seen: set[str] = set()
    offset = 0
    while offset < MAX_TRACKS:
        params: dict[str, Any] = {"owner_id": owner, "count": PAGE, "offset": offset}
        if playlist is not None:
            params["album_id"] = playlist
            if key:
                params["access_key"] = key
        res = await _call(c, token, "audio.get", **params) or {}
        items = res.get("items") or []
        for a in items:
            row = track_row(a) if isinstance(a, dict) else None
            ext = f"{a.get('owner_id')}_{a.get('id')}" if isinstance(a, dict) else ""
            if row and ext not in seen:
                seen.add(ext)
                out.append(SnapItem(external_id=ext, raw=row))
        offset += PAGE
        if len(items) < PAGE or offset >= int(res.get("count") or 0):
            break
        await asyncio.sleep(PAUSE_S)
    return out


def _cover(p: dict[str, Any]) -> str | None:
    photo = p.get("photo") if isinstance(p.get("photo"), dict) else {}
    return next((str(photo[k]) for k in ("photo_600", "photo_300", "photo_1200", "photo_270") if photo.get(k)), None)


async def fetch(token: str) -> Snapshot:
    """«Моя музыка» as the playlist «ВКонтакте», then the account's playlists in VK's order."""
    async with _client() as c:
        me = (await _call(c, token, "users.get") or [{}])[0]
        if not me.get("id"):
            raise SourceError("ВК не сказал, чей это вход — войдите заново")
        owner = int(me["id"])
        snap = Snapshot(account=" ".join(x for x in (me.get("first_name"), me.get("last_name")) if x) or f"id{owner}")
        try:
            mine = await _tracks(c, token, owner)
        except PermissionError as e:
            raise SourceError("ВК не отдаёт музыку этого аккаунта") from e
        snap.collections.append(SnapCollection(kind="playlist", external_id="audios", title=MY_MUSIC, items=mine))

        playlists: list[dict[str, Any]] = []
        offset = 0
        while offset < MAX_PLAYLISTS:
            await asyncio.sleep(PAUSE_S)
            try:
                res = await _call(c, token, "audio.getPlaylists", owner_id=owner, count=PAGE, offset=offset) or {}
            except PermissionError:
                break
            items = [p for p in (res.get("items") or []) if isinstance(p, dict)]
            playlists += items
            offset += PAGE
            if len(items) < PAGE or offset >= int(res.get("count") or 0):
                break
        seen: set[tuple[Any, Any]] = set()
        for p in playlists:
            if (p.get("owner_id"), p.get("id")) in seen:  # VK's pages can overlap while the lists change
                continue
            seen.add((p.get("owner_id"), p.get("id")))
            # a playlist followed is a copy: its tracks are read from the one it was made of
            src = p.get("original") if isinstance(p.get("original"), dict) else None
            at = (int(src["owner_id"]), int(src["playlist_id"]), src.get("access_key")) if src and src.get("playlist_id") \
                else (int(p.get("owner_id") or owner), int(p.get("id") or 0), p.get("access_key"))
            if not at[1] or not str(p.get("title") or "").strip():
                continue
            await asyncio.sleep(PAUSE_S)
            try:
                items = await _tracks(c, token, at[0], playlist=at[1], key=at[2])
            except PermissionError:
                continue
            if items:
                snap.collections.append(SnapCollection(
                    kind="playlist", external_id=f"{p.get('owner_id')}_{p.get('id')}", title=str(p["title"]).strip(),
                    items=items, cover_url=_cover(p), selected=not src))  # a followed one is offered, not ticked
    return snap
