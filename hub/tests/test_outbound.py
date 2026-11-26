"""The Hub's outside requests: the proxy each goes through, and the journal of them."""

from __future__ import annotations

import httpx
from sqlalchemy import select

from app import outbound
from app.db import session_scope
from app.models import IntegrationConnection, OutboundRequest, User
from tests.test_flow import env as env
from tests.test_flow import src_mp3 as src_mp3


def test_a_host_is_of_its_service():
    assert outbound.service_of("api.music.yandex.net") == "yandex"
    assert outbound.service_of("spclient.wg.spotify.com") == "spotify"
    assert outbound.service_of("generativelanguage.googleapis.com") == "ai"
    assert outbound.service_of("oauth2.googleapis.com") == "google"
    assert outbound.service_of("rr3---sn-abc.googlevideo.com") == "youtube"
    assert outbound.service_of("cdn.example.org") == "example.org"
    for own in ("navidrome", "embedder", "127.0.0.1", "10.0.0.5", "host.docker.internal", None):
        assert outbound.service_of(own) is None


def test_the_values_of_the_query_are_not_kept():
    path = outbound.clean_path("https://api.vk.com/method/audio.get?access_token=SECRET&v=5.131&owner_id=1")
    assert path == "/method/audio.get?access_token&v&owner_id" and "SECRET" not in path


async def _admin(env):
    async with session_scope() as s:
        u = await s.scalar(select(User).limit(1))
        u.is_admin = True
        conn = IntegrationConnection(provider="yandex", owner_user_id=u.id, subject="x", account="me", plan="plus")
        s.add(conn)
        await s.flush()
        return conn.id


async def test_an_account_goes_through_its_proxy_a_service_through_its_own(env, monkeypatch):
    client = env["client"]
    conn = await _admin(env)
    a = (await client.post("/api/admin/proxies", json={"url": "socks5://me:p%40ss@10.1.1.1:1080", "name": "RU"})).json()
    b = (await client.post("/api/admin/proxies", json={"url": "1.2.3.4:3128"})).json()
    assert (a["protocol"], a["host"], a["port"], a["login"], a["has_password"]) == ("socks5", "10.1.1.1", 1080, "me", True)
    assert "password" not in a and b["protocol"] == "http"
    assert (await client.post("/api/admin/proxies", json={"url": "1.2.3.4:3128"})).status_code == 409
    assert (await client.put("/api/admin/proxies/services/yandex", json={"proxy_id": b["id"]})).status_code == 204
    assert (await client.put(f"/api/admin/proxies/accounts/{conn}", json={"proxy_id": a["id"]})).status_code == 204
    await outbound.refresh()
    assert outbound.proxy_url("yandex") == (b["id"], "http://1.2.3.4:3128")
    assert outbound.proxy_url("yandex", conn) == (a["id"], "socks5://me:p%40ss@10.1.1.1:1080")
    assert outbound.proxy_url("deezer") == (None, None)
    with outbound.account(conn):
        assert outbound.proxy_url("deezer")[0] == a["id"]  # the account's, whatever the service
    page = (await client.get("/api/admin/proxies")).json()
    assert {s["service"]: s["proxy_id"] for s in page["services"]}["yandex"] == b["id"]
    assert {x["id"]: x["proxy_id"] for x in page["accounts"]}[conn] == a["id"]
    assert (await client.delete(f"/api/admin/proxies/{b['id']}")).status_code == 204
    await outbound.refresh()
    assert outbound.proxy_url("yandex") == (None, None)  # gone with its proxy


async def test_requests_are_journaled_and_summed_up(env, monkeypatch):
    client = env["client"]
    await _admin(env)
    calls = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(429 if "limited" in request.url.path else 200, content=b"{}",
                              headers={"content-length": "2"})

    transport = outbound.RoutingTransport()
    monkeypatch.setattr(transport, "_transport", lambda url: httpx.MockTransport(answer))
    async with httpx.AsyncClient(transport=transport) as c:
        with outbound.purpose("test"):
            await c.get("https://api.deezer.com/search?q=secret&limit=1")
            await c.get("https://api.deezer.com/limited")
            await c.get("http://navidrome:4533/rest/ping")  # the Hub's own: not journaled
    assert len(calls) == 3
    await outbound.flush()
    async with session_scope() as s:
        rows = list(await s.scalars(select(OutboundRequest).where(OutboundRequest.purpose == "test").order_by(OutboundRequest.id)))
    assert [(r.service, r.path, r.status, r.bytes) for r in rows] == [
        ("deezer", "/search?q&limit", 200, 2), ("deezer", "/limited", 429, 2)]
    summary = (await client.get("/api/admin/outbound/summary?hours=1")).json()
    deezer = next(r for r in summary["services"] if r["key"] == "deezer")
    assert (deezer["requests"], deezer["errors"], deezer["limited"]) == (2, 1, 1)
    limited = (await client.get("/api/admin/outbound/requests?outcome=limited")).json()
    assert [r["path"] for r in limited["items"]] == ["/limited"]
    assert (await client.get("/api/admin/outbound/requests?service=deezer&limit=1")).json()["more"] is True


async def test_not_for_everyone(env):
    async with session_scope() as s:
        u = await s.scalar(select(User).limit(1))
        u.is_admin = False
    assert (await env["client"].get("/api/admin/proxies")).status_code == 403
    assert (await env["client"].get("/api/admin/outbound/summary")).status_code == 403


async def test_spotapi_requests_are_journaled(env, monkeypatch):
    from types import SimpleNamespace

    from spotapi.connection.http import HTTPClient

    async def answer(self, method, url, **kwargs):
        return SimpleNamespace(status_code=SimpleNamespace(as_int=lambda: 200), body=b"{}")

    monkeypatch.setattr(HTTPClient, "request", answer)
    monkeypatch.setattr(outbound, "_spotapi_hooked", False)
    outbound.install_spotapi_hook()
    with outbound.purpose("spotapi-test"):
        await HTTPClient.request(object(), "POST", "https://api-partner.spotify.com/pathfinder/v2/query?operationName=x")
    await outbound.flush()
    async with session_scope() as s:
        row = await s.scalar(select(OutboundRequest).where(OutboundRequest.purpose == "spotapi-test"))
    assert (row.service, row.method, row.path, row.status, row.bytes) == (
        "spotify", "POST", "/pathfinder/v2/query?operationName", 200, 2)


async def test_the_spotify_session_is_opened_anew_through_a_new_proxy(monkeypatch):
    from app.catalog import spotify

    closed = []

    class Old:
        async def __aexit__(self, *exc):
            closed.append(True)

    catalog = spotify.SpotifyCatalog()
    catalog._client, catalog._proxy = Old(), None
    monkeypatch.setattr(outbound, "proxy_url", lambda service, connection_id=None: (1, "http://1.2.3.4:3128"))
    opened = []

    def warm():
        opened.append(True)
        catalog._client = "new"

    monkeypatch.setattr(catalog, "warm", warm)
    assert await catalog._get() == "new" and closed == [True] and opened == [True]


def test_more_hosts_are_of_their_services():
    assert outbound.service_of("playback.media-streaming.soundcloud.cloud") == "soundcloud"
    assert outbound.service_of("eu.hitmoz.com") == "hitmoz"
    assert outbound.service_of("yt3.googleusercontent.com") == "youtube"
