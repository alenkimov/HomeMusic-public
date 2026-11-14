"""Spotify-like artist and album pages with library ownership (FR-4)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AlbumOut, ArtistPageOut, DiscographyOut, NamesakeIn, NamesakeOut
from app.catalog import namesakes, popular
from app.auth import current_user
from app.catalog.models import ArtistPage, CatalogAlbum, split_ref
from app.db import get_session
from app.library.owned import annotate_albums, annotate_tracks
from app.models import User
from app.services import svc

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def _split(ref: str) -> tuple[str, str]:
    try:
        return split_ref(ref)
    except ValueError as e:
        raise HTTPException(400, "Неверная ссылка на каталог") from e


async def get_artist_page(ref: str) -> ArtistPage:
    src, id_ = _split(ref)
    try:
        if src == "dz" and svc.deezer:
            return await svc.deezer.get_artist_page(id_)
        if src == "hm" and svc.hitmoz_catalog:
            return await svc.hitmoz_catalog.get_artist(id_)
    except HTTPException:
        raise
    except Exception as e:
        if type(e).__name__ == "CatalogNotFound":
            raise HTTPException(404, "Исполнитель не найден") from e
        raise HTTPException(502, f"Каталог недоступен: {e}") from e
    raise HTTPException(404, "Исполнитель не найден")


async def get_album(ref: str) -> CatalogAlbum:
    src, id_ = _split(ref)
    try:
        if src == "dz" and svc.deezer:
            return await svc.deezer.get_album(id_)
        if src == "hm" and svc.hitmoz_catalog:
            return await svc.hitmoz_catalog.get_album(id_)
    except Exception as e:
        if type(e).__name__ == "CatalogNotFound":
            raise HTTPException(404, "Альбом не найден") from e
        raise HTTPException(502, f"Каталог недоступен: {e}") from e
    raise HTTPException(404, "Альбом не найден")


@router.get("/artist/{ref}", response_model=ArtistPageOut)
async def artist(ref: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    page = await get_artist_page(ref)
    albums = await annotate_albums(s, page.albums, user.id)
    albums.sort(key=lambda a: (a.release_date is not None, a.release_date), reverse=True)
    heard, extra = await asyncio.gather(popular.listeners(page.artist.name), popular.more(page.artist, page.top_tracks))
    return ArtistPageOut(
        artist=page.artist,
        top_tracks=await annotate_tracks(s, [*page.top_tracks, *extra], user.id),
        lastfm_listeners=heard, top_from_lastfm=len(extra),
        albums=[a for a in albums if a.record_type == "album"],
        singles=[a for a in albums if a.record_type in ("single", "ep")],
        compilations=[a for a in albums if a.record_type == "compile"],
        excluded=len(namesakes.of(ref)),
    )


@router.get("/namesakes", response_model=list[NamesakeOut])
async def namesakes_list(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The releases said to be a namesake's, by artist: where they are brought back («Скрытое»)."""
    return await namesakes.listed(s)


@router.post("/namesakes")
async def namesakes_exclude(body: NamesakeIn, user: User = Depends(current_user),
                            s: AsyncSession = Depends(get_session, scope="function")) -> dict[str, int]:
    """«Это другой TAK»: these releases the catalog files under the artist are not theirs — for everyone here."""
    src, id_ = _split(body.artist_ref)
    if src != "dz" or svc.deezer is None or not body.album_refs:
        raise HTTPException(400, "Нужен исполнитель каталога и его релизы")
    want = set(body.album_refs)
    try:
        artist = await svc.deezer.get_artist(id_)
        albums = [a for a in await svc.deezer.get_artist_albums(id_, everyones=True) if a.ref in want]
    except Exception as e:
        raise HTTPException(502, f"Каталог недоступен: {e}") from e
    n = await namesakes.exclude(s, user.id, body.artist_ref, artist.name, albums)
    await s.commit()
    return {"excluded": n, "total": len(namesakes.of(body.artist_ref))}


@router.post("/namesakes/restore")
async def namesakes_restore(body: NamesakeIn, user: User = Depends(current_user),
                            s: AsyncSession = Depends(get_session, scope="function")) -> dict[str, int]:
    """They are the artist's after all."""
    n = await namesakes.restore(s, body.artist_ref, body.album_refs)
    await s.commit()
    return {"restored": n, "total": len(namesakes.of(body.artist_ref))}


@router.get("/artist-named")
async def artist_named(name: str, user: User = Depends(current_user)) -> dict[str, str | None]:
    """The catalog's artist of this very name — where only a name is known (a track playing from a list that does
    not say who its artists are). `ref` null: the catalog has nobody called so."""
    from app.api.medialib import _in_catalog

    return {"ref": await _in_catalog(name)}


DISCOGRAPHY_PARALLEL = 4  # releases asked of the catalog at once (each is kept by the catalog's own cache after)


@router.get("/artist/{ref}/discography", response_model=DiscographyOut)
async def discography(ref: str, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    """Every release of the artist with its tracks, newest first. The catalog gives the tracks a release at a time;
    they are asked for here, a few at once, and kept — the first opening of a long discography takes seconds, the
    next ones none. A release whose tracks the catalog does not give comes without them."""
    page = await get_artist_page(ref)
    gate = asyncio.Semaphore(DISCOGRAPHY_PARALLEL)

    async def whole(a: CatalogAlbum) -> CatalogAlbum:
        async with gate:
            try:
                return await get_album(a.ref)
            except Exception as e:
                log.info("discography of %s: no tracks of %s: %r", ref, a.ref, e)
                return a

    albums = list(await asyncio.gather(*(whole(a) for a in page.albums)))
    # ownership of every track in one go, then back to its release
    flat = [t for a in albums for t in (a.tracks or [])]
    told = iter(await annotate_tracks(s, flat, user.id))
    outs = await annotate_albums(s, [a.model_copy(update={"tracks": None}) for a in albums], user.id)
    for o, a in zip(outs, albums, strict=True):
        if a.tracks is not None:
            o.tracks = [next(told) for _ in a.tracks]
            owned = sum(1 for t in o.tracks if t.owned)
            o.owned_count = owned
            o.owned_status = "full" if owned and owned >= len(o.tracks) else ("partial" if owned else "none")
    outs.sort(key=lambda a: (a.release_date is not None, a.release_date), reverse=True)
    return DiscographyOut(artist=page.artist, albums=outs, excluded=len(namesakes.of(ref)))


@router.get("/album/{ref}", response_model=AlbumOut)
async def album(ref: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    a = await get_album(ref)
    return (await annotate_albums(s, [a], user.id))[0]
