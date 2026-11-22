"""The Navidrome library as the user's player sees it: artists, albums, «Любимое», covers. The Android app
has these as native screens (Tempus); the PWA in a plain browser shows them from here (`web/src/pages/Home.tsx`,
`MediaLibrary.tsx`). Everything is asked of Navidrome as the user, so their stars and play counts are the ones shown.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import datetime
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import NdAlbumOut, NdArtistOut, NdArtistPageOut, NdHomeOut, NdLikedOut, NdSongOut
from app.auth import current_user, user_navidrome
from app.catalog import registry as releases
from app.db import get_session
from app.library import artists as artist_names
from app.library import tracks as registry
from app.library.artwork import album_cover_url
from app.library.credits import split_artists
from app.library.navidrome import NavidromeClient, SubsonicError
from app.library.owned import HAS_FILE, put_right, with_cover_fallbacks, with_files
from app.mixes.resolve import library_track
from app.models import Album, Track, TrackFile, User

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


async def with_artist_links(s: AsyncSession, pairs: Iterable[tuple[NdSongOut, Track | None]]) -> None:
    """Resolve each displayed credit using its own spelling and any catalog record for that spelling."""
    entries = [(out, artist_names.refs_of(track.catalog if track is not None else None)) for out, track in pairs]
    reader = await artist_names.reader(s, (name for out, _ in entries for name in [out.artist, *out.artists]),
                                      (ref for _, refs in entries for ref in refs.values()))

    for out, refs in entries:
        def address(name: str) -> tuple[int | None, str | None]:
            ref = refs.get(artist_names.key(name))
            local = reader.artist(name)
            linked = reader.artist(name, ref)
            if out.owned and local and linked != local:
                return local[0], None
            return linked[0] if linked else None, ref

        out.hub_artist_id, out.artist_ref = address(out.artist)
        links = [address(name) for name in out.artists]
        out.hub_artist_ids = [ident for ident, _ in links]
        out.artist_refs = [ref for _, ref in links]


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
        out.append(NdSongOut(
            ref=ref, source=ref.split(":", 1)[0], id=ref.split(":", 1)[1], title=x.get("title") or "",
            artist=artist, artists=names, album_title=x.get("album"), album_artist=x.get("displayAlbumArtist"),
            duration_s=x.get("duration"), track_no=x.get("track"), disc_no=x.get("discNumber"),
            isrc=(x.get("isrc") or [None])[0] if isinstance(x.get("isrc"), list) else x.get("isrc"),
            explicit=t.explicit if t is not None else None,
            spotify_id=cat.get("spotify_id"), spotify_plays=cat.get("spotify_plays"),
            cover_url=cover_url(x.get("coverArt"), BIG_COVER), cover_small_url=cover_url(x.get("coverArt")),
            owned=True, library_track_id=t.id if t is not None else None, nd_song_id=sid,
            added_at=acquired.get(sid),
            nd_album_id=x.get("albumId"), nd_artist_id=x.get("artistId"), nd_artist_ids=artist_ids,
        ))
        if t is not None:
            put_right(out[-1], t)  # Navidrome learns a new name at its next scan; the Hub knows it now
    await with_artist_links(s, [(song, rows.get(song.nd_song_id)) for song in out])
    await with_files(s, out)
    await with_cover_fallbacks(s, [(o, rows[o.nd_song_id]) for o in out if o.nd_song_id in rows])
    from app.library import versions

    await versions.mark(s, out)  # how many recordings the song of each has
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
    ids = [t.library_track_id for t in out.tracks if t.library_track_id is not None]
    library_albums: set[int] = set()
    for i in range(0, len(ids), 500):
        library_albums.update((await s.execute(select(Track.album_id).where(
            Track.id.in_(ids[i:i + 500]), Track.album_id.is_not(None)).distinct())).scalars())
    if len(library_albums) == 1 and (local := await s.get(Album, library_albums.pop())):
        out.library_album_id = local.id
        _, ids = await releases.addresses(s, (), [local.id])
        out.release_id = ids.get(local.id)
        if out.release_id is None and (release := await releases.library_album(s, local.id)):
            out.release_id = (await releases.opened(s, release, (), local.id)).id
        out.cover_url = album_cover_url(local) or out.cover_url
        out.title, out.artist = local.title, local.album_artist
    return out


@router.get("/artists", response_model=list[NdArtistOut])
async def artists(user: User = Depends(current_user)):
    out = [artist_out(a) for a in await nd_call(user, lambda nd: nd.get_artists())]
    from app.db import session_scope
    from app.library.artists import key
    from app.library.looks import spotify_photos

    async with session_scope() as s:
        photos = await spotify_photos(s)
    for artist in out:
        artist.cover_url = artist.cover_url or photos.get(key(artist.name))
    return out


LIB_ARTIST_TRACKS = 50


def _credited(t: Track) -> list[str]:
    return [artist_names.key(name) for name in split_artists(t.artist)]


async def _artist_tracks(s: AsyncSession, name: str) -> list[Track]:
    """The library's tracks the artist is credited on, the newest first."""
    want = artist_names.key(name)
    if not want:
        return []
    rows = (await s.execute(select(Track).where(HAS_FILE, Track.artist.ilike(f"%{name.strip()}%"))
                            .order_by(Track.id.desc()))).scalars().all()
    return [t for t in rows if want in _credited(t)]


def _catalog_refs_of(tracks: list[Track], name: str) -> set[str]:
    """Profiles that the tracks credit under this artist's spelling."""
    want = artist_names.key(name)
    return {ref for t in tracks if (_credited(t) or [""])[0] == want
            and (ref := artist_names.refs_of(t.catalog).get(want))}


@router.get("/artists/{artist_id}", response_model=NdArtistPageOut)
async def artist(artist_id: str, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    a = await nd_call(user, lambda nd: nd.get_artist(artist_id))
    if not a.get("id"):
        raise HTTPException(404, "Исполнитель не найден")
    name = a.get("name") or ""
    # newest year first, albums without a year last, ties by title
    albums_ = sorted(a.get("album") or [], key=lambda x: (not x.get("year"), -(x.get("year") or 0),
                                                          (x.get("name") or x.get("title") or "").casefold(), x.get("id") or ""))
    out = NdArtistPageOut(artist=artist_out(a), albums=[album_out(x) for x in albums_])
    local = await artist_names.holder(s, name)
    if local is not None:
        out.hub_artist_id = local.id
        if await artist_names.profiles(s, local.id):
            return out

    theirs = await _artist_tracks(s, name)
    if local is None:
        # Ask the catalog about a few of the artist's own tracks before looking up a name alone.
        first = [t for t in theirs if (_credited(t) or [""])[0] == artist_names.key(name)][:3]
        if await registry.fill_catalog(s, first):
            await s.commit()
        refs = _catalog_refs_of(theirs, name)
        out.catalog_ref = next(iter(refs)) if len(refs) == 1 else None
        if not refs:
            out.catalog_ref = await _in_catalog(name)
        if out.catalog_ref and (known := await artist_names.by_ref(s, out.catalog_ref)) is not None:
            out.hub_artist_id = known.id
    if out.catalog_ref is None:  # the page is the library's own: their tracks, as the Hub has them
        from app.library.cards import track_with_file

        shown = theirs[:LIB_ARTIST_TRACKS]
        out.tracks = [track_with_file(t) for t in shown]
        pairs = list(zip(out.tracks, shown, strict=True))
        for o, t in pairs:
            put_right(o, t)
        await with_artist_links(s, pairs)
        await with_cover_fallbacks(s, pairs)
        await with_files(s, out.tracks)
    return out


async def _in_catalog(name: str) -> str | None:
    """An exact spelling with a single matching profile in its catalog."""
    from app.services import svc

    if not name.strip():
        return None
    want = artist_names.key(name)
    for catalog in (svc.deezer, svc.spotify):
        if catalog is None:
            continue
        try:
            found = await catalog.search(name, kinds=("artist",), limit=5)
        except Exception as e:
            log.info("library artist %r not looked up in the catalog: %r", name, e)
            continue
        matches = {x.ref for x in found.artists if artist_names.key(x.name) == want}
        if matches:
            return next(iter(matches)) if len(matches) == 1 else None
    return None


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
    one the library has no file of among the rest — given as the database makes them of their kept cards
    (`cards.list_json`). The albums and artists are Navidrome's stars."""
    import json

    from sqlalchemy import text

    from app import playlists
    from app.favorites import parse_time, project_liked, snapshot
    from app.library import cards
    from app.models import naive

    try:
        body = await nd_call(user, lambda nd: nd.get_starred2())
    except HTTPException:  # Navidrome is away: the tracks are the Hub's own, the rest waits
        body = None
    await project_liked(s, user.id, body.get("song") or [] if body is not None else None)
    await s.commit()
    body = body or {}
    pid = (await playlists.liked(s, user.id)).id
    # the user's own word on what Navidrome stars, of each kind apart (`snapshot`): the track likes — thousands —
    # are read only if Navidrome stars a song the playlist has not
    decisions = {(st.target_type, alias): st for kind in ("album", "artist")
                 for st in (await snapshot(s, user.id, only=kind)).values() for alias in st.aliases}
    # a song starred in Navidrome that the Hub has no track of (a file put into the library by hand) is no part
    # of the playlist: it is listed as Navidrome gives it
    have = set((await s.execute(text(
        "SELECT DISTINCT f.nd_song_id FROM playlist_items pi JOIN track_files f ON f.track_id = pi.track_id "
        "WHERE pi.playlist_id = :pid AND f.nd_song_id IS NOT NULL"), {"pid": pid})).scalars())
    foreign = [x for x in body.get("song") or [] if x.get("id") and x["id"] not in have]
    if foreign:
        decisions |= {(st.target_type, alias): st for st in (await snapshot(s, user.id, only="track")).values()
                      for alias in st.aliases}
        foreign = [x for x in foreign if not ((st := decisions.get(("track", f"nd:{x['id']}"))) and not st.liked)]
    extra = []
    for raw, song in zip(foreign, await songs_out(s, foreign) if foreign else [], strict=True):
        song.added_at = naive(parse_time(raw.get("starred"))) or song.added_at
        extra.append(song.model_dump(mode="json"))
    songs = await cards.list_json(s, pid, user.id, extra=extra)

    def visible(typ, obj):
        st = decisions.get((typ, f"nd:{obj.get('id')}"))
        return st is None or st.liked

    albums = [album_out(a).model_dump(mode="json")
              for a in sorted(body.get("album") or [], key=lambda x: x.get("starred") or "", reverse=True) if visible("album", a)]
    artists = [artist_out(a).model_dump(mode="json") for a in body.get("artist") or [] if visible("artist", a)]
    return Response(f'{{"songs":{songs},"albums":{json.dumps(albums)},"artists":{json.dumps(artists)}}}',
                    media_type="application/json")



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
