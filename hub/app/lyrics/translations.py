"""The translations of lyrics the Hub keeps (`models.LyricsTranslation`): one per track and language, made the first
time a listener asks for it and kept — the next time it opens at once, for everyone, without the internet.

A translation is line for line with the text it was made of (`source_sig`): another text chosen for the track makes
it stale and it is made again. Lines a listener corrected by hand (`edited_json`) survive a new translation of the
same text. Which translators are used, and in what order, is the admin's setting (`app_settings`).
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.lyrics import service, translate
from app.models import AppSetting, Lyrics, LyricsTranslation, Track, utcnow

SETTINGS_KEY = "lyrics_translate"
DEFAULT_ORDER = ("llm", "google", "mymemory")

_locks: dict[tuple[int, str], asyncio.Lock] = {}


@dataclass
class Result:
    status: str  # ready / same (the song is in that language) / skipped (a language the listener reads) / none
    lang: str
    detected: str | None = None
    provider: str | None = None
    lines: list[str] = field(default_factory=list)
    edited: list[int] = field(default_factory=list)


def source_lines(row: Lyrics | None) -> list[str]:
    """The text a translation goes line for line with: the timed lines, or the lines of a plain text."""
    if row is None or row.kind == "none":
        return []
    if row.lines_json:
        return [str(x.get("text") or "") for x in row.lines_json]
    return (row.plain or "").split("\n")


def sig_of(lines: list[str]) -> str:
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()


def _clean(value: dict[str, Any] | None) -> dict[str, Any]:
    seen: set[str] = set()
    out = []
    for x in (value or {}).get("providers") or []:
        pid = x.get("id") if isinstance(x, dict) else None
        if pid in translate.PROVIDERS and pid not in seen:
            seen.add(pid)
            out.append({"id": pid, "on": bool(x.get("on", True))})
    out += [{"id": pid, "on": True} for pid in DEFAULT_ORDER if pid not in seen]
    return {"providers": out}


async def get_settings(s: AsyncSession) -> dict[str, Any]:
    row = await s.get(AppSetting, SETTINGS_KEY)
    return _clean(row.value_json if row else None)


async def save_settings(s: AsyncSession, value: dict[str, Any]) -> dict[str, Any]:
    clean = _clean(value)
    row = await s.get(AppSetting, SETTINGS_KEY)
    if row is None:
        s.add(AppSetting(key=SETTINGS_KEY, value_json=clean))
    else:
        row.value_json = clean
    return clean


def _result(row: LyricsTranslation, detected: str | None) -> Result:
    return Result("ready", row.lang, detected, row.provider, list(row.lines_json or []), list(row.edited_json or []))


async def get(s: AsyncSession, track: Track, lang: str, *, skip: frozenset[str] = frozenset(), force: bool = False,
              provider: str | None = None) -> Result:
    """The track's lyrics in `lang`: kept, or made now. `skip` — the languages the listener reads (a song in one of
    them is left alone); `force` — translate anyway; `provider` — make it again with this translator."""
    lyrics = await service.ensure(s, track)
    lines = source_lines(lyrics)
    if not any(x.strip() for x in lines):
        await s.commit()
        return Result("none", lang)
    sig = sig_of(lines)
    detected = translate.detect("\n".join(lines))
    track_id, artist, title = track.id, track.artist, track.title
    kept = await s.get(LyricsTranslation, (track_id, lang))
    if kept is not None and kept.source_sig == sig and provider is None:
        return _result(kept, detected)
    if not force and provider is None:
        if detected == lang:
            return Result("same", lang, detected)
        if detected in skip:
            return Result("skipped", lang, detected)
    providers = [provider] if provider else [x["id"] for x in (await get_settings(s))["providers"] if x["on"]]
    await s.commit()  # nothing of the database is held while a translator thinks

    lock = _locks.setdefault((track_id, lang), asyncio.Lock())
    async with lock:
        kept = await s.get(LyricsTranslation, (track_id, lang), populate_existing=True)
        if kept is not None and kept.source_sig == sig and provider is None:  # made while this request waited
            return _result(kept, detected)
        got, by = await translate.translate(lines, lang, providers, artist=artist.split(",")[0].strip(), title=title,
                                             source=detected)
        if kept is None:
            kept = LyricsTranslation(track_id=track_id, lang=lang, edited_json=[])
            s.add(kept)
        elif kept.source_sig == sig:  # the same text translated again: the listener's own lines stay
            old = list(kept.lines_json or [])
            for i in kept.edited_json or []:
                if 0 <= i < len(got) and i < len(old):
                    got[i] = old[i]
        else:
            kept.edited_json = []
        kept.provider, kept.lines_json, kept.source_sig, kept.updated_at = by, got, sig, utcnow()
        await s.commit()
        return _result(kept, detected)


async def edit(s: AsyncSession, track_id: int, lang: str, index: int, text: str) -> Result:
    """A line corrected by hand."""
    row = await s.get(LyricsTranslation, (track_id, lang))
    if row is None or not 0 <= index < len(row.lines_json or []):
        raise LookupError("Такой строки в переводе нет")
    lines = list(row.lines_json)
    lines[index] = text.strip()
    row.lines_json = lines
    row.edited_json = sorted({*(row.edited_json or []), index})
    row.updated_at = utcnow()
    await s.commit()
    return _result(row, None)
