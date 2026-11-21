"""An artist's name, spellings and other projects (app/library/artists.py), as the pages change them."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.catalog import ARTIST_URLS
from app.auth import current_user
from app.catalog.models import CATALOG_SERVICES, CatalogArtist
from app.db import get_session
from app.events import bus
from app.jobs.queue import enqueue, notify
from app.library import artists
from app.library.looks import profile_card
from app.models import Artist, User
from app.services import svc

router = APIRouter(prefix="/api/artists", tags=["artists"])


class ArtistNameOut(BaseModel):
    id: int | None = None  # None: the name of a namesake, which is not a spelling of theirs to change
    name: str
    source: str  # catalog / library / manual
    main: bool = False


class ArtistPickOut(BaseModel):
    id: int
    name: str


class ArtistBriefOut(ArtistPickOut):
    """Another artist, as a page names them: the catalog's record that leads to their page, and a picture."""

    ref: str | None = None  # none: only the name leads to them
    nb_fans: int | None = None
    picture_url: str | None = None


class ArtistProjectOut(ArtistBriefOut):
    also: list[str] = []


class ArtistCardOut(BaseModel):
    """An artist as the Hub knows them: the one name, the other spellings, the projects linked, and who looks like
    the same artist under another spelling. `rewriting`: the library is being written in the name just now."""

    id: int
    name: str
    names: list[ArtistNameOut] = []
    held_by: str | None = None  # a namesake: bare text of this name is taken for that artist
    profiles: list[str] = []  # the catalog's records of the artist
    projects: list[ArtistProjectOut] = []
    suggestions: list[ArtistBriefOut] = []
    rewriting: bool = False


class ArtistProfileOut(BaseModel):
    ref: str
    service: str
    name: str
    picture_url: str | None = None
    followers: int | None = None
    url: str | None = None


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)


class NameIdIn(BaseModel):
    name_id: int


class OtherIn(BaseModel):
    """Another artist: one the Hub knows (`other_id`), or a name (one nobody has yet becomes an artist)."""

    other_id: int | None = None
    name: str | None = Field(default=None, max_length=300)


async def _artist(s: AsyncSession, artist_id: int) -> Artist:
    a = await artists.by_id(s, artist_id)
    if a is None:
        raise HTTPException(404, "Исполнитель не найден")
    return a


async def _other(s: AsyncSession, body: OtherIn) -> Artist:
    if body.other_id:
        return await _artist(s, body.other_id)
    if body.name and body.name.strip():
        return await artists.who(s, body.name, None, "manual")
    raise HTTPException(400, "Не указан исполнитель")


async def _done(s: AsyncSession, user: User, a: Artist, *, rewrite: bool = False) -> dict[str, Any]:
    if rewrite:
        await enqueue(s, "artist_rewrite", {"artist_id": a.id, "user_id": user.id})
    out = await artists.card(s, a)
    await s.commit()
    if rewrite:
        notify()
    bus.publish(user.id, "library", {"kind": "changed", "album_id": None})
    return {**out, "rewriting": rewrite}


@router.get("/of", response_model=ArtistCardOut | None)
async def of(name: str | None = None, ref: str | None = None, user: User = Depends(current_user),
             s: AsyncSession = Depends(get_session, scope="function")):
    """The artist a page is of: by the catalog's own (`ref`) or by a name. None: neither was given."""
    a = await artists.who(s, name, ref, "catalog")
    if a is None:
        return None
    out = await artists.card(s, a)
    await s.commit()
    return out


@router.get("/find", response_model=list[ArtistPickOut])
async def find(q: str = Query(min_length=1), user: User = Depends(current_user),
               s: AsyncSession = Depends(get_session, scope="function")):
    return await artists.find(s, q)


@router.post("/{artist_id}/names", response_model=ArtistCardOut)
async def add_name(artist_id: int, body: NameIn, user: User = Depends(current_user),
                   s: AsyncSession = Depends(get_session, scope="function")):
    """One more spelling. 409 with the artist who has it already: the two may be joined (`/merge`)."""
    a = await _artist(s, artist_id)
    try:
        await artists.add_name(s, user.id, a, body.name)
    except artists.Taken as e:
        raise HTTPException(409, {"artist_id": e.artist_id, "name": e.name}) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return await _done(s, user, a)


@router.delete("/{artist_id}/names/{name_id}", response_model=ArtistCardOut)
async def drop_name(artist_id: int, name_id: int, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    a = await _artist(s, artist_id)
    try:
        await artists.drop_name(s, user.id, a, name_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return await _done(s, user, a)


@router.post("/{artist_id}/main", response_model=ArtistCardOut)
async def set_main(artist_id: int, body: NameIdIn, user: User = Depends(current_user),
                   s: AsyncSession = Depends(get_session, scope="function")):
    """A spelling becomes the name: the library's tracks, albums and files are rewritten in it (a job)."""
    a = await _artist(s, artist_id)
    try:
        changed = await artists.set_main(s, user.id, a, body.name_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return await _done(s, user, a, rewrite=changed)


@router.post("/{artist_id}/merge", response_model=ArtistCardOut)
async def merge(artist_id: int, body: OtherIn, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    """The other artist is this one: their spellings come here, and the library is rewritten in this name."""
    a = await _artist(s, artist_id)
    other = await _other(s, body)
    if other.id == a.id:
        raise HTTPException(422, "Это тот же исполнитель")
    await artists.merge(s, user.id, a, other)
    return await _done(s, user, a, rewrite=True)


@router.post("/{artist_id}/projects", response_model=ArtistCardOut)
async def add_project(artist_id: int, body: OtherIn, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    a = await _artist(s, artist_id)
    try:
        await artists.link(s, user.id, a, await _other(s, body))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return await _done(s, user, a)


@router.delete("/{artist_id}/projects/{other_id}", response_model=ArtistCardOut)
async def drop_project(artist_id: int, other_id: int, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    a = await _artist(s, artist_id)
    await artists.link(s, user.id, a, await _artist(s, other_id), on=False)
    return await _done(s, user, a)


@router.post("/{artist_id}/apart", response_model=ArtistCardOut)
async def apart(artist_id: int, body: OtherIn, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    """«Это другой»: not offered to be joined with this one again."""
    a = await _artist(s, artist_id)
    await artists.apart(s, a, await _other(s, body))
    return await _done(s, user, a)


async def _profile_card(ref: str) -> CatalogArtist | None:
    try:
        async with asyncio.timeout(5):
            if ref.startswith(("ym:", "hm:")):
                return (await svc.artist_page(ref)).artist
            return await profile_card(ref)
    except Exception:
        return None


@router.get("/{artist_id}/profiles", response_model=list[ArtistProfileOut])
async def linked_profiles(artist_id: int, user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session, scope="function")):
    a = await _artist(s, artist_id)
    refs = await artists.profiles(s, a.id)
    await s.commit()
    cards = await asyncio.gather(*(_profile_card(ref) for ref in refs))
    out = []
    for ref, card in zip(refs, cards, strict=True):
        source, _, ref_id = ref.partition(":")
        link = ARTIST_URLS.get(source)
        out.append(ArtistProfileOut(
            ref=ref, service=CATALOG_SERVICES.get(source, "catalog"), name=card.name if card else a.name,
            picture_url=(card.picture_small_url or card.picture_url) if card else None,
            followers=card.nb_fans if card else None, url=link.format(quote(ref_id, safe="")) if link else None,
        ))
    return out


@router.delete("/{artist_id}/profiles/{ref}", response_model=ArtistCardOut)
async def drop_profile(artist_id: int, ref: str = Path(min_length=3, max_length=64), user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    a = await _artist(s, artist_id)
    if ref not in await artists.profiles(s, a.id):
        raise HTTPException(404, "Профиль уже отвязан или принадлежит другому исполнителю")
    await s.commit()
    card = await _profile_card(ref)
    if not await artists.detach_profile(s, user.id, a, ref, (card.name.strip() if card else "") or a.name):
        raise HTTPException(409, "Привязка профиля уже изменилась")
    await s.commit()
    return await _done(s, user, a)


# --- how an artist looks (app/library/looks.py) ------------------------------------------------------------------


class LookOptionOut(BaseModel):
    choice: str  # what to send back to choose it
    label: str
    note: str = ""
    url: str  # the picture


class LooksOut(BaseModel):
    """The photos and the wide pictures an artist may be shown with, and which is chosen (None: as by itself — the
    photo of the catalog's record the page is of, the wide picture the Hub finds)."""

    id: int
    name: str
    photo: str | None = None
    banner: str | None = None
    photos: list[LookOptionOut] = []
    banners: list[LookOptionOut] = []


class ChoiceIn(BaseModel):
    choice: str | None = Field(default=None, max_length=320)


async def _looks(s: AsyncSession, user: User, a: Artist) -> dict[str, Any]:
    from app.library import looks

    out = await looks.options(s, a)
    await s.commit()
    bus.publish(user.id, "library", {"kind": "changed", "album_id": None})
    return out


@router.get("/{artist_id}/looks", response_model=LooksOut)
async def get_looks(artist_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    from app.library import looks

    return await looks.options(s, await _artist(s, artist_id))


@router.put("/{artist_id}/photo", response_model=LooksOut)
async def put_photo(artist_id: int, body: ChoiceIn, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    from app.library import looks

    a = await _artist(s, artist_id)
    try:
        await looks.choose_photo(s, user.id, a, body.choice)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return await _looks(s, user, a)


@router.post("/{artist_id}/photo", response_model=LooksOut)
async def upload_photo(artist_id: int, file: UploadFile = File(...), user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    from app.library import looks

    a = await _artist(s, artist_id)
    try:
        await looks.choose_photo(s, user.id, a, "own", await file.read())
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return await _looks(s, user, a)


@router.put("/{artist_id}/banner", response_model=LooksOut)
async def put_banner(artist_id: int, body: ChoiceIn, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    from app.library import looks

    a = await _artist(s, artist_id)
    try:
        await looks.choose_banner(s, user.id, a, body.choice)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # the place the wide pictures come from did not answer
        raise HTTPException(502, f"Не удалось получить обложку: {e}") from e
    return await _looks(s, user, a)


@router.post("/{artist_id}/banner", response_model=LooksOut)
async def upload_banner(artist_id: int, file: UploadFile = File(...), user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    from app.library import looks

    a = await _artist(s, artist_id)
    try:
        await looks.choose_banner(s, user.id, a, "own", await file.read())
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return await _looks(s, user, a)


def _picture(image: bytes | None) -> Response:
    if not image:
        raise HTTPException(404, "Картинки нет")
    return Response(image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.get("/{artist_id}/photo")
async def photo(artist_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    from sqlalchemy import select

    from app.models import ArtistLook

    return _picture((await s.execute(select(ArtistLook.photo_image).where(ArtistLook.artist_id == artist_id))).scalar_one_or_none())


@router.get("/{artist_id}/banner")
async def banner(artist_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    from sqlalchemy import select

    from app.models import ArtistLook

    return _picture((await s.execute(select(ArtistLook.banner_image).where(ArtistLook.artist_id == artist_id))).scalar_one_or_none())


@router.get("/{artist_id}/banner-found")
async def banner_found(artist_id: int, name: str, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    """The wide picture found for the artist asked by this spelling of theirs — what the window offers. 404: none."""
    from app.library import looks

    a = await _artist(s, artist_id)
    try:
        image = await looks.found_banner(s, a, name)
    except Exception as e:
        raise HTTPException(502, f"Не удалось получить обложку: {e}") from e
    if not image:
        raise HTTPException(404, "По этому имени обложка не нашлась")
    return Response(image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

