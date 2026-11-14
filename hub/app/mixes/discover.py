"""«Открытия недели»: once a week, `SIZE` tracks of artists the user has never heard.

The same sources of «similar» as a «Микс дня» (`app.mixes.build`): the radios and «similar» of YouTube Music, Last.fm,
Deezer and Yandex, asked about the tracks the user likes now. What differs is what is let through:

- nothing the user knows: not a track of the library or of the history, and not an artist that is in the library, in
  the history of listens (the Hub's and the one brought from Spotify), or was in an earlier issue;
- little-known artists first: an artist with more than `MAX_FANS` listeners in the catalog is no discovery, and
  one with up to `SMALL_FANS` stands highest. An artist with fewer than `MIN_FANS` is left out too: with nobody
  listening these are mostly covers and stray uploads (the first issue ever built was full of them);
- one track of an artist, so a week brings `SIZE` names.

The tracks stay in the catalog: they play through the Hub and reach the library only when the user keeps one. Issues
are kept (`discovery_issues`); the week's issue is built on Monday morning, or on the first look if there is none.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.catalog.models import CatalogTrack, split_ref
from app.config import get_settings
from app.db import session_scope
from app.events import bus
from app.jobs.queue import enqueue, handler, notify
from app.matching.normalize import fingerprint
from app.mixes import build as builder
from app.mixes.build import Cand
from app.mixes.taste import Profile, artist_key
from app.models import DiscoveryIssue, Job, ListenHistory, Track, User, utcnow
from app.services import svc

log = logging.getLogger(__name__)

SIZE = 30
MAX_FANS = 300_000  # listeners of an artist in the catalog: above this everyone knows them
SMALL_FANS = 20_000  # up to this an artist is «little-known» in full; being smaller still earns nothing more
MIN_FANS = 300  # below this there is no telling a musician from a stray upload
FAN_CHECK = 200  # the best candidates whose artists are looked up
OBSCURE_WEIGHT = 1.5  # against ~1.0 of standing first in one source's list: a little-known artist outweighs a place or two
SEEDS = 16
BUILD_HOUR = 5  # Monday, local time
GENS = ("ytm_radio", "lastfm_similar", "deezer_radio", "yandex_similar", "similar_artists")
LISTENBRAINZ = "listenbrainz"  # the user's «Weekly Exploration» there, if they have an account (app/listenbrainz.py)
WEIGHTS = {LISTENBRAINZ: 1.2}  # made for this user out of what people with the same taste listen to
JOB = "discover_build"
CHECK_EVERY_S = 1800


def week_of(local: datetime) -> date:
    """The Monday of the issue that is current at this local time (before Monday's hour — last week's)."""
    day = local.date() - timedelta(days=local.weekday())
    return day - timedelta(days=7) if local.weekday() == 0 and local.hour < BUILD_HOUR else day


async def fans_of(artist_ref: str | None) -> int | None:
    """How many listeners the catalog counts for the artist (none: unknown)."""
    if not artist_ref or svc.deezer is None:
        return None
    src, id_ = split_ref(artist_ref)
    if src != "dz":
        return None
    try:
        return (await svc.deezer.get_artist(id_)).nb_fans
    except Exception as e:
        log.info("discover: artist %s not read: %r", artist_ref, e)
        return None


def obscurity(fans: int | None, rank: int | None) -> float:
    """1 — a little-known artist (up to `SMALL_FANS` listeners), falling to 0 at `MAX_FANS`. Unknown listeners — by
    the track's popularity."""
    if fans is None:
        return 1.0 - builder.popularity(rank) if rank else 0.5
    if fans <= SMALL_FANS:
        return 1.0
    return max(0.0, 1.0 - math.log(fans / SMALL_FANS) / math.log(MAX_FANS / SMALL_FANS))


def novel(c: Cand, p: Profile, known_artists: set[str], past: set[str]) -> str | None:
    """Why a candidate is not a discovery (none: it is one)."""
    t = c.track
    if t.source != "dz":
        return "library"
    if c.nd_song_id:
        return "library"
    fp = fingerprint(t.artist, t.title)
    if p.find(ref=t.ref, nd_song_id=None, fp=fp) is not None:
        return "heard"
    if t.ref in past or fp in past:
        return "earlier_issue"
    names = {artist_key(a) for a in [t.artist, *(t.artists or [])] if a}
    # the profile's artists too: one whose track is liked but not in the library yet is in neither list above
    if names & known_artists or any(a in p.artists or p.artist_listens.get(a, 0) > 0 for a in names):
        return "known_artist"
    return None


async def pick(p: Profile, cands: list[Cand], *, known_artists: set[str], past: set[str],
               stats: dict[str, Any]) -> list[Cand]:
    """The issue out of the candidates: the filters, the score, one track of an artist, the order."""
    cfg = get_settings().cfg.mixes
    rejected: dict[str, int] = {}
    kept: list[Cand] = []
    for c in cands:
        reason = builder.keep_reason(c, p, cfg) or novel(c, p, known_artists, past)
        if reason is None:
            kept.append(c)
        else:
            rejected[reason] = rejected.get(reason, 0) + 1
    for c in kept:
        c.familiar = False
        c.score = builder.score_parts(c, {**cfg.weights, **WEIGHTS}, p)["generators"]
    kept.sort(key=lambda c: -c.score)
    pool = kept[:FAN_CHECK]
    vectors = await builder.sound(p, pool, stats)
    refs = list(dict.fromkeys(c.track.artist_ref for c in pool if c.track.artist_ref))
    fans = dict(zip(refs, await asyncio.gather(*(fans_of(r) for r in refs)), strict=True))
    out: list[Cand] = []
    for c in pool:
        n = fans.get(c.track.artist_ref) if c.track.artist_ref else None
        if n is not None and n > MAX_FANS:
            rejected["popular"] = rejected.get("popular", 0) + 1
            continue
        if n is not None and n < MIN_FANS:
            rejected["nobody"] = rejected.get("nobody", 0) + 1
            continue
        c.score += builder.SIM_WEIGHT * (c.sim or 0.0) + OBSCURE_WEIGHT * obscurity(n, c.track.rank)
        c.parts = {"fans": n}
        out.append(c)
    out.sort(key=lambda c: -c.score)
    stats["rejected"], stats["kept"], stats["candidates"] = rejected, len(out), len(cands)
    return builder.order(builder._take(out, SIZE, {}, 1), vectors)


async def listenbrainz_tracks(user_id: int) -> list[CatalogTrack]:
    from app import listenbrainz
    from app.mixes.resolve import resolve_hits

    async with session_scope() as s:
        user = await s.get(User, user_id)
        if user is None or not user.lb_token_enc:
            raise builder.Off("ListenBrainz не подключён")
    hits = await listenbrainz.weekly_exploration(user_id)
    return [t for t in await resolve_hits(LISTENBRAINZ, hits) if t is not None]


async def known_artists_of(user_id: int) -> set[str]:
    """Artists the user has: the library's, and those of the history brought from another service."""
    async with session_scope() as s:
        names = set((await s.execute(select(Track.artist).where(Track.nd_song_id.is_not(None)).distinct())).scalars())
        names |= set((await s.execute(select(ListenHistory.artist).where(ListenHistory.user_id == user_id)
                                      .distinct())).scalars())
    return {artist_key(n) for n in names if n}


async def past_of(user_id: int, but: date | None = None) -> set[str]:
    """Refs and names of the tracks of the user's other issues."""
    out: set[str] = set()
    async with session_scope() as s:
        rows = (await s.execute(select(DiscoveryIssue).where(DiscoveryIssue.user_id == user_id))).scalars()
        for issue in rows:
            if issue.week == but:
                continue
            for t in issue.tracks_json or []:
                out.add(str(t.get("ref")))
                out.add(fingerprint(t.get("artist") or "", t.get("title") or ""))
    return out


async def build_for(user_id: int, now: datetime | None = None) -> DiscoveryIssue | None:
    """Builds this week's issue (in place of the one this week has, if any). None: nothing to go by yet."""
    from app.mixes.taste import build_profile
    from app.mixes.taste_math import V2Profile

    cfg = get_settings().cfg.mixes
    t0 = time.monotonic()
    week = week_of(builder.local_now(now))
    async with session_scope() as s:
        user = await s.get(User, user_id)
        if user is None:
            return None
        p = await build_profile(s, user, model=cfg.taste_model)
    if p.empty:
        log.info("discover: user %s has no history yet", user_id)
        return None
    if isinstance(p, V2Profile):
        from app.mixes.taste_select import prepare
        from app.mixes.taste_select import seeds as select_seeds

        await prepare(p)
        seeds = await select_seeds(p, SEEDS)
    else:
        seeds = p.seeds(SEEDS) or await builder.cold_seeds(p, SEEDS)
    stats: dict[str, Any] = {"seeds": [f"{t.artist} — {t.title}" for t in seeds]}
    gens = {k: v for k, v in builder.generators(p, seeds).items() if k in GENS}
    gens[LISTENBRAINZ] = lambda: listenbrainz_tracks(user_id)
    results = await builder.run_generators(gens)
    cands = builder.merge(results)
    await builder.link_library(cands)
    picked = await pick(p, list(cands.values()), known_artists=await known_artists_of(user_id),
                        past=await past_of(user_id, but=week), stats=stats)
    used: dict[str, int] = {}
    for c in picked:
        used[c.generator] = used.get(c.generator, 0) + 1
    stats["generators"] = {r.name: {"tracks": len(r.tracks), "used": used.get(r.name, 0), "seconds": r.seconds,
                                    **({"error": r.error} if r.error else {}), **({"off": True} if r.off else {})}
                           for r in results}
    stats["fans"] = [(c.parts or {}).get("fans") for c in picked]
    stats["build_seconds"] = round(time.monotonic() - t0, 1)
    if not picked:
        log.info("discover: nothing new found for user %s: %s", user_id, stats.get("rejected"))
        return None
    tracks = [c.track.model_dump(mode="json") for c in picked]
    async with session_scope() as s:
        issue = (await s.execute(select(DiscoveryIssue).where(
            DiscoveryIssue.user_id == user_id, DiscoveryIssue.week == week))).scalar_one_or_none()
        if issue is None:
            issue = DiscoveryIssue(user_id=user_id, week=week)
            s.add(issue)
        issue.tracks_json, issue.stats_json, issue.created_at = tracks, stats, utcnow()
        await s.flush()
        issue_id = issue.id
    # the issue's tracks are downloaded and kept as a playlist, the way «Микс дня» is (`app/for_you.py`)
    try:
        from app.mixes.core import save_weekly

        await save_weekly(user_id, picked, issue_id=issue_id, description=f"Неделя с {week:%d.%m.%Y}")
    except Exception:
        log.exception("discover: the issue of user %s was not made a playlist", user_id)
    bus.publish(user_id, "library", {"kind": "discoveries", "issue_id": issue.id})
    log.info("discover: issue of %s for user %s: %s tracks in %s s", week, user_id, len(tracks), stats["build_seconds"])
    return issue


@handler(JOB)
async def handle_build(job: Job) -> None:
    await build_for(int(job.payload_json["user_id"]))


async def building(s, user_id: int) -> bool:
    return (await s.execute(select(Job.id).where(
        Job.kind == JOB, Job.status.in_(("queued", "running")),
        func.json_extract(Job.payload_json, "$.user_id") == user_id).limit(1))).scalar_one_or_none() is not None


async def enqueue_build(s, user_id: int) -> bool:
    if await building(s, user_id):
        return False
    from app.mixes.core import PRIORITY_MIX

    await enqueue(s, JOB, {"user_id": user_id}, priority=PRIORITY_MIX, max_attempts=2)
    return True


async def due_users(now: datetime | None = None) -> list[int]:
    """Users who have no issue of the current week yet."""
    week = week_of(builder.local_now(now))
    out: list[int] = []
    async with session_scope() as s:
        have = set((await s.execute(select(DiscoveryIssue.user_id).where(DiscoveryIssue.week == week))).scalars())
        for user_id in (await s.execute(select(User.id).order_by(User.id))).scalars():
            if user_id not in have and await enqueue_build(s, user_id):
                out.append(user_id)
    if out:
        notify()
    return out


# a user whose build found nothing is not asked about again every half an hour: once a day is enough
_tried: dict[int, date] = {}


async def ensure_playlist(user_id: int) -> bool:
    """The user's latest issue is kept as a mix of the week (`core.save_weekly`) — made here for an issue built before
    issues were kept so, or whose keeping failed. True: it was made now."""
    from types import SimpleNamespace

    from app.library.owned import annotate_tracks
    from app.mixes.core import WEEKLY, save_weekly
    from app.models import Mix

    async with session_scope() as s:
        issue = (await s.execute(select(DiscoveryIssue).where(DiscoveryIssue.user_id == user_id)
                                 .order_by(DiscoveryIssue.week.desc()).limit(1))).scalar_one_or_none()
        if issue is None or not issue.tracks_json:
            return False
        kept = [m for m in (await s.execute(select(Mix).where(
            Mix.user_id == user_id, Mix.kind == WEEKLY, Mix.status == "active"))).scalars()
            if (m.stats_json or {}).get("issue") == issue.id]
        if kept:
            return False
        issue_id, week = issue.id, issue.week
        found = await annotate_tracks(s, as_tracks(issue), user_id)
    picked = [SimpleNamespace(track=CatalogTrack.model_validate(t.model_dump()), nd_song_id=t.nd_song_id,
                              library_track_id=t.library_track_id, generator="discover", score=0.0,
                              features=lambda t=t: {"track": CatalogTrack.model_validate(t.model_dump()).model_dump(mode="json")})
              for t in found]
    await save_weekly(user_id, picked, issue_id=issue_id, description=f"Неделя с {week:%d.%m.%Y}")
    return True


async def loop() -> None:
    await asyncio.sleep(420)
    while True:
        try:
            today = builder.local_now().date()
            users = [u for u in await _due_untried(today)]
            if users:
                log.info("discoveries queued for users %s", users)
        except Exception:
            log.exception("discoveries schedule failed")
        await asyncio.sleep(CHECK_EVERY_S)


async def _due_untried(today: date) -> list[int]:
    week = week_of(builder.local_now())
    out: list[int] = []
    async with session_scope() as s:
        have = set((await s.execute(select(DiscoveryIssue.user_id).where(DiscoveryIssue.week == week))).scalars())
        for user_id in (await s.execute(select(User.id).order_by(User.id))).scalars():
            if user_id in have or _tried.get(user_id) == today:
                continue
            _tried[user_id] = today
            if await enqueue_build(s, user_id):
                out.append(user_id)
    if out:
        notify()
    return out


def as_tracks(issue: DiscoveryIssue) -> list[CatalogTrack]:
    return [CatalogTrack(**t) for t in issue.tracks_json or []]
