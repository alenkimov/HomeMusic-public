"""A library track on music services: its ids there (`models.TrackLink`) and the links made of them («Поделиться
ссылкой»).

The Hub never asks a service for these ids — it keeps the ones it comes across: an import names every track by the
service's own id (Spotify's CSV, Yandex Music), and so do the sources of mixes (YouTube Music), remembered in
`source_map` against the catalog. `collect` copies those to the track, so they outlive the import they came with.
Deezer's id and the page the file was downloaded from are the track's own columns and are read from there.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Track, TrackLink, utcnow

TITLES = {"spotify": "Spotify", "yandex": "Яндекс Музыка", "ytm": "YouTube Music", "deezer": "Deezer",
          "youtube": "YouTube", "soundcloud": "SoundCloud"}
URLS = {"spotify": "https://open.spotify.com/track/{}", "yandex": "https://music.yandex.ru/track/{}",
        "ytm": "https://music.youtube.com/watch?v={}", "deezer": "https://www.deezer.com/track/{}"}
ORDER = ["spotify", "yandex", "ytm", "deezer", "youtube", "soundcloud"]
# song.link opens the same song on every service (Apple Music and the others the Hub knows nothing of)
UNIVERSAL = {"spotify": "https://song.link/s/{}", "deezer": "https://song.link/d/{}", "ytm": "https://song.link/y/{}",
             "youtube": "https://song.link/y/{}"}
YOUTUBE_ID = re.compile(r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/)([\w-]{11})")

# An import's row that is a library track: the row's id is the service's id of that track. Rows without one
# (`isrc:…`, `name:…` of a CSV line that had no link) are not ids.
FROM_IMPORTS = """
insert or ignore into track_links (track_id, service, external_id, created_at)
select it.track_id, i.source, it.external_id, :now
from import_items it join imports i on i.id = it.import_id join import_collections c on c.id = it.collection_id
where it.track_id is not null and c.kind in ('likes', 'playlist', 'dislikes') and instr(it.external_id, ':') = 0
"""
# A service id that is surely a catalog track the library has.
FROM_SOURCE_MAP = """
insert or ignore into track_links (track_id, service, external_id, created_at)
select t.id, m.source, m.external_id, :now
from source_map m join tracks t on m.ref = 'dz:' || t.deezer_track_id
where m.kind = 'track' and m.match = 'exact' and t.deezer_track_id is not null
"""


def stamp() -> str:
    """Now, the way SQLAlchemy writes a DateTime into SQLite."""
    return utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")


async def collect(s: AsyncSession, track_id: int | None = None) -> None:
    """Copy the service ids the Hub has come across to the tracks (to one track, or to all). An id a track has
    already stays."""
    params: dict[str, Any] = {"now": stamp()}
    only_imports = only_map = ""
    if track_id is not None:
        params["t"] = track_id
        only_imports, only_map = " and it.track_id = :t", " and t.id = :t"
    await s.execute(text(FROM_IMPORTS + only_imports), params)
    await s.execute(text(FROM_SOURCE_MAP + only_map), params)


def _universal(ids: dict[str, str]) -> str | None:
    for service, url in UNIVERSAL.items():
        if ids.get(service):
            return url.format(ids[service])
    return None


def open_url(*, deezer: str | None = None, isrc: str | None = None, spotify: str | None = None,
             artist: str | None = None, title: str | None = None, album: str | None = None) -> str | None:
    """The «HomeMusic» link of a track: the page that belongs to no server (`share_page_url`) with the track named
    after the `#` — its catalog id, ISRC, Spotify id, and the names to search by where the ids are unknown. Whoever
    opens it gets the track in their own HomeMusic (`find`)."""
    from app.config import get_settings

    base = (get_settings().share_page_url or "").strip()
    fields = {"dz": deezer, "isrc": isrc, "sp": spotify, "a": artist, "t": title, "al": album}
    fields = {k: str(v).strip()[:200] for k, v in fields.items() if v and str(v).strip()}
    if not base or not (fields.keys() & {"dz", "isrc", "sp", "t"}):
        return None
    return f"{base.rstrip('/')}/#{urlencode(fields)}"


def of_ids(ids: dict[str, str], urls: dict[str, str] | None = None, homemusic: str | None = None) -> dict[str, Any]:
    """`ids`: service -> the track's id there; `urls`: service -> a ready link."""
    urls = {**{k: URLS[k].format(v) for k, v in ids.items() if k in URLS}, **(urls or {})}
    return {"links": [{"service": k, "title": TITLES[k], "url": urls[k]} for k in ORDER if k in urls],
            "universal": _universal(ids),
            "homemusic": (homemusic if homemusic is not None
                          else open_url(deezer=ids.get("deezer"), spotify=ids.get("spotify"))) or None}


async def of_track(s: AsyncSession, track: Track) -> dict[str, Any]:
    await collect(s, track.id)
    ids = dict((await s.execute(select(TrackLink.service, TrackLink.external_id)
                                .where(TrackLink.track_id == track.id))).all())
    ids = {k: v for k, v in ids.items() if k in TITLES}
    if track.deezer_track_id:
        ids["deezer"] = str(track.deezer_track_id)
    urls: dict[str, str] = {}
    src = track.source_url or ""
    if m := YOUTUBE_ID.search(src):  # the upload the file is from
        ids["youtube"], urls["youtube"] = m.group(1), f"https://www.youtube.com/watch?v={m.group(1)}"
    elif "soundcloud.com/" in src:
        urls["soundcloud"] = src
    return of_ids(ids, urls, open_url(deezer=ids.get("deezer"), isrc=track.isrc, spotify=ids.get("spotify"),
                                      artist=track.artist, title=track.title, album=track.album) or "")


async def find(s: AsyncSession, *, deezer: str | None = None, isrc: str | None = None, spotify: str | None = None,
               artist: str | None = None, title: str | None = None) -> Track | None:
    """The library track a «HomeMusic» link names: by its catalog id, its Spotify id, its ISRC, then by the names."""
    from app.matching.normalize import fingerprint

    here = Track.nd_song_id.is_not(None)
    queries = []
    if deezer:
        queries.append(select(Track).where(here, Track.deezer_track_id == deezer))
    if spotify:
        queries.append(select(Track).join(TrackLink, TrackLink.track_id == Track.id)
                       .where(here, TrackLink.service == "spotify", TrackLink.external_id == spotify))
    if isrc:
        queries.append(select(Track).where(here, Track.isrc == isrc.upper().replace("-", "")))
    if artist and title:
        queries.append(select(Track).where(here, Track.fingerprint == fingerprint(artist, title)))
    for q in queries:
        track = (await s.execute(q.order_by(Track.id.desc()).limit(1))).scalar_one_or_none()
        if track is not None:
            return track
    return None
