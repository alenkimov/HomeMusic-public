"""«Отправить в Telegram»: pair an account and send library tracks through the shared bot."""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import light, telegram, telegram_chats
from app.auth import current_user
from app.config import get_settings
from app.db import get_session
from app.library import tagger
from app.lyrics import service as lyrics_service
from app.models import TelegramGroup, User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telegram", tags=["telegram"])

LIGHT_WAIT_S = 120  # a file over Telegram's limit is sent as its light copy; making one takes a while


class TelegramOut(BaseModel):
    configured: bool = False
    ready: bool = False  # the current user has started the bot using a personal link
    bot_username: str | None = None
    chat_name: str | None = None
    with_lyrics: bool = False  # «Отправить в Telegram» sends the track's text with it


class TelegramLinkOut(TelegramOut):
    start_url: str | None = None


class TelegramPrefsIn(BaseModel):
    with_lyrics: bool


class TelegramChatOut(BaseModel):
    id: int
    kind: str  # private/group/supergroup/channel
    title: str
    photo: str | None = None  # the picture's key (it changes with the picture); None — the chat has none


class TelegramSendIn(BaseModel):
    nd_song_id: str | None = None
    ref: str | None = None
    chat: int | None = None  # one of the bot's chats (`/chats`); None: the private chat with the user
    with_lyrics: bool | None = None  # None: as the user's setting says
    with_link: bool = False  # a button under the audio: the track's «HomeMusic» link (app/links.py)
    anonymous: bool = False


class TelegramSentOut(BaseModel):
    ok: bool = True
    title: str
    version_label: str | None = None
    light: bool = False  # the light copy was sent (the file is over Telegram's limit)
    lyrics: bool = False  # the track's text went with it


async def _bot() -> telegram.SharedBot | None:
    try:
        return await telegram.shared_bot()
    except telegram.TelegramError as exc:
        raise HTTPException(502, str(exc)) from exc


async def _required_bot() -> telegram.SharedBot:
    if (bot := await _bot()) is None:
        raise HTTPException(409, "Администратор Хаба ещё не настроил бота Telegram")
    return bot


async def _out(s: AsyncSession, user: User, bot: telegram.SharedBot | None) -> TelegramOut:
    linked = await telegram_chats.account(s, user.id, bot) if bot else None
    return TelegramOut(configured=bot is not None, ready=linked is not None, bot_username=bot.username if bot else None,
                       chat_name=linked.name if linked else None, with_lyrics=user.tg_with_lyrics)


async def _linked_id(s: AsyncSession, user: User, bot: telegram.SharedBot) -> str:
    account = await telegram_chats.account(s, user.id, bot)
    if account is None or account.telegram_id is None:
        raise HTTPException(409, "Откройте бота из HomeMusic и нажмите Start")
    return account.telegram_id


async def _chats_out(s: AsyncSession, user: User, *, refresh: bool = False) -> list[TelegramChatOut]:
    bot = await _bot()
    if bot and refresh:
        await _sync(bot)
    account = await telegram_chats.account(s, user.id, bot) if bot else None
    if bot is None or account is None or account.telegram_id is None:
        return []
    rows = await telegram_chats.groups(s, bot, account.telegram_id, refresh=refresh)
    return [TelegramChatOut(id=0, kind="private", title=account.name or "Мне"), *[
        TelegramChatOut(id=c.id, kind=c.kind, title=c.title, photo=c.photo_key if c.photo else None) for c in rows]]


async def _sync(bot: telegram.SharedBot) -> None:
    try:
        await telegram_chats.sync(bot)
    except telegram.TelegramError as exc:
        raise HTTPException(502, str(exc)) from exc


async def _allowed_group(s: AsyncSession, bot: telegram.SharedBot, chat: int, telegram_id: str) -> TelegramGroup:
    row = await s.get(TelegramGroup, chat)
    if row is not None:
        async with telegram.bot_of(bot.token, 10) as client:
            if await telegram_chats.can_send(client, bot, row, telegram_id):
                return row
    raise HTTPException(404, "Чат недоступен: проверьте своё членство и права бота")


@router.get("", response_model=TelegramOut)
async def status(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await _out(s, user, await _bot())


@router.post("/link", response_model=TelegramLinkOut)
async def link(response: Response, user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")):
    """Issue a short-lived Start link for the authenticated Hub account."""
    bot = await _required_bot()
    url = await telegram_chats.link_url(s, user.id, bot)
    out = await _out(s, user, bot)
    await s.commit()
    response.headers["Cache-Control"] = "no-store"
    return TelegramLinkOut(**out.model_dump(), start_url=url)


@router.post("/check", response_model=TelegramOut)
async def check(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """Look for the chat again: the user has pressed Start in the bot."""
    bot = await _required_bot()
    await _sync(bot)
    return await _out(s, user, bot)


@router.get("/chats", response_model=list[TelegramChatOut])
async def chats(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await _chats_out(s, user)


@router.post("/chats/refresh", response_model=list[TelegramChatOut])
async def refresh_chats(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    rows = await _chats_out(s, user, refresh=True)
    await s.commit()
    return rows


@router.get("/chats/{chat}/photo")
async def chat_photo(chat: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    bot = await _required_bot()
    telegram_id = await _linked_id(s, user, bot)
    row = await _allowed_group(s, bot, chat, telegram_id)
    if not row.photo:
        raise HTTPException(404, "У чата нет картинки")
    return Response(row.photo, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.delete("", response_model=TelegramOut)
async def disconnect(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    await telegram_chats.unlink(s, user.id)
    await s.commit()
    return await _out(s, user, await _bot())


@router.put("/prefs", response_model=TelegramOut)
async def prefs(body: TelegramPrefsIn, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    u = await s.get(User, user.id)
    assert u is not None
    u.tg_with_lyrics = body.with_lyrics
    await s.commit()
    return await _out(s, u, await _bot())


@router.post("/send", response_model=TelegramSentOut)
async def send(body: TelegramSendIn, user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")):
    bot = await _required_bot()
    await _linked_id(s, user, bot)
    track = await lyrics_service.track_of(s, nd_song_id=body.nd_song_id, ref=body.ref)
    if track is None:
        raise HTTPException(404, "Этого трека нет в библиотеке — сначала скачайте его")
    src = get_settings().library_dir / track.path
    if not src.is_file():
        raise HTTPException(404, "Файл трека не найден в библиотеке")
    path, is_light = src, False
    if src.stat().st_size > telegram.MAX_FILE_BYTES:
        copy, _ = await light.get(src, f"lib:{track.id}", wait_s=LIGHT_WAIT_S)
        if copy is None or copy.stat().st_size > telegram.MAX_FILE_BYTES:
            raise HTTPException(413, "Файл больше 50 МБ — Telegram не принимает такие от ботов")
        path, is_light = copy, True
    try:
        cover = await asyncio.to_thread(tagger.read_cover, src)
    except Exception as e:  # a file the tagger cannot read is still sent, without the picture
        log.info("telegram: no cover of %s: %r", track.path, e)
        cover = None
    text = ""
    if body.with_lyrics if body.with_lyrics is not None else user.tg_with_lyrics:
        try:
            text = lyrics_service.text_of(await lyrics_service.ensure(s, track))
            await s.commit()
        except Exception as e:  # the track goes without its text rather than not at all
            log.info("telegram: no lyrics of %s: %r", track.path, e)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", f"{track.artist} - {track.title}").strip()[:120] or "track"
    link = None
    if body.with_link:
        from app import links

        try:
            link = (await links.of_track(s, track)).get("homemusic") or None
            await s.commit()
        except Exception as e:  # the track goes without its button rather than not at all
            log.info("telegram: no link of %s: %r", track.path, e)
    from app.library.versions import label_of

    version_label = await label_of(s, track.id)
    display_title = f"{track.title} ({version_label})" if version_label else track.title
    # Re-read the pairing and verify group membership just before uploading.
    await s.commit()
    sender_id = await _linked_id(s, user, bot)
    target = sender_id if body.chat is None else (await _allowed_group(s, bot, body.chat, sender_id)).chat_id
    try:
        await telegram.send_audio(bot.token, target, path, filename=f"{name}{path.suffix}",
                                  title=display_title, performer=track.artist, duration_s=track.duration_s, cover=cover,
                                  lyrics=text, link=link, sender_id=None if body.anonymous else int(sender_id))
    except telegram.TelegramError as e:
        raise HTTPException(502, str(e)) from e
    return TelegramSentOut(title=track.title, version_label=version_label, light=is_light, lyrics=bool(text))
