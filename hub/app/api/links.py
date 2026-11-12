"""«Поделиться ссылкой»: a track on the music services the Hub knows its id of (app/links.py)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import links
from app.auth import current_user
from app.catalog.models import split_ref
from app.db import get_session
from app.lyrics.service import track_of
from app.models import User

router = APIRouter(prefix="/api/links", tags=["links"])
log = logging.getLogger(__name__)


class LinkOut(BaseModel):
    service: str  # spotify/yandex/ytm/deezer/youtube/soundcloud
    title: str
    url: str


class LinksOut(BaseModel):
    links: list[LinkOut] = []
    universal: str | None = None  # song.link: the same song on every service
    homemusic: str | None = None  # opens in the HomeMusic of whoever follows it (`links.open_url`)


class OpenOut(BaseModel):
    """What a «HomeMusic» link turned out to be here."""
    nd_song_id: str | None = None  # in the library: play it
    ref: str | None = None  # not in the library, but in the catalog
    album_ref: str | None = None  # … on this album's page
    title: str | None = None
    artist: str | None = None
    query: str | None = None  # all there is to go by: search for it


@router.get("", response_model=LinksOut)
async def get(nd_song_id: str | None = None, ref: str | None = None, user: User = Depends(current_user),
              s: AsyncSession = Depends(get_session, scope="function")):
    track = await track_of(s, nd_song_id=nd_song_id, ref=ref)
    if track is not None:
        out = await links.of_track(s, track)
        await s.commit()
        return out
    if ref:  # a catalog track that is not in the library: its catalog id is all there is
        src, id_ = split_ref(ref)
        if src == "dz":
            return links.of_ids({"deezer": id_})
    return LinksOut()


@router.get("/open", response_model=OpenOut)
async def open_link(dz: str | None = Query(None, pattern=r"^\d{1,20}$"), isrc: str | None = Query(None, max_length=20),
                    sp: str | None = Query(None, max_length=40), a: str | None = Query(None, max_length=300),
                    t: str | None = Query(None, max_length=300), user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    """A «HomeMusic» link someone shared, opened here: the track in this library, else in the catalog, else the
    words to search by."""
    from app.services import svc

    track = await links.find(s, deezer=dz, isrc=isrc, spotify=sp, artist=a, title=t)
    if track is not None:
        return OpenOut(nd_song_id=track.nd_song_id, title=track.title, artist=track.artist)
    query = " ".join(x.strip() for x in (a, t) if x and x.strip()) or None
    found = None
    try:
        if dz:
            found = await svc.deezer.get_track(dz)
        elif isrc:
            found = await svc.deezer.get_track_by_isrc(isrc)
    except Exception as e:  # the catalog is away or has no such track: the names are still there
        log.info("open link: catalog track %s not read: %r", dz or isrc, e)
    if found is not None:
        return OpenOut(ref=found.ref, album_ref=found.album_ref, title=found.title, artist=found.artist,
                       query=query or f"{found.artist} {found.title}")
    return OpenOut(title=t, artist=a, query=query)
