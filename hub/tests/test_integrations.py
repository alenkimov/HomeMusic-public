import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import respx
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app import integrations, security
from app.api import integrations as integration_api
from app.db import run_migrations, session_scope
from app.models import IntegrationAuthAttempt, IntegrationCredential, User
from tests.support import app_client

BASE = "/api/integrations"


def test_migration_preserves_personal_sessions_and_discards_server_spotify_session(tmp_path):
    db_file = tmp_path / "upgrade.sqlite"
    cfg = Config("alembic.ini")
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file.as_posix()}")
    command.upgrade(cfg, "20a000000029")
    with sqlite3.connect(db_file) as db:
        db.execute("""
            INSERT INTO users(id, nd_username, nd_password_enc, is_admin, created_at, lb_user, lb_token_enc, lb_sent_until,
                              tg_with_lyrics)
            VALUES (1, 'owner', '', 1, '2026-11-20', 'listener', 'lb-encrypted', '2026-11-19', 0)
        """)
        db.executemany("""
            INSERT INTO imports (id, user_id, source, method, status, mode, account, token_enc, keep_token,
                                 auth_json, options_json, created_at, updated_at)
            VALUES (?, 1, ?, 'token', 'done', 'economy', 'account', ?, ?, '{}', '{}', '2026-11-20', '2026-11-20')
        """, [(1, "yandex", "ym-encrypted", 1), (2, "vk", "vk-encrypted", 0)])
        db.execute("INSERT INTO app_settings VALUES ('spotify_account', ?)",
                   (json.dumps({"cookie_enc": "sp-encrypted"}),))

    run_migrations(db_file)
    with sqlite3.connect(db_file) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("""
            SELECT provider, owner_user_id, retained, shared, state, encrypted
            FROM integration_connections JOIN integration_credentials ON connection_id = id ORDER BY provider
        """).fetchall() == [
            ("listenbrainz", 1, 1, 0, "connected", "lb-encrypted"),
            ("vk", 1, 0, 0, "connected", "vk-encrypted"),
            ("yandex", 1, 1, 0, "connected", "ym-encrypted"),
        ]
        assert db.execute("SELECT 1 FROM app_settings WHERE key = 'spotify_account'").fetchone() is None
        assert db.execute("SELECT sent_until FROM listenbrainz_sync").fetchall() == [("2026-11-19",)]
        assert db.execute("SELECT source FROM imports JOIN integration_connections ON connection_id = integration_connections.id"
                          " WHERE source = provider ORDER BY source").fetchall() == [("vk",), ("yandex",)]


async def test_connections_and_private_imports_are_scoped_to_the_owner(hub_env, monkeypatch):
    from app.main import create_app

    credential = '{"credentials":"fake-session"}'
    identity = {"subject": "spotify-guest", "plan": "free"}
    exchange = AsyncMock(return_value={**identity, "credential": credential})
    monkeypatch.setattr(integration_api.spotify_session, "exchange", exchange)
    monkeypatch.setattr(integration_api.spotify_session, "check",
                        AsyncMock(return_value={**identity, "liked_read": 3}))
    monkeypatch.setattr(integration_api, "_lastfm", AsyncMock())
    monkeypatch.setattr(integration_api.yandex, "device_start", AsyncMock(return_value={
        "device_code": "secret-device-code", "user_code": "ABCD", "verification_url": "https://ya.ru/device",
    }))
    monkeypatch.setattr(integration_api.yandex, "device_poll", AsyncMock(return_value="yandex-token"))
    yandex_client = SimpleNamespace(me=SimpleNamespace(
        account=SimpleNamespace(uid=456, login="yandex-guest"), plus=SimpleNamespace(has_plus=True),
    ))
    monkeypatch.setattr(integration_api, "yandex_client", lambda token: SimpleNamespace(init=lambda: yandex_client))

    def navidrome(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"subsonic-response": {
            "status": "ok", "user": {"adminRole": request.url.params["u"] == "admin"}
        }})

    app = create_app()
    with respx.mock(assert_all_called=False) as transport:
        transport.route(host="navidrome.test").mock(side_effect=navidrome)
        async with (
            app.router.lifespan_context(app),
            app_client(app, login=None) as helper,
            app_client(app, login=("guest", "testpw")) as guest,
            app_client(app) as admin,
        ):
            assert (await helper.get(BASE)).status_code == 401
            assert (await guest.post(BASE + "/lastfm", json={"key": "key"})).status_code == 403
            key = await admin.post(BASE + "/lastfm", json={"key": "old-key"})
            assert key.status_code == 200, key.text
            replacement = await admin.post(BASE + "/lastfm", json={"key": "new-key"})
            assert replacement.json()["id"] == key.json()["id"]
            assert await integrations.lastfm_key() == "new-key"

            granted = await guest.post(BASE + "/spotify/connect", json={"shared": False})
            ticket = {"ticket": granted.json()["ticket"]}
            assert (await admin.post(BASE + "/auth/poll", json=ticket)).status_code == 404
            authorization = urlsplit(granted.json()["authorization_url"])
            assert authorization.netloc == "accounts.spotify.com"
            params = parse_qs(authorization.query)
            assert params["state"] == [ticket["ticket"]]
            redirect = urlsplit(params["redirect_uri"][0])
            payload = {"state": ticket["ticket"], "code": "spotify-code"}
            assert (await helper.get(redirect.path, params=payload)).status_code == 404
            callback = await helper.get(redirect.path, params=payload, headers={"Host": redirect.netloc})
            assert callback.status_code == 200 and "Spotify подключён" in callback.text
            assert (await helper.get(redirect.path, params=payload, headers={"Host": redirect.netloc})).status_code == 403
            exchange.assert_awaited_once()
            received_code, verifier = exchange.await_args.args
            assert received_code == "spotify-code"
            expected = integration_api.spotify_session.authorize(ticket["ticket"], verifier)
            assert expected == granted.json()["authorization_url"]
            assert (await guest.post(BASE + "/auth/poll", json=ticket)).json() == {"stage": "connected"}

            account = next(row for row in (await guest.get(BASE)).json()["connections"] if row["provider"] == "spotify")
            url = BASE + f"/{account['id']}"
            assert account["account"] == "spotify-guest" and account["plan"] == "free" and account["can_manage"]
            assert [row["provider"] for row in (await admin.get(BASE)).json()["connections"]] == ["lastfm"]
            assert await integrations.shared_search_credential("spotify") is None
            async with session_scope() as s:
                secret = (await s.get(IntegrationCredential, account["id"])).encrypted
                assert "fake-session" not in secret and security.decrypt(secret) == credential
                owner = await s.scalar(select(User.id).where(User.nd_username == "guest"))
                vk = await integrations.save(s, provider="vk", owner_id=owner, secret="vk-session", subject="1")
            assert all(row["provider"] != "vk" for row in (await guest.get(BASE)).json()["connections"])
            assert (await guest.get("/api/imports/accounts/vk")).json()[0]["id"] == vk.id
            assert (await admin.delete(f"/api/imports/accounts/{vk.id}")).status_code == 404
            assert (await guest.delete(f"/api/imports/accounts/{vk.id}")).status_code == 200

            assert (await guest.patch(url, json={"shared": True})).json()["shared"]
            assert await integrations.shared_search_credential("spotify") == credential
            outsider = (await admin.get(BASE)).json()["connections"][-1]
            assert outsider["owner"] == "guest" and outsider["account"] is None and not outsider["can_manage"]
            assert (await admin.delete(url)).status_code == 404
            assert (await admin.post(url + "/check", json={})).status_code == 404
            assert (await guest.post(url + "/check", json={})).json()["liked_read"] == 3
            saved = {"source": "spotify", "method": "saved", "connection_id": account["id"]}
            assert (await admin.get("/api/imports/accounts/spotify")).json() == []
            assert (await admin.post("/api/imports", json=saved)).status_code == 400
            transfer = await guest.post("/api/imports", json=saved)
            assert transfer.status_code == 200 and transfer.json()["has_token"]
            assert (await guest.patch(url, json={"shared": False})).status_code == 200
            assert await integrations.shared_search_credential("spotify") is None
            assert (await guest.delete(url)).status_code == 200
            data = await guest.get(f"/api/imports/{transfer.json()['id']}")
            assert not data.json()["has_token"]

            grant = (await guest.post(BASE + "/yandex/connect", json={})).json()
            assert grant["user_code"] == "ABCD" and "secret-device-code" not in str(grant)
            async with session_scope() as s:
                current = await s.get(IntegrationAuthAttempt, security.hash_token(grant["ticket"]))
                current.next_poll_at = None
            assert (await guest.post(BASE + "/auth/poll", json={"ticket": grant["ticket"]})).json() == {"stage": "connected"}
            yandex = next(row for row in (await guest.get(BASE)).json()["connections"] if row["provider"] == "yandex")
            assert yandex["plan"] == "plus" and yandex["account"] == "yandex-guest" and not yandex["shared"]
            assert not (await admin.get("/api/imports/accounts/yandex")).json()
