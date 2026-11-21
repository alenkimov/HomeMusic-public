"""«Микс дня» and the taste onboarding (MIXES.md 6, 8): see app/mixes/."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import favorites
from app.api.schemas import (
    ArtistMapOut,
    ArtistNearOut,
    FavoriteTarget,
    MixOut,
    MixRebuildIn,
    MixSettingsIn,
    MixSettingsOut,
    MixTodayOut,
    MixTrackOut,
    TasteArtistsIn,
    TasteOnboardingOut,
)
from app.auth import current_user
from app.catalog import lookup
from app.catalog.models import CatalogArtist, CatalogTrack, split_ref
from app.config import get_settings
from app.db import get_session
from app.jobs.queue import notify
from app.library import artists
from app.library.owned import annotate_tracks
from app.mixes import core
from app.mixes.build import GENERATOR_TITLES, MODE_TITLES
from app.models import Job, Mix, MixItem, PlayEvent, TrackArtist, User
from app.services import svc

router = APIRouter(tags=["mixes"])


async def mix_out(s: AsyncSession, mix: Mix, user_id: int) -> MixOut:
    items = list((await s.execute(select(MixItem).where(MixItem.mix_id == mix.id, MixItem.state == "active")
                                  .order_by(MixItem.position))).scalars())
    tracks = [CatalogTrack.model_validate((i.features_json or {}).get("track")) for i in items]
    annotated = await annotate_tracks(s, [t for t in tracks if t.source != "nd"], user_id)
    by_ref = {t.ref: t for t in annotated}
    out: list[MixTrackOut] = []
    for i, t in zip(items, tracks, strict=True):
        base = by_ref.get(t.ref)
        d = base.model_dump() if base is not None else {**t.model_dump(), "owned": bool(i.nd_song_id),
                                                        "library_track_id": i.track_id}
        if i.nd_song_id:
            d.update(owned=True, nd_song_id=i.nd_song_id, library_track_id=i.track_id or d.get("library_track_id"))
        f = i.features_json or {}
        out.append(MixTrackOut(**d, position=i.position, generator=i.generator,
                               generator_title=GENERATOR_TITLES.get(i.generator, i.generator),
                               generators=list((f.get("generators") or {}).keys()), familiar=i.familiar,
                               score=i.score, sim=f.get("sim")))
    return MixOut(id=mix.id, title=mix.title, description=mix.description, mode=mix.mode,  # type: ignore[arg-type]
                  mode_title=MODE_TITLES.get(mix.mode, mix.mode), created_at=mix.created_at,
                  nd_playlist_id=mix.nd_playlist_id, ready=sum(1 for t in out if t.nd_song_id), total=len(out),
                  tracks=out, generators=(mix.stats_json or {}).get("generators") or {})


async def settings_out(s: AsyncSession, user_id: int) -> MixSettingsOut:
    st = await core.settings_of(s, user_id)
    return MixSettingsOut(mode=st.mode, mix_hour=st.mix_hour, timezone=get_settings().timezone,  # type: ignore[arg-type]
                          next_build_at=core.next_build_at(st.mix_hour), nd_playlist_id=st.nd_playlist_id,
                          artists=list(st.artists_json or []))


async def _building(s: AsyncSession, user_id: int) -> bool:
    return (await s.execute(select(Job.id).where(
        Job.kind == "mix_build", Job.status.in_(("queued", "running")),
        func.json_extract(Job.payload_json, "$.user_id") == user_id).limit(1))).scalar_one_or_none() is not None


@router.get("/api/mixes/today", response_model=MixTodayOut)
async def today(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The user's current mix (lives until the next one), or None with the settings and whether to onboard."""
    mix = await core.current_mix(s, user.id)
    st = await settings_out(s, user.id)
    onboarding = mix is None and not st.artists and not await core.has_history(s, user.id)
    return MixTodayOut(mix=await mix_out(s, mix, user.id) if mix else None, building=await _building(s, user.id),
                       settings=st, onboarding=onboarding)


@router.get("/api/mixes/settings", response_model=MixSettingsOut)
async def get_settings_(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await settings_out(s, user.id)


@router.put("/api/mixes/settings", response_model=MixSettingsOut)
async def put_settings(body: MixSettingsIn, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    """The mode applies from the next mix (`POST /api/mixes/rebuild` — now); a new hour — from the next day."""
    st = await core.settings_of(s, user.id)
    if body.mode is not None:
        st.mode = body.mode
    if body.mix_hour is not None:
        st.mix_hour = body.mix_hour
    await s.flush()
    return await settings_out(s, user.id)


@router.post("/api/mixes/rebuild")
async def rebuild(body: MixRebuildIn | None = None, user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session, scope="function")):
    """Build the mix again now (admin, debugging): it replaces the current one when ready."""
    if not user.is_admin:
        raise HTTPException(403, "Только для администратора")
    user_id = (body.user_id if body else None) or user.id
    if await s.get(User, user_id) is None:
        raise HTTPException(404, "Пользователь не найден")
    queued = await core.enqueue_build(s, user_id, priority=0)
    await s.commit()
    notify()
    return {"queued": queued}


@router.get("/api/mixes/{mix_id}", response_model=MixOut)
async def get_mix(mix_id: int, user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session, scope="function")):
    mix = await s.get(Mix, mix_id)
    if mix is None or (mix.user_id != user.id and not user.is_admin):
        raise HTTPException(404, "Микс не найден")
    return await mix_out(s, mix, mix.user_id)


async def server_artists(s: AsyncSession, limit: int = 30) -> list[CatalogArtist]:
    """The artists of this server as catalog cards: the most listened to first (everyone's finished listens), then
    those the library has most tracks of."""
    heard = (await s.execute(select(TrackArtist.artist_id).join(PlayEvent, PlayEvent.track_id == TrackArtist.track_id)
                             .where(PlayEvent.kind == "finish").group_by(TrackArtist.artist_id)
                             .order_by(func.count(PlayEvent.id).desc()).limit(limit))).scalars().all()
    kept = (await s.execute(select(TrackArtist.artist_id).group_by(TrackArtist.artist_id)
                            .order_by(func.count().desc()).limit(limit))).scalars().all()
    place = {id_: i for i, id_ in enumerate(dict.fromkeys([*heard, *kept]))}
    if not place:
        return []
    cards = [card for id_ in list(place)[:limit] if (card := await _artist(s, f"artist:{id_}"))]
    return await artists.shown(s, cards)


def _interleave(lists: list[list]) -> list:
    return [x[i] for i in range(max((len(x) for x in lists), default=0)) for x in lists if i < len(x)]


async def _artist(s: AsyncSession, ref: str) -> CatalogArtist | None:
    refs, _ = await artists.whole(s, ref)
    if page := await lookup.artist_page(refs):
        return page.artist
    if artist := await artists.by_ref(s, ref):
        return CatalogArtist(ref=f"artist:{artist.id}", source="nd", id=str(artist.id),
                             name=artist.name, hub_artist_id=artist.id)
    return None


@router.get("/api/taste/onboarding", response_model=TasteOnboardingOut)
async def onboarding(q: str | None = Query(None, max_length=100), user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    """«Кого вы слушаете?»: artists to pick from — found by `q`, else those this server listens to (`source`
    "server"); a server with no music yet shows the catalog's chart ("chart")."""
    st = await core.settings_of(s, user.id)
    suggestions, source = [], "server"
    if q and q.strip():
        try:
            source = "search"
            found = await lookup.search(q.strip(), kinds=("artist",), limit=20)
            suggestions = (await artists.shown(s, found.artists))[:20]
        except lookup.CatalogUnavailable as e:
            raise HTTPException(502, f"Каталог не ответил: {e}") from e
    else:
        suggestions = await server_artists(s)
        if not suggestions and svc.deezer is not None:
            try:
                suggestions = await artists.shown(s, await svc.deezer.chart_artists(30))
                source = "chart"
            except Exception:
                pass
    needed = not st.artists_json and not await core.has_history(s, user.id)
    # picks made before an artist's records were tied together come back at the artist's one address
    at = await artists.addresses(s, [a.get("ref") for a in st.artists_json or []])
    selected = list({at.get(a["ref"], a["ref"]): {**a, "ref": at.get(a["ref"], a["ref"])} for a in st.artists_json or [] if a.get("ref")}.values())
    return TasteOnboardingOut(needed=needed, selected=selected, suggestions=suggestions, source=source)


@router.get("/api/taste/similar", response_model=list[CatalogArtist])
async def similar(ref: str = Query(max_length=64), user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session, scope="function")):
    """The artists the catalogs hold to be like `ref` (an artist of Deezer or of Yandex Music), closest first: the
    row «Похожие на …» that follows a pick, and «Похожие» of an artist's page."""
    theirs, _ = await artists.whole(s, ref)
    if not ref.startswith(("artist:", "sp:", "dz:", "ym:")):
        raise HTTPException(400, f"Неверный исполнитель: {ref}")
    calls = []
    for record in lookup.preferred_refs(theirs):
        src, ident = split_ref(record)
        if src == "dz" and svc.deezer is not None:
            calls.append(svc.deezer.get_artist_related(ident))
        if src == "ym" and svc.yandex is not None:
            calls.append(svc.yandex.similar(ident))
    lists = await asyncio.gather(*calls, return_exceptions=True)
    found = _interleave([x for x in lists if not isinstance(x, BaseException)])
    seen: set[str] = set()  # whom several of the records are like, once — by the name: two catalogs, one artist
    found = [a for a in found if not (artists.key(a.name) in seen or seen.add(artists.key(a.name)))]
    return [a for a in await artists.shown(s, found) if a.ref not in theirs]


MAP_ASKED = 60  # of Last.fm's nearest asked for: some the catalog does not know
MAP_MOST = 50  # of them on the map at most


@router.get("/api/taste/map", response_model=ArtistMapOut)
async def artist_map(ref: str = Query(max_length=64), n: int = Query(15, ge=1, le=MAP_MOST),
                     user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session, scope="function")):
    """«Карта»: the artists Last.fm holds to be near `ref` (a Deezer artist), nearest first, each with how near.
    Last.fm knows artists by name: each is looked up in the catalog, and one the catalog has nobody of that name for
    is left out — on the map an artist is someone to go to or to open. `n`: how many are wanted — looking each up
    takes a request to the catalog the first time, so the map asks for the nearest few, then for all (`more`: the
    answer may grow with a greater `n`). No Last.fm key → `404`."""
    from app.mixes import sources
    from app.mixes.build import artist_by_name

    if not await sources.lastfm_configured():
        raise HTTPException(404, "Карта исполнителей работает через Last.fm — на этом сервере ключ Last.fm не задан")
    center = await _artist(s, ref)
    if center is None:
        raise HTTPException(404, f"Исполнитель не найден: {ref}")
    # Last.fm is asked by the name the artist has here (the catalog's record may spell it another way)
    known = (await artists.shown(s, [center]))[0]
    try:
        near = await sources.lastfm_near_artists(known.name or center.name, MAP_ASKED)
    except sources.SourceDown as e:
        raise HTTPException(502, f"Last.fm не ответил: {e}") from e

    async def one(name: str) -> CatalogArtist | None:
        try:
            found = await artist_by_name(name)
            page = await lookup.artist_page([found]) if found else None
            return page.artist if page else None
        except Exception:
            return None

    asked = near[:n + n // 3 + 3]  # a few more than wanted: not everyone is found
    near, more = asked, len(asked) < len(near) and n < MAP_MOST
    cards = await asyncio.gather(*(one(name) for name, _ in near))
    at = await artists.addresses(s, [center.ref, *(a.ref for a in cards if a is not None)])
    match: dict[str, float] = {}
    for a, (_, m) in zip(cards, near):
        if a is not None:
            match.setdefault(at[a.ref], m)  # nearest first: an artist's nearest record says how near they are
    # as they are here: under their own names, each once — and the artist in the middle is not around themselves
    here = await artists.shown(s, [center, *(a for a in cards if a is not None)])
    around = [ArtistNearOut(artist=a, match=match[a.ref]) for a in here[1:] if a.ref in match and a.ref != here[0].ref]
    return ArtistMapOut(center=here[0], around=around[:n], more=more)


@router.post("/api/taste/artists", response_model=MixSettingsOut)
async def pick_artists(body: TasteArtistsIn, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    """The artists a user says they listen to (Deezer refs): «Микс дня» and «Моя волна» start from them, each is
    followed; the first mix is built now."""
    picked = []
    for ref in dict.fromkeys(body.artist_refs):
        if not ref.startswith(("artist:", "sp:", "dz:", "ym:")):
            raise HTTPException(400, f"Неверный исполнитель: {ref}")
        a = await _artist(s, ref)
        if a is None:
            raise HTTPException(404, f"Исполнитель не найден: {ref}")
        picked.append(a)
    # the artists as they are here: each once, at their one address, under their name
    picked = [{"ref": a.ref, "name": a.name} for a in await artists.shown(s, picked)]
    st = await core.settings_of(s, user.id)
    first = not st.artists_json and await core.current_mix(s, user.id) is None
    st.artists_json = picked
    # a pick is a follow: who one says one listens to is whose releases one is told of. Those followed already stay
    # as they are; taking an artist off the picks later does not unfollow them
    states = await favorites.snapshot(s, user.id)
    followed = {a[4:] for x in states.values() if x.target_type == "artist" and x.liked
                for a in x.aliases if a.startswith("ref:")}  # by any record of theirs
    new = [FavoriteTarget(target_type="artist", ref=a["ref"], target_data={"name": a["name"]})
           for a in picked if a["ref"] not in followed]
    if new:
        await favorites.command(s, user, new, liked=True, context="taste")
    if first:
        await core.enqueue_build(s, user.id, priority=0)
    await s.commit()
    notify()
    return await settings_out(s, user.id)
