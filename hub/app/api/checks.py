"""«Проверка сервисов» (admins): is each outside service working, and answering as it used to (`app.checks`)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import checks
from app.auth import admin_user
from app.config import get_settings
from app.db import get_session
from app.models import AppSetting, ServiceShape, User, utcnow

router = APIRouter(prefix="/api/admin/checks", tags=["checks"])

State = Literal["ok", "flaky", "changed", "broken", "off", "none"]
DayState = Literal["ok", "flaky", "broken", "none"]
DAYS = 14
LAST = 10  # the latest results of a check its state is told by
_WORST = {"broken": 5, "changed": 4, "flaky": 3, "ok": 2, "none": 1, "off": 0}


class CheckOut(BaseModel):
    key: str
    title: str
    state: State
    source: Literal["live", "probe"] | None = None  # how its last result is known: the real work, the nightly check
    at: datetime | None = None
    detail: str | None = None
    elapsed_ms: int | None = None
    missing: list[str] = []  # fields its answers stopped coming with
    off: str | None = None  # why it is not checked here


class ServiceOut(BaseModel):
    key: str
    name: str
    state: State
    nights: list[DayState]  # the last `DAYS` days, the oldest first
    checks: list[CheckOut]


class ChecksOut(BaseModel):
    checked_at: datetime | None  # the last nightly run (or «Проверить сейчас»)
    took_s: float | None
    running: bool
    attention: int  # services broken or answering otherwise: the count by the menu's line
    services: list[ServiceOut]


def state_of(latest: list[tuple[bool, str]]) -> State:
    """A check's state by its latest results, newest first ((ok, source) each): working by its last; broken — the
    nightly check failed, or the real work failed `BROKEN_AFTER` times in a row; between — «сбоит»."""
    if not latest:
        return "none"
    ok, source = latest[0]
    if ok:
        return "ok"
    if source == "probe":
        return "broken"
    run = 0
    for o, _ in latest:
        if o:
            break
        run += 1
    return "broken" if run >= checks.BROKEN_AFTER else "flaky"


def missing_of(shape: ServiceShape | None, now: datetime) -> list[str]:
    """The fields no answer has had for a while (`checks.MISSING_AFTER`) though many answers came."""
    if shape is None:
        return []
    answers, age = checks.MISSING_AFTER
    out = []
    for path, (last, seen_then) in (shape.paths or {}).items():
        try:
            when = datetime.fromisoformat(last)
        except (TypeError, ValueError):
            continue
        if shape.seen - int(seen_then) >= answers and now - when >= age:
            out.append(path)
    # a field whose parent is gone is said by the parent
    return sorted(p for p in out if not any(p != q and p.startswith(q) for q in out))


async def overview(s: AsyncSession) -> ChecksOut:
    from app.checks.probes import STATUS_KEY, running, unavailable

    now = utcnow()
    zone = get_settings().timezone
    latest: dict[str, list[tuple[bool, str, datetime, str | None, int | None]]] = {}
    rows = await s.execute(text("""
        SELECT "check", ok, source, at, detail, elapsed_ms FROM (
          SELECT *, row_number() OVER (PARTITION BY "check" ORDER BY at DESC, id DESC) AS n
          FROM service_checks WHERE at >= :since) x
        WHERE n <= :last ORDER BY "check", at DESC, id DESC"""), {"since": now - timedelta(days=checks.KEEP_DAYS), "last": LAST})
    for check, ok, source, at, detail, elapsed in rows:
        latest.setdefault(check, []).append((ok, source, at, detail, elapsed))
    # each check's days: whether it failed that day, and whether its last result of the day did
    days: dict[str, dict[str, tuple[bool, bool]]] = {}
    rows = await s.execute(text("""
        SELECT "check", to_char((at AT TIME ZONE 'UTC') AT TIME ZONE :zone, 'YYYY-MM-DD') AS day,
               bool_or(NOT ok) AS any_fail, (array_agg(ok ORDER BY at DESC, id DESC))[1] AS last_ok
        FROM service_checks WHERE at >= :since GROUP BY 1, 2"""), {"zone": zone, "since": now - timedelta(days=DAYS + 1)})
    for check, day, any_fail, last_ok in rows:
        days.setdefault(check, {})[day] = (bool(any_fail), bool(last_ok))
    shapes = {r.check: r for r in (await s.execute(select(ServiceShape))).scalars()}
    off = await unavailable()

    from zoneinfo import ZoneInfo

    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(zone)).date()
    span = [(today - timedelta(days=DAYS - 1 - i)).isoformat() for i in range(DAYS)]

    services: list[ServiceOut] = []
    for key, name in checks.SERVICES.items():
        outs: list[CheckOut] = []
        for c in (c for c in checks.CHECKS if c.service == key):
            got = latest.get(c.key, [])
            if c.key in off:
                outs.append(CheckOut(key=c.key, title=c.title, state="off", off=off[c.key]))
                continue
            state = state_of([(o, src) for o, src, *_ in got])
            missing = missing_of(shapes.get(c.key), now)
            if state == "ok" and missing:
                state = "changed"
            first = got[0] if got else None
            # a failure is told by its words; a working check by its last success's
            outs.append(CheckOut(key=c.key, title=c.title, state=state, source=first[1] if first else None,
                                 at=first[2] if first else None, detail=first[3] if first else None,
                                 elapsed_ms=first[4] if first else None, missing=missing))
        on = [o for o in outs if o.state != "off"]
        state: State = max((o.state for o in on), key=_WORST.__getitem__) if on else "off"
        nights: list[DayState] = []
        for day in span:
            marks = [days.get(o.key, {}).get(day) for o in on]
            marks = [m for m in marks if m]
            nights.append("none" if not marks else "broken" if any(not last for _, last in marks)
                          else "flaky" if any(fail for fail, _ in marks) else "ok")
        services.append(ServiceOut(key=key, name=name, state=state, nights=nights, checks=outs))
    status = (row.value_json if (row := await s.get(AppSetting, STATUS_KEY)) else None) or {}
    at = status.get("at")
    return ChecksOut(checked_at=datetime.fromisoformat(at) if at else None, took_s=status.get("took_s"),
                     running=await running(s), attention=sum(1 for x in services if x.state in ("broken", "changed")),
                     services=sorted(services, key=lambda x: -_WORST[x.state]))


@router.get("", response_model=ChecksOut)
async def get_checks(_: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await overview(s)


@router.post("/run", response_model=ChecksOut)
async def run_now(_: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    """«Проверить сейчас»: every check asked anew, in the worker (a minute or three)."""
    from app.checks.probes import request
    from app.jobs.queue import notify

    if await request(s, force=True):
        await s.commit()
        notify()
    return await overview(s)
