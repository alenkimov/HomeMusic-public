"""«Наведение порядка» (app/tidy): what it found, the answers to it, and the journal of what was accepted.

Anybody signed in answers — the library is everybody's, as its names are (app/library/edits.py); a file to delete
is still the business of who keeps it (app/library/access.py). The look-through by request costs the AI: the
admins start it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import admin_user, current_user
from app.config import get_settings
from app.db import get_session
from app.events import bus
from app.jobs.queue import notify
from app.models import TidyProposal, User
from app.tidy import ai, kinds, scan

router = APIRouter(prefix="/api/tidy", tags=["tidy"])

Section = Literal["names", "versions", "artists", "lyrics"]


class TidySectionOut(BaseModel):
    id: str
    title: str
    count: int  # waiting for an answer
    sure: int = 0  # of them, the ones a rule or a sure AI found


class TidyAiOut(BaseModel):
    state: Literal["ok", "limit", "off"]  # off: no AI set up, or no allowance
    spent_usd: float
    limit_usd: float


class TidyOverviewOut(BaseModel):
    checked_at: datetime | None = None
    running: bool
    can_scan: bool
    ai: TidyAiOut
    sections: list[TidySectionOut]
    total: int


class TidyProposalOut(BaseModel):
    id: int
    kind: str
    section: str
    subject_type: str
    subject_id: int
    was: str  # what the subject is, in words
    to: str  # what it would be
    show: dict[str, Any]  # what the page draws: the cover, whose it is, the lines of a text…
    by: Literal["rule", "ai"]
    sure: bool
    why: str
    state: str
    decided_at: datetime | None = None
    decided_by: str | None = None


class AcceptSureIn(BaseModel):
    section: Section


class AcceptSureOut(BaseModel):
    accepted: int
    failed: int


def _words(p: TidyProposal, show: dict[str, Any]) -> tuple[str, str]:
    if p.kind in ("title", "cyrillic"):
        return p.was_json["name"], p.to_json["name"]
    if p.kind == "version":
        return show.get("title_now") or "", f"Версия «{p.to_json.get('label')}» трека «{show.get('of_title')}»"
    if p.kind == "double":
        def copy(file: str | None, album: str | None) -> str:
            return " · ".join(x for x in (file, f"«{album}»" if album else "без альбома") if x)

        return copy(show.get("file"), show.get("album")), copy(show.get("keep_file"), show.get("keep_album"))
    if p.kind == "artist":
        return show.get("name") or "", show.get("into_name") or ""
    if p.kind == "uncensor":
        n = sum(len(w) for w in (p.to_json.get("replace") or {}).values())
        return "", f"Вернуть {n} {'слово' if n % 10 == 1 and n % 100 != 11 else 'слова' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'слов'} без цензуры"
    if p.kind == "lyrics":
        n = len(p.to_json.get("drop") or [])
        return "", f"Убрать {n} {'строку' if n % 10 == 1 and n % 100 != 11 else 'строки' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'строк'}"
    return "", ""


async def _out(s: AsyncSession, rows: list[TidyProposal]) -> list[TidyProposalOut]:
    from app.models import Track

    who = {}
    ids = {p.decided_by for p in rows if p.decided_by}
    if ids:
        who = dict((await s.execute(select(User.id, User.nd_username).where(User.id.in_(ids)))).all())
    titles = {}
    track_ids = {p.subject_id for p in rows if p.subject_type == "track"}
    if track_ids:
        titles = dict((await s.execute(select(Track.id, Track.title).where(Track.id.in_(track_ids)))).all())
    out = []
    for p in rows:
        show = dict(p.show_json or {})
        if p.subject_type == "track":
            show["title_now"] = titles.get(p.subject_id) or show.get("title") or ""
        # where it is in the text, by the places of its lines; who an artist joins
        if p.kind == "lyrics":
            show["drop_at"] = list(p.to_json.get("drop") or [])
        elif p.kind == "uncensor":
            show["fix_at"] = sorted(int(k) for k in (p.to_json.get("replace") or {}))
        elif p.kind == "artist":
            show["into_id"] = p.to_json.get("into")
        elif p.kind == "double":
            show["keep_id"] = p.to_json.get("keep")  # the copy that stays: its own row beside the one that goes
        elif p.kind == "version":
            show["of_id"] = p.to_json.get("of")  # the original: its own row above the version
        was, to = _words(p, show)
        out.append(TidyProposalOut(
            id=p.id, kind=p.kind, section=p.section, subject_type=p.subject_type, subject_id=p.subject_id, was=was,
            to=to, show=show, by=p.by, sure=p.sure, why=p.why, state=p.state, decided_at=p.decided_at,
            decided_by=who.get(p.decided_by)))
    return out


async def _proposal(s: AsyncSession, proposal_id: int) -> TidyProposal:
    p = await s.get(TidyProposal, proposal_id)
    if p is None:
        raise HTTPException(404, "Предложение не найдено")
    return p


async def _done(s: AsyncSession, user: User, after: kinds.After | None) -> None:
    if after is not None:
        await after.queue(s, user)
    await s.commit()
    notify()
    if after is not None:
        bus.publish(user.id, "library", {"kind": "changed", "album_id": None})
    bus.broadcast("tidy", {"kind": "decided"})


@router.get("", response_model=TidyOverviewOut)
async def overview(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    from app.api.library import split_waiting

    counts = {sec: (n, sure) for sec, n, sure in (await s.execute(
        select(TidyProposal.section, func.count(TidyProposal.id), func.count(TidyProposal.id).filter(TidyProposal.sure))
        .where(TidyProposal.state == "open").group_by(TidyProposal.section))).all()}
    albums = await split_waiting(s, user)
    sections = [TidySectionOut(id=sec, title=kinds.TITLES[sec], count=albums if sec == "albums" else counts.get(sec, (0, 0))[0],
                               sure=0 if sec == "albums" else counts.get(sec, (0, 0))[1]) for sec in kinds.SECTIONS]
    status = await scan.status(s)
    spent = await ai.spent(s)
    limit = get_settings().tidy_ai_usd_month
    state = "off" if not ai.available() else "ok" if spent < limit else "limit"
    return TidyOverviewOut(
        checked_at=datetime.fromisoformat(status["checked_at"]) if status.get("checked_at") else None,
        running=await scan.running(s), can_scan=user.is_admin,
        ai=TidyAiOut(state=state, spent_usd=round(spent, 4), limit_usd=limit),
        sections=sections, total=sum(x.count for x in sections))


@router.get("/proposals", response_model=list[TidyProposalOut])
async def proposals(section: Section, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    """What waits for an answer in the section: the sure ones first, then as they were found."""
    rows = list((await s.execute(select(TidyProposal).where(TidyProposal.section == section, TidyProposal.state == "open")
                                 .order_by(TidyProposal.sure.desc(), TidyProposal.id))).scalars())
    return await _out(s, rows)


@router.get("/marks", response_model=list[TidyProposalOut])
async def marks(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """Everything that waits for an answer, of every section: the pages mark what it is about (a track's row, an
    album's or an artist's name, the lines of a text) and lead from the mark to it."""
    rows = list((await s.execute(select(TidyProposal).where(TidyProposal.state == "open")
                                 .order_by(TidyProposal.sure.desc(), TidyProposal.id))).scalars())
    return await _out(s, rows)


@router.get("/journal", response_model=list[TidyProposalOut])
async def journal(section: Section | None = None, before: int | None = None, limit: int = Query(50, ge=1, le=200),
                  user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """What was decided — accepted, rejected, taken back — the latest decided first; `before`: the page after that
    proposal."""
    q = select(TidyProposal).where(TidyProposal.state.in_(("accepted", "rejected", "reverted")))
    if section:
        q = q.where(TidyProposal.section == section)
    if before and (last := await s.get(TidyProposal, before)) is not None and last.decided_at is not None:
        q = q.where(tuple_(TidyProposal.decided_at, TidyProposal.id) < tuple_(last.decided_at, last.id))
    rows = list((await s.execute(q.order_by(TidyProposal.decided_at.desc(), TidyProposal.id.desc()).limit(limit))).scalars())
    return await _out(s, rows)


async def _decide(s: AsyncSession, user: User, proposal_id: int, what: str) -> TidyProposalOut:
    p = await _proposal(s, proposal_id)
    after = None
    try:
        if what == "accept":
            after = await kinds.accept(s, user, p)
        elif what == "reject":
            await kinds.reject(s, user, p)
        elif what == "reopen":
            await kinds.reopen(s, p)
        elif what == "review":
            after = await kinds.review(s, user, p)
        else:
            after = await kinds.revert(s, user, p)
    except kinds.Changed as e:
        await s.commit()  # a proposal whose subject changed is gone: so it is for the next asking too
        bus.broadcast("tidy", {"kind": "decided"})
        raise HTTPException(409, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    (out,) = await _out(s, [p])
    await _done(s, user, after)
    return out


@router.post("/proposals/{proposal_id}/accept", response_model=TidyProposalOut)
async def accept(proposal_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await _decide(s, user, proposal_id, "accept")


@router.post("/proposals/{proposal_id}/reject", response_model=TidyProposalOut)
async def reject(proposal_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await _decide(s, user, proposal_id, "reject")


@router.post("/proposals/{proposal_id}/reopen", response_model=TidyProposalOut)
async def reopen(proposal_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """A rejection taken back."""
    return await _decide(s, user, proposal_id, "reopen")


@router.post("/proposals/{proposal_id}/review", response_model=TidyProposalOut)
async def review(proposal_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """«Вернуть на рассмотрение»: any decision taken back, the proposal open again (an accepted one undone first)."""
    return await _decide(s, user, proposal_id, "review")


@router.post("/proposals/{proposal_id}/revert", response_model=TidyProposalOut)
async def revert(proposal_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """What an accepted proposal did, taken back."""
    return await _decide(s, user, proposal_id, "revert")


@router.post("/accept-sure", response_model=AcceptSureOut)
async def accept_sure(body: AcceptSureIn, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    """Accept every sure proposal of the section; one that cannot be done (changed since, somebody else's file) is
    left, and the others go on."""
    ids = list((await s.execute(select(TidyProposal.id).where(TidyProposal.section == body.section,
                                                              TidyProposal.state == "open", TidyProposal.sure)
                                .order_by(TidyProposal.id))).scalars())
    accepted = failed = 0
    after = kinds.After(rewrite=set(), playlists=set())
    for proposal_id in ids:
        p = await s.get(TidyProposal, proposal_id)
        try:
            async with s.begin_nested():
                done = await kinds.accept(s, user, p)
        except (kinds.Changed, PermissionError, ValueError):
            failed += 1
            continue
        accepted += 1
        after.rewrite |= done.rewrite
        after.scan = after.scan or done.scan
        after.playlists |= done.playlists or set()
    await _done(s, user, after)
    return AcceptSureOut(accepted=accepted, failed=failed)


@router.post("/scan", response_model=TidyOverviewOut)
async def start_scan(user: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    """«Проверить сейчас»: a look-through is queued (unless one is under way)."""
    await scan.request(s)
    await s.commit()
    notify()
    return await overview(user, s)
