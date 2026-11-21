"""How an artist looks: the round photo and the wide picture over their page, as the listeners chose them.

By itself an artist is shown with the photo of the catalog's record their page is of, and with the wide picture
the Hub finds for that record (app/catalog/banners.py). Both are guesses: a catalog keeps a photo with every record
of the artist, and the wide picture is found by a name — another spelling gives another picture, and a namesake's
page may answer. So either can be chosen (`ArtistLook`, one row an artist, for everyone on the server):

* the photo — of one of the catalog's records of the artist, the cover of an album of theirs here, or a picture
  of one's own;
* the wide picture — the one found by one of the artist's spellings, none at all (the page opens with the round
  photo), or a picture of one's own.

A picture chosen is kept on the Hub (the bytes), so the page opens without the network; a photo of a catalog's
record is the catalog's own address. Nothing chosen: as it was by itself.
"""

from __future__ import annotations

import asyncio
import io
import secrets
import time
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import CatalogArtist, split_ref
from app.library import artists
from app.models import Album, AlbumArtist, Artist, ArtistLook, ArtistName, Edit, Track, TrackArtist, utcnow

PHOTO_PX = 640  # a round photo is small wherever it is shown
FOUND_FOR_S = 3600  # a wide picture found for a spelling is kept in memory this long, for the window that offers it

_found: dict[tuple[int, str], tuple[float, bytes | None]] = {}


async def of(s: AsyncSession, artist_id: int) -> ArtistLook | None:
    return await s.get(ArtistLook, artist_id)


async def profile_card(ref: str | None) -> CatalogArtist | None:
    """The catalog's own card of a record (kept by the catalog's client); None: the catalog is away, or knows it no more."""
    from app.services import svc

    if not ref:
        return None
    try:
        src, id_ = split_ref(ref)
        if src == "dz" and svc.deezer is not None:
            return await svc.deezer.get_artist(id_)
        if src == "sp" and svc.spotify is not None:
            return (await svc.spotify.artist_page(id_)).artist
        return None
    except Exception:
        return None


async def spotify_photos(s: AsyncSession) -> dict[str, str]:
    """Cover fallbacks for library lists from profiles already linked and read."""
    from app.models import ArtistName, ArtistRef, CatalogCache

    rows = await s.execute(
        select(ArtistName.key, CatalogCache.payload_json)
        .join(ArtistRef, ArtistRef.artist_id == ArtistName.artist_id)
        .join(CatalogCache, CatalogCache.key == "sp:artist:" + func.substr(ArtistRef.ref, 4))
        .where(ArtistRef.ref.like("sp:%"))
        .order_by(ArtistRef.tied_at)
    )
    photos = {}
    for name, value in rows:
        a = (value.get("v") or {}).get("artist") or {}
        if url := a.get("picture_small_url") or a.get("picture_url"):
            photos.setdefault(name, url)
    return photos


async def _album_cover(s: AsyncSession, artist_id: int) -> str | None:
    album_id = (await s.execute(
        select(Album.id).join(AlbumArtist, AlbumArtist.album_id == Album.id)
        .where(AlbumArtist.artist_id == artist_id, Album.cover_path.is_not(None)).order_by(Album.id.desc()).limit(1))).scalar_one_or_none()
    return f"/api/library/albums/{album_id}/cover" if album_id else None


async def photo(s: AsyncSession, artist: Artist, look: ArtistLook | None = None, *, chosen_only: bool = False) -> tuple[str, str] | None:
    """The artist's photo — (large, small) addresses — as chosen; where nothing was chosen (and not `chosen_only`):
    the photo of the first record a catalog keeps of them, else the cover of an album of theirs here."""
    look = look or await of(s, artist.id)
    choice = look.photo_choice if look else None
    if choice == "own" and look is not None and look.photo_key:
        url = f"/api/artists/{artist.id}/photo?v={look.photo_key}"
        return url, url
    if choice == "album":
        url = await _album_cover(s, artist.id)
        if url:
            return url, url
    if choice and choice.startswith("profile:"):
        a = await profile_card(choice.partition(":")[2])
        if a is not None and (a.picture_url or a.picture_small_url):
            return a.picture_url or a.picture_small_url, a.picture_small_url or a.picture_url
    if chosen_only:
        return None
    refs = await artists.profiles(s, artist.id)
    for ref in refs:
        a = await profile_card(ref)
        if a is not None and (a.picture_url or a.picture_small_url):
            return a.picture_url or a.picture_small_url, a.picture_small_url or a.picture_url
    url = await _album_cover(s, artist.id)
    return (url, url) if url else None


def banner(artist_id: int, look: ArtistLook | None) -> tuple[bool, str | None]:
    """(a choice was made, the address of the wide picture chosen — None: none is to be shown)."""
    if look is None or not look.banner_choice:
        return False, None
    if look.banner_choice == "none" or not look.banner_key:
        return True, None
    return True, f"/api/artists/{artist_id}/banner?v={look.banner_key}"


async def _titles(s: AsyncSession, artist: Artist) -> list[str]:
    """What the artist is known by — the songs and releases of every record a catalog keeps of them, and the tracks
    of theirs here: a page that carries any of it is theirs, not a namesake's."""
    from app.services import svc

    out: list[str] = list((await s.execute(
        select(Track.title).join(TrackArtist, TrackArtist.track_id == Track.id).where(TrackArtist.artist_id == artist.id).limit(200))).scalars())
    for ref in await artists.profiles(s, artist.id):
        try:
            src, _ = split_ref(ref)
            if src not in ("dz", "sp"):
                continue
            page = await svc.artist_page(ref)
            out += [t.title for t in page.top_tracks] + [a.title for a in page.albums]
        except Exception:  # a record the catalog does not give just now says nothing
            continue
    return out


async def spellings(s: AsyncSession, artist: Artist) -> list[str]:
    spelt = (await s.execute(select(ArtistName.name).where(ArtistName.artist_id == artist.id).order_by(ArtistName.id))).scalars().all()
    out: list[str] = []
    for n in (artist.name, *spelt):
        if artists.key(n) not in {artists.key(x) for x in out}:
            out.append(n)
    return out


async def found_banner(s: AsyncSession, artist: Artist, name: str) -> bytes | None:
    """The wide picture found for the artist asked by this spelling of theirs (None: none). Kept in memory a while."""
    from app.catalog import banners

    if artists.key(name) not in {artists.key(n) for n in await spellings(s, artist)}:
        return None
    k = (artist.id, artists.key(name))
    hit = _found.get(k)
    if hit and time.monotonic() - hit[0] < FOUND_FOR_S:
        return hit[1]
    asked = CatalogArtist(ref=f"hub:{artist.id}", source="dz", id=str(artist.id), name=name)
    image = await banners.find(asked, await _titles(s, artist))
    _found[k] = (time.monotonic(), image)
    return image


async def options(s: AsyncSession, artist: Artist) -> dict[str, Any]:
    """What the window offers: every photo and every wide picture the artist may be shown with, and which is chosen."""
    look = await of(s, artist.id)
    photos: list[dict[str, Any]] = []
    for i, ref in enumerate(await artists.profiles(s, artist.id)):
        a = await profile_card(ref)
        if a is not None and (a.picture_url or a.picture_small_url):
            photos.append({"choice": f"profile:{ref}", "label": f"профиль {i + 1}", "note": {"dz": "Deezer", "sp": "Spotify"}.get(a.source, "каталог"),
                           "url": a.picture_small_url or a.picture_url})
    album = await _album_cover(s, artist.id)
    if album:
        photos.append({"choice": "album", "label": "обложка альбома", "note": "из библиотеки", "url": album})
    if look is not None and look.photo_key:
        photos.append({"choice": "own", "label": "своё", "note": "загружено", "url": f"/api/artists/{artist.id}/photo?v={look.photo_key}"})
    banners_ = [{"choice": f"name:{n}", "label": n, "note": "найдена по этому имени", "url": f"/api/artists/{artist.id}/banner-found?name={quote(n)}"}
                for n in await spellings(s, artist)]
    for ref in await artists.profiles(s, artist.id):
        if ref.startswith("sp:") and (a := await profile_card(ref)) and a.banner_url:
            banners_.insert(0, {"choice": f"profile:{ref}", "label": "Spotify", "note": "", "url": a.banner_url})
    if look is not None and look.banner_choice == "own" and look.banner_key:
        banners_.append({"choice": "own", "label": "своя", "note": "загружена", "url": f"/api/artists/{artist.id}/banner?v={look.banner_key}"})
    return {"id": artist.id, "name": artist.name,
            "photo": look.photo_choice if look else None, "banner": look.banner_choice if look else None,
            "photos": photos, "banners": banners_}


def _jpeg(data: bytes, max_px: int) -> bytes:
    from PIL import Image, ImageOps

    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception as e:
        raise ValueError("Это не картинка") from e
    if max(im.size) > max_px:
        im.thumbnail((max_px, max_px))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88, optimize=True)
    return out.getvalue()


async def _row(s: AsyncSession, artist: Artist) -> ArtistLook:
    look = await of(s, artist.id)
    if look is None:
        look = ArtistLook(artist_id=artist.id)
        s.add(look)
    return look


async def choose_photo(s: AsyncSession, user_id: int | None, artist: Artist, choice: str | None, image: bytes | None = None) -> None:
    """The artist's photo: `profile:<ref>` (a record of theirs), `album`, `own` (with the picture), or None — as by itself."""
    look = await _row(s, artist)
    was = look.photo_choice
    if choice == "own":
        if image is not None:
            look.photo_image, look.photo_key = await asyncio.to_thread(_jpeg, image, PHOTO_PX), secrets.token_hex(4)
        elif not look.photo_key:
            raise ValueError("Сначала загрузите картинку")
    elif choice and choice.startswith("profile:"):
        if choice.partition(":")[2] not in await artists.profiles(s, artist.id):
            raise ValueError("У исполнителя нет такого профиля в каталоге")
    elif choice not in (None, "album"):
        raise ValueError("Неизвестный вариант")
    look.photo_choice, look.at, look.by = choice, utcnow(), user_id
    s.add(Edit(target_type="artist", target_id=artist.id, user_id=user_id, changes_json={"photo": [was, choice]}))
    await s.flush()


async def choose_banner(s: AsyncSession, user_id: int | None, artist: Artist, choice: str | None, image: bytes | None = None) -> None:
    """The wide picture over the artist's page: `name:<spelling>` (the one found by it), `none`, `own` (with the
    picture), or None — the one the Hub finds by itself."""
    from app.catalog.banners import WIDTH

    spotify_banner = None
    if choice and choice.startswith("profile:"):
        from app.jobs.downloads import fetch_cover

        ref = choice.partition(":")[2]
        a = await profile_card(ref) if ref.startswith("sp:") and ref in await artists.profiles(s, artist.id) else None
        if a is None or not a.banner_url or not (spotify_banner := await fetch_cover(a.banner_url, max_bytes=8_000_000)):
            raise ValueError("Обложка Spotify недоступна")
    look = await _row(s, artist)
    was = look.banner_choice
    if choice == "own":
        if image is not None:
            look.banner_image, look.banner_key = await asyncio.to_thread(_jpeg, image, WIDTH), secrets.token_hex(4)
        elif look.banner_choice != "own" or not look.banner_key:
            raise ValueError("Сначала загрузите картинку")
    elif spotify_banner is not None:
        look.banner_image = await asyncio.to_thread(_jpeg, spotify_banner, WIDTH)
        look.banner_key = secrets.token_hex(4)
    elif choice and choice.startswith("name:"):
        found = await found_banner(s, artist, choice.partition(":")[2])
        if found is None:
            raise ValueError("По этому имени обложка не нашлась")
        look.banner_image, look.banner_key = found, secrets.token_hex(4)
    elif choice in (None, "none"):
        look.banner_image, look.banner_key = None, None
    else:
        raise ValueError("Неизвестный вариант")
    look.banner_choice, look.at, look.by = choice, utcnow(), user_id
    s.add(Edit(target_type="artist", target_id=artist.id, user_id=user_id, changes_json={"banner": [was, choice]}))
    await s.flush()
