"""Telegram transport for the Hub's shared bot."""

from __future__ import annotations

import html
import io
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramConflictError,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import BufferedInputFile, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.token import TokenValidationError, validate_token

from app.config import get_settings

log = logging.getLogger(__name__)

HOST = "api.telegram.org"
MAX_FILE_BYTES = 50 * 1024 * 1024  # what a bot may upload
THUMB_PX = 320  # Telegram's limit for a thumbnail: a JPEG of at most 320 px and 200 kB
SEND_WAIT_S = 300
CAPTION_MAX = 1024  # of an audio's caption
MESSAGE_MAX = 4096
LYRICS_MESSAGES = 3  # a longer text is cut: nobody reads a wall of messages


class TelegramError(Exception):
    """Telegram refused or did not answer (the message is user-facing, Russian)."""


@dataclass(frozen=True)
class SharedBot:
    id: str
    username: str
    token: str = field(repr=False)


_shared: SharedBot | None = None


def clean_token(value: str) -> str:
    token = (value or "").strip()
    try:
        validate_token(token)
    except TokenValidationError:
        raise TelegramError("Это не похоже на ключ бота. Скопируйте его целиком из сообщения @BotFather") from None
    return token


def _proxy() -> str | None:
    """The Hub's outbound proxy, when Telegram is among the hosts it is set for (needs `aiohttp-socks`)."""
    s = get_settings()
    if not (s.outbound_proxy and HOST in s.proxy_hosts):
        return None
    try:
        import aiohttp_socks  # noqa: F401
    except ImportError:
        log.warning("telegram: OUTBOUND_PROXY is set for %s but aiohttp-socks is not installed — going direct", HOST)
        return None
    return s.outbound_proxy


def make_bot(token: str, wait_s: float = 30) -> Bot:
    """A bot for one call; the caller closes its session (`bot_of`). Tests replace this."""
    return Bot(token, session=AiohttpSession(proxy=_proxy(), timeout=wait_s))


def _explain(e: Exception) -> TelegramError:
    # never str(e) of a network error: it may carry the URL with the token
    if isinstance(e, TelegramUnauthorizedError | TelegramNotFound):
        return TelegramError("Telegram не принял ключ бота — проверьте его или получите новый у @BotFather")
    if isinstance(e, TelegramForbiddenError):
        return TelegramError("Бот не может написать в этот чат: в личном чате нажмите Start, "
                             "в группе или канале проверьте, что бот там есть и ему можно писать")
    if isinstance(e, TelegramConflictError):
        return TelegramError("У бота включён webhook или его опрашивает другая программа — нужен отдельный бот")
    if isinstance(e, TelegramEntityTooLarge):
        return TelegramError("Файл слишком большой для Telegram (боты отправляют до 50 МБ)")
    if isinstance(e, TelegramRetryAfter):
        return TelegramError("Telegram просит подождать — попробуйте через минуту")
    if isinstance(e, TelegramNetworkError | TimeoutError):
        return TelegramError(f"Telegram не отвечает ({type(e).__name__})")
    if isinstance(e, TelegramAPIError):
        return TelegramError(f"Telegram: {e.message}"[:300])
    return TelegramError(f"Не удалось связаться с Telegram ({type(e).__name__})")


@asynccontextmanager
async def bot_of(token: str, wait_s: float = 30) -> AsyncIterator[Bot]:
    """`async with bot_of(token) as bot:` — aiogram's errors come out as `TelegramError`."""
    bot = make_bot(token, wait_s)
    try:
        yield bot
    except TelegramError:
        raise
    except (TelegramAPIError, TimeoutError, OSError) as e:
        raise _explain(e) from None
    finally:
        await bot.session.close()


async def shared_bot() -> SharedBot | None:
    global _shared

    token = get_settings().telegram_bot_token.strip()
    if not token:
        return None
    if _shared is not None and _shared.token == token:
        return _shared
    token = clean_token(token)
    async with bot_of(token) as bot:
        me = await bot.get_me()
    if not me.username:
        raise TelegramError("У бота Telegram не задано имя")
    _shared = SharedBot(str(me.id), me.username, token)
    return _shared


def chat_name(chat: Any) -> str:
    name = " ".join(x for x in (getattr(chat, "first_name", None), getattr(chat, "last_name", None)) if x).strip()
    user = f"@{chat.username}" if getattr(chat, "username", None) else ""
    return " ".join(x for x in (name, user) if x) or str(chat.id)


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


def _quote(text: str) -> str:
    """A collapsed quote: Telegram shows its first lines and opens the rest on a tap."""
    return f"<blockquote expandable>{html.escape(text, quote=False)}</blockquote>"


def lyrics_messages(text: str, *, caption_room: int = CAPTION_MAX) -> tuple[str | None, list[str]]:
    """How a track's text goes with it: (the audio's caption, the messages after it). A short text fits the
    caption; a long one follows the audio as collapsed quotes, cut after `LYRICS_MESSAGES` of them."""
    text = (text or "").strip()
    if not text:
        return None, []
    if len(text) <= caption_room - 40:
        return _quote(text), []
    room = MESSAGE_MAX - 200
    chunks: list[str] = []
    cur = ""
    for line in text.splitlines():
        if len(cur) + len(line) + 1 > room and cur:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line[:room]
    if cur:
        chunks.append(cur)
    if len(chunks) > LYRICS_MESSAGES:
        chunks = chunks[:LYRICS_MESSAGES]
        chunks[-1] += "\n…"
    return None, [_quote(c) for c in chunks]


async def send_audio(token: str, chat_id: str, path: Path, *, filename: str, title: str, performer: str,
                     favorite_data: str, queue_data: str,
                     duration_s: int | None = None, cover: bytes | None = None, lyrics: str | None = None,
                     link: str | None = None, sender_id: int | None = None) -> None:
    thumb = thumbnail(cover)
    buttons = [[InlineKeyboardButton(text="В очередь", callback_data=queue_data),
                InlineKeyboardButton(text="❤️", callback_data=favorite_data)]]
    if link:
        buttons.append([InlineKeyboardButton(text="Открыть", url=link)])
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    chat = int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
    async with bot_of(token, SEND_WAIT_S) as bot:
        signature = ""
        if sender_id is not None:
            sender = await bot.get_chat(chat_id=sender_id, request_timeout=10)
            if sender.username:
                signature = f"От @{html.escape(sender.username)}"
        room = CAPTION_MAX - (len(signature) + 2 if signature else 0)
        lyrics_caption, after = lyrics_messages(lyrics or "", caption_room=room)
        caption = "\n\n".join(part for part in (signature, lyrics_caption) if part) or None
        sent = await bot.send_audio(
            chat_id=chat,
            audio=FSInputFile(path, filename=filename),
            title=title[:256],
            performer=performer[:256],
            duration=int(duration_s) if duration_s else None,
            thumbnail=BufferedInputFile(thumb, filename="cover.jpg") if thumb else None,
            caption=caption,
            parse_mode="HTML" if caption else None,
            reply_markup=markup,
            request_timeout=SEND_WAIT_S,
        )
        for text in after:
            await bot.send_message(chat_id=chat, text=text, parse_mode="HTML",
                                   reply_to_message_id=getattr(sent, "message_id", None))
