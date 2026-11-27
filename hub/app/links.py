"""A library track on music services: its ids there (`models.TrackLink`) and the links made of them («Поделиться
ссылкой»).

An import names every track by the service's own id (Spotify, Yandex Music), and so do the sources of mixes
(YouTube Music), remembered in
`source_map` against the catalog. `collect` copies those to the track, so they outlive the import they came with.
Deezer's id and the page the file was downloaded from are the track's own columns and are read from there.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.library.files import main_of
from app.library.tracks import dz_id
from app.models import Track, TrackLink, utcnow

log = logging.getLogger(__name__)

TITLES = {"spotify": "Spotify", "yandex": "Яндекс Музыка", "ytm": "YouTube Music", "deezer": "Deezer",
          "youtube": "YouTube", "soundcloud": "SoundCloud"}
URLS = {"spotify": "https://open.spotify.com/track/{}", "yandex": "https://music.yandex.ru/track/{}",
        "ytm": "https://music.youtube.com/watch?v={}", "deezer": "https://www.deezer.com/track/{}"}
ORDER = ["spotify", "yandex", "ytm", "deezer", "youtube", "soundcloud"]
YOUTUBE_ID = re.compile(r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/)([\w-]{11})")

# Copy provider IDs from matched import rows. Local tracks have synthetic IDs and no catalog link.
FROM_IMPORTS = """
insert into track_links (track_id, service, external_id, created_at)
select it.track_id, i.source, it.external_id, :now
from import_items it join imports i on i.id = it.import_id join import_collections c on c.id = it.collection_id
where it.track_id is not null and c.kind in ('likes', 'playlist', 'dislikes') and strpos(it.external_id, ':') = 0
"""
# A service id that is surely a catalog track the library has.
FROM_SOURCE_MAP = """
insert into track_links (track_id, service, external_id, created_at)
select t.id, m.source, m.external_id, :now
from source_map m join tracks t on m.ref = t.ref
where m.kind = 'track' and m.match = 'exact' and t.ref is not null
"""


async def collect(s: AsyncSession, track_id: int | None = None) -> None:
    """Copy the service ids the Hub has come across to the tracks (to one track, or to all). An id a track has
    already stays."""
    params: dict[str, Any] = {"now": utcnow()}
    only_imports = only_map = ""
    if track_id is not None:
        params["t"] = track_id
        only_imports, only_map = " and it.track_id = :t", " and t.id = :t"
    await s.execute(text(FROM_IMPORTS + only_imports + " on conflict do nothing"), params)
    await s.execute(text(FROM_SOURCE_MAP + only_map + " on conflict do nothing"), params)


def open_url(*, deezer: str | None = None, isrc: str | None = None, spotify: str | None = None,
             artist: str | None = None, title: str | None = None, album: str | None = None,
             version_label: str | None = None) -> str | None:
    """The «HomeMusic» link of a track: the page that belongs to no server (`share_page_url`) with the track named
    after the `#` — its catalog id, ISRC, Spotify id, and the names to search by where the ids are unknown. Whoever
    opens it gets the track in their own HomeMusic (`find`)."""
    from app.config import get_settings

    base = (get_settings().share_page_url or "").strip()
    fields = {"dz": deezer, "isrc": isrc, "sp": spotify, "a": artist, "t": title, "al": album, "v": version_label}
    fields = {k: str(v).strip()[:200] for k, v in fields.items() if v and str(v).strip()}
    if not base or not (fields.keys() & {"dz", "isrc", "sp", "t"}):
        return None
    return f"{base.rstrip('/')}/#{urlencode(fields)}"


def of_ids(ids: dict[str, str], urls: dict[str, str] | None = None, homemusic: str | None = None) -> dict[str, Any]:
    """`ids`: service -> the track's id there; `urls`: service -> a ready link."""
    urls = {**{k: URLS[k].format(v) for k, v in ids.items() if k in URLS}, **(urls or {})}
    return {"links": [{"service": k, "title": TITLES[k], "url": urls[k]} for k in ORDER if k in urls],
            "homemusic": (homemusic if homemusic is not None
                          else open_url(deezer=ids.get("deezer"), spotify=ids.get("spotify"))) or None}


async def of_track(s: AsyncSession, track: Track) -> dict[str, Any]:
    from app.library.versions import label_of

    spotify = await spotify_id(s, track=track)
    await collect(s, track.id)
    ids = dict((await s.execute(select(TrackLink.service, TrackLink.external_id)
                                .where(TrackLink.track_id == track.id))).all())
    ids = {k: v for k, v in ids.items() if k in TITLES}
    if dz_id(track.ref):
        ids["deezer"] = str(dz_id(track.ref))
    if spotify:
        ids["spotify"] = spotify
    urls: dict[str, str] = {}
    file = await main_of(s, track.id)
    src = (file.source_url if file else None) or ""
    if m := YOUTUBE_ID.search(src):  # the upload the file is from
        urls["youtube"] = f"https://www.youtube.com/watch?v={m.group(1)}"
    elif "soundcloud.com/" in src:
        urls["soundcloud"] = src
    return of_ids(ids, urls, open_url(deezer=ids.get("deezer"), isrc=track.isrc, spotify=ids.get("spotify"),
                                      artist=track.artist, title=track.title, album=track.album,
                                      version_label=await label_of(s, track.id)) or "")


async def spotify_id(s: AsyncSession, *, track: Track | None = None, ref: str | None = None) -> str | None:
    """Find and remember a Spotify identity for the exact recording."""
    from app.catalog.identity import matched_track
    from app.services import svc

    ref = track.ref if track and track.ref else ref
    if ref and ref.startswith("sp:"):
        return ref[3:]
    if track is not None:
        known = await s.get(TrackLink, (track.id, "spotify"))
        if known is not None:
            return known.external_id
        if external := (track.catalog or {}).get("spotify_id"):
            return external
    if svc.spotify is None or (track is None and not ref):
        return None
    try:
        async with asyncio.timeout(6):
            subject = track if track is not None else await svc.track(ref)
            result = await svc.spotify.search(f"{subject.artist} {subject.title}", kinds=("track",), limit=12)
            match = matched_track(subject, result.tracks)
    except Exception as error:
        log.info("Spotify track link %s unavailable: %r", ref, error)
        return None
    if match is None:
        return None
    if track is not None:
        await s.execute(insert(TrackLink).values(track_id=track.id, service="spotify", external_id=match.id,
                                                created_at=utcnow()).on_conflict_do_nothing())
    return match.id


async def find(s: AsyncSession, *, deezer: str | None = None, isrc: str | None = None, spotify: str | None = None,
               artist: str | None = None, title: str | None = None) -> Track | None:
    """The library track a «HomeMusic» link names: by its catalog id, its Spotify id, its ISRC, then by the names."""
    from app.matching.normalize import fingerprint

    here = Track.nd_song_id.is_not(None)
    queries = []
    if deezer:
        queries.append(select(Track).where(here, Track.ref == f"dz:{deezer}"))
    if spotify:
        queries.append(select(Track).where(here, Track.ref == f"sp:{spotify}"))
        queries.append(select(Track).join(TrackLink, TrackLink.track_id == Track.id)
                       .where(here, TrackLink.service == "spotify", TrackLink.external_id == spotify))
    if isrc:
        queries.append(select(Track).where(here, Track.isrc == isrc.upper().replace("-", "")))
    if artist and title and not spotify:
        queries.append(select(Track).where(here, Track.fingerprint == fingerprint(artist, title)))
    for q in queries:
        track = (await s.execute(q.order_by(Track.id.desc()).limit(1))).scalar_one_or_none()
        if track is not None:
            return track
    return None
