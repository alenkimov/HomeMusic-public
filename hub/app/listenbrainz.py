"""ListenBrainz, for a user who wants it: the Hub sends there what the user listens to and takes from there the
weekly playlist of things to explore.

Nothing here is needed for the Hub to work: a user without a token is not sent anywhere and «Открытия недели» are
built from the other sources. With a token (listenbrainz.org → Settings → User token):

- listens go there — every listen the statistics count (app/stats.py: 30 seconds and longer), the history brought
  from Spotify included, each once (`users.lb_sent_until` is how far they are sent). ListenBrainz builds its
  recommendations out of them, and shows them on the user's public profile there;
- «Weekly Exploration», the playlist ListenBrainz makes for the user every Monday, is one more source of
  «Открытия недели» (app/mixes/discover.py). It appears once ListenBrainz has a few weeks of listens.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from typing import Any

import httpx
from sqlalchemy import select

from app import stats
from app.db import session_scope
from app.mixes.sources import Hit
from app.models import User
from app.net import make_client
from app.security import decrypt

log = logging.getLogger(__name__)

API = "https://api.listenbrainz.org/1"
BATCH = 500  # listens in one request (ListenBrainz takes up to 1000)
MIN_TS = 1033430400  # ListenBrainz takes no listen older than October 2002
LOOP_S = 600
EXPLORATION = "weekly-exploration"


class Refused(Exception):
    """ListenBrainz did not take the token."""


async def _call(method: str, path: str, token: str | None = None, **kw: Any) -> Any:
    headers = {"Authorization": f"Token {token}"} if token else {}
    async with make_client(timeout=60) as c:
        for attempt in range(4):
            r = await c.request(method, f"{API}/{path}", headers=headers, **kw)
            if r.status_code == 429 and attempt < 3:  # the rate limit: it says how long to wait
                await asyncio.sleep(min(30.0, float(r.headers.get("X-RateLimit-Reset-In") or 5)))
                continue
            if r.status_code in (401, 403):
                raise Refused(r.text[:200])
            r.raise_for_status()
            return r.json()
    raise httpx.HTTPError("ListenBrainz keeps limiting the requests")


async def validate(token: str) -> str | None:
    """The name of the user the token is of (none: not a token)."""
    try:
        r = await _call("GET", "validate-token", token)
    except Refused:
        return None
    return str(r.get("user_name")) if r.get("valid") and r.get("user_name") else None


def listen_of(p: stats.Play, lib: stats.Library) -> dict[str, Any] | None:
    """A listen as ListenBrainz takes it (none: it has no name to go by, or is too old)."""
    t = lib.tracks.get(p.track_id) if p.track_id is not None else None
    title, artist, album = (t.title, t.artist, t.album) if t else (p.title, p.artist, p.album)
    ts = int(p.ts.replace(tzinfo=UTC).timestamp())
    if not title or not artist or ts < MIN_TS:
        return None
    meta: dict[str, Any] = {"artist_name": artist, "track_name": title,
                            "additional_info": {"media_player": "HomeMusic", "submission_client": "HomeMusic"}}
    if album:
        meta["release_name"] = album
    return {"listened_at": ts, "track_metadata": meta}


async def send(token: str, listens: list[dict[str, Any]]) -> None:
    for i in range(0, len(listens), BATCH):
        await _call("POST", "submit-listens", token, json={"listen_type": "import", "payload": listens[i:i + BATCH]})


async def sync_user(user_id: int) -> int:
    """Sends the listens ListenBrainz has not got from the Hub yet. Returns how many went."""
    async with session_scope() as s:
        user = await s.get(User, user_id)
        if user is None or not user.lb_token_enc:
            return 0
        token, since = decrypt(user.lb_token_enc), user.lb_sent_until
        lib = await stats.library(s)
        plays = [p for p in await stats.plays(s, user_id, lib) if p.counted and (since is None or p.ts > since)]
    if not plays:
        return 0
    listens = [x for x in (listen_of(p, lib) for p in plays) if x]
    try:
        await send(token, listens)
    except Refused:
        log.warning("listenbrainz: the token of user %s is not taken any more", user_id)
        return 0
    async with session_scope() as s:
        user = await s.get(User, user_id)
        if user is not None and user.lb_token_enc:
            user.lb_sent_until = max(p.ts for p in plays)
    return len(listens)


async def weekly_exploration(user_id: int) -> list[Hit]:
    """The tracks of the newest «Weekly Exploration» ListenBrainz made for the user, by name (empty: no account,
    or ListenBrainz has not made one yet)."""
    async with session_scope() as s:
        user = await s.get(User, user_id)
        if user is None or not user.lb_token_enc or not user.lb_user:
            return []
        token, name = decrypt(user.lb_token_enc), user.lb_user
    best: tuple[str, str] | None = None  # (date, playlist id)
    offset = 0
    while True:
        page = await _call("GET", f"user/{name}/playlists/createdfor", token, params={"offset": offset, "count": 50})
        lists = page.get("playlists") or []
        for item in lists:
            pl = item.get("playlist") or {}
            ext = (pl.get("extension") or {}).get("https://musicbrainz.org/doc/jspf#playlist") or {}
            patch = ((ext.get("additional_metadata") or {}).get("algorithm_metadata") or {}).get("source_patch")
            if patch != EXPLORATION:
                continue
            found = (str(pl.get("date") or ""), str(pl.get("identifier") or "").rstrip("/").rsplit("/", 1)[-1])
            if found[1] and (best is None or found[0] > best[0]):
                best = found
        offset += len(lists)
        if not lists or offset >= int(page.get("playlist_count") or 0):
            break
    if best is None:
        return []
    tracks = ((await _call("GET", f"playlist/{best[1]}", token)).get("playlist") or {}).get("track") or []
    out: list[Hit] = []
    for t in tracks:
        ident = t.get("identifier")
        mbid = str((ident[0] if isinstance(ident, list) and ident else ident) or "").rstrip("/").rsplit("/", 1)[-1]
        duration = t.get("duration")
        if t.get("title") and t.get("creator"):
            out.append(Hit(artist=str(t["creator"]), title=str(t["title"]),
                           duration_s=int(duration / 1000) if isinstance(duration, (int, float)) and duration else None,
                           source_id=mbid or None))
    return out


async def loop() -> None:
    await asyncio.sleep(240)
    while True:
        try:
            async with session_scope() as s:
                users = list((await s.execute(select(User.id).where(User.lb_token_enc.is_not(None)))).scalars())
            for user_id in users:
                sent = await sync_user(user_id)
                if sent:
                    log.info("listenbrainz: %s listens sent for user %s", sent, user_id)
        except Exception:
            log.exception("listenbrainz sync failed")
        await asyncio.sleep(LOOP_S)
