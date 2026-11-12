"""«Отправить в Telegram»: the user's own bot (app/telegram.py) — connecting it and sending library tracks to it."""

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
from app.models import TgChat, User
from app.security import decrypt, encrypt

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telegram", tags=["telegram"])

LIGHT_WAIT_S = 120  # a file over Telegram's limit is sent as its light copy; making one takes a while


class TelegramOut(BaseModel):
    connected: bool = False  # a bot's key is saved
    ready: bool = False  # ... and the chat it writes to is known
    bot_username: str | None = None
    chat_name: str | None = None
    with_lyrics: bool = False  # «Отправить в Telegram» sends the track's text with it


class TelegramConnectIn(BaseModel):
    token: str


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


class TelegramSentOut(BaseModel):
    ok: bool = True
    title: str
    light: bool = False  # the light copy was sent (the file is over Telegram's limit)
    lyrics: bool = False  # the track's text went with it


def _out(u: User) -> TelegramOut:
    return TelegramOut(connected=bool(u.tg_bot_token_enc), ready=bool(u.tg_bot_token_enc and u.tg_chat_id),
                       bot_username=u.tg_bot_username, chat_name=u.tg_chat_name, with_lyrics=bool(u.tg_with_lyrics))


async def _me(s: AsyncSession, user: User) -> User:
    u = await s.get(User, user.id)
    assert u is not None
    return u


def _listen(u: User) -> None:
    """The bot's chats are kept fresh in the background from now on."""
    if u.tg_bot_token_enc and not get_settings().disable_background:
        telegram_chats.start(u.id)


async def _chats_out(s: AsyncSession, u: User) -> list[TelegramChatOut]:
    return [TelegramChatOut(id=c.id, kind=c.kind, title=c.title, photo=c.photo_key if c.photo else None)
            for c in await telegram_chats.chats(s, u)]


@router.get("", response_model=TelegramOut)
async def status(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return _out(await _me(s, user))


@router.post("/connect", response_model=TelegramOut)
async def connect(body: TelegramConnectIn, user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session, scope="function")):
    """Save the bot's key (checked with Telegram first). The chat is bound at once when Start was pressed already."""
    try:
        token = telegram.clean_token(body.token)
        bot = await telegram.get_me(token)
        u = await _me(s, user)
        u.tg_bot_token_enc = encrypt(token)
        u.tg_bot_username = str(bot.get("username") or "")[:64] or None
        u.tg_chat_id = u.tg_chat_name = None
        await telegram_chats.forget(s, u.id)
        await telegram_chats.sync(s, u)
    except telegram.TelegramError as e:
        raise HTTPException(400, str(e)) from e
    await s.commit()
    _listen(u)
    return _out(u)


@router.post("/check", response_model=TelegramOut)
async def check(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """Look for the chat again: the user has pressed Start in the bot."""
    u = await _me(s, user)
    if not u.tg_bot_token_enc:
        raise HTTPException(409, "Бот не подключён")
    try:
        await telegram_chats.sync(s, u)
    except telegram.TelegramError as e:
        raise HTTPException(400, str(e)) from e
    await s.commit()
    _listen(u)
    return _out(u)


@router.get("/chats", response_model=list[TelegramChatOut])
async def chats(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """Where the bot can send: the private chat first, then the groups and channels the user added it to."""
    u = await _me(s, user)
    try:
        await telegram_chats.sync(s, u)
        await s.commit()
    except telegram.TelegramError as e:  # the list as it was
        log.info("telegram: chats not synced: %s", e)
    _listen(u)
    return await _chats_out(s, u)


@router.post("/chats/refresh", response_model=list[TelegramChatOut])
async def refresh_chats(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """«Обновить»: names and pictures again; a chat the bot was removed from leaves the list."""
    u = await _me(s, user)
    if not u.tg_bot_token_enc:
        raise HTTPException(409, "Бот не подключён")
    try:
        await telegram_chats.sync(s, u)
        await telegram_chats.refresh(s, u)
    except telegram.TelegramError as e:
        raise HTTPException(502, str(e)) from e
    await s.commit()
    _listen(u)
    return await _chats_out(s, u)


@router.get("/chats/{chat}/photo")
async def chat_photo(chat: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    row = await s.get(TgChat, chat)
    if row is None or row.user_id != user.id or not row.photo:
        raise HTTPException(404, "У чата нет картинки")
    return Response(row.photo, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.delete("", response_model=TelegramOut)
async def disconnect(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    u = await _me(s, user)
    u.tg_bot_token_enc = u.tg_bot_username = u.tg_chat_id = u.tg_chat_name = None
    await telegram_chats.forget(s, u.id)
    await s.commit()
    return _out(u)


@router.put("/prefs", response_model=TelegramOut)
async def prefs(body: TelegramPrefsIn, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    u = await _me(s, user)
    u.tg_with_lyrics = body.with_lyrics
    await s.commit()
    return _out(u)


@router.post("/send", response_model=TelegramSentOut)
async def send(body: TelegramSendIn, user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")):
    u = await _me(s, user)
    if not (u.tg_bot_token_enc and u.tg_chat_id):
        raise HTTPException(409, "Сначала подключите своего бота Telegram")
    target = u.tg_chat_id
    if body.chat is not None:
        row = await s.get(TgChat, body.chat)
        if row is None or row.user_id != u.id:
            raise HTTPException(404, "Этого чата больше нет в списке бота — обновите список")
        target = row.chat_id
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
    if body.with_lyrics if body.with_lyrics is not None else u.tg_with_lyrics:
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
    try:
        await telegram.send_audio(decrypt(u.tg_bot_token_enc), target, path, filename=f"{name}{path.suffix}",
                                  title=track.title, performer=track.artist, duration_s=track.duration_s, cover=cover,
                                  lyrics=text, link=link)
    except telegram.TelegramError as e:
        raise HTTPException(502, str(e)) from e
    return TelegramSentOut(title=track.title, light=is_light, lyrics=bool(text))
