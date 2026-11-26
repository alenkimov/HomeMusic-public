"""Read Spotify collections through a saved client session."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import sys
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import quote, unquote_plus, urlencode, urlsplit

import requests
from librespot.core import ApiClient, Session
from librespot.mercury import MercuryRequests
from librespot.metadata import AlbumId, ArtistId
from librespot.proto import Authentication_pb2 as authentication
from librespot.proto import ExtensionKind_pb2 as extension
from librespot.proto import Metadata_pb2 as metadata

from app.catalog import spotify_collection_pb2
from app.net import make_client

PORT = 4381
REDIRECT_URI = f"http://127.0.0.1:{PORT}/login"
_ID = r"[A-Za-z0-9]{22}"
_TRACK_URI = re.compile(rf"spotify:track:({_ID})")
_PLAYLIST_ID = re.compile(_ID)
_CONTEXT_PATH = "/context-resolve/v1/"
_COLLECTION_PATH = "/collection/v2/paging"
_COLLECTION_MIME = "application/vnd.collection-v2.spotify.proto"
_COLLECTION_PAGE_SIZE = 300
_BATCH = 100


class SessionError(Exception):
    def __init__(self, *, expired: bool = False):
        super().__init__("Spotify session expired" if expired else "Spotify client request failed")
        self.expired = expired


def authorize(state: str, verifier: str) -> str:
    challenge = base64.urlsafe_b64encode(sha256(verifier.encode("utf-8")).digest()).decode("ascii").rstrip("=")
    return "https://accounts.spotify.com/authorize?" + urlencode({
        "client_id": MercuryRequests.keymaster_client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": "streaming user-read-private",
        "state": state,
    })


async def exchange(code: str, verifier: str) -> dict[str, Any]:
    async with make_client(timeout=15) as client:
        result = await client.post("https://accounts.spotify.com/api/token", data={
            "grant_type": "authorization_code",
            "client_id": MercuryRequests.keymaster_client_id,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "code": code,
        })
    if result.status_code != 200:
        raise SessionError()
    token = result.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise SessionError()
    return await _run("connect", {"token": token})


async def check(secret: str, playlist_id: str | None) -> dict[str, Any]:
    return await _run("check", {"secret": secret, "playlist_id": playlist_id}, seconds=25 if playlist_id else 90)


async def collection(secret: str, playlist_id: str | None) -> dict[str, Any]:
    return await _run("collection", {"secret": secret, "playlist_id": playlist_id}, seconds=180)


async def search_tracks(secret: str, query: str, limit: int) -> list[dict[str, Any]]:
    return (await _run("search", {"secret": secret, "query": query, "limit": limit}, seconds=15))["tracks"]


async def _run(operation: str, payload: dict[str, Any], *, seconds: int = 25) -> dict[str, Any]:
    # Librespot manages its own sockets and threads. A timed-out request needs to release them all.
    # The session is another process: its web requests go through the proxy set for Spotify or the account
    # (`app.outbound`, by the environment `requests` reads), and the call is one row of the journal.
    import os
    import time

    from app import outbound

    pid, proxy = outbound.proxy_url("spotify")
    env = {**os.environ, **({"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy} if proxy else {})}
    t0, error, data = time.monotonic(), None, b""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.catalog.spotify_session", operation,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env,
    )
    try:
        async with asyncio.timeout(seconds):
            data, _ = await process.communicate(json.dumps(payload).encode("utf-8"))
    except TimeoutError:
        error = f"timed out after {seconds} s"
        raise
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        try:
            result = json.loads(data)
        except ValueError:
            result = None
        if error is None and (result is None or process.returncode or "error" in result):
            error = f"session: {(result or {}).get('error') or f'exit {process.returncode}'}"
        outbound.record(service="spotify", method="SESSION", host="librespot", path=operation,
                        elapsed_s=time.monotonic() - t0, error=error, proxy_id=pid)
    if not isinstance(result, dict):
        raise SessionError()
    if process.returncode or "error" in result:
        raise SessionError(expired=result.get("error") == "expired")
    return result


def _get(api: ApiClient, path: str) -> dict[str, Any]:
    response = api.send("GET", path, {"Accept": "application/json"}, None)
    response.raise_for_status()
    return response.json()


def _context(api: ApiClient, uri: str) -> list[dict[str, Any]]:
    pages = _get(api, _CONTEXT_PATH + uri).get("pages") or []
    rows = []
    visited = set()
    for page in pages:
        rows.extend(page.get("tracks") or [])
        if path := page.get("next_page_url") or (page.get("page_url") if not page.get("tracks") else None):
            # Context paging links are relative to the Spotify client, alongside the first page.
            path = path.removeprefix("hm://")
            if path.startswith("context-resolve/"):
                path = "/" + path
            if path.startswith(_CONTEXT_PATH) and path not in visited:
                visited.add(path)
                pages.append(_get(api, path))
    return rows


def _playlist(api: ApiClient, ident: str, first_only: bool) -> tuple[str, list[dict[str, Any]]]:
    if not _PLAYLIST_ID.fullmatch(ident):
        raise ValueError("Invalid playlist ID")
    path = f"/playlist/v2/playlist/{ident}"
    length = 5 if first_only else _BATCH
    snapshot = _get(api, f"{path}?from=0&length={length}&decorate=length,attributes")
    title = (snapshot.get("attributes") or {}).get("name") or "Плейлист"
    rows = (snapshot.get("contents") or {}).get("items") or []
    total = int(snapshot.get("length", len(rows)))
    while not first_only and len(rows) < total:
        page = _get(api, f"{path}?from={len(rows)}&length={length}")
        items = (page.get("contents") or {}).get("items") or []
        if not items:
            break
        rows.extend(items)
    return title, rows


def _liked_rows(api: ApiClient, username: str) -> list[dict[str, Any]]:
    """Read the saved library, including local entries omitted from the playback context."""
    request = spotify_collection_pb2.PageRequest(username=username, set="collection", limit=_COLLECTION_PAGE_SIZE)
    headers = {"Accept": _COLLECTION_MIME, "Content-Type": _COLLECTION_MIME}
    visited = set()
    saved = {}
    while True:
        with api.send("POST", _COLLECTION_PATH, headers, request.SerializeToString()) as response:
            response.raise_for_status()
            page = spotify_collection_pb2.PageResponse.FromString(response.content)
        for item in page.items:
            uri = item.uri
            if not item.is_removed and (_TRACK_URI.fullmatch(uri) or (uri.startswith("spotify:local:") and _local(uri))):
                saved.setdefault(uri, item.added_at)
        token = page.next_page_token
        if not token:
            break
        if token in visited:
            raise SessionError()
        visited.add(token)
        request.pagination_token = token

    # Collection pages do not guarantee the same ordering as the playback context.
    return [{"uri": uri, "metadata": {"added_at": stamp or None}}
            for uri, stamp in sorted(saved.items(), key=lambda item: item[1], reverse=True)]


def _track(ident: str, song: metadata.Track) -> dict[str, Any]:
    names = [artist.name for artist in song.artist if artist.name]
    refs = [f"sp:{ArtistId.from_hex(a.gid.hex()).id()}" if a.gid else None for a in song.artist if a.name]
    album = song.album
    cover = max(album.cover_group.image, key=lambda image: image.width, default=None)
    thumb = min(album.cover_group.image, key=lambda image: abs(image.width - 300), default=None)
    return {
        "id": ident, "ref": f"sp:{ident}", "source": "sp", "spotify_id": ident, "title": song.name,
        "artist": names[0] if names else "", "artists": names, "artist_refs": refs,
        "artist_ref": refs[0] if refs else None,
        "album_ref": f"sp:{AlbumId.from_hex(album.gid.hex()).id()}" if album.gid else None,
        "album_title": album.name or None, "album_artist": album.artist[0].name if album.artist else None,
        "duration_s": round(song.duration / 1000) or None,
        "track_no": song.number or None, "disc_no": song.disc_number or None,
        "isrc": next((item.id for item in song.external_id if item.type == "isrc"), None),
        "explicit": song.explicit if song.HasField("explicit") else None,
        "cover_url": f"https://i.scdn.co/image/{cover.file_id.hex()}" if cover else None,
        "cover_small_url": f"https://i.scdn.co/image/{thumb.file_id.hex()}" if thumb else None,
    }


def _local(uri: str) -> dict[str, Any] | None:
    fields = uri.split(":")
    if len(fields) != 6 or not fields[4]:
        return None
    artist, album, title = map(unquote_plus, fields[2:5])
    return {"uri": uri, "title": title, "artist": artist, "album": album or None,
            "duration_s": int(fields[5]) if fields[5].isdigit() else None}


def _date(value: Any) -> str | None:
    if str(value).isdigit():
        stamp = int(value)
        return datetime.fromtimestamp(stamp / 1000 if stamp >= 10**12 else stamp, UTC).isoformat()
    return value if isinstance(value, str) and "T" in value else None


def _tracks(api: ApiClient, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_uri = {row["uri"]: row for row in reversed(rows) if isinstance(row.get("uri"), str)}
    uris = list(dict.fromkeys(row["uri"] for row in rows if isinstance(row.get("uri"), str)))
    catalog_uris = [uri for uri in uris if _TRACK_URI.fullmatch(uri)]
    tracks: dict[str, dict[str, Any]] = {}
    for i in range(0, len(catalog_uris), _BATCH):
        batch = catalog_uris[i:i + _BATCH]
        for uri, data in zip(batch, api.get_ext_metadata_batched([extension.TRACK_V4] * len(batch), batch), strict=True):
            if data and (song := metadata.Track.FromString(data)).name:
                tracks[uri] = _track(uri.rsplit(":", 1)[1], song)
    result = []
    for uri in uris:
        track = _local(uri) if uri.startswith("spotify:local:") else tracks.get(uri)
        if track:
            row = by_uri[uri]
            result.append({"track": track, "added_at": _date(
                (row.get("metadata") or {}).get("added_at") or (row.get("attributes") or {}).get("timestamp"))})
    return result


def _perform(session: Session, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    plan = session.get_user_attribute("type")
    account = {"subject": session.username(), "plan": plan if plan in {"free", "premium"} else None}
    if operation == "connect":
        return {"credential": json.dumps(session.credentials()), **account}
    api = session.api()
    if operation == "search":
        query = quote(payload["query"], safe="")
        rows = _context(api, f"spotify:search:{query}")[:payload["limit"]]
        return {"tracks": [result["track"] for result in _tracks(api, rows) if "id" in result["track"]]}
    ident = payload.get("playlist_id")
    if ident:
        title, rows = _playlist(api, ident, operation == "check")
    else:
        title = "Любимые треки"
        rows = _liked_rows(api, session.username())
    if operation == "check":
        return {**account, "playlist": title if ident else None, "liked_read": None if ident else len(rows)}
    return {**account, "title": title, "tracks": _tracks(api, rows), "expected": len(rows)}


def _main() -> None:
    logging.disable(logging.CRITICAL)
    # These requests run inside the worker, including redirects and Librespot's authentication traffic.
    send = requests.Session.send

    def send_spotify(client, req, **kwargs):
        url = urlsplit(req.url)
        if url.scheme != "https" or not url.hostname or not url.hostname.endswith(".spotify.com") or url.hostname == "api.spotify.com":
            raise ValueError("Expected a Spotify client service")
        kwargs["timeout"] = (8, 15)
        return send(client, req, **kwargs)

    requests.Session.send = send_spotify
    session = None
    try:
        operation = sys.argv[1]
        payload = json.load(sys.stdin.buffer)
        config = (Session.Configuration.Builder().set_cache_enabled(False)
                  .set_do_cache_clean_up(False).set_store_credentials(False).build())
        builder = Session.Builder(config)
        if operation == "connect":
            builder.login_credentials = authentication.LoginCredentials(
                typ=authentication.AUTHENTICATION_SPOTIFY_TOKEN, auth_data=payload["token"].encode("utf-8"))
        else:
            secret = payload["secret"]
            if not secret.startswith("{"):
                raise SessionError(expired=True)
            builder.stored(base64.b64encode(secret.encode("utf-8")).decode("ascii"))
        session = builder.create()
        result = _perform(session, operation, payload)
    except Session.SpotifyAuthenticationException:
        result = {"error": "expired"}
    except SessionError as error:
        result = {"error": "expired" if error.expired else "unavailable"}
    except Exception:
        result = {"error": "unavailable"}
    sys.stdout.buffer.write(json.dumps(result).encode("utf-8"))
    sys.stdout.buffer.flush()
    if session:
        session.close()


if __name__ == "__main__":
    _main()
