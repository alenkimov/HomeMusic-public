"""Spotify-like artist and album pages with library ownership (FR-4)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AlbumOut, ArtistPageOut, ArtistSourceOut, DiscographyOut, NamesakeIn, NamesakeOut, ServiceLinkOut
from app.catalog import banners, namesakes, popular
from app.auth import current_user
from app.catalog.models import ArtistPage, CatalogAlbum, CatalogTrack, split_ref
from app.catalog.yandex import split_suffix
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
        return await svc.artist_page(f"{src}:{id_}")
    except Exception as e:
        if type(e).__name__ == "CatalogNotFound":
            raise HTTPException(404, "Исполнитель не найден") from e
        raise HTTPException(502, f"Каталог недоступен: {e}") from e


async def _tie_yandex(s: AsyncSession, who: Any, page: ArtistPage) -> None:
    """The artist Yandex knows by the same name, with music in common, is the same artist: their record there is
    tied to them — once; from then on the record itself says who it is, and their page is drawn of it too. A
    namesake has the name and none of the music: nobody is tied."""
    from app.library import artists

    if svc.yandex is None or page.artist.source == "ym" or any(r.startswith("ym:") for r in await artists.profiles(s, who.id)):
        return
    try:
        found = await svc.yandex.find_artist(who.name)
        if found is None or await artists.by_ref(s, found.ref) is not None:
            return
        theirs = await svc.yandex.get_artist(found.id)
    except Exception as e:  # Yandex is away: the page is the other catalogs' for now
        log.info("artist %s: Yandex not asked: %r", page.artist.ref, e)
        return
    def names(p: ArtistPage) -> set[str]:  # without what is written after a name («… (prod. by …)»)
        return {artists.fold(split_suffix(x.title)[0]) for x in [*p.albums, *p.top_tracks]}

    mine = names(page)
    if not mine and popular.sources.lastfm_configured():
        # a record with no music (the catalog knows the name and nothing else) tells nobody apart: what the artist
        # is listened to by — Last.fm's top of the name — does
        try:
            mine = {artists.fold(split_suffix(n)[0]) for n in await popular._top_names(who.name)}
        except Exception as e:
            log.info("artist %s: Last.fm's top not read: %r", page.artist.ref, e)
    if mine & names(theirs):
        await artists.who(s, found.name, found.ref, "catalog")


async def whole_artist(s: AsyncSession, ref: str) -> tuple[ArtistPage, list[str], list[ArtistSourceOut]]:
    """The artist a record of the catalog is of, as one page: a catalog may keep several records of one artist (old
    releases under one spelling, new under another), and the Hub knows which are theirs (app/library/artists.py).
    Returns the page — the name they have here, the releases of all the records (one that two of them carry, once),
    the most listened of them all — the records, the one with the most followers first (that one's picture is the
    artist's, and its address is the page's own), and each record as the catalog itself shows it."""
    from app.library import artists

    page = await get_artist_page(ref)
    who = await artists.who(s, page.artist.name, ref, "catalog")
    # who the record is of is known from now on — and written before the catalogs are asked again: the database has
    # one writer, and a page that waited for the network with a tie unwritten would keep every other write waiting
    await s.commit()
    if who is not None:
        await _tie_yandex(s, who, page)
        await s.commit()
    refs = [r for r in (await artists.profiles(s, who.id) if who is not None else []) if r != ref]

    async def other(r: str) -> ArtistPage | None:
        try:
            return await get_artist_page(r)
        except HTTPException as e:  # a record the catalog has dropped does not take the artist's page with it
            log.info("artist %s: the record %s is not read: %s", ref, r, e.detail)
            return None

    pages = [page, *[p for p in await asyncio.gather(*(other(r) for r in refs)) if p is not None]]
    # Yandex's record adds to the page and does not lead it while another catalog has one: its followers are
    # counted otherwise (likes of the whole service), and the other catalog's records are what the Hub's own are of
    pages.sort(key=lambda p: (p.artist.source != "ym", p.artist.nb_fans or 0), reverse=True)
    main = pages[0]
    albums: list[CatalogAlbum] = []
    seen: set[Any] = set()
    for p in pages:
        for a in p.albums:
            # one catalog's EP is another's single
            kind, year = "single" if a.record_type == "ep" else a.record_type, a.release_date.year if a.release_date else a.year
            same = (artists.fold(a.title), kind, year)
            # Yandex writes into a release's name what the others keep apart («… (prod. by …)»): its release is one
            # the page has already when the names agree up to that
            plain = (artists.fold(split_suffix(a.title)[0]), kind, year)
            if a.ref in seen or same in seen or (a.source == "ym" and plain in seen):
                continue
            seen.update((a.ref, same, plain))
            albums.append(a)
    # The most listened are the leading catalog's word (and Last.fm's after it, `popular.more`). Yandex's own list
    # is not added to another catalog's: it counts every track the artist is credited on — other people's tracks
    # with them as a guest come first there — and has no number to set the two lists against each other by. Where
    # the others have no list at all (a record with no music), Yandex's is the only word there is
    others = any(p.top_tracks for p in pages if p.artist.source != "ym")
    said = [p for p in pages if not (p.artist.source == "ym" and main.artist.source != "ym" and others)]
    top: dict[str, CatalogTrack] = {}
    for p in said:
        for t in p.top_tracks:
            top.setdefault(t.ref, t)
    tracks = list(top.values())
    if len(said) > 1:  # the most listened of them all, by the catalog's own count of it
        tracks = sorted(tracks, key=lambda t: t.rank or 0, reverse=True)[:max(len(p.top_tracks) for p in said)]
    fans = [p.artist.nb_fans for p in pages if p.artist.nb_fans and p.artist.source == main.artist.source]
    artist = main.artist.model_copy(update={"name": who.name if who is not None else main.artist.name,
                                             "nb_fans": sum(fans) if fans else main.artist.nb_fans})
    sources = [await _source(p) for p in pages]
    return ArtistPage(artist=artist, top_tracks=tracks, albums=albums), [p.artist.ref for p in pages], sources


SERVICES = {"dz": "deezer", "ym": "yandex"}
ARTIST_URLS = {"dz": "https://www.deezer.com/artist/{}", "ym": "https://music.yandex.ru/artist/{}"}


async def _source(p: ArtistPage) -> ArtistSourceOut:
    """A record of a catalog as that catalog shows the artist: their name and picture there, how many follow them —
    and, where the catalog says it (Yandex), how many listened in the last month."""
    a = p.artist
    heard = None
    if a.source == "ym" and svc.yandex is not None:
        try:
            heard = await svc.yandex.listeners(a.id)
        except Exception as e:
            log.info("artist %s: listeners not read: %r", a.ref, e)
    return ArtistSourceOut(service=SERVICES.get(a.source, "catalog"), ref=a.ref, name=a.name,
                           picture_url=a.picture_small_url or a.picture_url, followers=a.nb_fans, listeners=heard,
                           releases=len(p.albums), url=ARTIST_URLS[a.source].format(a.id) if a.source in ARTIST_URLS else None)


ALBUM_URLS = {"dz": "https://www.deezer.com/album/{}", "ym": "https://music.yandex.ru/album/{}"}
TWIN_WAIT_S = 4.0  # a release's page does not wait longer for the other catalog


def same_release(a: CatalogAlbum, b: CatalogAlbum) -> bool:
    """Two catalogs' records of one release: the same artist, the same name up to what one of them writes after it
    («… (prod. by …)»), and the same year — or, where a reissue moved the year, as many tracks."""
    from app.library import artists

    def year(x: CatalogAlbum) -> int | None:
        return x.release_date.year if x.release_date else x.year

    if artists.fold(split_suffix(a.title)[0]) != artists.fold(split_suffix(b.title)[0]):
        return False
    if not {artists.key(n) for n in [a.artist, *a.artists]} & {artists.key(n) for n in [b.artist, *b.artists]}:
        return False
    if year(a) and year(b) and year(a) == year(b):
        return True
    return bool(a.nb_tracks and a.nb_tracks == b.nb_tracks)


async def album_links(a: CatalogAlbum) -> list[ServiceLinkOut]:
    """Where the release can be opened: in the catalog its record is of, and in the other one where that has the
    same release (`same_release`) — asked by the artist and the name. The other catalog away, or slow: its own only."""
    links = [ServiceLinkOut(service=SERVICES[a.source], url=ALBUM_URLS[a.source].format(a.id))] if a.source in ALBUM_URLS else []
    if a.nb_tracks is None and a.tracks:
        a = a.model_copy(update={"nb_tracks": len(a.tracks)})

    async def twin() -> CatalogAlbum | None:
        if a.source == "dz" and svc.yandex is not None:
            found = (await svc.yandex.search(f"{a.artist} {split_suffix(a.title)[0]}")).albums
        elif a.source == "ym" and svc.deezer is not None:
            found = await svc.deezer.search_albums(a.artist, split_suffix(a.title)[0])
        else:
            return None
        return next((b for b in found if same_release(a, b)), None)

    try:
        other = await asyncio.wait_for(twin(), TWIN_WAIT_S)
    except Exception as e:  # asked again the next time the page is opened
        log.info("album %s: the other catalog not asked: %r", a.ref, e)
        other = None
    if other is not None:
        links.append(ServiceLinkOut(service=SERVICES[other.source], url=ALBUM_URLS[other.source].format(other.id)))
    return links


async def get_album(ref: str, *, credited: bool = False) -> CatalogAlbum:
    """`credited`: with everyone credited on each track (an album's own page; costs a request a track, once)."""
    src, id_ = _split(ref)
    try:
        return await svc.album(f"{src}:{id_}", credited=credited)
    except Exception as e:
        if type(e).__name__ == "CatalogNotFound":
            raise HTTPException(404, "Альбом не найден") from e
        raise HTTPException(502, f"Каталог недоступен: {e}") from e


@router.get("/artist/{ref}", response_model=ArtistPageOut)
async def artist(ref: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    page, records, sources = await whole_artist(s, ref)
    await s.commit()  # who the record is of is known from now on
    albums = await annotate_albums(s, page.albums, user.id)
    albums.sort(key=lambda a: (a.release_date is not None, a.release_date), reverse=True)
    # how the artist looks, where the listeners chose: the photo, the wide picture — or none over the page
    from app.library import artists, looks, versions

    who = await artists.by_ref(s, records[0])
    look = await looks.of(s, who.id) if who is not None else None
    chosen = await looks.photo(s, who, look, chosen_only=True) if who is not None else None
    if chosen:
        page.artist = page.artist.model_copy(update={"picture_url": chosen[0], "picture_small_url": chosen[1]})
    said, own_banner = looks.banner(who.id, look) if who is not None else (False, None)

    async def found_banner() -> str | None:
        return own_banner if said else await banners.url_of(page.artist, [t.title for t in page.top_tracks] + [a.title for a in page.albums])

    heard, extra, banner, by_song = await asyncio.gather(
        popular.listeners(page.artist.name), popular.more(page.artist, page.top_tracks), found_banner(),
        popular.heard(page.artist.name))
    # a song is among the most listened once: of its versions the preferred one
    top = await versions.once(s, await annotate_tracks(s, [*page.top_tracks, *extra], user.id))
    for t in top:
        t.lastfm_listeners = by_song.get(popular.song(t.title))
    return ArtistPageOut(
        artist=page.artist,
        top_tracks=top,
        lastfm_listeners=heard, top_from_lastfm=len(extra), banner_url=banner,
        albums=[a for a in albums if a.record_type == "album"],
        singles=[a for a in albums if a.record_type in ("single", "ep")],
        compilations=[a for a in albums if a.record_type == "compile"],
        excluded=sum(len(namesakes.of(r)) for r in records), profiles=records,
        # where the artist is known: the catalog's records of them, and Last.fm — which knows an artist by the name
        sources=[*sources, *([ArtistSourceOut(service="lastfm", name=page.artist.name, listeners=heard,
                                              url=f"https://www.last.fm/music/{quote(page.artist.name, safe='')}")] if heard else [])],
    )


@router.get("/artist/{ref}/banner")
async def artist_banner(ref: str, user: User = Depends(current_user)):
    """The wide picture over the artist's page, as the Hub keeps it."""
    data = await banners.image_of(ref)
    if data is None:
        raise HTTPException(404, "Нет картинки")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})


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
    page, records, _ = await whole_artist(s, ref)
    await s.commit()
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
    return DiscographyOut(artist=page.artist, albums=outs, excluded=sum(len(namesakes.of(r)) for r in records), profiles=records)


@router.get("/album/{ref}", response_model=AlbumOut)
async def album(ref: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    a = await get_album(ref, credited=True)
    out = (await annotate_albums(s, [a], user.id))[0]
    out.links = await album_links(a)
    return out
