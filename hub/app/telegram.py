"""Sending library tracks to the user's own Telegram bot.

Each user connects a bot of their own: the token BotFather gave (kept encrypted, as the Navidrome password is) and
the private chat the bot may write to — a bot cannot start a conversation, so the user presses Start in it and the
Hub finds that chat in the bot's updates. The Hub talks to the official Bot API over HTTPS itself (`getMe`,
`getUpdates`, `sendAudio`): three plain requests with a per-user token need no bot framework.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from app.net import make_client

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,60}$")
MAX_FILE_BYTES = 50 * 1024 * 1024  # what a bot may upload
THUMB_PX = 320  # Telegram's limit for a thumbnail: a JPEG of at most 320 px and 200 kB


class TelegramError(Exception):
    """Telegram refused or did not answer (the message is user-facing, Russian)."""


def clean_token(value: str) -> str:
    token = (value or "").strip()
    if not TOKEN_RE.match(token):
        raise TelegramError("Это не похоже на ключ бота. Скопируйте его целиком из сообщения @BotFather")
    return token


async def _call(token: str, method: str, *, wait_s: float = 30, **kw: Any) -> Any:
    try:
        async with make_client(http2=False, timeout=wait_s) as c:
            r = await c.post(f"{API}/bot{token}/{method}", **kw)
    except httpx.HTTPError as e:
        # never the URL: it carries the token
        raise TelegramError(f"Telegram не отвечает ({type(e).__name__})") from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 200 and body.get("ok"):
        return body.get("result")
    desc = str(body.get("description") or f"HTTP {r.status_code}")
    if r.status_code in (401, 404):
        raise TelegramError("Telegram не принял ключ бота — проверьте его или получите новый у @BotFather")
    if r.status_code == 403:
        raise TelegramError("Бот не может написать в этот чат — откройте бота и нажмите Start")
    if r.status_code == 409:
        raise TelegramError("У бота включён webhook или его опрашивает другая программа — нужен отдельный бот")
    if r.status_code == 413:
        raise TelegramError("Файл слишком большой для Telegram (боты отправляют до 50 МБ)")
    if r.status_code == 429:
        raise TelegramError("Telegram просит подождать — попробуйте через минуту")
    raise TelegramError(f"Telegram: {desc}"[:300])


async def get_me(token: str) -> dict[str, Any]:
    """The bot of a token: {id, username, first_name}."""
    return await _call(token, "getMe")


def _chat_name(chat: dict[str, Any]) -> str:
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x).strip()
    user = f"@{chat['username']}" if chat.get("username") else ""
    return " ".join(x for x in (name, user) if x) or str(chat.get("id"))


async def find_chat(token: str) -> tuple[str, str] | None:
    """The private chat of whoever wrote to the bot last (id, name); None — nobody pressed Start yet."""
    updates = await _call(token, "getUpdates", json={"limit": 100, "timeout": 0, "allowed_updates": ["message"]})
    for u in reversed(updates or []):
        chat = ((u.get("message") or {}).get("chat")) or {}
        if chat.get("type") == "private" and chat.get("id"):
            return str(chat["id"]), _chat_name(chat)
    return None


def thumbnail(cover: bytes | None) -> bytes | None:
    if not cover:
        return None
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(cover)).convert("RGB")
        im.thumbnail((THUMB_PX, THUMB_PX))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception as e:
        log.info("telegram: no thumbnail: %r", e)
        return None


async def send_audio(token: str, chat_id: str, path: Path, *, filename: str, title: str, performer: str,
                     duration_s: int | None = None, cover: bytes | None = None) -> None:
    data: dict[str, Any] = {"chat_id": chat_id, "title": title[:256], "performer": performer[:256]}
    if duration_s:
        data["duration"] = str(int(duration_s))
    with path.open("rb") as f:
        files: dict[str, Any] = {"audio": (filename, f, "application/octet-stream")}
        if thumb := thumbnail(cover):
            files["thumbnail"] = ("cover.jpg", thumb, "image/jpeg")
        await _call(token, "sendAudio", wait_s=300, data=data, files=files)
