"""«Отправить в Telegram»: the user's own bot (app/telegram.py) — connecting it and sending library tracks to it."""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import light, telegram
from app.auth import current_user
from app.config import get_settings
from app.db import get_session
from app.library import tagger
from app.lyrics import service as lyrics_service
from app.models import User
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


class TelegramSendIn(BaseModel):
    nd_song_id: str | None = None
    ref: str | None = None
    with_lyrics: bool | None = None  # None: as the user's setting says


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


async def _bind_chat(u: User, token: str) -> None:
    found = await telegram.find_chat(token)
    if found:
        u.tg_chat_id, u.tg_chat_name = found[0], found[1][:128]


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
        await _bind_chat(u, token)
    except telegram.TelegramError as e:
        raise HTTPException(400, str(e)) from e
    await s.commit()
    return _out(u)


@router.post("/check", response_model=TelegramOut)
async def check(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """Look for the chat again: the user has pressed Start in the bot."""
    u = await _me(s, user)
    if not u.tg_bot_token_enc:
        raise HTTPException(409, "Бот не подключён")
    try:
        await _bind_chat(u, decrypt(u.tg_bot_token_enc))
    except telegram.TelegramError as e:
        raise HTTPException(400, str(e)) from e
    await s.commit()
    return _out(u)


@router.delete("", response_model=TelegramOut)
async def disconnect(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    u = await _me(s, user)
    u.tg_bot_token_enc = u.tg_bot_username = u.tg_chat_id = u.tg_chat_name = None
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
    try:
        await telegram.send_audio(decrypt(u.tg_bot_token_enc), u.tg_chat_id, path, filename=f"{name}{path.suffix}",
                                  title=track.title, performer=track.artist, duration_s=track.duration_s, cover=cover,
                                  lyrics=text)
    except telegram.TelegramError as e:
        raise HTTPException(502, str(e)) from e
    return TelegramSentOut(title=track.title, light=is_light, lyrics=bool(text))
