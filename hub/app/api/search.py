"""Unified search: library + catalog (Deezer, fallback hitmoz), and raw provider search."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from mutagen import MutagenError
from PIL import Image
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import listen
from app.api.library import track_out
from app.api.progress import EmitProgress, progress_stream
from app.api.schemas import (
    AlbumOut,
    CatalogPlaylistOut,
    CoverOptionOut,
    CoverSearchOut,
    ProviderSearchOut,
    SearchArtistOut,
    SearchHistoryOut,
    SearchMineOut,
    SearchOut,
    SearchRecentIn,
    SearchRecentOut,
    SearchTrackOut,
    SearchTracksOut,
)
from app.auth import current_user
from app.catalog import lookup, release_matches
from app.catalog.cache import cache_get, cache_set_many
from app.catalog.models import CATALOG_SERVICES, CatalogAlbum, CatalogArtist, CatalogTrack, SearchResults
from app.catalog.spotify import ID as SPOTIFY_ID
from app.catalog.identity import same_recording
from app.config import get_settings
from app.db import get_session, session_scope
from app.events import bus
from app.jobs.downloads import fetch_cover
from app.library import artists as artist_names
from app.library import edits, tagger
from app.library.artwork import cover_info
from app.library.importer import library_abs
from app.library.owned import annotate_albums, annotate_tracks
from app.library.tracks import current_files
from app.matching.normalize import has_cyrillic, normalize_title
from app.matching.select import rank_candidates
from app.models import Album, SearchHistory, Track, User, utcnow
from app.providers.base import TrackQuery
from app.services import svc

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/search", tags=["search"])

ROW_LIMIT = 12  # artists and albums: one horizontal row each
TRACK_PAGE = 25  # tracks: the first page, then more as the list is scrolled (GET /api/search/tracks)
HISTORY_LIMIT = 30  # the lines of «Недавнее» that are shown
HISTORY_KEPT = 300  # the lines that are kept: «Вы часто ищете» counts further back than «Недавнее» shows
FREQUENT_LIMIT = 8
SearchScope = Literal["catalog", "sources", "reuploads"]


def _recent_queries(user_id: int):
    return select(SearchHistory).where(SearchHistory.user_id == user_id).order_by(
        SearchHistory.searched_at.desc(), SearchHistory.key
    )


def _recent_out(r: SearchHistory) -> SearchRecentOut:
    return SearchRecentOut(key=r.key, kind=r.kind, title=r.title, subtitle=r.subtitle, ref=r.ref, cover_url=r.cover_url,
                           link=r.link)


@router.get("/history", response_model=SearchHistoryOut)
async def search_history(response: Response, user: User = Depends(current_user),
                         s: AsyncSession = Depends(get_session, scope="function")):
    response.headers["Cache-Control"] = "no-store"
    rows = (await s.execute(_recent_queries(user.id).limit(HISTORY_LIMIT))).scalars()
    often = (await s.execute(
        select(SearchHistory).where(SearchHistory.user_id == user.id, SearchHistory.times > 1)
        .order_by(SearchHistory.times.desc(), SearchHistory.searched_at.desc(), SearchHistory.key).limit(FREQUENT_LIMIT)
    )).scalars()
    return SearchHistoryOut(items=[_recent_out(r) for r in rows], frequent=[_recent_out(r) for r in often])


@router.post("/history", response_model=dict[str, bool])
async def remember_search(body: SearchRecentIn, user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session, scope="function")):
    """A result was opened, or a query asked for with Enter: it goes to the top of «Недавнее» (once — the same
    thing again only moves up)."""
    key = f"q:{body.title.casefold()}" if body.kind == "query" else f"{body.kind}:{body.ref}"
    await s.execute(select(func.pg_advisory_xact_lock(7013424, user.id)))
    stmt = insert(SearchHistory).values(
        user_id=user.id, key=key, kind=body.kind, title=body.title, subtitle=body.subtitle or None, ref=body.ref,
        cover_url=body.cover_url, link=body.link, searched_at=utcnow()
    )
    await s.execute(stmt.on_conflict_do_update(
        index_elements=[SearchHistory.user_id, SearchHistory.key],
        set_={**{c: getattr(stmt.excluded, c) for c in ("kind", "title", "subtitle", "ref", "cover_url", "link", "searched_at")},
              "times": SearchHistory.times + 1},
    ))
    keep = _recent_queries(user.id).limit(HISTORY_KEPT).with_only_columns(SearchHistory.key)
    await s.execute(delete(SearchHistory).where(
        SearchHistory.user_id == user.id, SearchHistory.key.not_in(keep)
    ))
    await s.commit()
    bus.publish(user.id, "search_history", {})
    return {"ok": True}


@router.delete("/history", response_model=dict[str, bool])
async def clear_search_history(key: str | None = Query(None, max_length=1024), user: User = Depends(current_user),
                               s: AsyncSession = Depends(get_session, scope="function")):
    """All of «Недавнее», or the one line `key`."""
    await s.execute(select(func.pg_advisory_xact_lock(7013424, user.id)))
    gone = delete(SearchHistory).where(SearchHistory.user_id == user.id)
    await s.execute(gone if key is None else gone.where(SearchHistory.key == key))
    await s.commit()
    bus.publish(user.id, "search_history", {})
    return {"ok": True}


async def search_library(s: AsyncSession, q: str, limit: int = 20) -> list[Track]:
    """The library's own songs that match: tracks that have a file (one the Hub only knows of — in a playlist,
    liked, not downloaded — is found in the catalog, like any track not owned). By a track's other names and its
    artists' other spellings too, Cyrillic and Latin read as one (app/library/artists.py `found`)."""
    return [t for t, _ in await artist_names.found(s, q, limit)]


@router.get("", response_model=SearchOut)
async def search(q: str = Query(min_length=1), user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    return await _catalog_search(q.strip(), user, s)


async def _library_results(q: str, user: User) -> SearchOut:
    from app.api.playlists import tracks_out
    from app.library import versions

    async with session_scope() as s:
        owned = await artist_names.found(s, q)
        files = await current_files(s, [t.id for t, _ in owned])
        owned = await versions.once(s, owned, lambda x: x[0].id)
        many = await versions.counts(s, [t.id for t, _ in owned])
        library = [track_out(t, files.get(t.id)).model_copy(update={"found_by": by, "versions": many.get(t.id, 0)}) for t, by in owned]
        mine = await tracks_out(s, user, [t for t, _ in owned[:OWN_FIRST]]) if owned else []
    return SearchOut(library=library, tracks=[SearchTrackOut(**t.model_dump(), found_in=["library"]) for t in mine])


async def _catalog_search(q: str, user: User, s: AsyncSession, emit: EmitProgress | None = None) -> SearchOut:
    from app.library import versions

    if not q:
        return SearchOut()
    deezer_next = None
    library = SearchOut()
    found: dict[str, SearchResults] = {}
    errors: list[str] = []

    async def deezer_search() -> tuple[SearchResults, int | None]:
        async with asyncio.TaskGroup() as group:
            details = group.create_task(svc.deezer.search(q, kinds=("artist", "album"), limit=ROW_LIMIT))
            page = group.create_task(svc.deezer.search_track_page(q, 0, TRACK_PAGE))
        result = details.result()
        tracks, next_offset = page.result()
        return result.model_copy(update={"tracks": tracks}), next_offset

    async def hitmoz_search() -> SearchResults:
        return SearchResults(artists=await svc.hitmoz_catalog.search_artists(q))

    async def snapshot(*, complete: bool) -> SearchOut:
        spotify = found.get("spotify")
        spotify_next = TRACK_PAGE if spotify and len(spotify.tracks) >= TRACK_PAGE else None
        out = SearchOut(library=library.library, tracks=library.tracks, errors=errors,
                        tracks_next=(deezer_next or spotify_next) if complete else None)
        catalogs = [found[name] for name in ("spotify", "deezer", "yandex", "hitmoz") if name in found]
        if catalogs or complete:
            out.artists, out.albums, tracks = await _catalog_matches(s, catalogs, user.id, q, complete=complete)
            out.tracks = _with_library(library.tracks, await versions.once(s, tracks))
            out.playlists = [p for catalog in catalogs for p in catalog.playlists]
        return out

    async def ask(name: str, call: Callable[[], Awaitable]) -> SearchOut | SearchResults | tuple[SearchResults, int | None] | None:
        try:
            result = await call()
        except Exception as exc:
            if name == "library":
                raise
            log.info("%s search failed: %r", name, exc)
            if name in ("deezer", "hitmoz"):
                errors.append(f"Каталог {'Deezer' if name == 'deezer' else 'hitmoz'} недоступен")
            if emit:
                emit("source", {"service": name, "state": "error"})
            return None
        if emit:
            payload = result[0] if name == "deezer" else result
            emit("source", {"service": name, "state": "ready" if any((
                getattr(payload, "library", None), payload.artists, payload.albums, payload.tracks, payload.playlists
            )) else "empty"})
        return result

    calls: dict[str, Callable[[], Awaitable]] = {"library": lambda: _library_results(q, user)}
    if svc.deezer is not None:
        calls["deezer"] = deezer_search
    if svc.yandex is not None:
        calls["yandex"] = lambda: svc.yandex.search(q)
    if svc.spotify is not None:
        calls["spotify"] = lambda: svc.spotify.search(q, limit=TRACK_PAGE)
    allow_hitmoz = svc.hitmoz_catalog is not None and has_cyrillic(q)
    if allow_hitmoz and "deezer" not in calls:
        calls["hitmoz"] = hitmoz_search
    if emit:
        emit("sources", [{"service": name, "state": "searching"} for name in calls])

    async with asyncio.TaskGroup() as group:
        pending = {name: group.create_task(ask(name, call)) for name, call in calls.items()}
        while pending:
            done, _ = await asyncio.wait(pending.values(), return_when=asyncio.FIRST_COMPLETED)
            changed = False
            for name, task in list(pending.items()):
                if task not in done:
                    continue
                pending.pop(name)
                result = task.result()
                if name == "library":
                    library = result
                    changed |= bool(library.library)
                elif name == "deezer":
                    if result is not None:
                        found[name], deezer_next = result
                        changed = True
                    if allow_hitmoz and (result is None or not (result[0].artists or result[0].tracks)):
                        if emit:
                            emit("source", {"service": "hitmoz", "state": "searching"})
                        pending["hitmoz"] = group.create_task(ask("hitmoz", hitmoz_search))
                elif result is not None:
                    found[name] = result
                    changed = True
            if emit and pending and changed:
                out = await snapshot(complete=False)
                if out.library or out.artists or out.albums or out.tracks or out.playlists:
                    emit("partial", out)
    return await snapshot(complete=True)


@router.get("/stream")
async def search_stream(q: str = Query(min_length=1, max_length=512), scope: SearchScope = "catalog",
                        user: User = Depends(current_user)):
    """One connection for live source statuses and the results already available."""
    async def run(emit: EmitProgress) -> SearchOut | ProviderSearchOut:
        if scope == "catalog":
            async with session_scope() as s:
                return await _catalog_search(q.strip(), user, s, emit)
        return await _file_search(q.strip(), _provider_names(scope), emit)

    return progress_stream(run, "Не удалось завершить поиск")


@router.get("/spotify/playlist/{id_}", response_model=CatalogPlaylistOut)
async def spotify_playlist(id_: str, user: User = Depends(current_user),
                           s: AsyncSession = Depends(get_session, scope="function")):
    if not SPOTIFY_ID.fullmatch(id_):
        raise HTTPException(400, "Неверная ссылка на плейлист Spotify")
    if svc.spotify is None:
        raise HTTPException(503, "Каталог Spotify недоступен")
    try:
        playlist, tracks, skipped = await svc.spotify.playlist(id_)
    except Exception as error:
        log.info("Spotify playlist %s unavailable: %r", id_, error)
        raise HTTPException(502, "Не удалось открыть публичный плейлист Spotify") from error
    catalog_tracks = [track for track in tracks if isinstance(track, CatalogTrack)]
    return CatalogPlaylistOut(playlist=playlist, tracks=await annotate_tracks(s, catalog_tracks, user.id),
                              local_tracks=len(tracks) - len(catalog_tracks), skipped=skipped)


OWN_FIRST = 5  # of the library's own songs that match, at the head of the tracks


def _search_groups[T: (CatalogArtist, CatalogAlbum, CatalogTrack)](
    rows: list[T], same: Callable[[T, T], bool], *, aliases: bool = False,
) -> list[list[T]]:
    """Keep the first record as the card and attribute only unambiguous matches."""
    groups: list[list[T]] = []
    unique: dict[str, T] = {}
    for row in rows:
        unique.setdefault(row.ref, row)
    for row in unique.values():
        matches = [group for group in groups if all(same(row, other) for other in group)]
        if len(matches) == 1 and (aliases or all(
            other.source != row.source and sum(
                candidate.source == row.source and same(candidate, other) for candidate in unique.values()
            ) == 1
            for other in matches[0]
        )):
            matches[0].append(row)
        else:
            groups.append([row])
    return groups


def _services(rows: list[CatalogArtist] | list[CatalogAlbum] | list[CatalogTrack]) -> list[str]:
    return list(dict.fromkeys(CATALOG_SERVICES[row.source] for row in rows if row.source in CATALOG_SERVICES))


async def _search_track_rows(s: AsyncSession, groups: list[list[CatalogTrack]], user_id: int) -> list[SearchTrackOut]:
    annotated = await annotate_tracks(s, [release_matches.combined_track(group) for group in groups], user_id)
    return [SearchTrackOut(**t.model_dump(), found_in=_services(group)) for t, group in zip(annotated, groups, strict=True)]


def _with_confirmed[T: (CatalogAlbum, CatalogTrack)](
    rows: list[T], confirmed: dict[str, list[T]], same: Callable[[T, T], bool],
) -> list[list[T]]:
    positions = {row.ref: pos for pos, row in reversed(list(enumerate(rows)))}
    groups = _search_groups([row for row in rows if row.ref not in confirmed], same)
    seen: set[str] = set()
    for row in rows:
        if (group := confirmed.get(row.ref)) and group[0].ref not in seen:
            groups.append(group)
            seen.add(group[0].ref)
    return sorted(groups, key=lambda group: min(positions.get(row.ref, len(rows)) for row in group))


async def _catalog_matches(s: AsyncSession, catalogs: list[SearchResults], user_id: int, query: str, *, complete: bool) -> tuple[
    list[SearchArtistOut], list[AlbumOut], list[SearchTrackOut],
]:
    from app.api.catalog import get_album, same_release

    cached = await release_matches.search(s, query) if complete else SearchResults()
    track_rows = [*(t for c in catalogs for t in c.tracks), *cached.tracks]
    album_rows = [*(a for c in catalogs for a in c.albums), *cached.albums]
    if complete:
        await release_matches.discover(s, album_rows, lambda a: get_album(a.ref))
    confirmed = await release_matches.groups_for(s, [
        *(a.ref for a in album_rows), *(t.album_ref for t in track_rows if t.album_ref),
    ])
    spotify_plays = {t.ref: t.spotify_plays for t in track_rows if t.spotify_plays is not None}
    confirmed_tracks: dict[str, list[CatalogTrack]] = {}
    for group in {g[0].ref: g for g in confirmed.values()}.values():
        for paired in release_matches.track_groups(group):
            current = [t.model_copy(update={"spotify_plays": spotify_plays[t.ref]})
                       if t.ref in spotify_plays else t for t in paired]
            confirmed_tracks.update((t.ref, current) for t in paired)
    tracks = _with_confirmed(track_rows, confirmed_tracks, same_recording)
    album_refs = {frozenset((a.album_ref, b.album_ref)) for group in tracks for a in group for b in group
                  if a.album_ref and b.album_ref and a.album_ref != b.album_ref}

    def same_album(a: CatalogAlbum, b: CatalogAlbum) -> bool:
        a_title, b_title = normalize_title(a.title), normalize_title(b.title)
        if a_title.text != b_title.text or a_title.version != b_title.version:
            return False
        if not release_matches.compatible(a, b):
            return False
        return same_release(a.model_copy(update={"title": a_title.text}), b.model_copy(update={"title": b_title.text})) or (
            frozenset((a.ref, b.ref)) in album_refs
            and artist_names.key(a.artist) == artist_names.key(b.artist)
        )

    albums = _with_confirmed(album_rows, confirmed, same_album)
    artists = [a for c in catalogs for a in c.artists]
    tied = (await artist_names.reader(s, [], [a.ref for a in artists])).tied
    # A common release or recording can identify two compatible profiles that have not been linked yet.
    def credits(row: CatalogAlbum | CatalogTrack) -> list[tuple[str, str]]:
        return [(artist_names.key(name), ref) for name, ref in zip(
            [row.artist, *row.artists], [row.artist_ref, *row.artist_refs], strict=False,
        ) if name and ref]

    peers = {frozenset((left, right)) for group in [*albums, *tracks]
             for i, a in enumerate(group) for b in group[i + 1:]
             for name, left in credits(a) for other_name, right in credits(b) if name == other_name}

    def same_artist(a: CatalogArtist, b: CatalogArtist) -> bool:
        if a.ref in tied and b.ref in tied:
            return tied[a.ref][0] == tied[b.ref][0]
        return frozenset((a.ref, b.ref)) in peers

    cards = []
    for group in _search_groups(artists, same_artist, aliases=True):
        head = group[0]
        picture = next((a for a in group if a.picture_url or a.picture_small_url), head)
        cards.append(SearchArtistOut(**head.model_dump(), found_in=_services(group)).model_copy(update={
            "picture_url": picture.picture_url, "picture_small_url": picture.picture_small_url,
        }))
    released = await annotate_albums(s, [
        release_matches.combined(group).model_copy(update={"tracks": None})
        if group[0].ref in confirmed else group[0] for group in albums
    ], user_id)
    for album, group in zip(released, albums, strict=True):
        album.found_in = _services(group)
    return (
        await artist_names.shown(s, cards, exact=True),
        released,
        await _search_track_rows(s, tracks, user_id),
    )


def _with_library(library: list[SearchTrackOut], tracks: list[SearchTrackOut]) -> list[SearchTrackOut]:
    def keys(t: SearchTrackOut) -> list[str]:
        return [key for key in (t.ref, f"nd:{t.nd_song_id}" if t.nd_song_id else "", f"lib:{t.library_track_id}" if t.library_track_id else "") if key]

    out = list(library)
    local = {key: i for i, t in enumerate(library) for key in keys(t)}
    for t in tracks:
        pos = next((local[k] for k in keys(t) if k in local), None)
        if pos is None:
            out.append(t)
        else:
            out[pos] = out[pos].model_copy(update={"found_in": list(dict.fromkeys([*out[pos].found_in, *t.found_in]))})
    return out


MINE_LIMIT = 100


@router.get("/mine", response_model=SearchMineOut)
async def search_mine(q: str = Query(min_length=1), user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    """The search of «Библиотека»: what the listener keeps and nothing else — the artists they follow, by their
    names in every spelling, and the tracks of their playlists and of «Любимое», with a file or without one, as the
    search of the library reads them (Cyrillic and Latin as one, other names of a track). No releases: a release
    is not kept as such."""
    from app import playlists, releases
    from app.api.playlists import tracks_out
    from app.models import Playlist, PlaylistItem

    words = artist_names.fold(q).split()
    if not words:
        return SearchMineOut()
    kept = (await s.execute(
        select(PlaylistItem.track_id).join(Playlist, Playlist.id == PlaylistItem.playlist_id)
        .where(Playlist.user_id == user.id, Playlist.kind.in_((playlists.USER, playlists.LIKED))).distinct())).scalars().all()
    tracks = await tracks_out(s, user, [t for t, _ in await artist_names.found(s, q, MINE_LIMIT, among=kept)])
    follows = [f for f in await releases.follows(s, user.id) if all(w in artist_names.fold(f.name) for w in words)][:20]

    async def shown(ref: str, name: str) -> CatalogArtist:
        src, _, id_ = ref.partition(":")
        try:  # their picture, where the catalog is there to say it
            return (await svc.artist_page(ref)).artist if src != "dz" or svc.deezer is None else await svc.deezer.get_artist(id_)
        except Exception as e:
            log.info("search of the library: artist %s not read: %r", ref, e)
            return CatalogArtist(ref=ref, source=src, id=id_, name=name)  # type: ignore[arg-type]

    artists = await artist_names.shown(s, list(await asyncio.gather(*(shown(f.ref, f.name) for f in follows))))
    return SearchMineOut(artists=artists, tracks=tracks)


@router.get("/tracks", response_model=SearchTracksOut)
async def search_tracks(q: str = Query(min_length=1), offset: int = Query(0, ge=0),
                        limit: int = Query(TRACK_PAGE, ge=1, le=100), user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    """Catalog tracks for the manual picker and for the next pages of the main search."""
    try:
        tracks, nxt = await lookup.track_page(q.strip(), offset, limit)
    except lookup.CatalogUnavailable as error:
        raise HTTPException(502, str(error)) from error
    groups = _search_groups(tracks, same_recording)
    return SearchTracksOut(tracks=await _search_track_rows(s, groups, user.id), next_offset=nxt)


@router.get("/providers", response_model=ProviderSearchOut)
async def search_providers(q: str = Query(min_length=1), provider: str | None = None,
                           group: Literal["sources", "reuploads"] | None = None,
                           user: User = Depends(current_user)):
    """Raw downloadable files. "Artist - Title" is split for ranking; otherwise results keep provider order.
    `group`: only the regular sources, or only re-uploads by small channels (slower: SoundCloud takes seconds).
    Each file can then be listened to and downloaded by its ref "file:<provider>:<provider_id>" (`app.listen`)."""
    return await _file_search(q, [provider] if provider else _provider_names(group))


def _provider_names(group: Literal["sources", "reuploads"] | None) -> list[str]:
    reuploads = {p.name for p in svc.reupload_providers()}
    return [
        n for n in svc.providers if group is None or (n in reuploads) == (group == "reuploads")
    ]


async def _file_search(q: str, names: list[str], emit: EmitProgress | None = None) -> ProviderSearchOut:
    out = await _provider_results(q, names, emit=emit)
    await listen.remember_files(out.candidates)
    return out


async def _provider_results(q: str, names: list[str], *, limit: int = 15,
                            timeout_s: float | None = None, emit: EmitProgress | None = None) -> ProviderSearchOut:
    artist, _, title = q.partition(" - ")
    tq = TrackQuery(artist=artist.strip() if title else q.strip(), title=title.strip() if title else "")
    out = ProviderSearchOut()

    async def one(name: str):
        p = svc.providers.get(name)
        if p is None:
            return []
        try:
            async with asyncio.timeout(timeout_s):
                raw = await p.search(tq, limit=limit)
        except Exception as e:
            out.errors.append(f"{name}: {e}")
            if emit:
                emit("source", {"service": name, "state": "error"})
            return []
        if emit:
            emit("source", {"service": name, "state": "ready" if raw else "empty"})
        if title:
            ranked = rank_candidates(tq, raw, get_settings().cfg.matching)
            return ranked or raw
        return raw

    if emit is None:
        for lst in await asyncio.gather(*(one(n) for n in names)):
            out.candidates.extend(lst)
        return out

    emit("sources", [{"service": name, "state": "searching"} for name in names])
    async with asyncio.TaskGroup() as group:
        ordered = [group.create_task(one(name)) for name in names]
        pending = set(ordered)
        completed = {}
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            available = {task: task.result() for task in done}
            completed.update(available)
            if pending and any(available.values()):
                await listen.remember_files([c for batch in available.values() for c in batch])
                emit("partial", ProviderSearchOut(candidates=[c for task in ordered for c in completed.get(task, [])],
                                                  errors=out.errors))
    out.candidates = [c for task in ordered for c in task.result()]
    return out


COVER_SEARCH_TTL_S = 3600
COVER_MAX_BYTES = 20 * 1024 * 1024
_COVER_PREFIX = "cover-search:"


@router.get("/covers", response_model=CoverSearchOut)
async def search_covers(q: str = Query(min_length=1, max_length=512), album: bool = False,
                        user: User = Depends(current_user)):
    """Artwork from the library, catalogs and the same provider search as the file picker."""
    q = q.strip()
    if not q:
        return CoverSearchOut(covers=[])
    query = q.replace(" - ", " ")
    async with session_scope() as s:
        tracks = await search_library(s, query, limit=6)
        albums = list((await s.execute(select(Album).where(
            Album.title.icontains(query, autoescape=True), Album.cover_path.is_not(None)).limit(6))).scalars()) if album else []

    async def catalog_results():
        requests = []
        if svc.deezer is not None:
            requests.append(svc.deezer.search(query, kinds=("album", "track") if album else ("track",), limit=8))
        if svc.yandex is not None:
            requests.append(svc.yandex.search(query))
        if svc.spotify is not None:
            requests.append(svc.spotify.search(query, kinds=("album", "track") if album else ("track",), limit=8))
        return await asyncio.gather(*(asyncio.wait_for(request, 10) for request in requests), return_exceptions=True)

    sources, catalogs = await asyncio.gather(
        _provider_results(q, list(svc.providers), limit=6, timeout_s=12), catalog_results())
    covers: dict[str, CoverOptionOut] = {}
    locations: dict[str, dict] = {}

    def add(title: str, artist: str, source: str, *, url: str | None = None,
            track_id: int | None = None, album_id: int | None = None) -> None:
        if url:
            try:
                parsed = urlsplit(url)
            except ValueError:
                return
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or len(url) > 2048:
                return
            identity = url
            location = {"url": url}
        elif track_id is not None:
            identity = f"track:{track_id}"
            location = {"track_id": track_id}
        elif album_id is not None:
            identity = f"album:{album_id}"
            location = {"album_id": album_id}
        else:
            return
        key = hashlib.sha256(identity.encode()).hexdigest()
        covers.setdefault(key, CoverOptionOut(key=key, title=title, artist=artist, source=source, track_id=track_id))
        locations[_COVER_PREFIX + key] = location

    for a in albums:
        add(a.title, a.album_artist, "homemusic", album_id=a.id)
    for t in tracks:
        if t.cover_key or t.path:
            add(t.title, t.artist, "homemusic", track_id=t.id)
    for cat in catalogs:
        if isinstance(cat, BaseException):
            continue
        for row in ([*cat.albums[:5], *cat.tracks[:6]] if album else cat.tracks[:8]):
            add(row.title, row.artist, {"dz": "deezer", "ym": "yandex", "sp": "spotify"}[row.source],
                url=row.cover_url or row.cover_small_url)
    per_source: dict[str, int] = {}
    for candidate in sources.candidates:
        count = per_source.get(candidate.provider, 0)
        if candidate.cover_url and count < 3:
            add(candidate.title, candidate.artist, candidate.provider, url=candidate.cover_url)
            per_source[candidate.provider] = count + 1
    await cache_set_many(locations, COVER_SEARCH_TTL_S)
    return CoverSearchOut(covers=list(covers.values()),
                          incomplete=bool(sources.errors) or any(isinstance(cat, BaseException) for cat in catalogs))


async def _library_cover(location: dict) -> bytes | None:
    async with session_scope() as s:
        t = await s.get(Track, location["track_id"]) if "track_id" in location else None
        if t and t.cover_key and (image := await edits.track_cover(s, t.id)):
            return image
        album_id = t.album_id if t else location.get("album_id")
        a = await s.get(Album, album_id) if album_id is not None else None
        album_path = a.cover_path if a else None
        track_path = t.path if t else None
    if album_path and (path := library_abs(album_path)).is_file():
        return await asyncio.to_thread(path.read_bytes)
    if track_path and (path := library_abs(track_path)).is_file():
        return await asyncio.to_thread(tagger.read_cover, path)
    return None


@router.get("/covers/{key}")
async def cover_image(key: str, user: User = Depends(current_user)):
    """Serve and measure an image from a search result. The caller supplies a result key."""
    if len(key) != 64 or (location := await cache_get(_COVER_PREFIX + key)) is None:
        raise HTTPException(404, "Обложка больше недоступна. Повторите поиск.")
    try:
        url = location.get("url")
        image = await asyncio.wait_for(fetch_cover(url, max_bytes=COVER_MAX_BYTES), 20) if url else await _library_cover(location)
    except (MutagenError, TimeoutError, OSError, ValueError):
        raise HTTPException(502, "Не удалось получить обложку") from None
    if not image:
        raise HTTPException(404, "Обложка недоступна")
    if len(image) > COVER_MAX_BYTES:
        raise HTTPException(413, "Картинка слишком большая")
    info = await asyncio.to_thread(cover_info, image)
    if not info or not info.get("w") or not info.get("h"):
        raise HTTPException(415, "Не удалось прочитать картинку")
    mime = Image.MIME.get(info["format"], "application/octet-stream")
    return Response(image, media_type=mime, headers={
        "X-Cover-Width": str(info["w"]), "X-Cover-Height": str(info["h"]), "X-Cover-Format": info["format"] or "",
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })
