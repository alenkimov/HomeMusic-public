"""«Отправить в Telegram»: pairing accounts and sending tracks through the shared bot. The bot is a fake in
place of aiogram's (`telegram.make_bot`): it answers as Telegram would and keeps what was sent."""

from __future__ import annotations

import uuid
from itertools import count
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError
from aiogram.types import Update

from tests.support import app_client, filed_track

TOKEN = "123456789:made-up-key-of-no-bot"  # made up: the shape of a key, no bot behind it
BOT_ID = 123456789
UPDATES = count(1)


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
        return SimpleNamespace(id=BOT_ID, username="my_music_bot", first_name="Music")

    async def get_updates(self, **kw):
        self._check()
        self.state["allowed_updates"] = kw["allowed_updates"]
        return [item for item in self.state.get("updates", []) if item.update_id >= (kw.get("offset") or 0)]

    async def send_audio(self, **kw):
        self._check()
        if self.state.get("blocked"):
            raise TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")  # type: ignore[arg-type]
        self.state.setdefault("sent", []).append(kw)
        return SimpleNamespace(message_id=7)

    async def send_message(self, **kw):
        self.state.setdefault("messages", []).append(kw)

    async def answer_callback_query(self, **kw):
        self.state.setdefault("callback_answers", []).append(kw)

    async def get_chat(self, chat_id, **kw):
        if chat_id > 0:
            self.state.setdefault("profile_checks", []).append(chat_id)
            return SimpleNamespace(username=self.state.get("usernames", {}).get(chat_id))
        return self.state.get("chats", {}).get(chat_id, SimpleNamespace(title=None, photo=None))

    async def get_chat_member(self, chat_id, user_id):
        who = self.state.get("members", {}).get((chat_id, user_id))
        if who is None:
            raise TelegramBadRequest(method=None, message="Member not found")  # type: ignore[arg-type]
        return who

    async def download(self, file_id, destination):
        destination.write(f"jpeg of {file_id}".encode())


def message(chat_id: int, kind: str = "private", *, text: str = "", sender: int | None = None, **chat):
    return SimpleNamespace(update_id=next(UPDATES), my_chat_member=None, callback_query=None, message=SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type=kind, **{"first_name": None, "last_name": None, "username": None, **chat}),
        from_user=SimpleNamespace(id=sender if sender is not None else chat_id, is_bot=False),
        text=text, migrate_to_chat_id=None))


def membership(status: str, **rights):
    return SimpleNamespace(status=status, **rights)


def member(chat_id: int, kind: str, status: str, *, title: str = "Друзья", **rights):
    chat = SimpleNamespace(id=chat_id, type=kind, title=title, first_name=None, last_name=None, username=None)
    return SimpleNamespace(update_id=next(UPDATES), message=None, callback_query=None, my_chat_member=SimpleNamespace(
        chat=chat, new_chat_member=membership(status, **rights)))


@pytest.fixture
async def env(hub_env, monkeypatch):
    from app import telegram, telegram_chats
    from app.config import get_settings
    from app.main import create_app

    state: dict = {}
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    get_settings.cache_clear()
    monkeypatch.setattr(telegram, "_shared", None)
    monkeypatch.setattr(telegram_chats, "_offsets", {})
    monkeypatch.setattr(telegram, "make_bot", lambda token, wait_s=30: FakeBot(token, state))
    app = create_app()
    async with app.router.lifespan_context(app):
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(return_value=httpx.Response(200, json={"subsonic-response": {
                "status": "ok", "version": "1.16.1", "user": {"adminRole": True}}}))
            async with app_client(app) as client:
                yield {"client": client, "state": state, "music": hub_env, "app": app}


async def add_track(music, nd_song_id: str, size: int = 2000) -> None:
    from app.db import session_scope
    from app.matching.normalize import fingerprint

    path = music / "library" / "Кино" / "Группа крови.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    async with session_scope() as s:
        s.add(filed_track(uid=str(uuid.uuid4()), path="Кино/Группа крови.mp3", nd_song_id=nd_song_id, artist="Кино",
                    title="Группа крови", duration_s=245, fingerprint=fingerprint("Кино", "Группа крови")))


async def code_for(c: httpx.AsyncClient) -> str:
    r = await c.post("/api/telegram/link")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store" and TOKEN not in r.text
    url = urlsplit(r.json()["start_url"])
    assert url.hostname == "t.me" and url.path == "/my_music_bot"
    return parse_qs(url.query)["start"][0]


async def pair(c: httpx.AsyncClient, state: dict, chat_id: int, **name) -> None:
    code = await code_for(c)
    state.setdefault("usernames", {})[chat_id] = name.get("username")
    state.setdefault("updates", []).append(message(chat_id, text=f"/start {code}", **name))
    assert (await c.post("/api/telegram/check")).json()["ready"]


async def test_link_send_and_disconnect(env):
    c, state = env["client"], env["state"]
    await add_track(env["music"], "nd1")
    assert (await c.get("/api/telegram")).json() == {
        "configured": True, "ready": False, "bot_username": "my_music_bot", "chat_name": None, "with_lyrics": False}
    assert (await c.post("/api/telegram/send", json={"nd_song_id": "nd1"})).status_code == 409

    state["updates"] = [message(7, text="/start")]
    assert not (await c.post("/api/telegram/check")).json()["ready"]
    await pair(c, state, 42, first_name="Алён", username="alen")
    assert (await c.get("/api/telegram")).json()["chat_name"] == "Алён @alen"

    r = await c.post("/api/telegram/send", json={"nd_song_id": "nd1"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "title": "Группа крови", "version_label": None, "light": False, "lyrics": False}
    [sent] = state["sent"]
    assert (sent["chat_id"], sent["title"], sent["performer"], sent["duration"]) == (42, "Группа крови", "Кино", 245)
    assert sent["audio"].filename == "Кино - Группа крови.mp3" and sent["thumbnail"] is None
    assert sent["caption"] == "От @alen" and state["closed"] >= 4

    # with the text: a short one is the audio's caption, collapsed; «by default» is the user's setting
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Lyrics, Track

    async with session_scope() as s:
        track = (await s.execute(select(Track))).scalar_one()
        s.add(Lyrics(track_id=track.id, kind="synced", source="lrclib", lines_json=[{"t": 0, "text": "Тёплое место"}, {"t": 9, "text": "<но>"}]))
    checks = len(state["profile_checks"])
    r = await c.post("/api/telegram/send", json={"nd_song_id": "nd1", "with_lyrics": True, "anonymous": True})
    assert r.json()["lyrics"] is True
    assert state["sent"][-1]["caption"] == "<blockquote expandable>Тёплое место\n&lt;но&gt;</blockquote>"
    assert len(state["profile_checks"]) == checks
    assert state["sent"][-1]["parse_mode"] == "HTML"
    [[enqueue, heart]] = state["sent"][-1]["reply_markup"].inline_keyboard
    assert enqueue.text == "В очередь" and enqueue.callback_data
    assert heart.text == "❤️" and heart.callback_data
    state["usernames"][42] = "alen_new"
    r = await c.post("/api/telegram/send", json={"nd_song_id": "nd1", "with_lyrics": True})
    assert r.status_code == 200, r.text
    assert state["sent"][-1]["caption"] == "От @alen_new\n\n<blockquote expandable>Тёплое место\n&lt;но&gt;</blockquote>"
    # the track's «HomeMusic» link as a button under the audio, when asked for
    assert (await c.post("/api/telegram/send", json={"nd_song_id": "nd1", "with_link": True})).status_code == 200
    [[enqueue, heart], [button]] = state["sent"][-1]["reply_markup"].inline_keyboard
    assert button.text == "Открыть" and button.url.startswith("https://alenkimov.github.io/HomeMusic-public/#")
    assert enqueue.text == "В очередь" and 1 <= len(enqueue.callback_data.encode("utf-8")) <= 64
    assert heart.text == "❤️" and 1 <= len(heart.callback_data.encode("utf-8")) <= 64
    assert (await c.put("/api/telegram/prefs", json={"with_lyrics": True})).json()["with_lyrics"] is True
    assert (await c.post("/api/telegram/send", json={"nd_song_id": "nd1"})).json()["lyrics"] is True
    assert (await c.post("/api/telegram/send", json={"nd_song_id": "nd1", "with_lyrics": False})).json()["lyrics"] is False

    assert (await c.post("/api/telegram/send", json={"ref": "dz:404"})).status_code == 404  # not in the library
    # a track the Hub knows, its file not downloaded yet (a transfer made it): not a server error
    from app.matching.normalize import fingerprint

    async with session_scope() as s:
        s.add(Track(ref="dz:405", artist="Кино", title="Звезда", fingerprint=fingerprint("Кино", "Звезда")))
    r = await c.post("/api/telegram/send", json={"ref": "dz:405"})
    assert r.status_code == 404 and "не скачан" in r.json()["detail"]

    state["blocked"] = True
    r = await c.post("/api/telegram/send", json={"nd_song_id": "nd1"})
    assert r.status_code == 502 and "Start" in r.json()["detail"]

    assert (await c.delete("/api/telegram")).json()["ready"] is False
    assert (await c.get("/api/telegram")).json()["configured"] is True
    assert (await c.get("/api/telegram/chats")).json() == []


async def test_a_shared_audio_buttons_update_the_clickers_own_account(env):
    c, state = env["client"], env["state"]
    await add_track(env["music"], "nd1")
    await pair(c, state, 42)
    state["updates"].append(member(-100, "supergroup", "administrator"))
    state["members"] = {(-100, BOT_ID): membership("administrator"), (-100, 42): membership("member"),
                        (-100, 9): membership("member")}
    chats = await c.post("/api/telegram/chats/refresh")
    assert chats.status_code == 200, chats.text
    group = next(row["id"] for row in chats.json() if row["kind"] == "supergroup")
    sent = await c.post("/api/telegram/send", json={"nd_song_id": "nd1", "chat": group,
                                                   "with_link": True, "anonymous": True})
    assert sent.status_code == 200, sent.text
    [[enqueue, heart], [_]] = state["sent"][-1]["reply_markup"].inline_keyboard

    async def press(sender: int, data: str) -> str:
        update_id = next(UPDATES)
        state["updates"].append(Update.model_validate({
            "update_id": update_id,
            "callback_query": {"id": str(update_id), "chat_instance": "group",
                               "from": {"id": sender, "is_bot": False, "first_name": "Listener"},
                               "message": {"message_id": 7, "date": 0, "chat": {"id": -100, "type": "supergroup"}},
                               "data": data},
        }))
        assert (await c.post("/api/telegram/check")).status_code == 200
        answer = state["callback_answers"][-1]
        assert answer["callback_query_id"] == str(update_id)
        return answer["text"]

    async with app_client(env["app"], login=("other", "password")) as other:
        await pair(other, state, 9)
        messages_before = len(state["messages"])
        assert "Подключите Telegram" in await press(999, enqueue.callback_data)
        assert await press(9, enqueue.callback_data) == "Добавлено в очередь"
        assert (await c.get("/api/playback/queue")).json() == []
        [entry] = (await other.get("/api/playback/queue")).json()
        assert entry["track"]["nd_song_id"] == "nd1"
        assert entry["track"]["ref"] == f"lib:{entry['track']['library_track_id']}"
        tampered = heart.callback_data[:-1] + ("0" if heart.callback_data[-1] != "0" else "1")
        assert "Подключите Telegram" in await press(999, heart.callback_data)
        assert await press(9, tampered) == "Этот трек недоступен"
        assert await press(9, heart.callback_data) == "Добавлено в Любимое"
        assert (await c.get("/api/nd/liked")).json()["songs"] == []
        assert [t["nd_song_id"] for t in (await other.get("/api/nd/liked")).json()["songs"]] == ["nd1"]

        assert await press(9, heart.callback_data) == "Уже в Любимом"
        assert await press(42, heart.callback_data) == "Добавлено в Любимое"
        assert [t["nd_song_id"] for t in (await c.get("/api/nd/liked")).json()["songs"]] == ["nd1"]
        assert "callback_query" in state["allowed_updates"]
        assert len(state["messages"]) == messages_before


async def test_shared_bot_can_be_disabled(env, monkeypatch):
    from app.config import get_settings

    c = env["client"]
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    get_settings.cache_clear()
    assert (await c.get("/api/telegram")).json()["configured"] is False
    assert (await c.post("/api/telegram/link")).status_code == 409


async def test_link_is_private_expiring_and_single_use(env):
    from datetime import timedelta

    from sqlalchemy import update

    from app.db import session_scope
    from app.models import TelegramAccount, utcnow

    c, state = env["client"], env["state"]
    code = await code_for(c)
    state["updates"] = [message(-100, "group", sender=42, text=f"/start {code}")]
    assert not (await c.post("/api/telegram/check")).json()["ready"]
    async with session_scope() as s:
        await s.execute(update(TelegramAccount).values(link_expires_at=utcnow() - timedelta(seconds=1)))
    state["updates"].append(message(42, text=f"/start {code}"))
    assert not (await c.post("/api/telegram/check")).json()["ready"]
    code = await code_for(c)
    state["updates"].append(message(42, text=f"/start {code}"))
    assert (await c.post("/api/telegram/check")).json()["ready"]
    assert (await c.post("/api/telegram/link")).json()["start_url"] is None
    await c.delete("/api/telegram")
    state["updates"].append(message(42, text=f"/start {code}"))
    assert not (await c.post("/api/telegram/check")).json()["ready"]


async def test_listing_sending_and_photos_require_current_membership(env):
    c, state = env["client"], env["state"]
    await add_track(env["music"], "nd1")
    assert (await c.get("/api/telegram/chats")).json() == []
    await pair(c, state, 42, first_name="Алён", username="alen")
    state["chats"] = {-100: SimpleNamespace(id=-100, title="Друзья и музыка", first_name=None, last_name=None, username=None,
                                            photo=SimpleNamespace(small_file_id="f1", small_file_unique_id="u1"))}
    state["updates"].extend([member(-100, "supergroup", "administrator"), member(-200, "group", "member"),
                             member(-400, "channel", "administrator", title="Канал", can_post_messages=True)])
    state["members"] = {
        (-100, BOT_ID): membership("administrator"), (-100, 42): membership("member"),
        (-400, BOT_ID): membership("administrator", can_post_messages=True), (-400, 42): membership("member"),
    }
    got = (await c.post("/api/telegram/chats/refresh")).json()
    assert [(x["kind"], x["title"], x["photo"]) for x in got] == [
        ("private", "Алён @alen", None), ("supergroup", "Друзья и музыка", "u1")]
    group, photo = got[1]["id"], f"/api/telegram/chats/{got[1]['id']}/photo"
    data = {"nd_song_id": "nd1", "chat": group}
    r = await c.get(photo)
    assert r.content == b"jpeg of f1" and r.headers["content-type"] == "image/jpeg"
    assert (await c.post("/api/telegram/send", json=data)).status_code == 200
    assert (state["sent"][-1]["chat_id"], state["sent"][-1]["caption"]) == (-100, "От @alen")
    assert (await c.post("/api/telegram/send", json={**data, "anonymous": True})).status_code == 200
    assert (state["sent"][-1]["chat_id"], state["sent"][-1]["caption"]) == (-100, None)
    async with app_client(env["app"], login=("other", "password")) as other:
        await pair(other, state, 9)
        assert [x["kind"] for x in (await other.get("/api/telegram/chats")).json()] == ["private"]
        assert (await other.get(photo)).status_code == 404
        assert (await other.post("/api/telegram/send", json=data)).status_code == 404
        state["members"][-100, 9] = membership("member")
        assert (await other.post("/api/telegram/send", json=data)).status_code == 200
        assert (state["sent"][-1]["chat_id"], state["sent"][-1]["caption"]) == (-100, None)

    # A cached tile and its picture URL stop working as soon as the user leaves.
    state["members"][-100, 42] = membership("left")
    assert (await c.post("/api/telegram/send", json=data)).status_code == 404
    assert (await c.get(photo)).status_code == 404
    state["members"][-100, 42] = membership("member")
    state["members"][-100, BOT_ID] = membership("member")
    assert (await c.post("/api/telegram/send", json=data)).status_code == 404
    state["members"][-400, 42] = membership("administrator", can_post_messages=True)
    assert [x["title"] for x in (await c.get("/api/telegram/chats")).json()] == ["Алён @alen", "Канал"]
