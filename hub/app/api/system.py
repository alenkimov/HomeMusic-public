"""Health, share-target fallback."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import HealthOut
from app.auth import admin_user, current_user
from app.config import get_settings
from app.db import get_session
from app.library.navidrome import admin_client
from app.models import User
from app.services import svc

log = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


async def _navidrome_ok() -> bool:
    nd = admin_client()
    try:
        return await asyncio.wait_for(nd.ping(), timeout=5)
    except Exception:
        return False
    finally:
        await nd.aclose()


_BUILD_META = re.compile(r'<meta\s+name="hm-build"\s+content="([^"]+)"')
_build_cache: tuple[tuple[str, int, int], str | None] | None = None


def web_build(static_dir: Path) -> str | None:
    """Build id of the PWA in `static_dir` (stamped into index.html by web/vite.config.ts); None without a build."""
    global _build_cache
    index = static_dir / "index.html"
    try:
        st = index.stat()
    except OSError:
        return None
    key = (str(index), st.st_mtime_ns, st.st_size)
    if _build_cache is None or _build_cache[0] != key:
        m = _BUILD_META.search(index.read_text(encoding="utf-8", errors="replace"))
        _build_cache = (key, m.group(1) if m else None)
    return _build_cache[1]


@router.get("/api/ping", include_in_schema=False)
async def ping():
    """The Hub answers: what a page asks to know it can reach it (`web/src/lib/reach.ts`). Asks nothing of anyone
    else, so that a slow Navidrome is not taken for a lost connection."""
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/healthz", response_model=HealthOut)
async def healthz():
    return HealthOut(ok=True, navidrome=await _navidrome_ok(), web_build=web_build(get_settings().static_dir))


@router.get("/api/system/health", response_model=HealthOut)
async def health(user: User = Depends(admin_user)):
    from app.housekeeping import smoke_test_providers

    providers: dict = {}
    hitmoz = svc.providers.get("hitmoz")
    if hitmoz is not None and hasattr(hitmoz, "health"):
        try:
            providers["hitmoz_domain"] = await hitmoz.health()
        except Exception as e:
            providers["hitmoz_domain"] = {"ok": False, "error": repr(e)}
    providers["search"] = await smoke_test_providers()
    return HealthOut(ok=True, navidrome=await _navidrome_ok(), providers=providers,
                     web_build=web_build(get_settings().static_dir))


@router.post("/share-target")
async def share_target(request: Request, s: AsyncSession = Depends(get_session, scope="function")):
    """Fallback when the service worker did not intercept the share (e.g. first launch before SW install)."""
    from app.auth import COOKIE

    if not request.cookies.get(COOKIE):
        return RedirectResponse("/#/login?next=share", status_code=303)
    # the PWA re-shares from its own page; nothing is stored server-side without the SW
    return RedirectResponse("/#/share?fallback=1", status_code=303)


class ClientErrorIn(BaseModel):
    """An error the app showed to a listener: its words, and the page it was shown on."""

    text: str = Field(max_length=500)
    page: str = Field(default="", max_length=200)


@router.post("/api/system/client-error")
async def client_error(body: ClientErrorIn, request: Request, user: User = Depends(current_user)) -> dict:
    """Written to the Hub's log: who saw which error where, and in what browser. Nothing else is done with it."""
    log.warning("shown to %s on %s: %s | %s", user.nd_username, body.page or "/", body.text,
                (request.headers.get("user-agent") or "")[:160], extra={"user_id": user.id})
    return {"ok": True}

