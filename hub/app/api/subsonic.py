"""The Hub as a Subsonic server (`/rest/…`): any Subsonic app — Feishin, Symfonium, Amperfy on an iPhone — pointed at
the Hub instead of Navidrome gets the library as ever, and with it what only the Hub has.

Every call goes on to Navidrome as it came, with the app's own sign-in, and its answer comes back untouched. A few
are the Hub's own:
- `search3` / `search2`: after the library's songs come the catalog's ones the library has not got;
- `stream` / `download`, `getSong`, `getCoverArt` of such a song: it plays at once, from the sources (`app/listen.py`);
- `star` of such a song: it goes to «Любимое» and is downloaded (the way ♥ of the web app does it).

A song of the catalog has an id of the Hub's own (`hm-…`: its catalog ref); once it is downloaded the library has
it under Navidrome's id. The listener is told by the app's sign-in: the same name and password as in the Hub (a
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
from app.catalog.models import CatalogTrack, split_ref
from app.config import get_settings
from app.db import session_scope
from app.library.owned import annotate_tracks
from app.models import User
from app.net import make_client
from app.security import decrypt
from app.services import svc

router = APIRouter(tags=["subsonic"])
log = logging.getLogger(__name__)

PREFIX = "hm-"
SEARCH_LIMIT = 25
SEARCH_TIMEOUT_S = 6  # the catalog is asked this long; the library's own answer never waits longer
KNOWN_MAX = 5000
PASSED = ("content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified", "cache-control",
          "content-disposition", "x-content-duration")
# calls that name a song and can do without the catalog's one: an empty answer of their kind
EMPTY = {"getLyrics": {"lyrics": {}}, "getLyricsBySongId": {"lyricsList": {}}, "getSimilarSongs": {"similarSongs": {}},
         "getSimilarSongs2": {"similarSongs2": {}}, "scrobble": {}, "setRating": {}, "unstar": {}}

_known: OrderedDict[str, CatalogTrack] = OrderedDict()  # catalog songs shown lately, by their ref
_nd: httpx.AsyncClient | None = None


def ext_id(ref: str) -> str:
    return PREFIX + base64.urlsafe_b64encode(ref.encode()).decode().rstrip("=")


def ext_ref(id_: str | None) -> str | None:
    """The catalog ref of a song id of the Hub's own; None for Navidrome's ids."""
    if not id_ or not id_.startswith(PREFIX):
        return None
    raw = id_[len(PREFIX):]
    try:
        ref = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        split_ref(ref)
    except Exception:
        return None
    return ref


def _remember(t: CatalogTrack) -> None:
    _known[t.ref] = t
    _known.move_to_end(t.ref)
    while len(_known) > KNOWN_MAX:
        _known.popitem(last=False)


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
    return out


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


async def _cover(ref: str, params: list[tuple[str, str]]) -> Response:
    t = await _track(ref)
    size = _int(params, "size", 0)
    url = t and ((t.cover_small_url if 0 < size <= 300 else None) or t.cover_url or t.cover_small_url)
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


async def _keep(refs: list[str], user: User) -> None:
    """♥ of catalog songs: «Любимое» gets them and they are downloaded."""
    from app import playlists
    from app.api.downloads import create_batch
    from app.api.schemas import DownloadIn
    from app.jobs.queue import notify

    async with session_scope() as s:
        user = await s.get(User, user.id)
        liked = await playlists.liked(s, user.id)
        await create_batch(s, user, DownloadIn(track_refs=refs, playlist_id=str(liked.id)), origin="catalog")
    notify()


@router.api_route("/rest/{endpoint:path}", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def rest(endpoint: str, request: Request) -> Response:
    endpoint = endpoint.removesuffix(".view")
    params = await _params(request)
    fmt = _one(params, "f")
    refs = {v: ext_ref(v) for k, v in params if v.startswith(PREFIX)}
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

    ref = ext_ref(_one(params, "id"))
    if endpoint in ("stream", "download") and ref:
        return await _play(request, ref, user, fmt)
    if endpoint == "getCoverArt" and ref:
        return await _cover(ref, params)
    if endpoint == "getSong" and ref:
        t = await _track(ref)
        return _ok(fmt, song=song(t)) if t else _error(fmt, 70, "Трек не найден в каталоге")
    if endpoint == "star":
        try:
            await _keep(list(dict.fromkeys(ext)), user)
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
