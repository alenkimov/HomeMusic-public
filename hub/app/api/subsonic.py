"""The Hub as a Subsonic server (`/rest/…`): any Subsonic app — Feishin, Symfonium, Amperfy on an iPhone — pointed at
the Hub instead of Navidrome gets the library as ever, and with it what only the Hub has.

Every call goes on to Navidrome as it came, with the app's own sign-in, and its answer comes back untouched. A few
are the Hub's own:
- `search3` / `search2`: after the library's songs come the catalog's ones the library has not got — and, in
  `search3`, the catalog's albums and artists;
- `stream` / `download`, `getSong`, `getCoverArt` of such a song: it plays at once, from the sources (`app/listen.py`);
- `getAlbum`, `getArtist` of the catalog's: the album with its tracks (the ones the library has under Navidrome's
  ids), the artist with the releases;
- `star` of such a song: it goes to «Любимое» and is downloaded (the way ♥ of the web app does it); of such an
  album: the album is downloaded.

What is the catalog's has an id of the Hub's own — `hms-…` a song, `hma-…` an album, `hmr-…` an artist: its catalog
ref, and what it is (a ref alone does not say). Once a song is downloaded the library has it under Navidrome's id. The listener is told by the app's sign-in: the same name and password as in the Hub (a
Subsonic token is checked against the password the Hub keeps). An account the Hub does not know gets Navidrome alone.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections import OrderedDict
from typing import Any
from urllib.parse import parse_qsl, urlencode
from xml.sax.saxutils import escape, quoteattr

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from app import listen
from app.catalog.models import CatalogAlbum, CatalogArtist, CatalogTrack, split_ref
from app.config import get_settings
from app.db import session_scope
from app.library.owned import annotate_albums, annotate_tracks
from app.models import User
from app.net import make_client
from app.security import decrypt
from app.services import svc

router = APIRouter(tags=["subsonic"])
log = logging.getLogger(__name__)

PREFIX = "hm"
SONG, ALBUM, ARTIST = "s", "a", "r"
SEARCH_LIMIT = 25
SEARCH_ROW = 10  # albums and artists of the catalog a search adds
SEARCH_TIMEOUT_S = 6  # the catalog is asked this long; the library's own answer never waits longer
KNOWN_MAX = 5000
PASSED = ("content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified", "cache-control",
          "content-disposition", "x-content-duration")
# calls that name a song and can do without the catalog's one: an empty answer of their kind
EMPTY = {"getLyrics": {"lyrics": {}}, "getLyricsBySongId": {"lyricsList": {}}, "getSimilarSongs": {"similarSongs": {}},
         "getSimilarSongs2": {"similarSongs2": {}}, "scrobble": {}, "setRating": {}, "unstar": {},
         "getArtistInfo": {"artistInfo": {}}, "getArtistInfo2": {"artistInfo2": {}}, "getAlbumInfo": {"albumInfo": {}},
         "getAlbumInfo2": {"albumInfo": {}}}

# what of the catalog was shown lately, by its ref: the next call about it need not ask the catalog again
_known: OrderedDict[str, CatalogTrack] = OrderedDict()
_albums: OrderedDict[str, CatalogAlbum] = OrderedDict()
_artists: OrderedDict[str, CatalogArtist] = OrderedDict()
_nd: httpx.AsyncClient | None = None


def ext_id(ref: str, kind: str = SONG) -> str:
    return f"{PREFIX}{kind}-" + base64.urlsafe_b64encode(ref.encode()).decode().rstrip("=")


def ext_of(id_: str | None) -> tuple[str, str] | None:
    """(what it is, its catalog ref) of an id of the Hub's own; None for Navidrome's ids."""
    if not id_ or len(id_) < 5 or not id_.startswith(PREFIX) or id_[3] != "-" or id_[2] not in (SONG, ALBUM, ARTIST):
        return None
    raw = id_[4:]
    try:
        ref = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        split_ref(ref)
    except Exception:
        return None
    return id_[2], ref


def ext_ref(id_: str | None, kind: str = SONG) -> str | None:
    """The catalog ref of a song (an album, an artist) id of the Hub's own; None for anything else."""
    got = ext_of(id_)
    return got[1] if got and got[0] == kind else None


def _keep_known(cache: OrderedDict, ref: str, value: Any) -> None:
    cache[ref] = value
    cache.move_to_end(ref)
    while len(cache) > KNOWN_MAX:
        cache.popitem(last=False)


def _remember(t: CatalogTrack) -> None:
    _keep_known(_known, t.ref, t)


def song(t: CatalogTrack) -> dict[str, Any]:
    """A catalog track as Subsonic's `Child`. What the catalog cannot know of a file is a plain guess (320 kbps mp3)."""
    _remember(t)
    id_ = ext_id(t.ref)
    duration = t.duration_s or 0
    out: dict[str, Any] = {
        "id": id_, "isDir": False, "title": t.title, "artist": ", ".join(t.artists) if t.artists else t.artist,
        "album": t.album_title or "", "coverArt": id_, "duration": duration, "bitRate": 320,
        "size": duration * 40000, "suffix": "mp3", "contentType": "audio/mpeg", "isVideo": False, "type": "music",
        "mediaType": "song", "path": f"{t.artist}/{t.album_title or 'Каталог'}/{t.title}.mp3",
        "displayArtist": ", ".join(t.artists) if t.artists else t.artist,
    }
    if t.track_no:
        out["track"] = t.track_no
    if t.release_date:
        out["year"] = t.release_date.year
    if t.album_ref:
        out["albumId"] = ext_id(t.album_ref, ALBUM)
    if t.artist_ref:
        out["artistId"] = ext_id(t.artist_ref, ARTIST)
    return out


def album(a: CatalogAlbum) -> dict[str, Any]:
    """A catalog album as Subsonic's `AlbumID3`."""
    _keep_known(_albums, a.ref, a)
    id_ = ext_id(a.ref, ALBUM)
    out: dict[str, Any] = {"id": id_, "name": a.title, "artist": a.artist, "coverArt": id_,
                           "songCount": a.nb_tracks or len(a.tracks or []),
                           "duration": sum(t.duration_s or 0 for t in a.tracks or [])}
    if a.artist_ref:
        out["artistId"] = ext_id(a.artist_ref, ARTIST)
    if year := a.year or (a.release_date.year if a.release_date else None):
        out["year"] = year
    if a.genre:
        out["genre"] = a.genre
    return out


def artist(a: CatalogArtist) -> dict[str, Any]:
    """A catalog artist as Subsonic's `ArtistID3`."""
    _keep_known(_artists, a.ref, a)
    id_ = ext_id(a.ref, ARTIST)
    return {"id": id_, "name": a.name, "coverArt": id_, "albumCount": a.nb_albums or 0}


async def _album(ref: str, user: User, fmt: str | None) -> Response:
    """The album with its tracks: the ones the library has go under Navidrome's ids — they play from the library,
    and a ♥ on them is Navidrome's."""
    from app.api.catalog import get_album

    try:
        a = await get_album(ref)
    except Exception as e:
        log.warning("subsonic: the catalog does not give the album %s: %r", ref, e)
        return _error(fmt, 70, "Альбом не найден в каталоге")
    async with session_scope() as s:
        tracks = await annotate_tracks(s, a.tracks or [], user.id)
    songs = []
    for t in tracks:
        entry = song(t)
        if t.owned and t.nd_song_id:
            entry["id"] = t.nd_song_id
        songs.append(entry)
    return _ok(fmt, album={**album(a), "songCount": len(songs), "song": songs})


async def _artist(ref: str, fmt: str | None) -> Response:
    from app.api.catalog import get_artist_page

    try:
        page = await get_artist_page(ref)
    except Exception as e:
        log.warning("subsonic: the catalog does not give the artist %s: %r", ref, e)
        return _error(fmt, 70, "Исполнитель не найден в каталоге")
    albums = [album(a) for a in page.albums]
    return _ok(fmt, artist={**artist(page.artist), "albumCount": len(albums), "album": albums})


async def _track(ref: str) -> CatalogTrack | None:
    if ref in _known:
        return _known[ref]
    src, id_ = split_ref(ref)
    if src == "dz" and svc.deezer is not None:
        try:
            t = await svc.deezer.get_track(id_)
        except Exception as e:
            log.warning("subsonic: the catalog does not give %s: %r", ref, e)
            return None
        _remember(t)
        return t
    return None


# --- what the app sent --------------------------------------------------------------------------------------


async def _params(request: Request) -> list[tuple[str, str]]:
    params = list(request.query_params.multi_items())
    if request.method == "POST" and "form-urlencoded" in request.headers.get("content-type", ""):
        params += parse_qsl((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)
    return params


def _one(params: list[tuple[str, str]], key: str) -> str | None:
    return next((v for k, v in params if k == key), None)


async def _listener(params: list[tuple[str, str]]) -> User | None:
    """Whose sign-in the app sent — if the Hub knows the account and the password fits. Navidrome checks it again."""
    name = _one(params, "u")
    if not name:
        return None
    async with session_scope() as s:
        user = (await s.execute(select(User).where(User.nd_username == name))).scalar_one_or_none()
    if user is None or not user.nd_password_enc:
        return None
    try:
        password = decrypt(user.nd_password_enc)
    except Exception:
        return None
    given, token, salt = _one(params, "p"), _one(params, "t"), _one(params, "s")
    if given is not None:
        if given.startswith("enc:"):
            try:
                given = bytes.fromhex(given[4:]).decode()
            except ValueError:
                return None
        return user if given == password else None
    if token and salt is not None:
        return user if hashlib.md5((password + salt).encode()).hexdigest() == token.lower() else None
    return None


# --- answers ------------------------------------------------------------------------------------------------


def _xml(tag: str, value: dict[str, Any]) -> str:
    attrs, kids = [], []
    for k, v in value.items():
        if v is None:
            continue
        if isinstance(v, dict):
            kids.append(_xml(k, v))
        elif isinstance(v, list):
            kids += [_xml(k, x) if isinstance(x, dict) else f"<{k}>{escape(str(x))}</{k}>" for x in v]
        else:
            attrs.append(f"{k}={quoteattr('true' if v is True else 'false' if v is False else str(v))}")
    head = " ".join([tag, *attrs])
    return f"<{head}>{''.join(kids)}</{tag}>" if kids else f"<{head}/>"


def _render(fmt: str | None, resp: dict[str, Any]) -> Response:
    if fmt in ("json", "jsonp"):
        return Response(json.dumps({"subsonic-response": resp}, ensure_ascii=False), media_type="application/json")
    body = _xml("subsonic-response", {"xmlns": "http://subsonic.org/restapi", **resp})
    return Response('<?xml version="1.0" encoding="UTF-8"?>' + body, media_type="application/xml")


def _ok(fmt: str | None, **body: Any) -> Response:
    return _render(fmt, {"status": "ok", "version": "1.16.1", "type": "homemusic", "openSubsonic": True, **body})


def _error(fmt: str | None, code: int, message: str) -> Response:
    return _render(fmt, {"status": "failed", "version": "1.16.1", "type": "homemusic", "openSubsonic": True,
                         "error": {"code": code, "message": message}})


# --- Navidrome ----------------------------------------------------------------------------------------------


def _navidrome() -> httpx.AsyncClient:
    global _nd
    if _nd is None:
        _nd = httpx.AsyncClient(timeout=httpx.Timeout(60, read=None), follow_redirects=False)
    return _nd


async def _pass(request: Request, endpoint: str, params: list[tuple[str, str]]) -> Response:
    """The call as it came, and Navidrome's answer as it is (audio and pictures too, with `Range`)."""
    headers = {k: request.headers[k] for k in ("range", "if-none-match", "if-modified-since", "if-range") if k in request.headers}
    headers["accept-encoding"] = "identity"
    url = f"{get_settings().navidrome_url.rstrip('/')}/rest/{endpoint}"
    client = _navidrome()
    try:
        if request.method == "POST":
            req = client.build_request("POST", url, content=urlencode(params), headers={
                **headers, "content-type": "application/x-www-form-urlencoded"})
        else:
            req = client.build_request(request.method, url, params=params, headers=headers)
        r = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        log.warning("subsonic: Navidrome did not answer %s: %r", endpoint, e)
        return _error(_one(params, "f"), 0, "Библиотека не отвечает")
    passed = {k: r.headers[k] for k in PASSED if k in r.headers}
    return StreamingResponse(r.aiter_raw(), status_code=r.status_code, headers=passed, background=BackgroundTask(r.aclose))


async def _ask(endpoint: str, params: list[tuple[str, str]]) -> dict[str, Any] | None:
    """Navidrome's answer to the call, read (as JSON whatever the app asked for); None when it gave none."""
    query = [(k, v) for k, v in params if k not in ("f", "callback")] + [("f", "json")]
    try:
        r = await _navidrome().post(f"{get_settings().navidrome_url.rstrip('/')}/rest/{endpoint}", content=urlencode(query),
                                    headers={"content-type": "application/x-www-form-urlencoded"}, timeout=30)
        body = r.json().get("subsonic-response")
    except Exception as e:
        log.warning("subsonic: Navidrome did not answer %s: %r", endpoint, e)
        return None
    return body if isinstance(body, dict) else None


# --- the Hub's own calls ------------------------------------------------------------------------------------


def _int(params: list[tuple[str, str]], key: str, default: int) -> int:
    try:
        return int(_one(params, key) or default)
    except ValueError:
        return default


async def _search(endpoint: str, params: list[tuple[str, str]], user: User, fmt: str | None) -> Response:
    resp = await _ask(endpoint, params)
    if resp is None:
        return _error(fmt, 0, "Библиотека не отвечает")
    q = (_one(params, "query") or "").strip().strip('"').strip()
    count = _int(params, "songCount", 20)
    # an empty query is an app copying the whole library, a later page is the library's own: neither is a search
    if resp.get("status") == "ok" and q and count > 0 and _int(params, "songOffset", 0) == 0 and svc.deezer is not None:
        try:
            tracks, _ = await asyncio.wait_for(svc.deezer.search_track_page(q, 0, min(count, SEARCH_LIMIT)), SEARCH_TIMEOUT_S)
            seen: set[str] = set()
            tracks = [t for t in tracks if not (t.ref in seen or seen.add(t.ref))]
            async with session_scope() as s:
                found = await annotate_tracks(s, tracks, user.id)
            key = "searchResult3" if endpoint == "search3" else "searchResult2"
            box = resp.setdefault(key, {})
            box["song"] = list(box.get("song") or []) + [song(t) for t in found if not t.owned]
        except Exception as e:  # the catalog is away: the library's own answer goes as it is
            log.warning("subsonic: the catalog search failed: %r", e)
    # the catalog's albums and artists, for the apps that browse by tags (`search3`); the first page only
    wants = (_int(params, "albumCount", 20) > 0 and _int(params, "albumOffset", 0) == 0,
             _int(params, "artistCount", 20) > 0 and _int(params, "artistOffset", 0) == 0)
    if resp.get("status") == "ok" and q and endpoint == "search3" and any(wants) and svc.deezer is not None:
        try:
            more = await asyncio.wait_for(svc.deezer.search(q, kinds=("artist", "album"), limit=SEARCH_ROW), SEARCH_TIMEOUT_S)
            box = resp.setdefault("searchResult3", {})
            if wants[0] and more.albums:
                async with session_scope() as s:
                    albums = await annotate_albums(s, more.albums, user.id)
                box["album"] = list(box.get("album") or []) + [album(a) for a in albums if a.owned_status != "full"]
            if wants[1] and more.artists:
                have = {str(x.get("name") or "").casefold() for x in box.get("artist") or []}
                box["artist"] = list(box.get("artist") or []) + [artist(a) for a in more.artists if a.name.casefold() not in have]
        except Exception as e:
            log.warning("subsonic: the catalog search (albums, artists) failed: %r", e)
    return _render(fmt, resp)


async def _play(request: Request, ref: str, user: User, fmt: str | None) -> Response:
    rng = request.headers.get("range") or ""
    try:
        m = await listen.open_media(ref, rng or None)
    except listen.ListenError as e:
        return _error(fmt, 70, str(e))
    if m.choice.kind == "full" and (not rng or rng.startswith("bytes=0-")):
        from app.imports.core import listened

        await listened(user.id, ref)  # a track of an economy import is downloaded once listened to
    if m.path is not None:
        return FileResponse(m.path, media_type=m.media_type, headers={"Cache-Control": "no-store"})
    headers = {**m.headers, "accept-ranges": "bytes", "cache-control": "no-store"}
    if m.body is None:
        return Response(status_code=m.status, headers=headers)
    return StreamingResponse(m.body, status_code=m.status, headers=headers, media_type=m.media_type,
                             background=BackgroundTask(m.close) if m.close else None)


async def _cover(kind: str, ref: str, params: list[tuple[str, str]]) -> Response:
    small = 0 < _int(params, "size", 0) <= 300
    url: str | None = None
    if kind == SONG:
        t = await _track(ref)
        url = t and ((t.cover_small_url if small else None) or t.cover_url or t.cover_small_url)
    elif kind == ALBUM:
        a = _albums.get(ref)
        if a is None:
            from app.api.catalog import get_album

            try:
                a = await get_album(ref)
                _keep_known(_albums, ref, a)
            except Exception:
                a = None
        url = a and ((a.cover_small_url if small else None) or a.cover_url or a.cover_small_url)
    else:
        r = _artists.get(ref)
        if r is None:
            from app.api.catalog import get_artist_page

            try:
                r = (await get_artist_page(ref)).artist
                _keep_known(_artists, ref, r)
            except Exception:
                r = None
        url = r and ((r.picture_small_url if small else None) or r.picture_url or r.picture_small_url)
    if not url:
        return Response(status_code=404)
    try:
        async with make_client(timeout=20) as c:
            r = await c.get(url)
    except Exception as e:
        log.warning("subsonic: no cover of %s: %r", ref, e)
        return Response(status_code=404)
    if r.status_code != 200:
        return Response(status_code=404)
    return Response(r.content, media_type=r.headers.get("content-type", "image/jpeg"),
                    headers={"cache-control": "public, max-age=86400"})


async def _keep(refs: list[str], albums: list[str], user: User) -> None:
    """♥ of the catalog's: «Любимое» gets the songs and they are downloaded; an album is downloaded whole."""
    from app import playlists
    from app.api.downloads import create_batch
    from app.api.schemas import DownloadIn
    from app.jobs.queue import notify

    async with session_scope() as s:
        user = await s.get(User, user.id)
        if refs:
            liked = await playlists.liked(s, user.id)
            await create_batch(s, user, DownloadIn(track_refs=refs, playlist_id=str(liked.id)), origin="catalog")
        for ref in albums:
            await create_batch(s, user, DownloadIn(album_ref=ref), origin="catalog")
    notify()


@router.api_route("/rest/{endpoint:path}", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def rest(endpoint: str, request: Request) -> Response:
    endpoint = endpoint.removesuffix(".view")
    params = await _params(request)
    fmt = _one(params, "f")
    refs = {v: ext_of(v) for k, v in params if v.startswith(PREFIX)}
    ext = [r for r in refs.values() if r]
    searching = endpoint in ("search3", "search2")
    if not ext and not searching:
        return await _pass(request, endpoint, params)
    user = await _listener(params)
    if user is None:
        # not the Hub's account (or not its password): Navidrome alone answers, and knows no song of the catalog
        return await _pass(request, endpoint, params) if not ext else _error(fmt, 40, "Wrong username or password")
    if searching:
        return await _search(endpoint, params, user, fmt)

    of = ext_of(_one(params, "id"))
    ref = of[1] if of and of[0] == SONG else None
    if endpoint in ("stream", "download") and ref:
        return await _play(request, ref, user, fmt)
    if endpoint == "getCoverArt" and of:
        return await _cover(of[0], of[1], params)
    if endpoint == "getSong" and ref:
        t = await _track(ref)
        return _ok(fmt, song=song(t)) if t else _error(fmt, 70, "Трек не найден в каталоге")
    if endpoint == "getAlbum" and of and of[0] == ALBUM:
        return await _album(of[1], user, fmt)
    if endpoint == "getArtist" and of and of[0] == ARTIST:
        return await _artist(of[1], fmt)
    if endpoint == "star":
        try:
            await _keep(list(dict.fromkeys(r for k, r in ext if k == SONG)),
                        list(dict.fromkeys(r for k, r in ext if k == ALBUM)), user)
        except Exception as e:
            log.warning("subsonic: could not keep %s: %r", ext, e)
            return _error(fmt, 0, "Не удалось добавить трек в «Любимое»")
        rest_ = [(k, v) for k, v in params if not refs.get(v)]
        if any(k in ("id", "albumId", "artistId") for k, _ in rest_):  # the library's own songs of the same call
            return await _pass(request, endpoint, rest_)
        return _ok(fmt)
    if endpoint in EMPTY:
        rest_ = [(k, v) for k, v in params if not refs.get(v)]
        if endpoint in ("scrobble", "unstar", "setRating") and any(k == "id" for k, _ in rest_):
            return await _pass(request, endpoint, rest_)
        return _ok(fmt, **EMPTY[endpoint])
    return _error(fmt, 70, "Этого трека ещё нет в библиотеке: поставьте ♥, и он скачается")
