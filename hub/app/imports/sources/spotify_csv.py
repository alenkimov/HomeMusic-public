"""A Spotify playlist exported to CSV (Exportify, https://exportify.app): one file is one playlist, in its order.

The file carries what the official API gives: track URI, names, album, duration and the ISRC, by which the catalog
match is exact where a text search fails (Deezer keeps Japanese titles romanized). `Liked_Songs.csv` is the likes.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from app.imports.sources.base import SnapCollection, SnapItem, Snapshot, SourceError

MAX_FILES = 30
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10_000
ISRC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")
_LIKES_RE = re.compile(r"^liked[\s_-]*songs$", re.I)


def clean_isrc(value: str | None) -> str | None:
    isrc = (value or "").replace("-", "").strip().upper()
    return isrc if ISRC_RE.match(isrc) else None


def _title(filename: str) -> str:
    name = re.sub(r"\.csv$", "", (filename or "").replace("\\", "/").rsplit("/", 1)[-1], flags=re.I)
    return re.sub(r"\s+", " ", name.replace("_", " ")).strip() or "Spotify"


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise SourceError("Не удалось прочитать файл: нужна кодировка UTF-8")


def _col(row: dict[str, str], *names: str) -> str:
    for n in names:
        if (v := row.get(n)) is not None:
            return v.strip()
    return ""


def track_raw(row: dict[str, str]) -> tuple[str, dict[str, Any]] | None:
    title = _col(row, "Track Name")
    artists = [a.strip() for a in re.split(r"[;,]", _col(row, "Artist Name(s)")) if a.strip()]
    if not title or not artists:
        return None
    try:
        duration = round(int(_col(row, "Track Duration (ms)", "Duration (ms)") or 0) / 1000) or None
    except ValueError:
        duration = None
    isrc = clean_isrc(_col(row, "ISRC"))
    uri = _col(row, "Track URI", "Spotify URI")
    track_id = uri.rsplit(":", 1)[-1] if uri.startswith("spotify:track:") else ""
    # a local file of the playlist has no Spotify id: the names are its key
    external = track_id or (f"isrc:{isrc}" if isrc else f"name:{artists[0].lower()}|{title.lower()}"[:120])
    raw: dict[str, Any] = {"artist": ", ".join(artists), "artists": artists, "title": title,
                           "album": _col(row, "Album Name") or None, "duration_s": duration}
    if isrc:
        raw["isrc"] = isrc
    if cover := _col(row, "Album Image URL"):
        raw["cover_url"] = cover
    if added := _col(row, "Added At"):
        raw["liked_at"] = added
    return external, raw


def parse_file(filename: str, data: bytes) -> SnapCollection:
    if len(data) > MAX_FILE_BYTES:
        raise SourceError(f"Файл «{filename}» больше {MAX_FILE_BYTES // (1024 * 1024)} МБ")
    reader = csv.DictReader(io.StringIO(_decode(data), newline=""))
    fields = set(reader.fieldnames or [])
    if not {"Track Name", "Artist Name(s)"} <= fields:
        raise SourceError(f"«{filename}» не похож на выгрузку Exportify: нет колонок Track Name и Artist Name(s)")
    title = _title(filename)
    likes = bool(_LIKES_RE.match(title))
    items: list[SnapItem] = []
    seen: set[str] = set()
    for n, row in enumerate(reader):
        if n >= MAX_ROWS:
            break
        parsed = track_raw(row)
        if parsed is None or parsed[0] in seen:
            continue
        seen.add(parsed[0])
        items.append(SnapItem(parsed[0], parsed[1]))
    if not items:
        raise SourceError(f"В файле «{filename}» нет треков")
    cover = next((i.raw["cover_url"] for i in items if i.raw.get("cover_url")), None)
    return SnapCollection(kind="likes" if likes else "playlist", external_id=f"csv:{title.lower()}",
                          title="Любимые треки" if likes else title, items=items, cover_url=cover)


def parse(files: list[tuple[str, bytes]]) -> Snapshot:
    if not files:
        raise SourceError("Выберите файл CSV")
    if len(files) > MAX_FILES:
        raise SourceError(f"За один раз не больше {MAX_FILES} файлов")
    colls: dict[str, SnapCollection] = {}
    for name, data in files:
        c = parse_file(name, data)
        colls[c.external_id] = c  # the same playlist twice: the last file wins
    ordered = sorted(colls.values(), key=lambda c: c.kind != "likes")
    return Snapshot(account=", ".join(c.title for c in ordered)[:200], collections=ordered)
