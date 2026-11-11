"""«Отправить в Telegram»: connecting the user's own bot and sending a library track to it. The bot is a fake in
place of aiogram's (`telegram.make_bot`): it answers as Telegram would and keeps what was sent."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
import respx
from aiogram.exceptions import TelegramForbiddenError, TelegramUnauthorizedError

from tests.support import app_client

TOKEN = "123456789:AAEhBOweik6ad9r_QXMENQjcrGbqCr4K-bs"  # made up: the shape of a key, no bot behind it


class FakeBot:
    """What the Hub uses of `aiogram.Bot`. `state` is shared by the bots of a test: updates, sent, error."""

    def __init__(self, token: str, state: dict):
        self.token, self.state = token, state
        self.session = SimpleNamespace(close=self._close)

    async def _close(self) -> None:
        self.state["closed"] = self.state.get("closed", 0) + 1

    def _check(self) -> None:
        if self.token != TOKEN:
            raise TelegramUnauthorizedError(method=None, message="Unauthorized")  # type: ignore[arg-type]

    async def get_me(self):
        self._check()
        return SimpleNamespace(id=1, username="my_music_bot", first_name="Music")

    async def get_updates(self, **kw):
        self._check()
        return self.state.get("updates", [])

    async def send_audio(self, **kw):
        self._check()
        if self.state.get("blocked"):
            raise TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")  # type: ignore[arg-type]
        self.state.setdefault("sent", []).append(kw)
        return SimpleNamespace(message_id=7)

    async def send_message(self, **kw):
        self.state.setdefault("messages", []).append(kw)


def update(chat_id: int, kind: str, **chat):
    return SimpleNamespace(message=SimpleNamespace(chat=SimpleNamespace(id=chat_id, type=kind, **{
        "first_name": None, "last_name": None, "username": None, **chat})))


@pytest.fixture
async def env(hub_env, monkeypatch):
    from app import telegram
    from app.main import create_app

    state: dict = {}
    monkeypatch.setattr(telegram, "make_bot", lambda token, wait_s=30: FakeBot(token, state))
    app = create_app()
    async with app.router.lifespan_context(app):
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(return_value=httpx.Response(200, json={"subsonic-response": {
                "status": "ok", "version": "1.16.1", "user": {"adminRole": True}}}))
            async with app_client(app) as client:
                yield {"client": client, "state": state, "music": hub_env}


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
    c, state = env["client"], env["state"]
    await add_track(env["music"], "nd1")
    assert (await c.get("/api/telegram")).json() == {"connected": False, "ready": False, "bot_username": None, "chat_name": None, "with_lyrics": False}
    # nothing is sent before a bot is connected
    assert (await c.post("/api/telegram/send", json={"nd_song_id": "nd1"})).status_code == 409

    r = await c.post("/api/telegram/connect", json={"token": "not a token"})
    assert r.status_code == 400 and "BotFather" in r.json()["detail"]

    r = await c.post("/api/telegram/connect", json={"token": f"  {TOKEN}\n"})
    assert r.status_code == 200, r.text
    assert r.json() == {"connected": True, "ready": False, "bot_username": "my_music_bot", "chat_name": None, "with_lyrics": False}
    assert TOKEN not in r.text  # the key never comes back

    # the user pressed Start: the last private chat is theirs (a group the bot sits in is not)
    state["updates"] = [update(42, "private", first_name="Алён", username="alen"), update(-100, "group")]
    r = await c.post("/api/telegram/check")
    assert r.json() == {"connected": True, "ready": True, "bot_username": "my_music_bot", "chat_name": "Алён @alen", "with_lyrics": False}

    r = await c.post("/api/telegram/send", json={"nd_song_id": "nd1"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "title": "Группа крови", "light": False, "lyrics": False}
    [sent] = state["sent"]
    assert (sent["chat_id"], sent["title"], sent["performer"], sent["duration"]) == (42, "Группа крови", "Кино", 245)
    assert sent["audio"].filename == "Кино - Группа крови.mp3" and sent["thumbnail"] is None
    assert sent["caption"] is None and state["closed"] >= 4  # no text asked for; every call closed its bot's session

    # with the text: a short one is the audio's caption, collapsed; «by default» is the user's setting
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Lyrics, Track

    async with session_scope() as s:
        track = (await s.execute(select(Track))).scalar_one()
        s.add(Lyrics(track_id=track.id, kind="synced", source="lrclib", lines_json=[{"t": 0, "text": "Тёплое место"}, {"t": 9, "text": "<но>"}]))
    r = await c.post("/api/telegram/send", json={"nd_song_id": "nd1", "with_lyrics": True})
    assert r.json()["lyrics"] is True
    assert state["sent"][-1]["caption"] == "<blockquote expandable>Тёплое место\n&lt;но&gt;</blockquote>"
    assert state["sent"][-1]["parse_mode"] == "HTML"
    assert (await c.put("/api/telegram/prefs", json={"with_lyrics": True})).json()["with_lyrics"] is True
    assert (await c.post("/api/telegram/send", json={"nd_song_id": "nd1"})).json()["lyrics"] is True
    assert (await c.post("/api/telegram/send", json={"nd_song_id": "nd1", "with_lyrics": False})).json()["lyrics"] is False

    assert (await c.post("/api/telegram/send", json={"ref": "dz:404"})).status_code == 404  # not in the library

    state["blocked"] = True
    r = await c.post("/api/telegram/send", json={"nd_song_id": "nd1"})
    assert r.status_code == 502 and "Start" in r.json()["detail"]

    assert (await c.delete("/api/telegram")).json()["connected"] is False
    assert (await c.post("/api/telegram/check")).status_code == 409


async def test_a_refused_key_is_not_saved(env):
    c = env["client"]
    other = "987654321:AAEhBOweik6ad9r_QXMENQjcrGbqCr4K-bs"  # well-formed, but Telegram does not know it
    r = await c.post("/api/telegram/connect", json={"token": other})
    assert r.status_code == 400 and other not in r.text and "не принял" in r.json()["detail"]
    assert (await c.get("/api/telegram")).json()["connected"] is False
