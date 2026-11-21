"""Account pairing, group membership and personal favorites for the shared bot."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import logging
import re
import secrets
from datetime import timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import favorites, telegram
from app.api.schemas import FavoriteTarget
from app.config import get_settings
from app.db import session_scope
from app.events import bus
from app.models import Playlist, PlaylistItem, TelegramAccount, TelegramGroup, Track, User, utcnow

log = logging.getLogger(__name__)
GROUP_KINDS = ("group", "supergroup", "channel")
POLL_S = 25
_LINK_TTL = timedelta(minutes=15)
_LINK = re.compile(r"hm_[A-Za-z0-9_-]{32}")
_FAVORITE = re.compile(r"like:([1-9][0-9]{0,18}):[a-f0-9]{32}")
_TG_ERRORS = (TelegramAPIError, TimeoutError, OSError)
_offsets: dict[str, int] = {}
_poll_lock = asyncio.Lock()


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def account(s: AsyncSession, user_id: int, bot: telegram.SharedBot) -> TelegramAccount | None:
    return await s.scalar(select(TelegramAccount).where(
        TelegramAccount.user_id == user_id, TelegramAccount.bot_id == bot.id, TelegramAccount.telegram_id.is_not(None)
    ).execution_options(populate_existing=True))


async def link_url(s: AsyncSession, user_id: int, bot: telegram.SharedBot) -> str | None:
    code = "hm_" + secrets.token_urlsafe(24)
    attrs = dict(bot_id=bot.id, telegram_id=None, name=None, link_digest=_digest(code),
                 link_expires_at=utcnow() + _LINK_TTL)
    stmt = insert(TelegramAccount).values(user_id=user_id, **attrs).on_conflict_do_update(
        index_elements=[TelegramAccount.user_id], set_=attrs,
        where=(TelegramAccount.bot_id != bot.id) | TelegramAccount.telegram_id.is_(None),
    ).returning(TelegramAccount.user_id)
    if await s.scalar(stmt) is None:
        return None
    return f"https://t.me/{bot.username}?start={code}"


async def unlink(s: AsyncSession, user_id: int) -> None:
    await s.execute(delete(TelegramAccount).where(TelegramAccount.user_id == user_id))


def _plain(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _status(member: Any) -> str:
    return _plain(member.status)


def _can_post(member: Any, kind: str) -> bool:
    return _status(member) == "creator" or (
        _status(member) == "administrator" and (kind != "channel" or bool(member.can_post_messages)))


def _lookup(bot: telegram.SharedBot, chat_id: int | str):
    return (TelegramGroup.bot_id == bot.id, TelegramGroup.chat_id == str(chat_id))


async def _chat_details(bot: Bot, chat_id: str, previous: str | None = None) -> dict:
    try:
        full = await bot.get_chat(chat_id=int(chat_id))
    except _TG_ERRORS:
        return {}
    fields: dict[str, Any] = {"title": full.title[:256]} if full.title else {}
    if full.photo is None:
        fields.update(photo=None, photo_key=None)
    elif full.photo.small_file_unique_id != previous:
        try:
            buf = io.BytesIO()
            await bot.download(full.photo.small_file_id, destination=buf)
            fields.update(photo=buf.getvalue(), photo_key=full.photo.small_file_unique_id[:64])
        except _TG_ERRORS:
            pass
    return fields


async def _save_group(s: AsyncSession, shared: telegram.SharedBot, bot: Bot, chat: Any) -> None:
    existing = await s.scalar(select(TelegramGroup).where(*_lookup(shared, chat.id)))
    attrs = {"kind": _plain(chat.type), "title": (chat.title or str(chat.id))[:256]}
    # A transient Telegram error leaves the last known picture in place.
    attrs.update(await _chat_details(bot, str(chat.id), existing.photo_key if existing else None))
    stmt = insert(TelegramGroup).values(bot_id=shared.id, chat_id=str(chat.id), **attrs)
    await s.execute(stmt.on_conflict_do_update(index_elements=["bot_id", "chat_id"], set_=attrs))


async def can_send(bot: Bot, shared: telegram.SharedBot, group: TelegramGroup, telegram_id: str) -> bool:
    if group.bot_id != shared.id or group.kind not in GROUP_KINDS:
        return False
    try:
        bot_member = await bot.get_chat_member(chat_id=int(group.chat_id), user_id=int(shared.id))
        if not _can_post(bot_member, group.kind):
            return False
        member = await bot.get_chat_member(chat_id=int(group.chat_id), user_id=int(telegram_id))
    except _TG_ERRORS:
        return False  # Telegram did not confirm membership
    if group.kind == "channel":
        return _can_post(member, "channel")
    return _status(member) in ("creator", "administrator", "member") or (
        _status(member) == "restricted" and bool(member.is_member) and bool(member.can_send_audios))


async def groups(s: AsyncSession, shared: telegram.SharedBot, telegram_id: str, *, refresh: bool = False) -> list[TelegramGroup]:
    rows = list((await s.scalars(select(TelegramGroup).where(TelegramGroup.bot_id == shared.id))).all())
    if not rows:
        return []
    limit = asyncio.Semaphore(8)
    async with telegram.bot_of(shared.token, 10) as bot:
        async def visible(row: TelegramGroup) -> bool:
            async with limit:
                if not await can_send(bot, shared, row, telegram_id):
                    return False
                if refresh:
                    for key, value in (await _chat_details(bot, row.chat_id, row.photo_key)).items():
                        setattr(row, key, value)
                return True

        allowed = await asyncio.gather(*(visible(row) for row in rows))
    return sorted((row for row, yes in zip(rows, allowed, strict=True) if yes), key=lambda row: row.title.casefold())


async def _private(s: AsyncSession, shared: telegram.SharedBot, msg: Any) -> str | None:
    sender = msg.from_user
    if sender is None or sender.is_bot or sender.id != msg.chat.id:
        return None
    text = (msg.text or "").split(maxsplit=1)
    is_start = bool(text and text[0].lower() in ("/start", f"/start@{shared.username.lower()}"))
    tg_id = str(sender.id)
    existing = aliased(TelegramAccount)
    occupied = select(existing.user_id).where(existing.bot_id == shared.id, existing.telegram_id == tg_id).exists()
    if is_start and len(text) == 2 and _LINK.fullmatch(text[1].strip()):
        stmt = (update(TelegramAccount)
                .where(TelegramAccount.bot_id == shared.id,
                       TelegramAccount.link_digest == _digest(text[1].strip()),
                       TelegramAccount.link_expires_at > utcnow(), TelegramAccount.telegram_id.is_(None), ~occupied)
                .values(telegram_id=tg_id, name=telegram.chat_name(msg.chat)[:128], link_digest=None, link_expires_at=None)
                .returning(TelegramAccount.user_id))
        if await s.scalar(stmt) is not None:
            return "Telegram подключён. Теперь в HomeMusic можно отправлять треки в этот чат."
        if await s.scalar(select(existing.user_id).where(existing.bot_id == shared.id, existing.telegram_id == tg_id)):
            return "Этот Telegram уже подключён к HomeMusic."
        return "Ссылка для подключения устарела. Откройте новую в HomeMusic → Меню → Telegram."
    linked = await s.scalar(select(existing.user_id).where(existing.bot_id == shared.id, existing.telegram_id == tg_id))
    if linked:
        return "Telegram уже подключён. Отправляйте треки через «Поделиться» в HomeMusic." if is_start else None
    return "Чтобы получать треки, откройте HomeMusic → Меню → Telegram и нажмите «Открыть бота»."


async def _apply(s: AsyncSession, shared: telegram.SharedBot, bot: Bot, item: Update) -> tuple[int, str] | None:
    if event := item.my_chat_member:
        kind = _plain(event.chat.type)
        if kind in GROUP_KINDS:
            if _can_post(event.new_chat_member, kind):
                await _save_group(s, shared, bot, event.chat)
            else:
                await s.execute(delete(TelegramGroup).where(*_lookup(shared, event.chat.id)))
        return None
    if (msg := item.message) is None:
        return None
    kind = _plain(msg.chat.type)
    if kind == "private":
        reply = await _private(s, shared, msg)
        return (msg.chat.id, reply) if reply else None
    if msg.migrate_to_chat_id:
        row = await s.scalar(select(TelegramGroup).where(*_lookup(shared, msg.chat.id)))
        if row is not None:
            stmt = insert(TelegramGroup).values(bot_id=shared.id, chat_id=str(msg.migrate_to_chat_id),
                                                kind="supergroup", title=row.title, photo=row.photo, photo_key=row.photo_key)
            await s.execute(stmt.on_conflict_do_nothing(index_elements=["bot_id", "chat_id"]))
            await s.delete(row)
        return None
    # Discover a group that had already made the bot an admin before HomeMusic started polling it.
    command = (msg.text or "").lower().split(maxsplit=1)
    if kind in ("group", "supergroup") and command and command[0] in ("/homemusic", f"/homemusic@{shared.username.lower()}"):
        try:
            member = await bot.get_chat_member(chat_id=msg.chat.id, user_id=int(shared.id))
        except _TG_ERRORS:
            return None
        if _can_post(member, kind):
            await _save_group(s, shared, bot, msg.chat)
    return None


async def _reply(bot: Bot, chat_id: int, text: str) -> None:
    url = get_settings().public_hub_url.rstrip("/")
    markup = None
    if url.startswith(("http://", "https://")):
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть HomeMusic", url=url + "/#/more")]])
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
    except _TG_ERRORS:
        log.info("telegram: could not reply to a private message")


def favorite_data(track: Track) -> str:
    action = f"like:{track.id}"
    # Bind the button to this recording even if SQLite later reuses its row ID.
    message = f"telegram:{action}:{track.created_at.isoformat()}"
    signature = hmac.digest(get_settings().hub_secret_key.encode("ascii"), message.encode("ascii"), "sha256")
    return f"{action}:{signature[:16].hex()}"


async def _like(s: AsyncSession, shared: telegram.SharedBot, query: CallbackQuery) -> tuple[int | None, str]:
    data = query.data or ""
    match = _FAVORITE.fullmatch(data)
    if not match or query.from_user.is_bot or int(match[1]) > 2**63 - 1:
        return None, "Кнопка недоступна"
    user = await s.scalar(select(User).join(TelegramAccount, TelegramAccount.user_id == User.id).where(
        TelegramAccount.bot_id == shared.id, TelegramAccount.telegram_id == str(query.from_user.id)))
    if user is None:
        return None, "Подключите Telegram в HomeMusic → Меню → Telegram бот"
    track = await s.get(Track, int(match[1]))
    if track is None or not hmac.compare_digest(data, favorite_data(track)):
        return None, "Этот трек недоступен"

    liked = select(PlaylistItem.id).join(Playlist).where(
        Playlist.user_id == user.id, Playlist.kind == "liked", PlaylistItem.track_id == track.id)
    if await s.scalar(select(liked.exists())):
        return None, "Уже в Любимом"
    await favorites.command(s, user, [FavoriteTarget(track_id=track.id)], liked=True,
                            operation=f"tg:{query.id}", client="telegram")
    await favorites.project_liked(s, user.id)
    return user.id, "Добавлено в Любимое"


async def _answer_favorite(shared: telegram.SharedBot, bot: Bot, query: CallbackQuery) -> None:
    try:
        async with session_scope() as s:
            user_id, text = await _like(s, shared, query)
    except Exception as exc:
        log.warning("telegram: could not add a favorite: %s", type(exc).__name__)
        text = "Не удалось добавить в Любимое. Попробуйте ещё раз"
    else:
        if user_id is not None:
            bus.publish(user_id, "library", {"kind": "starred"})
    try:
        await bot.answer_callback_query(callback_query_id=query.id, text=text, request_timeout=5)
    except _TG_ERRORS:
        log.info("telegram: could not answer favorite button")


async def _read(shared: telegram.SharedBot, bot: Bot, wait: int) -> None:
    updates = await bot.get_updates(offset=_offsets.get(shared.id), timeout=wait, limit=100,
                                    allowed_updates=["message", "my_chat_member", "callback_query"],
                                    request_timeout=wait + 15)
    for item in updates:
        reply = None
        if query := item.callback_query:
            await _answer_favorite(shared, bot, query)
        else:
            async with session_scope() as s:
                reply = await _apply(s, shared, bot, item)
        _offsets[shared.id] = item.update_id + 1
        if reply:
            await _reply(bot, *reply)


async def sync(shared: telegram.SharedBot) -> None:
    """Read updates on request only when the background listener is disabled."""
    if get_settings().disable_background:
        async with _poll_lock, telegram.bot_of(shared.token) as bot:
            await _read(shared, bot, 0)


async def poll() -> None:
    while True:
        try:
            shared = await telegram.shared_bot()
            if shared is None:
                return
            async with telegram.bot_of(shared.token, POLL_S + 30) as bot:
                while True:
                    await _read(shared, bot, POLL_S)
        except telegram.TelegramError as exc:
            log.info("telegram: shared bot is not polled: %s", exc)
            await asyncio.sleep(60)
        except Exception as exc:
            log.warning("telegram: shared bot polling failed: %s", type(exc).__name__)
            await asyncio.sleep(60)
