"""«Отправить в Telegram»: connecting the user's own bot and sending a library track to it. Telegram is a mock."""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from tests.support import app_client

TOKEN = "123456789:AAEhBOweik6ad9r_QXMENQjcrGbqCr4K-bs"
BOT = f"https://api.telegram.org/bot{TOKEN}"


def ok(result) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


@pytest.fixture
async def env(hub_env):
    from app.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(return_value=httpx.Response(200, json={"subsonic-response": {
                "status": "ok", "version": "1.16.1", "user": {"adminRole": True}}}))
            async with app_client(app) as client:
                yield {"client": client, "mock": mock, "music": hub_env}


async def add_track(music, nd_song_id: str, size: int = 2000) -> None:
    from app.db import session_scope
    from app.matching.normalize import fingerprint
    from app.models import Track

    path = music / "library" / "Кино" / "Группа крови.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    async with session_scope() as s:
        s.add(Track(uid=str(uuid.uuid4()), path="Кино/Группа крови.mp3", nd_song_id=nd_song_id, artist="Кино",
                    title="Группа крови", duration_s=245, fingerprint=fingerprint("Кино", "Группа крови")))


async def test_connect_bind_send_and_disconnect(env):
    c, mock = env["client"], env["mock"]
    await add_track(env["music"], "nd1")
    assert (await c.get("/api/telegram")).json() == {"connected": False, "ready": False, "bot_username": None, "chat_name": None}
    # nothing is sent before a bot is connected
    assert (await c.post("/api/telegram/send", json={"nd_song_id": "nd1"})).status_code == 409

    r = await c.post("/api/telegram/connect", json={"token": "not a token"})
    assert r.status_code == 400 and "BotFather" in r.json()["detail"]

    mock.post(f"{BOT}/getMe").mock(return_value=ok({"id": 1, "username": "my_music_bot"}))
    updates = mock.post(f"{BOT}/getUpdates").mock(return_value=ok([]))
    r = await c.post("/api/telegram/connect", json={"token": f"  {TOKEN}\n"})
    assert r.status_code == 200, r.text
    assert r.json() == {"connected": True, "ready": False, "bot_username": "my_music_bot", "chat_name": None}
    assert TOKEN not in r.text  # the key never comes back

    # the user pressed Start: the last private chat is theirs (a group the bot sits in is not)
    updates.mock(return_value=ok([
        {"message": {"chat": {"id": 42, "type": "private", "first_name": "Алён", "username": "alen"}}},
        {"message": {"chat": {"id": -100, "type": "group", "title": "chat"}}},
    ]))
    r = await c.post("/api/telegram/check")
    assert r.json() == {"connected": True, "ready": True, "bot_username": "my_music_bot", "chat_name": "Алён @alen"}

    sent = mock.post(f"{BOT}/sendAudio").mock(return_value=ok({"message_id": 7}))
    r = await c.post("/api/telegram/send", json={"nd_song_id": "nd1"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "title": "Группа крови", "light": False}
    body = sent.calls.last.request.content
    assert b'name="chat_id"\r\n\r\n42' in body and "Группа крови".encode() in body and b'name="audio"' in body

    assert (await c.post("/api/telegram/send", json={"ref": "dz:404"})).status_code == 404  # not in the library

    sent.mock(return_value=httpx.Response(403, json={"ok": False, "description": "Forbidden: bot was blocked by the user"}))
    r = await c.post("/api/telegram/send", json={"nd_song_id": "nd1"})
    assert r.status_code == 502 and "Start" in r.json()["detail"]

    assert (await c.delete("/api/telegram")).json()["connected"] is False
    assert (await c.post("/api/telegram/check")).status_code == 409


async def test_a_refused_key_is_not_saved(env):
    c, mock = env["client"], env["mock"]
    mock.post(f"{BOT}/getMe").mock(return_value=httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))
    r = await c.post("/api/telegram/connect", json={"token": TOKEN})
    assert r.status_code == 400 and TOKEN not in r.text
    assert (await c.get("/api/telegram")).json()["connected"] is False
