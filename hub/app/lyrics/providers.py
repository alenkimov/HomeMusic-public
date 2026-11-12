"""Where lyrics come from. A provider takes what the Hub knows of a track and returns a `Found` or None.

- `file`    — the text in the file's own tags, or an .lrc next to it: what the owner put there comes first;
- `lrclib`  — LRCLIB, a free and open database (line-synced, some word-synced), no key;
- `lrcred`  — lrc.red: word-synced texts found by the recording's ISRC, no key;
- `lyricsplus` — LyricsPlus (a public instance of the open YouLy+ backend): word-synced texts by ISRC or by the
  names; what it serves is gathered from music services, and the instance is somebody's own — it may go away;
- `netease` — NetEase Cloud Music, strong on Asian music; its API is not an official one and may change;
- `ovh`     — lyrics.ovh, plain text only: the last resort.

Each is asked in the order of the settings (`service.py`); a provider that fails is skipped, never fatal.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from rapidfuzz import fuzz

from app.lyrics import parse
from app.matching.normalize import normalize_artist
from app.net import make_client

log = logging.getLogger(__name__)

UA = "HomeMusicHub/0.1 (self-hosted music server; https://github.com/justman-1/HomeMusic-public)"
DURATION_TOLERANCE_S = 3
TITLES = {"file": "Теги файла", "lrclib": "LRCLIB", "lrcred": "lrc.red", "lyricsplus": "LyricsPlus",
          "netease": "NetEase", "ovh": "lyrics.ovh"}
ORDER = ["file", "lrclib", "lrcred", "lyricsplus", "netease", "ovh"]
ISRC_TOLERANCE_S = 5  # an ISRC names the recording: the length only guards against a wrong record


@dataclass
class Query:
    artist: str  # the main artist
    title: str
    album: str | None = None
    duration_s: int | None = None
    path: Path | None = None  # the file in the library
    isrc: str | None = None  # the recording's code: a source that knows it needs no guessing by names


@dataclass
class Found:
    source: str
    lines: list[parse.Line] | None = None
    plain: str | None = None
    instrumental: bool = False  # the source says the track has no words at all (then there is no text)

    @property
    def kind(self) -> str:
        return parse.kind_of(self.lines, self.plain)


def _from_text(source: str, text: str | None) -> Found | None:
    text = (text or "").strip()
    if not text:
        return None
    if parse.has_timestamps(text):
        lines = parse.parse_lrc(text)
        if lines:
            return Found(source, lines=lines)
    return Found(source, plain=text)


# --- the file itself ----------------------------------------------------------------------------------


def _read_tags(path: Path) -> str | None:
    import mutagen

    f = mutagen.File(path)
    if f is None or f.tags is None:
        return None
    tags = f.tags
    for key in list(tags.keys()):
        k = str(key)
        low = k.lower()
        if k.startswith("USLT") or k == "\xa9lyr" or low in ("lyrics", "unsyncedlyrics", "syncedlyrics") or low.endswith(":lyrics"):
            v = tags[key]
            v = v[0] if isinstance(v, list) and v else v
            text = getattr(v, "text", v)
            if isinstance(text, list):
                text = text[0] if text else ""
            if isinstance(text, bytes):
                text = text.decode("utf-8", "replace")
            if str(text).strip():
                return str(text)
    return None


async def from_file(q: Query) -> Found | None:
    if q.path is None or not q.path.is_file():
        return None
    side = q.path.with_suffix(".lrc")
    if side.is_file():
        try:
            if (found := _from_text("file", side.read_text(encoding="utf-8-sig", errors="replace"))) is not None:
                return found
        except OSError:
            pass
    return _from_text("file", await asyncio.to_thread(_read_tags, q.path))


# --- LRCLIB -------------------------------------------------------------------------------------------


def _lrclib_found(d: dict[str, Any]) -> Found | None:
    if d.get("instrumental"):
        return None
    if d.get("hasWordSync") and d.get("lyricsfile"):
        try:
            lines = parse.parse_lyricsfile(yaml.safe_load(d["lyricsfile"]))
            if lines:
                return Found("lrclib", lines=lines)
        except yaml.YAMLError:
            pass
    if d.get("syncedLyrics"):
        lines = parse.parse_lrc(d["syncedLyrics"])
        if lines:
            return Found("lrclib", lines=lines)
    return Found("lrclib", plain=d["plainLyrics"]) if (d.get("plainLyrics") or "").strip() else None


async def from_lrclib(q: Query) -> Found | None:
    params: dict[str, Any] = {"artist_name": q.artist, "track_name": q.title}
    if q.duration_s:
        params["duration"] = q.duration_s
    async with make_client(headers={"User-Agent": UA}, timeout=20) as c:
        r = await c.get("https://lrclib.net/api/get", params=params)
        if r.status_code == 200:
            d = r.json()
            # the record of this very track (artist, title, length) says so; a looser search match is not trusted
            return Found("lrclib", instrumental=True) if d.get("instrumental") else _lrclib_found(d)
        if r.status_code != 404:
            r.raise_for_status()
        # no exact record: the search is looser about the names, the duration still has to agree
        r = await c.get("https://lrclib.net/api/search", params={"artist_name": q.artist, "track_name": q.title})
        if r.status_code != 200:
            return None
        best: Found | None = None
        for d in r.json() or []:
            if q.duration_s and d.get("duration") and abs(float(d["duration"]) - q.duration_s) > DURATION_TOLERANCE_S:
                continue
            found = _lrclib_found(d)
            if found is not None and (best is None or (found.lines and not best.lines)):
                best = found
                if best.lines:
                    break
        return best


# --- lrc.red ------------------------------------------------------------------------------------------


def _length_agrees(q: Query, seconds: Any, tolerance: int) -> bool:
    return not (q.duration_s and isinstance(seconds, (int, float)) and seconds > 0
                and abs(float(seconds) - q.duration_s) > tolerance)


async def from_lrcred(q: Query) -> Found | None:
    """lrc.red knows a text by the ISRC of the recording: no ISRC, nothing to ask."""
    if not q.isrc:
        return None
    async with make_client(headers={"User-Agent": UA}, timeout=20) as c:
        r = await c.get(f"https://lrc.red/s/{quote(q.isrc.upper(), safe='')}.json")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        meta = r.json() or {}
        if not _length_agrees(q, meta.get("duration"), ISRC_TOLERANCE_S):
            return None
        files = meta.get("files") or {}
        if url := files.get("lyricsfile"):
            r = await c.get(url)
            if r.status_code == 200:
                try:
                    lines = parse.parse_lyricsfile(yaml.safe_load(r.text))
                    if lines:
                        return Found("lrcred", lines=lines)
                except yaml.YAMLError:
                    pass
        if url := files.get("lrc"):
            r = await c.get(url)
            if r.status_code == 200 and (lines := parse.parse_lrc(r.text)):
                return Found("lrcred", lines=lines)
    return None


# --- LyricsPlus ---------------------------------------------------------------------------------------

LYRICSPLUS_URL = "https://lyricsplus.prjktla.my.id/v2/lyrics/get"


async def from_lyricsplus(q: Query) -> Found | None:
    """By the ISRC where there is one; else by the names and the length, which the service matches itself — then
    its text is taken only if its length agrees with the track's."""
    params: dict[str, Any] = {"isrc": q.isrc.upper()} if q.isrc else {"title": q.title, "artist": q.artist}
    if not q.isrc:
        if not q.duration_s:
            return None  # names alone, with no length to check the answer by, are a guess
        params["duration"] = q.duration_s
        if q.album:
            params["album"] = q.album
    async with make_client(headers={"User-Agent": UA}, timeout=25) as c:
        r = await c.get(LYRICSPLUS_URL, params=params)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json() or {}
    total = ((d.get("metadata") or {}).get("totalDuration"))
    seconds = total / 1000 if isinstance(total, (int, float)) and total > 3600 else total  # it gives ms
    if not _length_agrees(q, seconds, ISRC_TOLERANCE_S if q.isrc else DURATION_TOLERANCE_S):
        return None
    lines = parse.parse_lyricsplus(d.get("lyrics"))
    return Found("lyricsplus", lines=lines) if lines else None


# --- NetEase ------------------------------------------------------------------------------------------

NETEASE_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                 "Chrome/140.0 Safari/537.36", "Referer": "https://music.163.com/"}


def _same_song(q: Query, name: str, artists: list[str], duration_ms: int | None) -> bool:
    if q.duration_s and duration_ms and abs(duration_ms / 1000 - q.duration_s) > DURATION_TOLERANCE_S:
        return False
    if fuzz.token_set_ratio(q.title.casefold(), (name or "").casefold()) < 85:
        return False
    want = normalize_artist(q.artist)
    for a in artists:
        got = normalize_artist(a)
        if fuzz.ratio(want.text, got.text) >= 80 or (want.latin and got.latin and fuzz.ratio(want.latin, got.latin) >= 80):
            return True
    # NetEase often names the artist in another script: a title and a length that agree to the second are enough
    return bool(q.duration_s and duration_ms and abs(duration_ms / 1000 - q.duration_s) <= 1
                and fuzz.ratio(q.title.casefold(), (name or "").casefold()) >= 95)


async def from_netease(q: Query) -> Found | None:
    async with make_client(headers=NETEASE_HEADERS, timeout=20, http2=False) as c:
        r = await c.get("https://music.163.com/api/search/get", params={"s": f"{q.artist} {q.title}", "type": 1, "limit": 10})
        r.raise_for_status()
        songs = ((r.json() or {}).get("result") or {}).get("songs") or []
        song = next((x for x in songs if _same_song(q, x.get("name") or "", [a.get("name") or "" for a in x.get("artists") or []],
                                                    x.get("duration"))), None)
        if song is None:
            return None
        r = await c.get("https://music.163.com/api/song/lyric/v1",
                        params={"id": song["id"], "cp": "false", "lv": 0, "yv": 1, "tv": 0, "rv": 0, "kv": 0, "ytv": 0, "yrv": 0})
        r.raise_for_status()
        d = r.json() or {}
    if (yrc := ((d.get("yrc") or {}).get("lyric") or "")).strip():
        lines = parse.parse_yrc(yrc)
        if lines:
            return Found("netease", lines=lines)
    lrc = ((d.get("lrc") or {}).get("lyric") or "").strip()
    if "纯音乐" in lrc[:60]:  # «pure music»: NetEase's mark of an instrumental
        return Found("netease", instrumental=True)
    if not lrc:
        return None
    lines = parse.parse_lrc(lrc)
    if lines:
        return Found("netease", lines=lines)
    text = "\n".join(ln for ln in lrc.splitlines() if ln.strip() and not ln.lstrip().startswith("{"))
    return Found("netease", plain=text) if text.strip() else None


# --- lyrics.ovh ---------------------------------------------------------------------------------------


async def from_ovh(q: Query) -> Found | None:
    async with make_client(headers={"User-Agent": UA}, timeout=20) as c:
        r = await c.get(f"https://api.lyrics.ovh/v1/{quote(q.artist, safe='')}/{quote(q.title, safe='')}")
    if r.status_code != 200:
        return None
    text = str((r.json() or {}).get("lyrics") or "").replace("\r\n", "\n").strip()
    # the service heads some texts with «Paroles de la chanson … par …»
    if text.lower().startswith("paroles de la chanson"):
        text = text.split("\n", 1)[1].strip() if "\n" in text else ""
    return Found("ovh", plain=text) if text else None


PROVIDERS: dict[str, Callable[[Query], Awaitable[Found | None]]] = {
    "file": from_file, "lrclib": from_lrclib, "lrcred": from_lrcred, "lyricsplus": from_lyricsplus,
    "netease": from_netease, "ovh": from_ovh,
}


async def ask(source: str, q: Query) -> Found | None:
    """One provider; its failure is logged and reads as «nothing found»."""
    fn = PROVIDERS.get(source)
    if fn is None:
        return None
    try:
        return await fn(q)
    except Exception as e:
        log.info("lyrics: %s failed for %s - %s: %r", source, q.artist, q.title, e)
        return None
