"""«Поделиться ссылкой»: a track on the music services the Hub knows its id of (app/links.py)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import links
from app.auth import current_user
from app.catalog import popular
from app.catalog.models import split_ref
from app.catalog.spotify import ID as SPOTIFY_ID
from app.db import get_session, session_scope
from app.lyrics.service import track_of
from app.models import Track, User
from app.services import svc

router = APIRouter(prefix="/api/links", tags=["links"])
log = logging.getLogger(__name__)
_audience_gate = asyncio.Semaphore(6)


class LinkOut(BaseModel):
    service: str  # spotify/yandex/ytm/deezer/youtube/soundcloud
    title: str
    url: str


class LinksOut(BaseModel):
    links: list[LinkOut] = []
    homemusic: str | None = None  # opens in the HomeMusic of whoever follows it (`links.open_url`)


class OpenOut(BaseModel):
    """What a «HomeMusic» link turned out to be here."""
    nd_song_id: str | None = None  # in the library: play it
    ref: str | None = None  # not in the library, but in the catalog
    album_ref: str | None = None  # … on this album's page
    title: str | None = None
    artist: str | None = None
    query: str | None = None  # all there is to go by: search for it
    artist_ref: str | None = None
    playlist_id: str | None = None


class AudienceTrackIn(BaseModel):
    ref: str = Field(min_length=3, max_length=280, pattern=r"^[a-z]+:.+$")
    nd_song_id: str | None = Field(None, max_length=128)
    library_track_id: int | None = Field(None, gt=0)
    spotify_id: str | None = Field(None, max_length=128)
    artist: str = Field(max_length=512)
    title: str = Field(max_length=512)
    spotify: bool = False
    lastfm: bool = False


class AudienceIn(BaseModel):
    tracks: list[AudienceTrackIn] = Field(max_length=16)


class AudienceOut(BaseModel):
    spotify_plays: int | None = None
    lastfm_listeners: int | None = None


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
        ids = {"deezer": id_} if src == "dz" else {}
        if spotify := await links.spotify_id(s, ref=ref):
            ids["spotify"] = spotify
        return links.of_ids(ids)
    return LinksOut()


@router.get("/spotify-track")
async def spotify_track(nd_song_id: str | None = None, ref: str | None = None, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    from app.services import svc

    track = await track_of(s, nd_song_id=nd_song_id, ref=ref)
    id_ = await links.spotify_id(s, track=track, ref=ref)
    await s.commit()
    if id_ and svc.spotify is not None:
        try:
            return {"spotify_plays": (await svc.spotify.track(id_)).spotify_plays, "spotify_id": id_}
        except Exception as error:
            log.info("Spotify track %s plays unavailable: %r", id_, error)
    return {"spotify_plays": None, "spotify_id": id_}


async def _spotify_audience(track: AudienceTrackIn) -> int | None:
    if svc.spotify is None:
        return None
    external = track.spotify_id
    if not external or not SPOTIFY_ID.fullmatch(external):
        async with session_scope() as s:
            local = await s.get(Track, track.library_track_id) if track.library_track_id else None
            local = local or await track_of(s, nd_song_id=track.nd_song_id, ref=track.ref)
            external = await links.spotify_id(s, track=local, ref=track.ref)
    if external and SPOTIFY_ID.fullmatch(external):
        return (await svc.spotify.track(external)).spotify_plays
    return None


@router.post("/track-audience", response_model=list[AudienceOut])
async def track_audience(body: AudienceIn, user: User = Depends(current_user)):
    """Fetch just the missing public metrics for the rows the listener can see."""
    async def read(track: AudienceTrackIn) -> AudienceOut:
        async def metric(name: str) -> int | None:
            try:
                async with _audience_gate, asyncio.timeout(14):
                    return (await _spotify_audience(track) if name == "spotify" else
                            await popular.track_listeners(track.artist, track.title))
            except Exception as error:
                log.debug("%s audience for %s unavailable: %r", name, track.ref, error)
                return None

        fields = [name for name in ("spotify", "lastfm") if getattr(track, name)]
        keys = {"spotify": "spotify_plays", "lastfm": "lastfm_listeners"}
        values = await asyncio.gather(*(metric(name) for name in fields))
        return AudienceOut(**{keys[name]: value for name, value in zip(fields, values, strict=True)})

    return await asyncio.gather(*(read(track) for track in body.tracks))


@router.get("/open", response_model=OpenOut)
async def open_link(dz: str | None = Query(None, pattern=r"^\d{1,20}$"), isrc: str | None = Query(None, max_length=20),
                    sp: str | None = Query(None, max_length=40), a: str | None = Query(None, max_length=300),
                    t: str | None = Query(None, max_length=300), url: str | None = Query(None, max_length=2048),
                    user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    """A «HomeMusic» link someone shared, opened here: the track in this library, else in the catalog, else the
    words to search by."""
    from app.services import svc

    if url:
        from app.catalog.spotify import expand

        try:
            resource = await expand(url)
        except Exception as error:
            log.info("Spotify link couldn't be expanded: %r", error)
            resource = None
        if not resource:
            return OpenOut()
        kind, id_ = resource
        if kind == "artist":
            return OpenOut(artist_ref=f"sp:{id_}")
        if kind == "album":
            return OpenOut(album_ref=f"sp:{id_}")
        if kind == "playlist":
            return OpenOut(playlist_id=id_)
        sp = id_

    track = await links.find(s, deezer=dz, isrc=isrc, spotify=sp, artist=a, title=t)
    if track is not None:
        return OpenOut(nd_song_id=track.nd_song_id, title=track.title, artist=track.artist)
    query = " ".join(x.strip() for x in (a, t) if x and x.strip()) or None
    found = None
    try:
        if sp and svc.spotify is not None:
            found = await svc.spotify.track(sp)
        elif dz and svc.deezer is not None:
            found = await svc.deezer.get_track(dz)
        elif isrc and svc.deezer is not None:
            found = await svc.deezer.get_track_by_isrc(isrc)
    except Exception as e:  # the catalog is away or has no such track: the names are still there
        log.info("open link: catalog track %s not read: %r", dz or isrc, e)
    if found is not None:
        return OpenOut(ref=found.ref, album_ref=found.album_ref, title=found.title, artist=found.artist,
                       query=query or f"{found.artist} {found.title}")
    return OpenOut(title=t, artist=a, query=query)
