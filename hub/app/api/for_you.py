"""«Для вас»: the lists the Hub makes by itself, as one kind of thing — «Микс дня», «Открытия недели», the tops,
«Давно не слушал».

Each is made its own way (`app/mixes`, `app/stats`) and kept as a playlist of Navidrome (`app/for_you.py`). Here
they are told to the pages alike: what lists there are and how many tracks each has, and a list as tracks to show
with what is its own beside them — a mix's mode, the issues of the discoveries, the listens a top counted. What
changes a list (a mix's settings and rebuild, building an issue) stays where it was: `/api/mixes`,
`/api/discoveries`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import for_you, stats
from app.api.schemas import ForYouItem, ForYouOut
from app.auth import current_user
from app.db import get_session
from app.library.owned import annotate_tracks
from app.mixes import core, discover
from app.models import DiscoveryIssue, ForYouList, Mix, Track, User

router = APIRouter(prefix="/api/for-you", tags=["for-you"])

MIX = "mix"
ORDER = (MIX, for_you.WEEKLY, *stats.AUTO)
TITLES = {MIX: core.PLAYLIST_NAME, **for_you.NAMES}
# the column of a top's own listens
COLUMNS = {"top-month": "За месяц", "top-year": "За год", "top-all": "Прослушивания", "forgotten": "Прослушивания"}
BUILD_POLL_S, READY_POLL_S, ISSUE_POLL_S = 4, 8, 20


async def _latest_issue(s: AsyncSession, user_id: int) -> DiscoveryIssue | None:
    return (await s.execute(select(DiscoveryIssue).where(DiscoveryIssue.user_id == user_id)
                            .order_by(DiscoveryIssue.week.desc()).limit(1))).scalar_one_or_none()


@router.get("", response_model=list[ForYouItem])
async def index(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The lists, in their order, with how many tracks each has now (None: not known — nothing was made yet)."""
    counts: dict[str, int | None] = {}
    mix = await core.current_mix(s, user.id)
    counts[MIX] = None if mix is None else sum(1 for _ in (await s.execute(select(core.MixItem.id).where(
        core.MixItem.mix_id == mix.id, core.MixItem.state == "active"))).scalars())
    issue = await _latest_issue(s, user.id)
    counts[for_you.WEEKLY] = len(issue.tracks_json or []) if issue else None
    kept = {r.kind: r for r in (await s.execute(select(ForYouList).where(ForYouList.user_id == user.id))).scalars()}
    for kind in stats.AUTO:
        # as the playlist was last written (rewritten now and then); a list never written yet is counted now
        counts[kind] = len(kept[kind].songs_json or []) if kind in kept else len(await stats.auto(s, user.id, kind))
    return [ForYouItem(kind=k, title=TITLES[k], count=counts[k]) for k in ORDER]


@router.get("/{kind}", response_model=ForYouOut)
async def one(kind: str, mix: int | None = Query(None), issue: int | None = Query(None),
              user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """A list as its page shows it. `mix`: a mix of the past by its id; `issue`: an issue of the past."""
    if kind == MIX:
        return await _mix(s, user, mix)
    if kind == for_you.WEEKLY:
        return await _weekly(s, user, issue)
    if kind in stats.AUTO:
        return await _auto(s, user, kind)
    raise HTTPException(404, "Такого списка нет")


async def _mix(s: AsyncSession, user: User, mix_id: int | None) -> ForYouOut:
    from app.api.mixes import _building, mix_out, settings_out

    current = await core.current_mix(s, user.id)
    shown = current
    if mix_id is not None:
        shown = await s.get(Mix, mix_id)
        if shown is None or shown.kind != core.DAILY or (shown.user_id != user.id and not user.is_admin):
            shown = current  # an old link: today's
    building = await _building(s, user.id)
    settings = await settings_out(s, user.id)
    out = await mix_out(s, shown, shown.user_id) if shown else None
    is_current = bool(shown and current and shown.id == current.id)
    notes = []
    if out and out.ready < out.total:
        notes.append(f"скачано {out.ready}")
    if out and not is_current:
        notes.append("прежний микс")
    waiting = out is not None and out.ready < out.total and is_current
    return ForYouOut(
        kind=MIX, title=TITLES[MIX], context=f"mix:{shown.id}" if shown else MIX, note=" · ".join(notes) or None,
        tracks=[t.model_dump(mode="json") for t in out.tracks] if out else [],
        refresh_s=BUILD_POLL_S if building else READY_POLL_S if waiting else None,
        mix={"id": shown.id if shown else None, "current": is_current, "building": building,
             "settings": settings.model_dump(mode="json"),
             "onboarding": shown is None and not settings.artists and not await core.has_history(s, user.id)})


async def _weekly(s: AsyncSession, user: User, issue_id: int | None) -> ForYouOut:
    issues = list((await s.execute(select(DiscoveryIssue).where(DiscoveryIssue.user_id == user.id)
                                   .order_by(DiscoveryIssue.week.desc()))).scalars())
    shown = next((x for x in issues if x.id == issue_id), None) if issue_id is not None else (issues[0] if issues else None)
    if issue_id is not None and shown is None:
        raise HTTPException(404, "Такого выпуска нет")
    tracks = await annotate_tracks(s, discover.as_tracks(shown), user.id) if shown else []
    building = await discover.building(s, user.id)
    return ForYouOut(
        kind=for_you.WEEKLY, title=TITLES[for_you.WEEKLY], context="discoveries",
        tracks=[t.model_dump(mode="json") for t in tracks], refresh_s=ISSUE_POLL_S if building else None,
        weekly={"issue": shown.id if shown else None, "week": shown.week.isoformat() if shown else None,
                "latest": bool(shown and issues and shown.id == issues[0].id), "building": building,
                "issues": [{"id": x.id, "week": x.week.isoformat(), "count": len(x.tracks_json or [])} for x in issues]})


async def _auto(s: AsyncSession, user: User, kind: str) -> ForYouOut:
    from app.api.playlists import tracks_out

    items = await stats.auto(s, user.id, kind)
    ids = [x["nd_song_id"] for x in items]
    rows: dict[str, Track] = {}
    if ids:
        for t in (await s.execute(select(Track).where(Track.nd_song_id.in_(ids)))).scalars():
            rows.setdefault(t.nd_song_id, t)
    order = [rows[i] for i in ids if i in rows]
    tracks: list[dict[str, Any]] = []
    for t in await tracks_out(s, user, order):
        d = t.model_dump(mode="json")
        d["added_at"] = None  # when it came into the library is no part of a top
        tracks.append(d)
    return ForYouOut(kind=kind, title=TITLES[kind], context="auto", tracks=tracks,
                     plays={x["nd_song_id"]: x["plays"] for x in items}, plays_label=COLUMNS[kind])
