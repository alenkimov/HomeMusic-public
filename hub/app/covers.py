"""Pictures of the catalogs and the services (covers, artists' photos), kept on the Hub's disk: each is asked of its
CDN once, and the web app gets them from the Hub (`/api/covers`) — a device needs no way to the CDN of its own (Spotify's
is out of reach of some networks), and the service worker keeps them for when there is no network at all.

A picture is kept under its address (`staging/covers/<md5>.img`): the CDNs' addresses name one image for good. A
smaller copy of a picture for a list (`sized`) is made once and kept beside it."""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.config import get_settings
from app.net import make_client

log = logging.getLogger(__name__)

# where pictures are taken from: the catalogs' and the import services' image CDNs — and nothing else, the Hub is
# not a way out for whatever address a page names
ALLOWED = ("scdn.co", "spotifycdn.com", "dzcdn.net", "avatars.yandex.net", "avatars.mds.yandex.net", "userapi.com",
           "ytimg.com", "sndcdn.com", "lastfm.freetls.fastly.net", "mzstatic.com")


def allowed(url: str | None) -> bool:
    if not url:
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == d or host.endswith("." + d) for d in ALLOWED)


# Deezer's pictures live under several host names with the same paths, and they refuse this network by spells: the
# same address gives the picture now and 403 a moment later, and after a run of requests all of them refuse for a
# while. So a picture of theirs is asked of each name in turn, unhurriedly, one at a time. Other CDNs are asked at
# once, a few at a time.
_DZ_HOSTS = ("cdn-images.dzcdn.net", "e-cdns-images.dzcdn.net", "e-cdn-images.dzcdn.net")
_DZ_PACE_S = 0.25
_dz_gate = asyncio.Lock()
_others = asyncio.Semaphore(8)
_coming: dict[str, asyncio.Future[bytes | None]] = {}
MAX_BYTES = 12_000_000


def kept_path(url: str) -> Path:
    return get_settings().staging_dir / "covers" / f"{hashlib.md5(url.encode()).hexdigest()}.img"


async def _read(p: Path, max_bytes: int | None) -> bytes | None:
    if max_bytes is not None and p.stat().st_size > max_bytes:
        return None
    return await asyncio.to_thread(p.read_bytes)


async def fetch_cover(url: str | None, *, max_bytes: int | None = None) -> bytes | None:
    """The picture at `url`: from the disk if it was ever got, else from its CDN (then kept). None: not given just
    now, or larger than `max_bytes`."""
    if not url:
        return None
    kept = kept_path(url)
    if kept.is_file():
        return await _read(kept, max_bytes)
    # the same picture asked for by several at once (a list's rows): one request
    if (coming := _coming.get(url)) is None:
        coming = _coming[url] = asyncio.ensure_future(_download(url, kept))
        coming.add_done_callback(lambda _: _coming.pop(url, None))
    image = await asyncio.shield(coming)
    if image is None or (max_bytes is not None and len(image) > max_bytes):
        return None
    return image


async def _download(url: str, kept: Path) -> bytes | None:
    named = next((h for h in _DZ_HOSTS if f"//{h}/" in url), None)
    if named:
        tries = list(dict.fromkeys(url.replace(named, h) for h in (*_DZ_HOSTS[1:], _DZ_HOSTS[0])))
        async with _dz_gate:
            if kept.is_file():  # it came while this request waited its turn
                return await _read(kept, None)
            for u in tries:
                await asyncio.sleep(_DZ_PACE_S)
                if (image := await _get(u, kept)) is not None:
                    return image
    else:
        async with _others:
            if (image := await _get(url, kept)) is not None:
                return image
    log.info("cover not given just now: %s", url)
    return None


async def _get(u: str, kept: Path) -> bytes | None:
    try:
        async with make_client(timeout=30) as c, c.stream("GET", u) as r:
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                return None
            image = bytearray()
            async for chunk in r.aiter_bytes():
                image.extend(chunk)
                if len(image) > MAX_BYTES:
                    return None
    except httpx.HTTPError as e:
        log.warning("cover download failed %s: %r", u, e)
        return None
    kept.parent.mkdir(parents=True, exist_ok=True)
    part = kept.with_suffix(".part")
    await asyncio.to_thread(part.write_bytes, image)
    part.replace(kept)  # whole or not at all: a reader never finds half a picture
    return bytes(image)


def image_type(data: bytes) -> str:
    """The picture's type by its first bytes (the CDNs give JPEG, PNG and WebP)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "application/octet-stream"


async def sized(key: str, size: int, load: Callable[[], Awaitable[bytes | None]]) -> bytes | None:
    """A copy of a picture no larger than `size` px, as JPEG — made once and kept (`key` names the picture as it is:
    a new picture is to have a new key, the file's time in it). `load` gives the picture itself when the copy is to be
    made."""
    p = get_settings().staging_dir / "covers" / "sized" / f"{hashlib.md5(key.encode()).hexdigest()}-{size}.jpg"
    if p.is_file():
        return await asyncio.to_thread(p.read_bytes)
    data = await load()
    if data is None:
        return None
    small = await asyncio.to_thread(_resize, data, size)
    p.parent.mkdir(parents=True, exist_ok=True)
    part = p.with_suffix(".part")
    await asyncio.to_thread(part.write_bytes, small)
    part.replace(p)
    return small


def _resize(data: bytes, size: int) -> bytes:
    from PIL import Image

    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()
