"""The chats a user's bot can send to (`models.TgChat`): the private chat with the user and every group or channel
the user has added the bot to.

Telegram never tells a bot which chats it is in — only what happens: «you were added», «you were removed», a message.
So the Hub listens to each connected bot (`start`: a long poll per bot, in the background) and keeps the list itself.
Only the bot's owner brings a chat into the list: the one who added the bot (or wrote in the group) must be the user
of the private chat — a stranger who finds the bot and adds it somewhere changes nothing.

Where nothing polls (tests, `DISABLE_BACKGROUND`) the API reads the bot's updates itself (`sync`); never both at
once — Telegram ends a long poll when a second `getUpdates` comes.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import telegram
from app.db import session_scope
from app.models import TgChat, User, utcnow
from app.security import decrypt

log = logging.getLogger(__name__)

POLL_S = 25
ALLOWED = ["message", "channel_post", "my_chat_member"]
RETRY_S = 60
RETRY_BROKEN_S = 600  # the key was revoked, or another program polls the bot

_offsets: dict[int, int] = {}  # user id -> the next update to ask for
_tasks: dict[int, asyncio.Task] = {}


class Gone(Exception):
    """The bot is not in the chat any more."""


def _plain(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _title(chat: Any) -> str:
    return (getattr(chat, "title", None) or telegram.chat_name(chat))[:256]


def _is_owner(u: User, who: Any) -> bool:
    return bool(u.tg_chat_id) and who is not None and str(getattr(who, "id", "")) == u.tg_chat_id


async def _row(s: AsyncSession, user_id: int, chat_id: Any) -> TgChat | None:
    return (await s.execute(select(TgChat).where(TgChat.user_id == user_id, TgChat.chat_id == str(chat_id))
                            .limit(1))).scalar_one_or_none()


async def _put(s: AsyncSession, u: User, chat: Any) -> TgChat:
    row = await _row(s, u.id, chat.id)
    if row is None:
        row = TgChat(user_id=u.id, chat_id=str(chat.id), kind=_plain(chat.type), title=_title(chat))
        s.add(row)
    else:
        row.kind, row.title, row.updated_at = _plain(chat.type), _title(chat), utcnow()
    await s.flush()
    return row


async def _look(bot: Any, row: TgChat) -> None:
    """The chat's name and picture as they are now. `Gone`: Telegram says the bot has no such chat."""
    try:
        full = await bot.get_chat(chat_id=int(row.chat_id))
        row.title = _title(full)
        photo = getattr(full, "photo", None)
        key = getattr(photo, "small_file_unique_id", None)
        if key is None:
            row.photo = row.photo_key = None
        elif key != row.photo_key:
            buf = io.BytesIO()
            await bot.download(photo.small_file_id, destination=buf)
            row.photo, row.photo_key = buf.getvalue(), str(key)[:64]
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        raise Gone from e
    except Exception as e:  # the list works without pictures
        log.info("telegram: no picture of chat %s: %s", row.chat_id, type(e).__name__)


async def apply(s: AsyncSession, u: User, updates: list[Any], bot: Any) -> None:
    """What the bot's updates say about its chats."""
    fresh: dict[int, TgChat] = {}
    for up in updates:
        member = getattr(up, "my_chat_member", None)
        msg = getattr(up, "message", None) or getattr(up, "channel_post", None)
        if member is not None:
            chat = member.chat
            if _plain(chat.type) == "private":
                continue
            status = _plain(getattr(member.new_chat_member, "status", ""))
            row = await _row(s, u.id, chat.id)
            # a channel takes posts from its admins only
            can_write = status in ("administrator", "creator") or (status == "member" and _plain(chat.type) != "channel")
            if not can_write:
                if row is not None:
                    fresh.pop(row.id, None)
                    await s.delete(row)
                    await s.flush()
            elif row is not None or _is_owner(u, getattr(member, "from_user", None)):
                row = await _put(s, u, chat)
                fresh[row.id] = row
        elif msg is not None:
            chat = msg.chat
            if _plain(chat.type) == "private":
                if not u.tg_chat_id:  # the first to press Start is the owner
                    u.tg_chat_id = str(chat.id)
                if str(chat.id) == u.tg_chat_id:
                    u.tg_chat_name = telegram.chat_name(chat)[:128]
                    row = await _put(s, u, chat)
                    fresh[row.id] = row
                continue
            row = await _row(s, u.id, chat.id)
            moved = getattr(msg, "migrate_to_chat_id", None)  # a group that became a supergroup has a new id
            if moved and row is not None:
                row.chat_id, row.kind = str(moved), "supergroup"
                await s.flush()
            elif row is None and _plain(chat.type) in ("group", "supergroup") and _is_owner(u, getattr(msg, "from_user", None)):
                row = await _put(s, u, chat)  # a group the bot was in before the Hub listened
                fresh[row.id] = row
    for row in fresh.values():
        try:
            await _look(bot, row)
        except Gone:
            pass


async def _take(s: AsyncSession, u: User, bot: Any, wait_s: int) -> None:
    offset = _offsets.get(u.id)
    updates = await bot.get_updates(limit=100, timeout=wait_s, allowed_updates=ALLOWED, request_timeout=wait_s + 15,
                                    **({"offset": offset} if offset else {}))
    if not updates:
        return
    await apply(s, u, list(updates), bot)
    last = getattr(updates[-1], "update_id", None)
    if last is not None:
        _offsets[u.id] = last + 1


async def sync(s: AsyncSession, u: User) -> None:
    """Read the bot's updates now — unless the background already does (then the list is seconds fresh)."""
    if polling(u.id) or not u.tg_bot_token_enc:
        return
    async with telegram.bot_of(decrypt(u.tg_bot_token_enc)) as bot:
        await _take(s, u, bot, 0)


async def refresh(s: AsyncSession, u: User) -> None:
    """Names and pictures again; a chat the bot was removed from goes."""
    if not u.tg_bot_token_enc:
        return
    rows = list((await s.execute(select(TgChat).where(TgChat.user_id == u.id))).scalars())
    async with telegram.bot_of(decrypt(u.tg_bot_token_enc)) as bot:
        for row in rows:
            try:
                await _look(bot, row)
            except Gone:
                if row.kind != "private":
                    await s.delete(row)


async def chats(s: AsyncSession, u: User) -> list[TgChat]:
    rows = list((await s.execute(select(TgChat).where(TgChat.user_id == u.id))).scalars())
    return sorted(rows, key=lambda r: (r.kind != "private", r.title.casefold()))


async def forget(s: AsyncSession, user_id: int) -> None:
    """The bot is disconnected (or replaced): its chats are not this user's any more."""
    stop(user_id)
    _offsets.pop(user_id, None)
    await s.execute(delete(TgChat).where(TgChat.user_id == user_id))


# --- background ---------------------------------------------------------------------------------------


def polling(user_id: int) -> bool:
    task = _tasks.get(user_id)
    return task is not None and not task.done()


async def _poll(user_id: int) -> None:
    first = True
    while True:
        try:
            async with session_scope() as s:
                u = await s.get(User, user_id)
                token = decrypt(u.tg_bot_token_enc) if u is not None and u.tg_bot_token_enc else None
                if token and first:
                    assert u is not None
                    await refresh(s, u)
            if not token:
                return
            first = False
            async with telegram.bot_of(token, POLL_S + 30) as bot:
                offset = _offsets.get(user_id)
                updates = await bot.get_updates(limit=100, timeout=POLL_S, allowed_updates=ALLOWED,
                                                request_timeout=POLL_S + 15, **({"offset": offset} if offset else {}))
                if updates:
                    async with session_scope() as s:
                        u = await s.get(User, user_id)
                        if u is None or not u.tg_bot_token_enc:
                            return
                        await apply(s, u, list(updates), bot)
                    _offsets[user_id] = updates[-1].update_id + 1
        except asyncio.CancelledError:
            raise
        except telegram.TelegramError as e:
            broken = "ключ" in str(e) or "webhook" in str(e)
            log.info("telegram: bot of user %s is not polled: %s", user_id, e)
            await asyncio.sleep(RETRY_BROKEN_S if broken else RETRY_S)
        except Exception as e:
            log.warning("telegram: polling of user %s failed: %s", user_id, type(e).__name__)
            await asyncio.sleep(RETRY_S)


def start(user_id: int) -> None:
    if not polling(user_id):
        _tasks[user_id] = asyncio.create_task(_poll(user_id), name=f"telegram-{user_id}")


def stop(user_id: int) -> None:
    task = _tasks.pop(user_id, None)
    if task is not None:
        task.cancel()


async def start_all() -> None:
    async with session_scope() as s:
        ids = list((await s.execute(select(User.id).where(User.tg_bot_token_enc.is_not(None)))).scalars())
    for user_id in ids:
        start(user_id)


def stop_all() -> None:
    for user_id in list(_tasks):
        stop(user_id)
