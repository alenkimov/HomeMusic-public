"""Lyrics of a library track (app/lyrics): for the player, the user's own choices and the admin's sources."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import admin_user, current_user
from app.db import get_session
from app.jobs.queue import notify
from app.lyrics import service, translate, translations
from app.lyrics.providers import TITLES
from app.models import Lyrics, Track, User

router = APIRouter(prefix="/api/lyrics", tags=["lyrics"])


class LyricsOut(BaseModel):
    track_id: int | None = None  # None: the track is not in the Hub's library (no lyrics for it)
    kind: str = "none"  # word / synced / plain / none
    source: str | None = None
    source_title: str | None = None
    lines: list[dict[str, Any]] | None = None
    plain: str | None = None
    offset_ms: int = 0
    pinned: bool = False
    # how the text is read (app/lyrics/reading.py), for a Japanese, Chinese or Korean one: `{"lang", "lines":
    # [Latin letters, line for line with `lines` or with the lines of `plain`], "ruby"?: [[[piece, kana or null]…]…]}`
    reading: dict[str, Any] | None = None


class CandidateOut(BaseModel):
    source: str
    source_title: str
    kind: str
    preview: str


class LyricsHitOut(BaseModel):
    track_id: int
    nd_song_id: str | None = None
    ref: str | None = None
    line: str = ""  # the line the words were found in


class OffsetIn(BaseModel):
    track_id: int
    offset_ms: int = Field(ge=-15_000, le=15_000)


class ChooseIn(BaseModel):
    track_id: int
    source: str | None = None  # None: this track has no lyrics (an instrumental, a wrong text)


class SourceIn(BaseModel):
    id: str
    on: bool = True


class SettingsIn(BaseModel):
    sources: list[SourceIn]
    retry_days: int = 7
    on_add: bool = True


class SettingsOut(BaseModel):
    sources: list[dict[str, Any]]  # {id, title, on}
    retry_days: int
    on_add: bool
    stats: dict[str, int]


def _out(row: Lyrics) -> LyricsOut:
    from app.lyrics import reading, translate, translations

    text = translations.source_lines(row)
    return LyricsOut(track_id=row.track_id, kind=row.kind, source=row.source, source_title=TITLES.get(row.source or ""),
                     lines=row.lines_json, plain=row.plain, offset_ms=row.offset_ms, pinned=row.pinned,
                     reading=reading.of(text, translate.detect("\n".join(text))) if text else None)


async def _own(s: AsyncSession, track_id: int) -> Track:
    track = await s.get(Track, track_id)
    if track is None:
        raise HTTPException(404, "Трек не найден в библиотеке")
    return track


@router.get("", response_model=LyricsOut)
async def get(nd_song_id: str | None = None, ref: str | None = None, user: User = Depends(current_user),
              s: AsyncSession = Depends(get_session, scope="function")):
    """The lyrics of a track the page names: from the Hub's database, or found now (seconds) and kept."""
    track = await service.track_of(s, nd_song_id=nd_song_id, ref=ref)
    if track is None:
        return LyricsOut()
    row = await service.ensure(s, track)
    await s.commit()
    return _out(row)


@router.get("/search", response_model=list[LyricsHitOut])
async def search(q: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The library's tracks whose lyrics have `q` in them (the search of a playlist looks inside the texts too)."""
    return await service.search(s, q)


@router.get("/candidates", response_model=list[CandidateOut])
async def candidates(track_id: int, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    found = await service.candidates(s, await _own(s, track_id))
    def preview(f) -> str:
        texts = [x["text"] for x in f.lines] if f.lines else (f.plain or "").splitlines()
        return "\n".join(texts[:4])

    return [CandidateOut(source=f.source, source_title=TITLES[f.source], kind=f.kind, preview=preview(f)) for f in found]


class SourceOut(BaseModel):
    id: str
    title: str


class ProbeOut(BaseModel):
    source: str
    source_title: str
    status: str  # found / none / timeout / error
    seconds: float
    kind: str | None = None
    preview: str | None = None


def _preview(f) -> str:
    texts = [x["text"] for x in f.lines] if f.lines else (f.plain or "").splitlines()
    return "\n".join(texts[:4])


@router.get("/sources", response_model=list[SourceOut])
async def sources(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The sources that are on, in the admin's order: «Другой вариант» asks each of them itself."""
    return [SourceOut(id=x, title=TITLES[x]) for x in service.enabled(await service.get_settings_row(s))]


@router.get("/probe", response_model=ProbeOut)
async def probe(track_id: int, source: str, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    """What one source has for the track, and how long it took to say (it is not waited for longer than
    `PROBE_WAIT_S`): the page asks the sources side by side and shows each as it answers."""
    if source not in TITLES:
        raise HTTPException(400, "Неизвестный источник")
    track = await _own(s, track_id)
    await s.close()  # nothing of the database is held while a source is slow
    status, found, seconds = await service.probe(track, source)
    return ProbeOut(source=source, source_title=TITLES[source], status=status, seconds=seconds,
                    kind=found.kind if found else None, preview=_preview(found) if found else None)


@router.post("/choose", response_model=LyricsOut)
async def choose(body: ChooseIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    if body.source is not None and body.source not in TITLES:
        raise HTTPException(400, "Неизвестный источник")
    try:
        row = await service.choose(s, await _own(s, body.track_id), body.source)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    await s.commit()
    return _out(row)


class ResetIn(BaseModel):
    track_id: int


@router.post("/reset", response_model=LyricsOut)
async def reset(body: ResetIn, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    """Forget the choice made by hand («no lyrics for this track», another source's text) and look for the text
    again, as for a new track."""
    track = await _own(s, body.track_id)
    row = await s.get(Lyrics, track.id)
    if row is not None:
        row.pinned = False
    row = await service.ensure(s, track, force=True)
    await s.commit()
    return _out(row)


@router.post("/offset", response_model=LyricsOut)
async def offset(body: OffsetIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    """The text runs early or late against this file: shift it (kept with the text, for everyone)."""
    row = await s.get(Lyrics, body.track_id)
    if row is None:
        raise HTTPException(404, "У этого трека нет текста")
    row.offset_ms = body.offset_ms
    await s.commit()
    return _out(row)


class TranslationOut(BaseModel):
    status: str  # ready / same (the song is in that language) / skipped (a language the listener reads) / none
    lang: str
    detected: str | None = None  # the language of the lyrics, where the Hub can tell
    provider: str | None = None
    provider_title: str | None = None
    lines: list[str] = []  # line for line with the lyrics (`lines`, or the lines of `plain`)
    edited: list[int] = []  # the lines corrected by hand


class RetranslateIn(BaseModel):
    track_id: int
    lang: str
    provider: str | None = None  # None: the admin's order


class TranslationLineIn(BaseModel):
    track_id: int
    lang: str
    index: int = Field(ge=0)
    text: str = Field(max_length=1000)


class TranslatorsOut(BaseModel):
    providers: list[dict[str, Any]]  # {id, title, on}, in the order they are tried
    langs: list[dict[str, str]]  # {code, title}
    llm: bool  # the Hub's LLM is set up


class TranslatorsIn(BaseModel):
    providers: list[SourceIn]


def _translation_out(r: translations.Result) -> TranslationOut:
    return TranslationOut(status=r.status, lang=r.lang, detected=r.detected, provider=r.provider,
                          provider_title=translate.TITLES.get(r.provider or ""), lines=r.lines, edited=r.edited)


def _lang(code: str) -> str:
    if code not in translate.LANGS:
        raise HTTPException(400, "Неизвестный язык перевода")
    return code


@router.get("/translation", response_model=TranslationOut)
async def translation(track_id: int, lang: str, skip: str = "", force: bool = False,
                      user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The track's lyrics in `lang`: from the database, or translated now (seconds with Google, up to minutes with a
    local model) and kept. `skip`: the languages the listener reads, comma-separated — a song in one of them is
    left alone unless `force`."""
    track = await _own(s, track_id)
    try:
        r = await translations.get(s, track, _lang(lang), skip=frozenset(x for x in skip.split(",") if x), force=force)
    except translate.TranslateError as e:
        raise HTTPException(502, str(e)) from e
    return _translation_out(r)


@router.post("/translation/redo", response_model=TranslationOut)
async def retranslate(body: RetranslateIn, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    """Translate again (with another translator): the lines corrected by hand stay."""
    if body.provider is not None and body.provider not in translate.PROVIDERS:
        raise HTTPException(400, "Неизвестный переводчик")
    track = await _own(s, body.track_id)
    order = [x["id"] for x in (await translations.get_settings(s))["providers"] if x["on"]]
    try:
        r = await translations.get(s, track, _lang(body.lang), force=True, provider=body.provider or (order[0] if order else "llm"))
    except translate.TranslateError as e:
        raise HTTPException(502, str(e)) from e
    return _translation_out(r)


@router.put("/translation/line", response_model=TranslationOut)
async def edit_translation(body: TranslationLineIn, user: User = Depends(current_user),
                           s: AsyncSession = Depends(get_session, scope="function")):
    """A line of the translation corrected by hand (kept for everyone, and through a new translation)."""
    try:
        r = await translations.edit(s, body.track_id, _lang(body.lang), body.index, body.text)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return _translation_out(r)


async def _translators_out(s: AsyncSession) -> TranslatorsOut:
    from app.ai.llm import configured

    v = await translations.get_settings(s)
    return TranslatorsOut(providers=[{**x, "title": translate.TITLES[x["id"]]} for x in v["providers"]],
                          langs=[{"code": code, "title": names[0]} for code, names in translate.LANGS.items()],
                          llm=configured())


@router.get("/translators", response_model=TranslatorsOut)
async def translators(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await _translators_out(s)


@router.put("/translators", response_model=TranslatorsOut)
async def save_translators(body: TranslatorsIn, user: User = Depends(admin_user),
                           s: AsyncSession = Depends(get_session, scope="function")):
    await translations.save_settings(s, body.model_dump())
    await s.commit()
    return await _translators_out(s)


async def _settings_out(s: AsyncSession) -> SettingsOut:
    v = await service.get_settings_row(s)
    return SettingsOut(sources=[{**x, "title": TITLES[x["id"]]} for x in v["sources"]], retry_days=v["retry_days"],
                       on_add=v["on_add"], stats=await service.stats(s))


@router.get("/settings", response_model=SettingsOut)
async def settings(user: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await _settings_out(s)


@router.put("/settings", response_model=SettingsOut)
async def save_settings(body: SettingsIn, user: User = Depends(admin_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    await service.save_settings(s, body.model_dump())
    await s.commit()
    return await _settings_out(s)


@router.post("/scan", response_model=SettingsOut)
async def scan(user: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    """Look for the lyrics of every track that was not checked yet, in the background."""
    await service.enqueue_scan_once(s, forced=True)
    await s.commit()
    notify()
    return await _settings_out(s)
