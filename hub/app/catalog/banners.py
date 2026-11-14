"""The wide picture over an artist's page.

The catalog has an artist's square photo and nothing else; Last.fm gives no pictures at all. YouTube Music has a
wide one over every artist's page — the Hub talks to it already for the radios of the mixes (mixes/sources.py), so
no key is needed. It is found by the artist's name, and a name is not an artist: the picture is taken only from a
page of exactly that name that has something of the artist's on it — a song or a release the catalog knows them by
(`theirs`; an artist the catalog knows nothing of is taken by the name alone). What is found — or that nothing was — is kept
(`artist_banners`): the picture itself, on the Hub, so it opens at once, without the network, and whatever Google
answers the listener's address with. Nothing found is looked for again after `RETRY_DAYS`.

YouTube Music is asked the way its own site asks: it may change that, and then there are no pictures — the page
shows the round photo as before.
"""

from __future__ import annotations

import asyncio
import io
import logging
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.catalog.models import CatalogArtist
from app.db import session_scope
from app.matching.normalize import normalize_title
from app.mixes.sources import YTM_API_URL, _runs_text, _ytm_headers, ytm_client_version
from app.models import ArtistBanner, utcnow
from app.net import make_client

log = logging.getLogger(__name__)

YTM_BROWSE_URL = "https://music.youtube.com/youtubei/v1/browse?prettyPrint=false"
ARTISTS_PARAMS = "EgWKAQIgAWoMEA4QChADEAQQCRAF"  # the «Artists» filter of the search
RETRY_DAYS = 7
WIDTH = 1920  # the widest it is kept: a page is no wider
TRIED = 3  # pages of that very name looked at

_tasks: dict[str, asyncio.Task] = {}


def _walk(data: Any, key: str) -> list[dict]:
    out: list[dict] = []
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if isinstance(row := cur.get(key), dict):
                out.append(row)
            stack.extend(reversed(list(cur.values())))
        elif isinstance(cur, list):
            stack.extend(reversed(cur))
    return out


def _first_text(row: dict) -> str:
    cols = row.get("flexColumns") or []
    return _runs_text(((cols[0] if cols else {}).get("musicResponsiveListItemFlexColumnRenderer") or {}).get("text"))


def artists_found(body: Any) -> list[tuple[str, str]]:
    """(name, the id of its page) of the artists a search answered, in its order."""
    out = []
    for row in _walk(body, "musicResponsiveListItemRenderer"):
        bid = ((row.get("navigationEndpoint") or {}).get("browseEndpoint") or {}).get("browseId")
        if bid and str(bid).startswith("UC"):
            out.append((_first_text(row), bid))
    return out


def page_of(body: Any) -> tuple[str, list[dict], set[str]]:
    """(the name over an artist's page, the sizes of its wide picture, the names of the songs and releases on it)."""
    header = next(iter((body.get("header") or {}).values()), {}) if isinstance(body, dict) else {}
    header = header if isinstance(header, dict) else {}
    pics = (((header.get("thumbnail") or {}).get("musicThumbnailRenderer") or {}).get("thumbnail") or {}).get("thumbnails") or []
    names = {normalize_title(t).text for row in _walk(body, "musicResponsiveListItemRenderer") if (t := _first_text(row))}
    names |= {normalize_title(t).text for row in _walk(body, "musicTwoRowItemRenderer") if (t := _runs_text(row.get("title")))}
    return _runs_text(header.get("title")), [p for p in pics if p.get("url") and p.get("width")], names - {""}


def theirs(known: set[str], on_page: set[str]) -> bool:
    """Something of the artist's is on the page: a name of the catalog and a name of the page are one, or one is
    the other with more said — YouTube Music writes «プシ (Psi)» where the catalog has «Psi», «My Level (Egotrip)»
    where it has «Egotrip». A word of one or two letters alone proves nothing."""
    words = [set(x.split()) for x in on_page]
    for k in known:
        kw = set(k.split())
        if len(k.replace(" ", "")) < 3:
            continue
        if any(w and (kw <= w or (w <= kw and len("".join(w)) >= 3)) for w in words):
            return True
    return False


def _wide(pics: list[dict]) -> dict | None:
    """The widest picture not wider than a page needs — and wide it must be: a square one is the photo again."""
    wide = [p for p in pics if p.get("height") and p["width"] / p["height"] >= 1.8]
    fit = [p for p in wide if p["width"] <= WIDTH]
    return max(fit, key=lambda p: p["width"]) if fit else (min(wide, key=lambda p: p["width"]) if wide else None)


def _jpeg(data: bytes) -> bytes:
    from PIL import Image

    im = Image.open(io.BytesIO(data)).convert("RGB")
    if im.width > WIDTH:
        im = im.resize((WIDTH, round(im.height * WIDTH / im.width)))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=86, optimize=True)
    return out.getvalue()


async def find(artist: CatalogArtist, titles: list[str]) -> bytes | None:
    """The artist's wide picture from YouTube Music, as a JPEG; None: no page that is surely theirs has one.
    `titles`: what the catalog knows the artist by — their popular tracks, their releases."""
    want = artist.name.strip().casefold()
    known = {normalize_title(t).text for t in titles} - {""}
    version = ytm_client_version()
    ctx = {"context": {"client": {"clientName": "WEB_REMIX", "clientVersion": version, "hl": "en"}}}
    async with make_client(timeout=20) as c:
        r = await c.post(YTM_API_URL, headers=_ytm_headers(version), json={**ctx, "query": artist.name, "params": ARTISTS_PARAMS})
        r.raise_for_status()
        found = artists_found(r.json())
        if not found:  # not «nobody of that name» but no answer at all: asked again, not kept as «none»
            raise RuntimeError("YouTube Music named no artists")
        same = [bid for name, bid in found if name.strip().casefold() == want][:TRIED]
        for bid in same:
            b = await c.post(YTM_BROWSE_URL, headers=_ytm_headers(version), json={**ctx, "browseId": bid})
            b.raise_for_status()
            name, pics, songs = page_of(b.json())
            if name.strip().casefold() != want or (known and not theirs(known, songs)):
                continue  # a namesake's page
            pic = _wide(pics)
            if pic is None:
                continue
            img = await c.get(pic["url"])
            img.raise_for_status()
            return await asyncio.to_thread(_jpeg, img.content)
    return None


async def _look(artist: CatalogArtist, titles: list[str]) -> None:
    try:
        image = await find(artist, titles)
    except Exception as e:  # YouTube Music did not answer: asked again the next time the page opens
        log.info("banner of %s (%r): %r", artist.ref, artist.name, e)
        return
    async with session_scope() as s:
        row = await s.get(ArtistBanner, artist.ref)
        if row is None:
            row = ArtistBanner(artist_ref=artist.ref)
            s.add(row)
        row.image, row.key, row.checked_at = image, secrets.token_hex(4) if image else None, utcnow()
    log.info("banner of %s (%r): %s", artist.ref, artist.name, "kept" if image else "none")


async def url_of(artist: CatalogArtist, titles: list[str], wait_s: float = 3.0) -> str | None:
    """The address of the artist's wide picture; None: there is none (yet). The first asking looks for it — the
    page waits `wait_s` for that, no longer; what comes later is there the next time."""
    async with session_scope() as s:
        row = (await s.execute(select(ArtistBanner.key, ArtistBanner.checked_at)
                               .where(ArtistBanner.artist_ref == artist.ref))).first()
    if row is not None and (row.key or utcnow() - row.checked_at < timedelta(days=RETRY_DAYS)):
        return f"/api/catalog/artist/{artist.ref}/banner?v={row.key}" if row.key else None
    task = _tasks.get(artist.ref)
    if task is None or task.done():
        task = _tasks[artist.ref] = asyncio.create_task(_look(artist, titles), name=f"banner:{artist.ref}")
        task.add_done_callback(lambda _t, ref=artist.ref: _tasks.pop(ref, None))
    try:
        await asyncio.wait_for(asyncio.shield(task), wait_s)
    except (TimeoutError, asyncio.TimeoutError):
        return None
    async with session_scope() as s:
        key = (await s.execute(select(ArtistBanner.key).where(ArtistBanner.artist_ref == artist.ref))).scalar_one_or_none()
    return f"/api/catalog/artist/{artist.ref}/banner?v={key}" if key else None


async def image_of(ref: str) -> bytes | None:
    async with session_scope() as s:
        return (await s.execute(select(ArtistBanner.image).where(ArtistBanner.artist_ref == ref))).scalar_one_or_none()
