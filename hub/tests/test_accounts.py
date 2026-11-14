"""Invites, registration, sign-in with Google and the admin's user list, against a fake Navidrome."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from datetime import timedelta

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from tests.support import app_client, filed_track

CLIENT_ID = "123-web.apps.googleusercontent.com"
CERTS = "https://www.googleapis.com/oauth2/v3/certs"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwks() -> dict:
    n = KEY.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "kid": "k1", "alg": "RS256", "use": "sig",
                      "n": _b64(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")), "e": _b64(n.e.to_bytes(3, "big"))}]}


def google_token(sub: str = "g-1", email: str = "anna.k@gmail.com", **over) -> str:
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": sub, "email": email,
              "email_verified": True, "iat": now, "exp": now + 3600, "name": "Анна", **over}
    head = _b64(json.dumps({"alg": "RS256", "kid": "k1", "typ": "JWT"}).encode())
    body = _b64(json.dumps(claims).encode())
    sig = KEY.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(sig)}"


@pytest.fixture
async def env(hub_env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PUBLIC_MUSIC_URL", "https://music.test:8444")

    from app.main import create_app

    # Navidrome: users and passwords, stars per user; "boris" uses the player only and never opened the Hub
    nd = {"users": {"admin": {"password": "adminpw", "isAdmin": True, "name": "Admin"},
                    "boris": {"password": "borispw", "isAdmin": False, "name": "Борис"}},
          "stars": {"admin": ["s1", "s2"]}, "logins": 0}

    def subsonic(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        ok = {"status": "ok", "version": "1.16.1"}
        u = nd["users"].get(p.get("u", ""))
        if u is None or hashlib.md5((u["password"] + p.get("s", "")).encode()).hexdigest() != p.get("t"):
            return httpx.Response(200, json={"subsonic-response": {"status": "failed", "error": {"code": 40, "message": "Wrong username or password"}}})
        ep = request.url.path.rsplit("/", 1)[-1]
        if ep == "getUser":
            return httpx.Response(200, json={"subsonic-response": {**ok, "user": {"adminRole": u["isAdmin"]}}})
        if ep == "getStarred2":
            songs = [{"id": i} for i in nd["stars"].get(p["u"], [])]
            return httpx.Response(200, json={"subsonic-response": {**ok, "starred2": {"song": songs}}})
        if ep == "getPlaylists":
            return httpx.Response(200, json={"subsonic-response": {**ok, "playlists": {"playlist": [{"id": "pl1"}]}}})
        return httpx.Response(200, json={"subsonic-response": ok})

    def native(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            body = json.loads(request.content)
            u = nd["users"].get(body["username"])
            if u is None or u["password"] != body["password"]:
                return httpx.Response(401, json={"error": "Invalid username or password"})
            nd["logins"] += 1
            return httpx.Response(200, json={"token": "jwt-admin"})
        if request.headers.get("x-nd-authorization") != "Bearer jwt-admin":
            return httpx.Response(401)
        if request.method == "GET":
            return httpx.Response(200, json=[
                {"id": f"nd-{name}", "userName": name, "name": u["name"], "isAdmin": u["isAdmin"],
                 "lastAccessAt": "2026-10-19T22:39:46.062261113Z" if name == "boris" else None,
                 "createdAt": "2026-10-01T10:00:00.5Z"}
                for name, u in nd["users"].items()])
        body = json.loads(request.content)
        if body["userName"] in nd["users"]:
            return httpx.Response(400, json={"errors": {"userName": "ra.validation.unique"}})
        nd["users"][body["userName"]] = {"password": body["password"], "isAdmin": body["isAdmin"], "name": body["name"]}
        return httpx.Response(200, json={"id": f"nd-{body['userName']}"})

    app = create_app()
    async with app.router.lifespan_context(app):
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test", path__startswith="/rest/").mock(side_effect=subsonic)
            mock.route(host="navidrome.test").mock(side_effect=native)
            mock.get(CERTS).mock(return_value=httpx.Response(200, json=jwks(), headers={"cache-control": "max-age=600"}))
            transport = httpx.ASGITransport(app=app)

            def client() -> httpx.AsyncClient:
                return httpx.AsyncClient(transport=transport, base_url="http://hub.test")

            async with app_client(app) as admin:
                yield {"admin": admin, "client": client, "nd": nd, "mock": mock}


async def make_invite(admin: httpx.AsyncClient, note: str = "Анна") -> dict:
    r = await admin.post("/api/admin/invites", json={"note": note})
    assert r.status_code == 200, r.text
    return r.json()


async def test_search_history_is_shared_between_sessions_and_isolated_between_accounts(env):
    from app.events import bus

    async with env["client"]() as device, env["client"]() as other:
        for client, username, password in ((device, "admin", "adminpw"), (other, "boris", "borispw")):
            r = await client.post("/api/auth/login", json={"username": username, "password": password})
            assert r.status_code == 200, r.text
        me = (await env["admin"].get("/api/auth/me")).json()
        other_me = (await other.get("/api/auth/me")).json()
        own_events = bus.subscribe(me["id"])
        other_events = bus.subscribe(other_me["id"])
        try:
            r = await env["admin"].post("/api/search/history", json={"title": "  Кино  "})
            assert r.status_code == 200, r.text
            assert json.loads(own_events.get_nowait()) == {"event": "search_history", "data": {}}
            assert other_events.empty()
            r = await device.get("/api/search/history")
            assert r.status_code == 200, r.text
            assert r.headers["cache-control"] == "no-store"
            assert r.json() == {"items": [{"key": "q:кино", "kind": "query", "title": "Кино", "subtitle": None, "ref": None,
                                           "cover_url": None, "link": None}], "frequent": []}
            assert (await other.get("/api/search/history")).json()["items"] == []
            r = await other.post("/api/search/history", json={"title": "Земфира"})
            assert r.status_code == 200, r.text
            r = await device.delete("/api/search/history")
            assert r.status_code == 200, r.text
            assert (await env["admin"].get("/api/search/history")).json()["items"] == []
            assert recent(await other.get("/api/search/history")) == ["Земфира"]
            assert json.loads(own_events.get_nowait()) == {"event": "search_history", "data": {}}
        finally:
            bus.unsubscribe(me["id"], own_events)
            bus.unsubscribe(other_me["id"], other_events)


def recent(r) -> list[str]:
    """«Недавнее» as the Hub gave it: the titles, the newest first."""
    return [x["title"] for x in r.json()["items"]]


async def test_search_history_keeps_thirty_recent_queries_and_moves_repeats_to_top(env):
    from app.db import session_scope
    from app.models import SearchHistory, utcnow

    admin = env["admin"]
    user_id = (await admin.get("/api/auth/me")).json()["id"]
    now = utcnow()
    async with session_scope() as s:
        s.add_all(SearchHistory(user_id=user_id, key=f"q:запрос {i}", kind="query", title=f"Запрос {i}",
                                searched_at=now - timedelta(seconds=30 - i)) for i in range(30))
    r = await admin.post("/api/search/history", json={"title": "Новый запрос"})
    assert r.status_code == 200, r.text
    queries = recent(await admin.get("/api/search/history"))
    assert queries == ["Новый запрос", *[f"Запрос {i}" for i in range(29, 0, -1)]]
    r = await admin.post("/api/search/history", json={"title": "  ЗАПРОС 15  "})
    assert r.status_code == 200, r.text
    assert recent(await admin.get("/api/search/history")) == [
        "ЗАПРОС 15", *[q for q in queries if q != "Запрос 15"]
    ]


async def test_concurrent_search_history_writes_do_not_lose_queries(env):
    async with env["client"]() as device:
        r = await device.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status_code == 200, r.text
        results = await asyncio.gather(
            env["admin"].post("/api/search/history", json={"title": "Кино"}),
            device.post("/api/search/history", json={"title": "Земфира"}),
        )
        for r in results:
            assert r.status_code == 200, r.text
        queries = recent(await device.get("/api/search/history"))
        assert len(queries) == 2 and set(queries) == {"Кино", "Земфира"}


@pytest.mark.parametrize("query", ["", " ", "я", " a ", "a" * 513])
async def test_search_history_rejects_invalid_queries(env, query):
    r = await env["admin"].post("/api/search/history", json={"title": query})
    assert r.status_code == 422, r.text
    assert (await env["admin"].get("/api/search/history")).json()["items"] == []


async def test_what_was_opened_from_the_results_is_kept_once_and_can_be_taken_out(env):
    """A result that was opened is a line of its own, with its picture and the page it leads to; opened again, it
    only moves up. A line is taken out by its key."""
    admin = env["admin"]
    track = {"kind": "track", "ref": "dz:100", "title": "Still Loving You", "subtitle": "Scorpions",
             "cover_url": "https://covers.test/1.jpg", "link": "/album/dz%3A9"}
    assert (await admin.post("/api/search/history", json=track)).status_code == 200
    assert (await admin.post("/api/search/history", json={"kind": "artist", "ref": "dz:7", "title": "Scorpions"})).status_code == 200
    assert (await admin.post("/api/search/history", json={"title": "still loving you"})).status_code == 200
    assert (await admin.post("/api/search/history", json=track)).status_code == 200
    items = (await admin.get("/api/search/history")).json()["items"]
    assert [(x["kind"], x["title"]) for x in items] == [("track", "Still Loving You"), ("query", "still loving you"), ("artist", "Scorpions")]
    assert items[0] == {"key": "track:dz:100", **track}
    # what is opened is known by its ref; a link is a page of the app
    assert (await admin.post("/api/search/history", json={"kind": "album", "title": "x"})).status_code == 422
    assert (await admin.post("/api/search/history", json={**track, "link": "https://elsewhere.test/"})).status_code == 422
    assert (await admin.delete("/api/search/history", params={"key": "track:dz:100"})).status_code == 200
    assert recent(await admin.get("/api/search/history")) == ["still loving you", "Scorpions"]


async def test_what_is_come_back_to_is_searched_often(env):
    """«Вы часто ищете»: what was opened or asked for more than once, the most often first; once is not often."""
    admin = env["admin"]
    for title, times in (("Vaundy", 3), ("wowaka", 2), ("раз", 1)):
        for _ in range(times):
            assert (await admin.post("/api/search/history", json={"kind": "artist", "ref": f"a:{title}", "title": title})).status_code == 200
    out = (await admin.get("/api/search/history")).json()
    assert [x["title"] for x in out["frequent"]] == ["Vaundy", "wowaka"]
    assert [x["title"] for x in out["items"]] == ["раз", "wowaka", "Vaundy"]
    assert (await admin.delete("/api/search/history", params={"key": "artist:a:Vaundy"})).status_code == 200
    assert [x["title"] for x in (await admin.get("/api/search/history")).json()["frequent"]] == ["wowaka"]


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_search_history_requires_authentication(env, method):
    async with env["client"]() as anonymous:
        r = await anonymous.request(method, "/api/search/history", json={"title": "Кино"})
        assert r.status_code == 401, r.text


async def test_options_tell_about_google(env, monkeypatch):
    async with env["client"]() as c:
        r = await c.get("/api/auth/options")
        assert r.json() == {"invites": True, "email_signup": False, "google_client_id": CLIENT_ID,
                            "music_url": "https://music.test:8444"}
        from app import config

        monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
        config.get_settings.cache_clear()
        assert (await c.get("/api/auth/options")).json()["google_client_id"] is None
        r = await c.post("/api/auth/google", json={"id_token": google_token()})
        assert r.status_code == 404


async def test_register_with_invite(env):
    inv = await make_invite(env["admin"])
    assert inv["status"] == "active" and len(inv["code"]) == 9 and inv["code"][4] == "-"
    async with env["client"]() as c:
        # typed by hand: lower case, spaces, no dash
        r = await c.post("/api/auth/register",
                         json={"code": f" {inv['code'].replace('-', ' ').lower()} ", "username": "anna", "password": "secret1"})
        assert r.status_code == 200, r.text
        assert r.json()["username"] == "anna" and not r.json()["is_admin"]
        assert (await c.get("/api/auth/me")).json()["username"] == "anna"
        # the Navidrome user carries the invite's note as its name
        assert env["nd"]["users"]["anna"] == {"password": "secret1", "isAdmin": False, "name": "Анна"}
        # the code is spent
        r = await c.post("/api/auth/register", json={"code": inv["code"], "username": "anna2", "password": "secret1"})
        assert r.status_code == 400
        # a regular user is not an admin
        assert (await c.get("/api/admin/invites")).status_code == 403
        assert (await c.get("/api/admin/users")).status_code == 403
    listed = (await env["admin"].get("/api/admin/invites")).json()
    assert listed[0]["status"] == "used" and listed[0]["used_by"] == "anna"
    assert (await env["admin"].delete(f"/api/admin/invites/{inv['id']}")).status_code == 409


async def test_register_rejects_bad_input_and_keeps_the_code(env):
    inv = await make_invite(env["admin"])
    async with env["client"]() as c:
        for body, status in [
            ({"code": "WRONG-CODE", "username": "anna", "password": "secret1"}, 400),
            ({"code": inv["code"], "username": "ан на", "password": "secret1"}, 400),
            ({"code": inv["code"], "username": "anna", "password": "123"}, 400),
            ({"code": inv["code"], "username": "boris", "password": "secret1"}, 409),  # taken in Navidrome
            ({"code": inv["code"], "username": "Admin", "password": "secret1"}, 409),  # taken in the Hub
        ]:
            r = await c.post("/api/auth/register", json=body)
            assert r.status_code == status, (body, r.text)
        # nothing above spent the code; Cyrillic names are fine
        r = await c.post("/api/auth/register", json={"code": inv["code"], "username": "Вера", "password": "secret1"})
        assert r.status_code == 200, r.text


async def test_expired_and_deleted_invites(env):
    from sqlalchemy import update

    from app.db import session_scope
    from app.models import Invite, utcnow

    old = await make_invite(env["admin"], "старое")
    gone = await make_invite(env["admin"], "отозванное")
    async with session_scope() as s:
        await s.execute(update(Invite).where(Invite.id == old["id"]).values(expires_at=utcnow() - timedelta(hours=1)))
    assert (await env["admin"].delete(f"/api/admin/invites/{gone['id']}")).json() == {"ok": True}
    statuses = {i["note"]: i["status"] for i in (await env["admin"].get("/api/admin/invites")).json()}
    assert statuses == {"старое": "expired"}
    async with env["client"]() as c:
        for inv in (old, gone):
            r = await c.post("/api/auth/register", json={"code": inv["code"], "username": "anna", "password": "secret1"})
            assert r.status_code == 400


async def test_guessing_codes_is_limited(env):
    async with env["client"]() as c:
        for _ in range(30):
            await c.post("/api/auth/register", json={"code": "AAAA-AAAA", "username": "x1", "password": "secret1"})
        r = await c.post("/api/auth/register", json={"code": "AAAA-AAAA", "username": "x1", "password": "secret1"})
        assert r.status_code == 429


@pytest.fixture
def letters(env, monkeypatch):
    """Email registration on, the letters it sends (no SMTP); `letters.deliver.fail` makes sending fail."""
    from app import config, mailer

    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_USER", "homemusic@mail.test")
    config.get_settings.cache_clear()

    class Sent(list):
        pass

    sent = Sent()

    def deliver(msg):
        if getattr(deliver, "fail", False):
            raise OSError("connection refused")
        sent.append(msg)

    monkeypatch.setattr(mailer, "_deliver", deliver)
    sent.deliver = deliver
    return sent


def code_of(letter) -> str:
    import re

    return re.search(r"\b(\d{6})\b", letter.get_content()).group(1)


def other_code(code: str) -> str:
    return "000000" if code != "000000" else "111111"


async def test_register_by_email(env, letters):
    async with env["client"]() as c:
        assert (await c.get("/api/auth/options")).json()["email_signup"] is True
        r = await c.post("/api/auth/email/code", json={"email": " Anna.K@Mail.test "})
        assert r.status_code == 200, r.text
        assert r.json() == {"resend_in": 60}
        letter = letters[-1]
        assert letter["To"] == "anna.k@mail.test" and letter["From"] == "HomeMusic <homemusic@mail.test>"
        code = code_of(letter)
        assert code in letter["Subject"]
        r = await c.post("/api/auth/email/register", json={"email": "anna.k@mail.test", "code": other_code(code),
                                                           "username": "anna", "password": "secret1"})
        assert r.status_code == 400 and "осталось попыток — 4" in r.json()["detail"]
        # typed by hand: spaces in the code, the address in another case
        r = await c.post("/api/auth/email/register", json={"email": "ANNA.K@mail.test", "code": f" {code[:3]} {code[3:]} ",
                                                           "username": "anna", "password": "secret1"})
        assert r.status_code == 200, r.text
        assert r.json()["username"] == "anna" and r.json()["email"] == "anna.k@mail.test"
        assert env["nd"]["users"]["anna"] == {"password": "secret1", "isAdmin": False, "name": "anna"}
        assert (await c.get("/api/auth/me")).json()["email"] == "anna.k@mail.test"
        # the letter is spent, and the address has an account now
        r = await c.post("/api/auth/email/register",
                         json={"email": "anna.k@mail.test", "code": code, "username": "anna2", "password": "secret1"})
        assert r.status_code == 400
        assert (await c.post("/api/auth/email/code", json={"email": "anna.k@mail.test"})).status_code == 409
    async with env["client"]() as c:  # sign-in by the email instead of the name
        r = await c.post("/api/auth/login", json={"username": "Anna.K@mail.test", "password": "secret1"})
        assert r.status_code == 200 and r.json()["username"] == "anna"
        r = await c.post("/api/auth/login", json={"username": "anna.k@mail.test", "password": "wrong1"})
        assert r.status_code == 401
    users = {u["username"]: u for u in (await env["admin"].get("/api/admin/users")).json()["users"]}
    assert users["anna"]["email"] == "anna.k@mail.test" and users["anna"]["invite_note"] is None


async def test_email_registration_keeps_the_code_on_bad_input(env, letters):
    async with env["client"]() as c:
        await c.post("/api/auth/email/code", json={"email": "gleb@mail.test"})
        code = code_of(letters[-1])
        for body, status in [
            ({"username": "ан на", "password": "secret1"}, 400),
            ({"username": "gleb", "password": "123"}, 400),
            ({"username": "boris", "password": "secret1"}, 409),  # taken in Navidrome
            ({"username": "Admin", "password": "secret1"}, 409),  # taken in the Hub
        ]:
            r = await c.post("/api/auth/email/register", json={"email": "gleb@mail.test", "code": code, **body})
            assert r.status_code == status, (body, r.text)
        r = await c.post("/api/auth/email/register",
                         json={"email": "gleb@mail.test", "code": code, "username": "Глеб", "password": "secret1"})
        assert r.status_code == 200, r.text


async def test_email_codes_are_limited(env, letters, monkeypatch):
    from sqlalchemy import select, update

    from app import config
    from app.db import session_scope
    from app.models import EmailCode, utcnow

    async def age(minutes: int) -> None:
        async with session_scope() as s:  # in Python: SQLite has no arithmetic on these datetimes
            for row in (await s.execute(select(EmailCode))).scalars():
                row.created_at -= timedelta(minutes=minutes)

    def reg(email: str, code: str) -> dict:
        return {"email": email, "code": code, "username": "aa", "password": "secret1"}

    async with env["client"]() as c:
        for bad in ["", "anna", "anna@", "@mail.test", "an na@mail.test"]:
            assert (await c.post("/api/auth/email/code", json={"email": bad})).status_code == 400, bad
        assert (await c.post("/api/auth/email/code", json={"email": "a@mail.test"})).status_code == 200
        r = await c.post("/api/auth/email/code", json={"email": "a@mail.test"})
        assert r.status_code == 429 and "через" in r.json()["detail"]
        first = code_of(letters[-1])
        await age(2)
        assert (await c.post("/api/auth/email/code", json={"email": "a@mail.test"})).status_code == 200
        second = code_of(letters[-1])
        # only the latest letter counts: the first one's code is a wrong code now (attempt 1 of 5)
        wrong = first if first != second else other_code(second)
        assert (await c.post("/api/auth/email/register", json=reg("a@mail.test", wrong))).status_code == 400
        # five wrong codes kill the letter: the right one no longer works
        for _ in range(4):
            r = await c.post("/api/auth/email/register", json=reg("a@mail.test", other_code(second)))
        assert "Запросите новое письмо" in r.json()["detail"]
        assert (await c.post("/api/auth/email/register", json=reg("a@mail.test", second))).status_code == 400
        # at most five letters to an address an hour
        for _ in range(3):
            await age(2)
            assert (await c.post("/api/auth/email/code", json={"email": "a@mail.test"})).status_code == 200
        await age(2)
        r = await c.post("/api/auth/email/code", json={"email": "a@mail.test"})
        assert r.status_code == 429 and "через час" in r.json()["detail"]
        # an expired code
        assert (await c.post("/api/auth/email/code", json={"email": "b@mail.test"})).status_code == 200
        code = code_of(letters[-1])
        async with session_scope() as s:
            await s.execute(update(EmailCode).where(EmailCode.email == "b@mail.test")
                            .values(expires_at=utcnow() - timedelta(minutes=1)))
        r = await c.post("/api/auth/email/register", json=reg("b@mail.test", code))
        assert r.status_code == 400 and "устарел" in r.json()["detail"]
        # a letter that did not go out: no wait for another one
        letters.deliver.fail = True
        assert (await c.post("/api/auth/email/code", json={"email": "c@mail.test"})).status_code == 502
        letters.deliver.fail = False
        assert (await c.post("/api/auth/email/code", json={"email": "c@mail.test"})).status_code == 200
        # the daily limit of the mailbox
        monkeypatch.setenv("EMAIL_DAILY_LIMIT", "7")  # as many as went out above
        config.get_settings.cache_clear()
        r = await c.post("/api/auth/email/code", json={"email": "d@mail.test"})
        assert r.status_code == 503 and "завтра" in r.json()["detail"]
    assert len(letters) == 7


async def test_email_registration_needs_smtp(env):
    async with env["client"]() as c:
        assert (await c.post("/api/auth/email/code", json={"email": "a@mail.test"})).status_code == 404
        r = await c.post("/api/auth/email/register",
                         json={"email": "a@mail.test", "code": "123456", "username": "aa", "password": "secret1"})
        assert r.status_code == 404


async def test_old_email_codes_are_purged(env, letters):
    from sqlalchemy import func, select

    from app.accounts import purge_email_codes
    from app.db import session_scope
    from app.models import EmailCode

    async with env["client"]() as c:
        for e in ("a@mail.test", "b@mail.test"):
            await c.post("/api/auth/email/code", json={"email": e})
    async with session_scope() as s:
        old = (await s.execute(select(EmailCode).where(EmailCode.email == "a@mail.test"))).scalar_one()
        old.created_at -= timedelta(days=3)
    assert await purge_email_codes() == 1
    async with session_scope() as s:
        assert (await s.execute(select(func.count(EmailCode.id)))).scalar_one() == 1


async def test_google_sign_in_registers_with_invite_then_signs_in(env):
    async with env["client"]() as c:
        r = await c.post("/api/auth/google", json={"id_token": google_token()})
        assert r.status_code == 409, r.text  # not linked, no code
        inv = await make_invite(env["admin"])
        r = await c.post("/api/auth/google", json={"id_token": google_token(), "code": inv["code"]})
        assert r.status_code == 200, r.text
        out = r.json()
        # the name comes from the email when the app sends none; the password works for the player
        assert out["username"] == "anna.k" and out["google_email"] == "anna.k@gmail.com"
        assert env["nd"]["users"]["anna.k"]["password"] == out["password"]
    async with env["client"]() as c:  # another phone: the same account, no code needed
        r = await c.post("/api/auth/google", json={"id_token": google_token()})
        assert r.status_code == 200, r.text
        assert r.json()["username"] == "anna.k" and r.json()["password"] == out["password"]
        assert (await c.get("/api/auth/me")).json()["username"] == "anna.k"


async def test_google_tokens_are_checked(env):
    inv = await make_invite(env["admin"])
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    good = google_token()
    head, body, _ = good.split(".")
    forged = f"{head}.{body}.{_b64(other.sign(f'{head}.{body}'.encode(), padding.PKCS1v15(), hashes.SHA256()))}"
    async with env["client"]() as c:
        for token in [forged, google_token(aud="someone-else"), google_token(exp=int(time.time()) - 3600),
                      google_token(iss="evil.example"), google_token(email_verified=False), "not-a-token"]:
            r = await c.post("/api/auth/google", json={"id_token": token, "code": inv["code"]})
            assert r.status_code == 401, (token, r.text)
    statuses = {i["id"]: i["status"] for i in (await env["admin"].get("/api/admin/invites")).json()}
    assert statuses[inv["id"]] == "active"


async def test_link_google_to_a_password_account(env):
    admin = env["admin"]
    r = await admin.post("/api/auth/google/link", json={"id_token": google_token(sub="g-admin", email="boss@gmail.com")})
    assert r.status_code == 200 and r.json()["google_email"] == "boss@gmail.com"
    async with env["client"]() as c:
        r = await c.post("/api/auth/google", json={"id_token": google_token(sub="g-admin")})
        assert r.status_code == 200 and r.json()["username"] == "admin" and r.json()["password"] == "adminpw"
        assert r.json()["is_admin"]
    # the same Google account can't go to a second user
    inv = await make_invite(admin)
    async with env["client"]() as c:
        r = await c.post("/api/auth/register", json={"code": inv["code"], "username": "anna", "password": "secret1"})
        assert r.status_code == 200
        r = await c.post("/api/auth/google/link", json={"id_token": google_token(sub="g-admin")})
        assert r.status_code == 409
    assert (await admin.delete("/api/auth/google/link")).json()["google_email"] is None
    async with env["client"]() as c:
        assert (await c.post("/api/auth/google", json={"id_token": google_token(sub="g-admin")})).status_code == 409


async def test_google_account_whose_password_changed(env):
    await env["admin"].post("/api/auth/google/link", json={"id_token": google_token(sub="g-admin")})
    env["nd"]["users"]["admin"]["password"] = "changed"
    async with env["client"]() as c:
        r = await c.post("/api/auth/google", json={"id_token": google_token(sub="g-admin")})
        assert r.status_code == 401 and "привяжите Google снова" in r.json()["detail"]


async def test_admin_user_list_with_spending_and_songs(env):
    from app.db import session_scope
    from app.models import AiUsage, DownloadBatch, DownloadItem, User, utcnow

    inv = await make_invite(env["admin"], "Анна")
    async with env["client"]() as c:
        await c.post("/api/auth/register", json={"code": inv["code"], "username": "anna", "password": "secret1"})
    async with session_scope() as s:
        from sqlalchemy import select

        admin = (await s.execute(select(User).where(User.nd_username == "admin"))).scalar_one()
        anna = (await s.execute(select(User).where(User.nd_username == "anna"))).scalar_one()
        s.add_all([
            AiUsage(user_id=anna.id, kind="chat", tokens_in=1000, tokens_out=100, cost_usd=0.0015),
            AiUsage(user_id=anna.id, kind="images", tokens_in=5000, tokens_out=300, cost_usd=0.002),
            AiUsage(user_id=anna.id, kind="chat", tokens_in=10, tokens_out=10, cost_usd=0.5,
                    created_at=utcnow() - timedelta(days=45)),
            AiUsage(user_id=admin.id, kind="chat", tokens_in=10, tokens_out=10, cost_usd=0.01),
        ])
        batch = DownloadBatch(user_id=anna.id, origin="search")
        s.add(batch)
        await s.flush()
        for status in ("done", "done", "failed", "queued"):
            s.add(DownloadItem(batch_id=batch.id, user_id=anna.id, status=status,
                               query_json={"query": {"artist": "Кино", "title": "Группа крови"}}))
        s.add(filed_track(uid="t1", path="a/1.mp3", artist="Кино", title="Группа крови", fingerprint="кино|группа крови",
                    added_by=anna.id))

    out = (await env["admin"].get("/api/admin/users")).json()
    by = {u["username"]: u for u in out["users"]}
    assert set(by) == {"admin", "anna", "boris"}
    m = by["anna"]
    assert m["ai_requests"] == 3 and m["ai_cost_usd"] == pytest.approx(0.5035)
    assert m["ai_cost_30d_usd"] == pytest.approx(0.0035)
    assert (m["downloads_done"], m["downloads_failed"], m["downloads_active"], m["tracks_added"]) == (2, 1, 1, 1)
    assert m["invite_note"] == "Анна" and m["name"] == "Анна" and m["in_hub"]
    assert m["starred_songs"] == 0 and m["playlists"] == 1 and m["last_seen_at"]
    assert by["admin"]["starred_songs"] == 2 and by["admin"]["is_admin"]
    # boris never opened the Hub: Navidrome's data only
    p = by["boris"]
    assert p["id"] is None and not p["in_hub"] and p["name"] == "Борис"
    assert p["player_seen_at"].startswith("2026-10-19T22:39:46.062261")
    assert out["ai_cost_usd"] == pytest.approx(0.5135) and out["ai_cost_30d_usd"] == pytest.approx(0.0135)
    assert out["library_tracks"] == 1

    d = (await env["admin"].get(f"/api/admin/users/{m['id']}")).json()
    assert [x["requests"] for x in d["ai_months"]] == [2, 1]
    assert [x["kind"] for x in d["ai_recent"]] == ["chat", "images", "chat"]
    assert len(d["recent_downloads"]) == 4 and d["recent_downloads"][0]["title"] == "Группа крови"
    assert (await env["admin"].get("/api/admin/users/999")).status_code == 404
    # the admin token is reused, not fetched for every call
    assert env["nd"]["logins"] == 1


async def test_user_list_survives_navidrome_admin_api_failure(env):
    env["nd"]["users"]["admin"]["password"] = "rotated"  # the Hub's admin password no longer works
    from app.library.navidrome import NavidromeAdmin

    NavidromeAdmin.forget_token()
    r = await env["admin"].get("/api/admin/users")
    assert r.status_code == 200
    assert [u["username"] for u in r.json()["users"]] == ["admin"]
    assert r.json()["users"][0]["starred_songs"] is None


async def test_chat_turn_records_spending(env, monkeypatch):
    from app import config

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    config.get_settings.cache_clear()
    reply = {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
             "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Привет"}}],
             "usage": {"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240, "cost": 0.00042}}
    route = env["mock"].post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=reply))
    r = await env["admin"].post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "привет"}]})
    assert r.status_code == 200, r.text
    assert r.json()["cost_usd"] == pytest.approx(0.00042)  # OpenRouter's own figure, not the configured price
    assert json.loads(route.calls[0].request.content)["usage"] == {"include": True}
    me = next(u for u in (await env["admin"].get("/api/admin/users")).json()["users"] if u["username"] == "admin")
    assert me["ai_requests"] == 1 and me["ai_cost_usd"] == pytest.approx(0.00042)

async def test_files_are_looked_after_by_who_brought_them(env):
    """«Мои файлы»: a listener sees and changes what they brought, an admin everything; an album with tracks of
    several listeners is changed only by an admin."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Album, User

    inv = await make_invite(env["admin"], "Анна")
    async with env["client"]() as anna:
        await anna.post("/api/auth/register", json={"code": inv["code"], "username": "anna", "password": "secret1"})
        async with session_scope() as s:
            admin_id = (await s.execute(select(User.id).where(User.nd_username == "admin"))).scalar_one()
            anna_id = (await s.execute(select(User.id).where(User.nd_username == "anna"))).scalar_one()
            hers = Album(uid="a-hers", title="Её", album_artist="Анна", created_by=anna_id)
            his = Album(uid="a-his", title="Его", album_artist="Админ", created_by=admin_id)
            both = Album(uid="a-both", title="Общий", album_artist="Оба", created_by=admin_id)
            s.add_all([hers, his, both])
            await s.flush()
            tracks = [
                filed_track(uid="f1", artist="Анна", title="Раз", fingerprint="анна|раз", album_id=hers.id, added_by=anna_id),
                filed_track(uid="f2", artist="Админ", title="Два", fingerprint="админ|два", album_id=his.id, added_by=admin_id),
                filed_track(uid="f3", artist="Оба", title="Три", fingerprint="оба|три", album_id=both.id, added_by=admin_id),
                filed_track(uid="f4", artist="Оба", title="Четыре", fingerprint="оба|четыре", album_id=both.id, added_by=anna_id),
            ]
            s.add_all(tracks)
            await s.flush()
            ids = {"hers": hers.id, "his": his.id, "both": both.id, "t_hers": tracks[0].id, "t_his": tracks[1].id}
            # he keeps his track in a playlist of his, she keeps hers in «Любимое» and in a playlist, and so does he
            from app import playlists as lists

            await lists.add(s, await lists.create(s, admin_id, "Его плейлист"), [tracks[1].id, tracks[3].id])
            await lists.add(s, await lists.liked(s, anna_id), [tracks[3].id])
            ids["t_shared"], ids["t_free"] = tracks[3].id, tracks[2].id

        mine = {a["title"]: a for a in (await anna.get("/api/library/albums")).json()}
        assert set(mine) == {"Её", "Общий"}
        assert mine["Её"]["can_edit"] and mine["Её"]["track_count"] == 1
        assert not mine["Общий"]["can_edit"] and mine["Общий"]["track_count"] == 1  # her track of it only
        assert [t["title"] for t in (await anna.get(f"/api/library/albums/{ids['both']}")).json()["tracks"]] == ["Четыре"]
        assert (await anna.get(f"/api/library/albums/{ids['his']}")).status_code == 404
        # a name is everybody's to put right (app/library/edits.py) — an album's and a track's, whoever brought the
        # files; deleting them and moving them about stays with who brought them
        assert (await anna.patch(f"/api/library/albums/{ids['both']}", json={"title": "Общий"})).status_code == 200
        r = await anna.patch(f"/api/library/tracks/{ids['t_his']}", json={"title": "Его трек"})
        assert r.status_code == 200 and r.json()["title"] == "Его трек"
        # a file is deleted by the one who alone keeps the track (library/access.py), whoever brought it:
        # his track is kept by him — she cannot; hers that he keeps too — she cannot either, though she brought it
        k = (await anna.get(f"/api/library/tracks/{ids['t_his']}/keeping")).json()
        assert (k["can_delete"], k["others"], k["mine"]) == (False, 1, [])
        assert (await anna.delete(f"/api/library/tracks/{ids['t_his']}")).status_code == 409
        k = (await anna.get(f"/api/library/tracks/{ids['t_shared']}/keeping")).json()
        assert (k["can_delete"], k["others"], [m["liked"] for m in k["mine"]]) == (False, 1, [True])
        assert (await anna.delete(f"/api/library/tracks/{ids['t_shared']}")).status_code == 409
        # one he brought and nobody keeps: hers to delete
        assert (await anna.get(f"/api/library/tracks/{ids['t_free']}/keeping")).json()["can_delete"] is True
        assert (await anna.delete(f"/api/library/tracks/{ids['t_free']}")).status_code == 200
        assert (await anna.post("/api/library/albums/merge", json={"target_id": ids["hers"], "source_ids": [ids["his"]]})).status_code == 404
        assert (await anna.delete(f"/api/library/tracks/{ids['t_hers']}")).status_code == 200

    everything = {a["title"]: a for a in (await env["admin"].get("/api/library/albums")).json()}
    assert {"Его", "Общий"} <= set(everything) and everything["Общий"]["track_count"] == 1  # «Три» she deleted
    assert all(a["can_edit"] for a in everything.values())
    # what she could not delete is still there
    assert len((await env["admin"].get(f"/api/library/albums/{ids['his']}")).json()["tracks"]) == 1
