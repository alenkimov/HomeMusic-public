"""Evidence that two catalog records describe the same recording."""

from __future__ import annotations

from typing import Any

from app.catalog.models import CatalogTrack
from app.matching.normalize import clean_isrc, normalize_artist, normalize_title, version_kind
from app.matching.score import artist_sim


def same_recording(a: Any, b: Any) -> bool:
    if getattr(a, "spotify_id", None) and a.spotify_id == getattr(b, "spotify_id", None):
        return True
    if a.duration_s and b.duration_s and abs(a.duration_s - b.duration_s) > 3:
        return False
    explicit_a, explicit_b = getattr(a, "explicit", None), getattr(b, "explicit", None)
    if explicit_a is not None and explicit_b is not None and explicit_a != explicit_b:
        return False
    title_a, title_b = normalize_title(a.title), normalize_title(b.title)
    if version_kind(title_a.version) != version_kind(title_b.version):
        return False
    names_a = getattr(a, "artists", None) or [a.artist]
    names_b = getattr(b, "artists", None) or [b.artist]
    if not any(artist_sim(normalize_artist(x), normalize_artist(y)) == 1 for x in names_a for y in names_b):
        return False
    code_a, code_b = clean_isrc(getattr(a, "isrc", None)), clean_isrc(getattr(b, "isrc", None))
    if code_a and code_b:
        return code_a == code_b
    album_a = getattr(a, "album_title", None) or getattr(a, "album", None)
    album_b = getattr(b, "album_title", None) or getattr(b, "album", None)
    return bool(a.duration_s and b.duration_s and title_a.text == title_b.text
                and album_a and album_b and normalize_title(album_a).text == normalize_title(album_b).text)


def matched_track(track: Any, candidates: list[CatalogTrack]) -> CatalogTrack | None:
    matches = {t.ref: t for t in candidates if same_recording(track, t)}
    return next(iter(matches.values())) if len(matches) == 1 else None
