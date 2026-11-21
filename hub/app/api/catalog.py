"""Spotify-like artist and album pages with library ownership (FR-4)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.library import read_cover_upload, save_album_cover
from app.api.progress import EmitProgress, SourceProgress, SourceState, progress_stream
from app.api.schemas import (
    AlbumOut,
    ArtistPageOut,
    ArtistSourceOut,
    DiscographyOut,
    LibAlbumOut,
    NamesakeIn,
    NamesakeOut,
    ServiceLinkOut,
)
from app.auth import current_user
from app.catalog import banners, lookup, namesakes, popular, registry, release_matches
from app.catalog.models import CATALOG_SERVICES, ArtistPage, CatalogAlbum, CatalogArtist, CatalogNotFound, SearchResults, split_ref
from app.catalog.identity import matched_track
from app.catalog.yandex import split_suffix
from app.db import get_session, session_scope
from app.library import tagger
from app.library.importer import ImportSpec, find_album, get_or_create_album, library_lock
from app.library.owned import annotate_albums, annotate_tracks
from app.models import Album, AlbumArtist, ArtistRef, CatalogRelease, Track, TrackArtist, User
from app.services import svc

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/catalog", tags=["catalog"])
_artist_registration = asyncio.Lock()


def _availability(result: object) -> SourceState:
    return "ready" if result else "empty"


async def _tracked[T](progress: SourceProgress | None, source: str, work: Awaitable[T],
                      outcome: Callable[[T], SourceState] = _availability) -> T:
    if progress is None:
        return await work
    return await progress.follow(CATALOG_SERVICES.get(source, source), work, outcome)


def _split(ref: str) -> tuple[str, str]:
    try:
        return split_ref(ref)
    except ValueError as e:
        raise HTTPException(400, "Неверная ссылка на каталог") from e


async def get_artist_page(ref: str) -> ArtistPage:
    src, id_ = _split(ref)
    try:
        return await svc.artist_page(f"{src}:{id_}")
    except CatalogNotFound as e:
        raise HTTPException(404, "Исполнитель не найден") from e
    except Exception as e:
        raise HTTPException(502, f"Каталог недоступен: {e}") from e


async def _tie_yandex(s: AsyncSession, who: Any, page: ArtistPage, progress: SourceProgress | None = None) -> None:
    """The artist Yandex knows by the same name, with music in common, is the same artist: their record there is
    tied to them — once; from then on the record itself says who it is, and their page is drawn of it too. A
    namesake has the name and none of the music: nobody is tied."""
    from app.library import artists

    if svc.yandex is None or page.artist.source == "ym" or any(r.startswith("ym:") for r in await artists.profiles(s, who.id)):
        return
    try:
        found = await _tracked(progress, "ym", svc.yandex.find_artist(who.name))
        if found is None or await artists.by_ref(s, found.ref) is not None:
            return
        theirs = await _tracked(progress, "ym", svc.yandex.get_artist(found.id))
    except Exception as e:  # Yandex is away: the page is the other catalogs' for now
        log.info("artist %s: Yandex not asked: %r", page.artist.ref, e)
        return
    def names(p: ArtistPage) -> set[str]:  # without what is written after a name («… (prod. by …)»)
        return {artists.fold(split_suffix(x.title)[0]) for x in [*p.albums, *p.top_tracks]}

    mine = names(page)
    if not mine and await popular.sources.lastfm_configured():
        # a record with no music (the catalog knows the name and nothing else) tells nobody apart: what the artist
        # is listened to by — Last.fm's top of the name — does
        try:
            names_ = await _tracked(progress, "lastfm", popular._top_names(who.name))
            mine = {artists.fold(split_suffix(n)[0]) for n in names_}
        except Exception as e:
            log.info("artist %s: Last.fm's top not read: %r", page.artist.ref, e)
    if mine & names(theirs):
        await artists.tie_profile(s, who.id, found.ref)


def _same_artist(a: ArtistPage, b: ArtistPage) -> bool:
    from app.library.artists import fold

    def titles(rows: list) -> set[str]:
        return {fold(x.title) for x in rows} - {""}

    tracks = titles(a.top_tracks) & titles(b.top_tracks)
    releases = titles(a.albums) & titles(b.albums)
    return len(tracks) >= 2 or len(releases) >= 2 or bool(tracks and releases and len(tracks | releases) >= 2)


async def spotify_profile(page: ArtistPage, progress: SourceProgress | None = None) -> ArtistPage | None:
    from app.library.artists import fold

    if svc.spotify is None:
        return None
    try:
        async with asyncio.timeout(8):
            matches = await _tracked(progress, "sp", svc.spotify.search(page.artist.name, kinds=("artist",), limit=5),
                                     lambda result: _availability(result.artists))
            candidates = [a for a in matches.artists if fold(a.name) == fold(page.artist.name)][:3]
            found = await asyncio.gather(*(_tracked(progress, "sp", svc.spotify.artist_page(a.id)) for a in candidates),
                                         return_exceptions=True)
    except Exception as error:
        log.info("Spotify artist match for %s unavailable: %r", page.artist.ref, error)
        return None
    matched = [p for p in found if isinstance(p, ArtistPage) and _same_artist(page, p)]
    return matched[0] if len(matched) == 1 else None


async def _profile_holder(s: AsyncSession, page: ArtistPage, progress: SourceProgress | None = None) -> tuple[Any, bool]:
    from app.library import artists

    who = await artists.holder(s, page.artist.name)
    if who is None:
        return None, True
    refs = await artists.profiles(s, who.id)
    for ref in refs:
        if ref.startswith(f"{page.artist.source}:"):
            continue
        try:
            other = await _tracked(progress, ref.partition(":")[0], asyncio.wait_for(svc.artist_page(ref), 5))
        except Exception:
            row = await s.get(ArtistRef, ref)
            try:
                other = ArtistPage.model_validate(row.page_json) if row and row.page_json else None
            except ValidationError:
                other = None
        if other and _same_artist(page, other):
            return who, False
    return None, not refs


async def _tie_deezer(s: AsyncSession, who: Any, page: ArtistPage, progress: SourceProgress | None = None) -> None:
    """Find the Deezer profile when a Spotify page has room for fallback tracks."""
    from app.library import artists

    if svc.deezer is None or any(r.startswith("dz:") for r in await artists.profiles(s, who.id)):
        return
    try:
        async with asyncio.timeout(8):
            found = await _tracked(progress, "dz", svc.deezer.search(page.artist.name, kinds=("artist",), limit=5),
                                   lambda result: _availability(result.artists))
            candidates = [a for a in found.artists if artists.key(a.name) == artists.key(page.artist.name)
                          and await artists.by_ref(s, a.ref) is None]
            pages = await asyncio.gather(*(_tracked(progress, "dz", get_artist_page(a.ref)) for a in candidates),
                                         return_exceptions=True)
            matches = [other for other in pages if isinstance(other, ArtistPage) and _same_artist(page, other)]
            if len(matches) == 1:
                await artists.tie_profile(s, who.id, matches[0].artist.ref)
    except Exception as e:
        log.info("Artist %s: Deezer search failed: %r", page.artist.ref, e)


async def whole_artist(s: AsyncSession, ref: str, *, include_sources: bool = True,
                       progress: SourceProgress | None = None) -> tuple[CatalogArtist, list[str], list[ArtistSourceOut], list[ArtistPage]]:
    """The artist a record of the catalog is of, as one page: a catalog may keep several records of one artist (old
    releases under one spelling, new under another), and the Hub knows which are theirs (app/library/artists.py).
    Returns the artist, record references, service links and the individual catalog pages."""
    from app.library import artists

    ident = registry.numeric_id(ref)
    who = await artists.by_id(s, ident) if ident is not None else await artists.by_ref(s, ref)
    live: dict[str, ArtistPage] = {}

    def page_state(page: ArtistPage) -> SourceState:
        return "ready" if include_sources else _availability(page.albums)

    if who is None:
        if ident is not None:
            raise HTTPException(404, "Исполнитель не найден")
        source, _ = _split(ref)
        page = await _tracked(progress, source, get_artist_page(ref), page_state)
        live[ref] = page
        match, allow_name_match = await _profile_holder(s, page, progress)
        async with _artist_registration:
            if match is not None:
                await artists.tie_profile(s, match.id, ref)
            who = await artists.who(s, page.artist.name, ref, "catalog", allow_name_match=allow_name_match)
            await s.commit()
        if who is None:
            raise HTTPException(404, "Исполнитель не найден")

    async def other(r: str) -> ArtistPage | None:
        try:
            return await _tracked(progress, r.partition(":")[0], get_artist_page(r), page_state)
        except HTTPException as e:
            log.info("artist %s: the record %s is not read: %s", who.id, r, e.detail)
            return None

    async def read_profiles() -> None:
        refs = [r for r in await artists.profiles(s, who.id) if r not in live and r not in attempted
                and (lookup.source_available(r) or (r.startswith("hm:") and svc.hitmoz_catalog is not None))]
        attempted.update(refs)
        live.update((p.artist.ref, p) for p in await asyncio.gather(*(other(r) for r in refs)) if p is not None)

    attempted: set[str] = set()
    await read_profiles()
    order = {"sp": 3, "dz": 2, "ym": 1}

    def priority(p: ArtistPage) -> tuple[int, int]:
        return order.get(p.artist.source, 2), p.artist.nb_fans or 0

    page = max(live.values(), key=priority, default=None)
    if page is not None:
        await _tie_yandex(s, who, page, progress)
        if page.artist.source != "dz":
            await _tie_deezer(s, who, page, progress)
        await s.commit()
        if not any(r.startswith("sp:") for r in await artists.profiles(s, who.id)):
            if spotify := await spotify_profile(page, progress):
                await artists.tie_profile(s, who.id, spotify.artist.ref)
                await s.commit()
    await read_profiles()
    rows = list(await s.scalars(select(ArtistRef).where(ArtistRef.artist_id == who.id)
                                .order_by(ArtistRef.tied_at, ArtistRef.ref)))
    saved = []
    for row in rows:
        if current := live.get(row.ref):
            data = current.model_dump(mode="json")
            if row.page_json != data:
                row.page_json = data
        elif row.page_json:
            try:
                saved.append(ArtistPage.model_validate(row.page_json))
            except ValidationError:
                log.warning("Invalid artist snapshot: %s", row.ref)
    await s.commit()
    pages = [*sorted(live.values(), key=priority, reverse=True), *sorted(saved, key=priority, reverse=True)]
    records = list(dict.fromkeys([*(p.artist.ref for p in pages), *(r.ref for r in rows)]))
    if not pages:
        return CatalogArtist(ref=f"artist:{who.id}", source="nd", id=str(who.id), name=who.name,
                             hub_artist_id=who.id), records, [], []
    main = pages[0]
    spotify_pages = [p for p in pages if p.artist.source == "sp"]
    fans = [p.artist.nb_fans for p in pages if p.artist.nb_fans and p.artist.source == main.artist.source]
    artist = main.artist.model_copy(update={"name": who.name, "hub_artist_id": who.id,
                                             "nb_fans": sum(fans) if fans else main.artist.nb_fans})
    if spotify_pages:
        photo = spotify_pages[0].artist
        artist.picture_url = artist.picture_url or photo.picture_url
        artist.picture_small_url = artist.picture_small_url or photo.picture_small_url
        artist.banner_url = photo.banner_url
    sources = [await _source(p) for p in pages] if include_sources else []
    return artist, records, sources, pages


async def _artist_albums(
    s: AsyncSession, albums: list[CatalogAlbum], progress: SourceProgress | None = None,
) -> dict[str, list[CatalogAlbum]]:
    by_ref: dict[str, CatalogAlbum] = {}
    for a in albums:
        by_ref.setdefault(a.ref, a)
    rows = list(by_ref.values())
    confirmed = await release_matches.discover(s, rows, lambda a: _tracked(progress, a.source, get_album(a.ref)))
    groups: dict[str, list[CatalogAlbum]] = {}
    singles = []
    for a in rows:
        if group := confirmed.get(a.ref):
            head = release_matches.combined(group)
            groups.setdefault(head.ref, [head, *(other for other in group if other.ref != head.ref)])
        else:
            singles.append(a)
    for a in singles:
        matches = [group for group in groups.values()
                   if all(same_release(a, b, linked_profiles=True) for b in group)
                   and all(sum(same_release(peer, b, linked_profiles=True)
                               for peer in singles if peer.source == a.source) == 1
                           for b in group)]
        if len(matches) == 1:
            matches[0].append(a)
        else:
            groups[a.ref] = [a]
    return groups


def _album_sources(group: list[CatalogAlbum]) -> list[str]:
    return list(dict.fromkeys(CATALOG_SERVICES[a.source] for a in group if a.source in CATALOG_SERVICES))


async def _library_artist_albums(s: AsyncSession, artist_id: int, *, with_tracks: bool = False) -> list[CatalogAlbum]:
    ids = list(await s.scalars(select(AlbumArtist.album_id).where(AlbumArtist.artist_id == artist_id)))
    return [album for aid in ids if (album := await registry.library_album(s, aid, with_tracks=with_tracks)) is not None]


ARTIST_URLS = {"dz": "https://www.deezer.com/artist/{}", "ym": "https://music.yandex.ru/artist/{}",
               "sp": "https://open.spotify.com/artist/{}"}


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
    return ArtistSourceOut(service=CATALOG_SERVICES[a.source] if a.source in ARTIST_URLS else "catalog", ref=a.ref, name=a.name,
                           picture_url=a.picture_small_url or a.picture_url, followers=a.nb_fans, listeners=heard,
                           monthly_listeners=a.monthly_listeners,
                           releases=len(p.albums), url=ARTIST_URLS[a.source].format(a.id) if a.source in ARTIST_URLS else None)


ALBUM_URLS = {"dz": "https://www.deezer.com/album/{}", "ym": "https://music.yandex.ru/album/{}",
              "sp": "https://open.spotify.com/album/{}"}
TWIN_WAIT_S = 4.0  # a release's page does not wait longer for the other catalog


def same_release(a: CatalogAlbum, b: CatalogAlbum, *, linked_profiles: bool = False) -> bool:
    """Recognize the same catalog title when neither provider gives conflicting edition metadata."""
    from app.library import artists

    def year(x: CatalogAlbum) -> int | None:
        return x.release_date.year if x.release_date else x.year

    if a.ref == b.ref or (a.source == b.source and (not a.artist_ref or a.artist_ref == b.artist_ref)):
        return False
    if not release_matches.compatible(a, b, linked_profiles=linked_profiles):
        return False
    def title(x: CatalogAlbum) -> str:
        base, suffix = split_suffix(x.title)
        return artists.fold(base if suffix.lstrip(" ([–-").casefold().startswith(("prod. ", "prod by ")) else x.title)

    if title(a) != title(b):
        return False
    if not linked_profiles and not {artists.key(n) for n in [a.artist, *a.artists]} & {artists.key(n) for n in [b.artist, *b.artists]}:
        return False
    if year(a) and year(b) and year(a) == year(b):
        return True
    return bool(a.nb_tracks and a.nb_tracks == b.nb_tracks)


async def album_links(a: CatalogAlbum, progress: SourceProgress | None = None) -> list[ServiceLinkOut]:
    """Where the release can be opened: in the catalog its record is of, and in the other one where that has the
    same release (`same_release`) — asked by the artist and the name. The other catalog away, or slow: its own only."""
    links = [ServiceLinkOut(service=CATALOG_SERVICES[a.source], url=ALBUM_URLS[a.source].format(a.id))] if a.source in ALBUM_URLS else []
    if a.nb_tracks is None and a.tracks:
        a = a.model_copy(update={"nb_tracks": len(a.tracks)})

    title = split_suffix(a.title)[0]
    q = f"{a.artist} {title}"

    async def twins() -> list[CatalogAlbum]:
        asks = {}
        if a.source != "ym" and svc.yandex is not None:
            asks["ym"] = svc.yandex.search(q)
        if a.source != "dz" and svc.deezer is not None:
            asks["dz"] = svc.deezer.search(q, kinds=("album",), limit=8)
        if a.source != "sp" and svc.spotify is not None:
            asks["sp"] = svc.spotify.search(q, kinds=("album",), limit=8)

        async def match(ask: Awaitable[SearchResults]) -> CatalogAlbum | None:
            result = await asyncio.wait_for(ask, TWIN_WAIT_S)
            return next((b for b in result.albums if same_release(a, b)), None)

        results = await asyncio.gather(*(_tracked(progress, src, match(ask)) for src, ask in asks.items()),
                                       return_exceptions=True)
        return [result for result in results if isinstance(result, CatalogAlbum)]

    for other in await twins():
        links.append(ServiceLinkOut(service=CATALOG_SERVICES[other.source], url=ALBUM_URLS[other.source].format(other.id)))
    return links


async def get_album(ref: str, *, credited: bool = False) -> CatalogAlbum:
    """`credited`: with everyone credited on each track (an album's own page; costs a request a track, once)."""
    src, id_ = _split(ref)
    try:
        return await svc.album(f"{src}:{id_}", credited=credited)
    except CatalogNotFound as e:
        raise HTTPException(404, "Альбом не найден") from e
    except Exception as e:
        raise HTTPException(502, f"Каталог недоступен: {e}") from e


@router.get("/artist/{ref}", response_model=ArtistPageOut)
async def artist(ref: str, request: Request, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    if "application/x-ndjson" not in request.headers.get("accept", ""):
        return await _artist_response(s, ref, user.id)

    async def run(emit: EmitProgress) -> ArtistPageOut:
        async with session_scope() as session:
            return await _artist_response(session, ref, user.id, SourceProgress(emit))

    return progress_stream(run, "Не удалось загрузить исполнителя")


async def _artist_response(s: AsyncSession, ref: str, user_id: int,
                           progress: SourceProgress | None = None) -> ArtistPageOut:
    profile, records, sources, pages = await whole_artist(s, ref, progress=progress)
    await s.commit()  # who the record is of is known from now on
    groups = await _artist_albums(s, [a for page in pages for a in page.albums], progress)
    albums = await annotate_albums(s, [group[0].model_copy(update={"tracks": None}) for group in groups.values()], user_id)
    for album in albums:
        album.found_in = _album_sources(groups[album.ref])
    # how the artist looks, where the listeners chose: the photo, the wide picture — or none over the page
    from app.library import artists, looks, versions

    who = await artists.by_id(s, profile.hub_artist_id)
    represented = {a.library_album_id for a in albums}
    local = [a for a in await _library_artist_albums(s, who.id) if int(a.id) not in represented]
    albums.extend(await annotate_albums(s, local, user_id))
    albums.sort(key=lambda a: (a.release_date.year if a.release_date else a.year or 0,
                               a.release_date.month if a.release_date else 0,
                               a.release_date.day if a.release_date else 0), reverse=True)
    look = await looks.of(s, who.id) if who is not None else None
    chosen = await looks.photo(s, who, look, chosen_only=True) if who is not None else None
    if chosen:
        profile = profile.model_copy(update={"picture_url": chosen[0], "picture_small_url": chosen[1]})
    said, own_banner = looks.banner(who.id, look) if who is not None else (False, None)

    async def found_banner() -> str | None:
        return own_banner if said else profile.banner_url or (await banners.url_of(
            profile, [t.title for p in pages for t in p.top_tracks] + [a.title for a in albums])
            if pages else None)

    heard, ranked, banner, by_song = await asyncio.gather(
        popular.listeners(profile.name), popular.ranked(profile, pages), found_banner(),
        popular.heard(profile.name))
    if not pages:
        local_tracks = list(await s.scalars(select(Track).join(TrackArtist).where(
            TrackArtist.artist_id == who.id, Track.path.is_not(None),
        ).order_by(Track.id.desc()).limit(popular.WANT)))
        ranked = [(registry.library_track(t), "library") for t in local_tracks]
    # a song is among the most listened once: of its versions the preferred one
    tracks = await annotate_tracks(s, [t for t, _ in ranked], user_id)
    top = await versions.once(s, [(t, source) for t, (_, source) in zip(tracks, ranked, strict=True)],
                              lambda row: row[0].library_track_id, replace=lambda first, preferred: (preferred[0], first[1]))
    for t, _ in top:
        t.lastfm_listeners = by_song.get(popular.song(t.title))
    return ArtistPageOut(
        artist=profile,
        top_tracks=[t for t, _ in top], top_track_sources=[source for _, source in top],
        lastfm_listeners=heard, banner_url=banner,
        albums=[a for a in albums if a.record_type == "album"],
        singles=[a for a in albums if a.record_type in ("single", "ep")],
        compilations=[a for a in albums if a.record_type == "compile"],
        excluded=sum(len(namesakes.of(r)) for r in records), profiles=records,
        # where the artist is known: the catalog's records of them, and Last.fm — which knows an artist by the name
        sources=[*sources, *([ArtistSourceOut(service="lastfm", name=profile.name, listeners=heard,
                                              url=f"https://www.last.fm/music/{quote(profile.name, safe='')}")] if heard else [])],
    )


@router.get("/artist/{ref}/banner")
async def artist_banner(ref: str, user: User = Depends(current_user)):
    """The wide picture over the artist's page, as the Hub keeps it."""
    data = await banners.image_of(ref)
    if data is None:
        raise HTTPException(404, "Нет картинки")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.get("/artist/{ref}/photo")
async def artist_photo(ref: str, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    from app.library import artists, looks

    ident = registry.numeric_id(ref)
    who = await artists.by_id(s, ident) if ident is not None else await artists.by_ref(s, ref)
    if who is not None and (pic := await looks.photo(s, who)):
        return {"url": pic[1]}
    if ident is not None:
        records = await artists.profiles(s, who.id) if who else []
        for record in records:
            if (a := await looks.profile_card(record)) and (url := a.picture_small_url or a.picture_url):
                return {"url": url}
        return {"url": None}
    a = await looks.profile_card(ref)
    return {"url": (a.picture_small_url or a.picture_url) if a is not None else None}


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
async def artist_named(name: str, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")) -> dict[str, str | None]:
    """The catalog's artist of this very name — where only a name is known (a track playing from a list that does
    not say who its artists are). `ref` null: the catalog has nobody called so."""
    from app.api.medialib import _in_catalog
    from app.library import artists

    local = await artists.holder(s, name)
    return {"ref": str(local.id) if local else await _in_catalog(name)}


DISCOGRAPHY_PARALLEL = 4  # releases asked of the catalog at once (each is kept by the catalog's own cache after)


@router.get("/artist/{ref}/discography", response_model=DiscographyOut)
async def discography(ref: str, request: Request, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    """Every release of the artist with its tracks. NDJSON clients also receive live catalog statuses."""
    if "application/x-ndjson" not in request.headers.get("accept", ""):
        return await _discography(s, ref, user.id)

    async def run(emit: EmitProgress) -> DiscographyOut:
        async with session_scope() as session:
            return await _discography(session, ref, user.id, SourceProgress(emit))

    return progress_stream(run, "Не удалось получить дискографию")


def _spotify_discography_state(result: tuple[list[CatalogAlbum], bool, bool]) -> SourceState:
    albums, partial, pending = result
    if pending:
        return "searching"
    return "error" if partial else _availability(albums)


async def _discography(s: AsyncSession, ref: str, user_id: int, progress: SourceProgress | None = None) -> DiscographyOut:
    profile, records, _, pages = await whole_artist(s, ref, include_sources=False, progress=progress)
    await s.commit()
    records_albums = [a for page in pages for a in page.albums]
    partial = pending = False
    if svc.spotify is not None:
        for spotify_ref in (r for r in records if r.startswith("sp:")):
            try:
                releases, incomplete, in_progress = await _tracked(
                    progress, "sp", svc.spotify.discography(spotify_ref.partition(":")[2]), _spotify_discography_state,
                )
            except Exception as e:
                log.info("discography of %s: Spotify releases unavailable: %r", ref, e)
                partial = True
                continue
            partial |= incomplete
            pending |= in_progress
            records_albums.extend(releases)
    gate = asyncio.Semaphore(DISCOGRAPHY_PARALLEL)

    async def whole(a: CatalogAlbum) -> CatalogAlbum:
        if a.tracks is not None:
            return a
        async def fetch() -> CatalogAlbum:
            async with gate:
                return await get_album(a.ref)

        try:
            return await _tracked(progress, a.source, fetch())
        except Exception as e:
            log.info("discography of %s: no tracks of %s: %r", ref, a.ref, e)
            return a

    by_ref: dict[str, CatalogAlbum] = {}
    for a in records_albums:
        by_ref.setdefault(a.ref, a)
    saved = await release_matches.groups_for(s, by_ref)
    for ref, group in saved.items():
        if ref in by_ref:
            by_ref[ref] = next(a for a in group if a.ref == ref)
    detailed = list(await asyncio.gather(*(whole(a) for a in by_ref.values())))
    groups = await _artist_albums(s, detailed, progress)
    albums = [group[0] for group in groups.values()]
    # ownership of every track in one go, then back to its release
    flat = [t for a in albums for t in (a.tracks or [])]
    told = iter(await annotate_tracks(s, flat, user_id))
    outs = await annotate_albums(s, [a.model_copy(update={"tracks": None}) for a in albums], user_id)
    for o, a, group in zip(outs, albums, groups.values(), strict=True):
        o.found_in = _album_sources(group)
        if a.tracks is not None:
            o.tracks = [next(told) for _ in a.tracks]
            owned = sum(1 for t in o.tracks if t.owned)
            o.owned_count = owned
            o.owned_status = "full" if owned and owned >= len(o.tracks) else ("partial" if owned else "none")
    represented = {a.library_album_id for a in outs}
    local = [a for a in await _library_artist_albums(s, profile.hub_artist_id, with_tracks=True)
             if int(a.id) not in represented]
    outs.extend(await annotate_albums(s, local, user_id))
    outs.sort(key=lambda a: (a.release_date.year if a.release_date else a.year or 0,
                             a.release_date.month if a.release_date else 0,
                             a.release_date.day if a.release_date else 0), reverse=True)
    return DiscographyOut(artist=profile, albums=outs, excluded=sum(len(namesakes.of(r)) for r in records),
                          profiles=records, spotify_partial=partial, spotify_pending=pending)


async def _release_record(
    s: AsyncSession, ref: str, *, credited: bool = True, progress: SourceProgress | None = None,
) -> tuple[CatalogAlbum, list[CatalogAlbum], CatalogRelease | None, list[str]]:
    ident = registry.numeric_id(ref)
    local_id = registry.numeric_id(ref[4:]) if ref.startswith("lib:") else None
    if local_id is not None:
        _, local = await registry.addresses(s, (), [local_id])
        row = await registry.by_address(s, str(local[local_id])) if local_id in local else None
    else:
        row = await registry.by_address(s, ref)
    if ident is not None and row is None:
        raise HTTPException(404, "Альбом не найден")
    saved = CatalogAlbum.model_validate(row.album_json) if row and row.album_json else None
    external = await registry.refs(s, row.id) if row else ([ref] if local_id is None else [])
    matches = await release_matches.groups_for(s, external)
    group = next((matches[r] for r in external if r in matches), [])
    known = saved or (release_matches.combined(group) if group else None)
    tries = list(dict.fromkeys([*(a.ref for a in group), *external]))
    error = None
    for source_ref in tries:
        if not lookup.source_available(source_ref) and not (source_ref.startswith("hm:") and svc.hitmoz_catalog is not None):
            continue
        try:
            ask = get_album(source_ref, credited=credited)
            if known or len(tries) > 1:
                ask = asyncio.wait_for(ask, TWIN_WAIT_S)
            live = await _tracked(progress, source_ref.partition(":")[0], ask)
        except HTTPException as exc:
            error = exc
            continue
        except TimeoutError:
            error = HTTPException(502, "Каталог недоступен")
            continue
        if not known:
            return live, group, row, tries
        alternatives = {track.ref: [track] for track in known.tracks or []}
        if group:
            alternatives.update((track.ref, tracks) for tracks in release_matches.track_groups(group) for track in tracks)
        live = live.model_copy(update={
            "alternate_titles": release_matches.alternate_titles(live.title, [
                known.title, *known.alternate_titles, *(a.title for a in group),
            ]),
            "tracks": [release_matches.combined_track([t, *(m for m in alternatives.get(t.ref, []) if m.ref != t.ref)])
                       .model_copy(update={
                           "alternate_titles": release_matches.alternate_titles(t.title, [
                               *t.alternate_titles, *(n for m in alternatives.get(t.ref, []) for n in [m.title, *m.alternate_titles]),
                           ]),
                       }) for t in live.tracks] if live.tracks is not None else (
                           known.tracks if known.ref == live.ref else None
                       ),
        })
        return live, group, row, tries
    if known and known.source != "nd":
        return known, group, row, tries
    aid = row.album_id if row and row.album_id is not None else local_id
    if aid is None and external:
        album_ids = list(await s.scalars(select(Track.album_id).where(
            Track.catalog["album_ref"].as_string().in_(external), Track.album_id.is_not(None),
        ).distinct().limit(2)))
        if len(album_ids) == 1:
            aid = album_ids[0]
    if aid is not None and (library := await registry.library_album(s, aid)) is not None:
        return library, group, row, tries
    if known:
        return known, group, row, tries
    raise error or HTTPException(404, "Альбом не найден")


@router.get("/album/{ref}", response_model=AlbumOut)
async def album(ref: str, request: Request, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    if "application/x-ndjson" not in request.headers.get("accept", ""):
        return await _album_response(s, ref, user.id)

    async def run(emit: EmitProgress) -> AlbumOut:
        async with session_scope() as session:
            return await _album_response(session, ref, user.id, SourceProgress(emit))

    return progress_stream(run, "Не удалось загрузить альбом")


async def _album_response(s: AsyncSession, ref: str, user_id: int,
                          progress: SourceProgress | None = None) -> AlbumOut:
    a, group, registered, refs = await _release_record(s, ref, progress=progress)
    if group:
        links = [ServiceLinkOut(service=CATALOG_SERVICES[b.source], url=ALBUM_URLS[b.source].format(b.id))
                 for b in sorted(group, key=lambda b: b.ref != a.ref) if b.source in ALBUM_URLS]
    else:
        links = await album_links(a, progress) if a.source in ALBUM_URLS else []
    if not group and a.source != "sp" and a.tracks and svc.spotify is not None:
        spotify = next((link.url.rsplit("/", 1)[-1] for link in links if link.service == "spotify"), None)
        if spotify:
            try:
                linked = await _tracked(progress, "sp", asyncio.wait_for(svc.spotify.album(spotify), TWIN_WAIT_S))
                a.tracks = [t.model_copy(update={"spotify_id": m.id, "spotify_plays": m.spotify_plays})
                            if (m := matched_track(t, linked.tracks or [])) else t for t in a.tracks]
            except Exception as error:
                log.info("Spotify album plays %s unavailable: %r", spotify, error)
    out = (await annotate_albums(s, [a], user_id))[0]
    local_id = registered.album_id if registered and registered.album_id is not None else out.library_album_id
    row = await registry.opened(s, a, refs, local_id)
    out.release_id = row.id
    out.found_in = _album_sources(group)
    out.links = links
    return out


async def editable_release(s: AsyncSession, ref: str) -> tuple[CatalogAlbum, Album | None]:
    release, group, registered, _ = await _release_record(s, ref, credited=False)
    if registered and registered.album_id is not None:
        return release, await s.get(Album, registered.album_id)
    if release.ref.startswith("lib:"):
        return release, await s.get(Album, int(release.id))
    records = group or [release]
    local = await find_album(
        s, title=release.title, album_artist=release.artist,
        deezer_album_id=release.id if release.source == "dz" else None,
        upc=release.upc, year=release.year or (release.release_date.year if release.release_date else None),
        catalog_refs=tuple(a.ref for a in records), track_refs=tuple(t.ref for a in records for t in a.tracks or []),
    )
    return release, local


def release_spec(release: CatalogAlbum, user: User) -> ImportSpec:
    return ImportSpec(
        title=release.title, artist=release.artist, album=release.title, album_artist=release.artist,
        catalog_album_ref=release.ref, year=release.year, release_date=release.release_date,
        deezer_album_id=release.id if release.source == "dz" else None,
        release_type={"compile": "compilation"}.get(release.record_type, release.record_type),
        genre=release.genre, upc=release.upc, added_by=user.id,
    )


@router.put("/album/{ref}/cover", response_model=LibAlbumOut)
async def put_catalog_cover(ref: str, file: UploadFile = File(...), user: User = Depends(current_user),
                            s: AsyncSession = Depends(get_session, scope="function")):
    """Keep the listener's cover in the library's record of this release, even before its files arrive."""
    data = await read_cover_upload(file)
    release, local = await editable_release(s, ref)
    if local is None:
        try:
            await asyncio.to_thread(tagger.prepare_cover, data)
        except ValueError as e:
            raise HTTPException(400, "Не удалось прочитать картинку") from e
        async with library_lock:
            local = await get_or_create_album(s, release_spec(release, user))
            await s.commit()
    return await save_album_cover(s, user, local, data)
