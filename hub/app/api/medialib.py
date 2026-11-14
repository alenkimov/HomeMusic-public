"""The Navidrome library as the user's player sees it: artists, albums, «Любимое», covers. The Android app
has these as native screens (Tempus); the PWA in a plain browser shows them from here (`web/src/pages/Home.tsx`,
`MediaLibrary.tsx`). Everything is asked of Navidrome as the user, so their stars and play counts are the ones shown.
"""

from __future__ import annotations

import logging

import asyncio
from datetime import datetime
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.library import tracks as registry
from app.library.owned import HAS_FILE, put_right, with_files
from app.api.schemas import NdAlbumOut, NdArtistOut, NdArtistPageOut, NdHomeOut, NdLikedOut, NdSongOut
from app.auth import current_user, user_navidrome
from app.db import get_session
from app.library.navidrome import NavidromeClient, SubsonicError
from app.mixes.resolve import library_track
from app.models import Track, TrackFile, User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/nd", tags=["medialib"])

ALBUM_LISTS = ("recent", "frequent", "newest", "starred", "alphabeticalByName", "alphabeticalByArtist", "random")
HOME_ROW = 15
HOME_RADIO = 30
SMALL_COVER = 300
BIG_COVER = 1000


def cover_url(cover_id: str | None, size: int = SMALL_COVER) -> str | None:
    return f"/api/nd/cover/{quote(cover_id, safe='')}?size={size}" if cover_id else None


def album_out(a: dict) -> NdAlbumOut:
    return NdAlbumOut(id=a["id"], title=a.get("name") or a.get("title") or "", artist=a.get("artist") or "",
                      artist_id=a.get("artistId"), year=a.get("year") or None, song_count=a.get("songCount") or 0,
                      duration_s=a.get("duration") or 0, cover_url=cover_url(a.get("coverArt")),
                      starred=bool(a.get("starred")))


def artist_out(a: dict) -> NdArtistOut:
    return NdArtistOut(id=a["id"], name=a.get("name") or "", album_count=a.get("albumCount") or 0,
                       cover_url=cover_url(a.get("coverArt")), starred=bool(a.get("starred")))


async def songs_out(s: AsyncSession, songs: list[dict]) -> list[NdSongOut]:
    """Navidrome songs as tracks: the Hub's own record of the file gives the Deezer ref (mixes, «Моя волна» and
    listening data know the song by it), the rest are `nd:<song>`."""
    ids = [x["id"] for x in songs if x.get("id")]
    rows: dict[str, Track] = {}
    acquired: dict[str, datetime] = {}
    for i in range(0, len(ids), 500):
        q = (select(Track, TrackFile.nd_song_id, TrackFile.acquired_at).join(TrackFile, TrackFile.track_id == Track.id)
             .where(TrackFile.nd_song_id.in_(ids[i:i + 500])))
        for t, song, at in (await s.execute(q)).all():
            rows[song], acquired[song] = t, at
    out = []
    for x in songs:
        sid = x.get("id")
        if not sid:
            continue
        t = rows.get(sid)
        ref = library_track(t).ref if t is not None else f"nd:{sid}"
        artist = x.get("artist") or ""
        credited = [a for a in x.get("artists") or [] if a.get("name")]
        names = [a["name"] for a in credited] or ([artist] if artist else [])
        artist_ids = [a.get("id") for a in credited] or ([x.get("artistId")] if artist else [])
        cat = (t.catalog or {}) if t is not None else {}
        main = cat.get("artist_ref") if str(cat.get("artist_ref") or "").startswith("dz:") else None
        refs = cat.get("artist_refs") or []
        # the catalog's artists of the song, by their places: the names may be spelt otherwise in the files
        artist_refs = refs if len(refs) == len(names) else ([main, *[None] * (len(names) - 1)] if main and names else [])
        out.append(NdSongOut(
            ref=ref, source="dz" if ref.startswith("dz:") else "nd", id=ref.split(":", 1)[1], title=x.get("title") or "",
            artist=artist, artists=names, artist_ref=main, artist_refs=artist_refs, album_title=x.get("album"), album_artist=x.get("displayAlbumArtist"),
            duration_s=x.get("duration"), track_no=x.get("track"), disc_no=x.get("discNumber"),
            isrc=(x.get("isrc") or [None])[0] if isinstance(x.get("isrc"), list) else x.get("isrc"),
            explicit=t.explicit if t is not None else None,
            cover_url=cover_url(x.get("coverArt"), BIG_COVER), cover_small_url=cover_url(x.get("coverArt")),
            owned=True, library_track_id=t.id if t is not None else None, nd_song_id=sid,
            added_at=acquired.get(sid),
            nd_album_id=x.get("albumId"), nd_artist_id=x.get("artistId"), nd_artist_ids=artist_ids,
        ))
        if t is not None:
            put_right(out[-1], t)  # Navidrome learns a new name at its next scan; the Hub knows it now
    await with_files(s, out)
    return out


async def nd_call(user: User, fn, not_found: str = "Не найдено в библиотеке"):
    """Runs `fn(nd)` as the user and turns Navidrome failures into HTTP errors: «not found» (70) → 404,
    «not authorized» (50, e.g. not the playlist's owner) → 403, any other Subsonic error or no answer → 502."""
    nd = user_navidrome(user)
    try:
        return await fn(nd)
    except SubsonicError as e:
        if e.code == 70:  # not found
            raise HTTPException(404, not_found) from e
        if e.code == 50:  # not authorized
            raise HTTPException(403, "Нет прав на это в библиотеке") from e
        raise HTTPException(502, f"Navidrome: {e.message}") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, "Navidrome не отвечает") from e
    finally:
        await nd.aclose()


def _radio(artists: list[dict]) -> list[NdArtistOut]:
    """«Радиостанции»: every artist, the liked ones first, then those with more albums."""
    ranked = sorted(artists, key=lambda a: (not a.get("starred"), -(a.get("albumCount") or 0), (a.get("name") or "").casefold()))
    return [artist_out(a) for a in ranked]


@router.get("/home", response_model=NdHomeOut)
async def home(user: User = Depends(current_user)):
    async def load(nd: NavidromeClient):
        return await asyncio.gather(*(nd.get_album_list2(t, size=HOME_ROW) for t in ("recent", "frequent", "newest", "starred")),
                                    nd.get_artists())

    recent, frequent, newest, starred, artists = await nd_call(user, load)
    return NdHomeOut(recent=[album_out(a) for a in recent], frequent=[album_out(a) for a in frequent],
                     newest=[album_out(a) for a in newest], starred=[album_out(a) for a in starred],
                     radio=_radio(artists)[:HOME_RADIO])


@router.get("/albums", response_model=list[NdAlbumOut])
async def albums(type: str = "alphabeticalByName", offset: int = Query(0, ge=0), size: int = Query(50, ge=1, le=500),
                 user: User = Depends(current_user)):
    if type not in ALBUM_LISTS:
        raise HTTPException(422, f"type: one of {', '.join(ALBUM_LISTS)}")
    return [album_out(a) for a in await nd_call(user, lambda nd: nd.get_album_list2(type, size=size, offset=offset))]


@router.get("/albums/{album_id}", response_model=NdAlbumOut)
async def album(album_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    a = await nd_call(user, lambda nd: nd.get_album(album_id))
    if not a.get("id"):
        raise HTTPException(404, "Альбом не найден")
    out = album_out(a)
    out.tracks = await songs_out(s, a.get("song") or [])
    return out


@router.get("/artists", response_model=list[NdArtistOut])
async def artists(user: User = Depends(current_user)):
    return [artist_out(a) for a in await nd_call(user, lambda nd: nd.get_artists())]


LIB_ARTIST_TRACKS = 50


def _credited(t: Track) -> list[str]:
    return [x.strip().casefold() for x in (t.artist or "").split(",") if x.strip()]


async def _artist_tracks(s: AsyncSession, name: str) -> list[Track]:
    """The library's tracks the artist is credited on, the newest first."""
    want = name.strip().casefold()
    if not want:
        return []
    rows = (await s.execute(select(Track).where(HAS_FILE, Track.artist.ilike(f"%{name.strip()}%"))
                            .order_by(Track.id.desc()))).scalars().all()
    return [t for t in rows if want in _credited(t)]


def _catalog_ref_of(tracks: list[Track], name: str) -> str | None:
    """The catalog's artist these tracks are of: the main artist of the catalog's records of the tracks this artist
    is named first on. It is the same person whatever the spelling — the catalog writes «Mikhail Krug» where the
    files say «Михаил Круг», and a search by the name finds nobody."""
    want = name.strip().casefold()
    votes: dict[str, int] = {}
    for t in tracks:
        ref = (t.catalog or {}).get("artist_ref")
        if ref and str(ref).startswith("dz:") and (_credited(t) or [""])[0] == want:
            votes[ref] = votes.get(ref, 0) + 1
    return max(votes, key=lambda r: votes[r]) if votes else None


@router.get("/artists/{artist_id}", response_model=NdArtistPageOut)
async def artist(artist_id: str, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    a = await nd_call(user, lambda nd: nd.get_artist(artist_id))
    if not a.get("id"):
        raise HTTPException(404, "Исполнитель не найден")
    name = a.get("name") or ""
    theirs = await _artist_tracks(s, name)
    # the few of their own tracks that do not say yet who they are of in the catalog are asked about now
    first = [t for t in theirs if (_credited(t) or [""])[0] == name.strip().casefold()][:3]
    if await registry.fill_catalog(s, first):
        await s.commit()
    catalog_ref = _catalog_ref_of(theirs, name) or await _in_catalog(name)
    tracks: list[NdSongOut] = []
    if catalog_ref is None:  # the page is the library's own: their tracks, as the Hub has them
        from app.api.playlists import track_with_file

        tracks = [track_with_file(t) for t in theirs[:LIB_ARTIST_TRACKS]]
        for o, t in zip(tracks, theirs, strict=False):
            put_right(o, t)
        await with_files(s, tracks)
    # newest year first, albums without a year last, ties by title
    albums_ = sorted(a.get("album") or [], key=lambda x: (not x.get("year"), -(x.get("year") or 0),
                                                          (x.get("name") or x.get("title") or "").casefold(), x.get("id") or ""))
    return NdArtistPageOut(artist=artist_out(a), albums=[album_out(x) for x in albums_], catalog_ref=catalog_ref,
                           tracks=tracks)


async def _in_catalog(name: str) -> str | None:
    """The catalog's artist of this very name (the best known of them, as the catalog orders its search); none: the
    catalog has nobody called so, or does not answer."""
    from app.services import svc

    if not name.strip() or svc.deezer is None:
        return None
    try:
        found = await svc.deezer.search(name, kinds=("artist",), limit=5)
    except Exception as e:
        log.info("library artist %r not looked up in the catalog: %r", name, e)
        return None
    want = name.strip().casefold()
    return next((x.ref for x in found.artists if x.name.strip().casefold() == want), None)


ALL_SONGS_PAGE = 500
ALL_SONGS_MAX = 50_000


@router.get("/songs", response_model=list[NdSongOut])
async def all_songs(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """Every song of the library (a selection by tags looks through all of it)."""
    from app.library import songs as library_songs

    return await songs_out(s, await nd_call(user, library_songs.load))


@router.get("/liked", response_model=NdLikedOut)
async def liked(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """«Любимое»: the tracks are the Hub's playlist of them (`favorites.project_liked`) — the newest like on top,
    one the library has no file of among the rest. The albums and artists are Navidrome's stars."""
    from app import playlists
    from app.api.playlists import tracks_out
    from app.favorites import project_liked, snapshot

    try:
        body = await nd_call(user, lambda nd: nd.get_starred2())
    except HTTPException:  # Navidrome is away: the tracks are the Hub's own, the rest waits
        body = None
    await project_liked(s, user.id, body.get("song") or [] if body is not None else None)
    await s.commit()
    body = body or {}
    rows = await playlists.items(s, (await playlists.liked(s, user.id)).id)
    songs = await tracks_out(s, user, [t for _, t in rows])
    for (item, _), song in zip(rows, songs, strict=True):
        song.added_at = item.added_at
    states = await snapshot(s, user.id)
    decisions = {(st.target_type, alias): st for st in states.values() for alias in st.aliases}
    # a song starred in Navidrome that the Hub has no track of (a file put into the library by hand) is no part
    # of the playlist: it is listed as Navidrome gives it
    have = {x.nd_song_id for x in songs}
    foreign = [x for x in body.get("song") or [] if x.get("id") and x["id"] not in have
               and not ((st := decisions.get(("track", f"nd:{x['id']}"))) and not st.liked)]
    if foreign:
        from app.favorites import parse_time
        from app.models import naive

        for raw, song in zip(foreign, await songs_out(s, foreign), strict=True):
            song.added_at = naive(parse_time(raw.get("starred"))) or song.added_at
            songs.append(song)
        songs.sort(key=lambda x: x.added_at or datetime.min, reverse=True)

    def visible(typ, obj):
        st = decisions.get((typ, f"nd:{obj.get('id')}"))
        return st is None or st.liked

    return NdLikedOut(songs=songs,
                      albums=[album_out(a) for a in sorted(body.get("album") or [], key=lambda x: x.get("starred") or "", reverse=True)
                              if visible("album", a)],
                      artists=[artist_out(a) for a in body.get("artist") or [] if visible("artist", a)])


@router.get("/cover/{cover_id}")
async def cover(cover_id: str, size: int | None = Query(None, ge=32, le=1500), user: User = Depends(current_user)):
    """A Navidrome cover (getCoverArt), so the page needs no Navidrome credentials."""
    nd = user_navidrome(user)
    try:
        url, params = nd.cover_art_url_params(cover_id, size)
        r = await nd.http.get(url, params=params)
    except httpx.HTTPError as e:
        raise HTTPException(502, "Navidrome не отвечает") from e
    finally:
        await nd.aclose()
    kind = r.headers.get("content-type", "")
    if r.status_code != 200 or not kind.startswith("image/"):
        raise HTTPException(404, "Нет обложки")
    return Response(r.content, media_type=kind, headers={"Cache-Control": "private, max-age=86400"})
