"""Reading a VK collection for an import: the sign-in (a code, a captcha, a refusal) and the snapshot.

VK is a fake here: the answers have the shape `vkpymusic` documents and VK's API reference gives; nothing of it was
taken from a live account.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.imports.sources import vk
from app.imports.sources.base import SourceError


def audio(i: int, title: str, artist: str = "Кино", **more) -> dict:
    return {"id": i, "owner_id": 7, "title": title, "artist": artist, "duration": 200 + i, **more}


@respx.mock
async def test_sign_in_asks_for_a_code_then_gives_the_token(monkeypatch):
    token = respx.post(vk.OAUTH)
    sms = respx.get(vk.API + "auth.validatePhone").mock(return_value=httpx.Response(200, json={"response": {}}))
    token.mock(return_value=httpx.Response(401, json={
        "error": "need_validation", "validation_type": "2fa_sms", "validation_sid": "sid1", "phone_mask": "+7 *** 12",
        "error_description": "sms sent"}))
    with pytest.raises(vk.NeedCode) as e:
        await vk.sign_in("+79990000000", "pw")
    assert "SMS" in e.value.hint and "+7 *** 12" in e.value.hint and not e.value.wrong
    sent = token.calls[0].request.url.params
    assert sent["grant_type"] == "password" and sent["username"] == "+79990000000" and sent["scope"] == "audio,offline"
    assert token.calls[0].request.headers["user-agent"] == vk.USER_AGENT
    assert sms.calls[0].request.url.params["sid"] == "sid1"  # VK was asked to send the code

    token.mock(return_value=httpx.Response(401, json={"error": "invalid_request", "error_description": "wrong code"}))
    with pytest.raises(vk.NeedCode) as e:
        await vk.sign_in("+79990000000", "pw", code="000000")
    assert e.value.wrong

    token.mock(return_value=httpx.Response(200, json={"access_token": "tok", "user_id": 7}))
    assert await vk.sign_in("+79990000000", "pw", code="123456") == "tok"
    assert token.calls[-1].request.url.params["code"] == "123456"


@respx.mock
async def test_sign_in_captcha_and_refusals():
    token = respx.post(vk.OAUTH)
    token.mock(return_value=httpx.Response(401, json={"error": "need_captcha", "redirect_uri": "https://id.vk.com/captcha?x=1"}))
    with pytest.raises(vk.NeedCaptcha) as e:
        await vk.sign_in("me", "pw")
    assert e.value.url == "https://id.vk.com/captcha?x=1"
    token.mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    assert await vk.sign_in("me", "pw", captcha="passed") == "tok"
    assert token.calls[-1].request.url.params["success_token"] == "passed"

    token.mock(return_value=httpx.Response(401, json={"error": "invalid_client", "error_description": "bad"}))
    with pytest.raises(SourceError, match="логин или пароль"):
        await vk.sign_in("me", "nope")
    token.mock(return_value=httpx.Response(401, json={"error": "9;Flood control"}))
    with pytest.raises(SourceError, match="слишком много попыток"):
        await vk.sign_in("me", "pw")
    token.mock(return_value=httpx.Response(401, json={"error": "weird", "error_description": "что-то новое"}))
    with pytest.raises(SourceError, match="что-то новое"):
        await vk.sign_in("me", "pw")


def test_track_row_names_the_version_and_every_artist():
    row = vk.track_row(audio(1, "Кукушка", subtitle="Remix", main_artists=[{"name": "Кино"}],
                             featured_artists=[{"name": "Полина"}]))
    assert row == {"artist": "Кино, Полина", "artists": ["Кино", "Полина"], "title": "Кукушка (Remix)",
                   "base": "Кукушка", "duration_s": 201}
    assert vk.track_row(audio(2, "Пачка сигарет"))["artists"] == ["Кино"]
    assert vk.track_row({"title": "", "artist": "Кино"}) is None


@respx.mock
async def test_snapshot_is_my_music_and_the_playlists(monkeypatch):
    monkeypatch.setattr(vk, "PAGE", 2)
    monkeypatch.setattr(vk, "PAUSE_S", 0)
    respx.get(vk.API + "users.get").mock(return_value=httpx.Response(200, json={
        "response": [{"id": 7, "first_name": "Алён", "last_name": "К"}]}))
    mine = [audio(1, "Группа крови"), audio(2, "Кукушка"), audio(3, "Пачка сигарет")]
    lists = {
        (7, 5): [audio(2, "Кукушка")],
        (-33, 9): [audio(40, "Чужая", artist="Другие")],
    }
    hasty = {"n": 0}

    def get(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        assert p["access_token"] == "tok"
        offset = int(p["offset"])
        if "album_id" not in p:
            if offset == 2 and not hasty["n"]:  # VK holds a call back for haste: it is made again
                hasty["n"] += 1
                return httpx.Response(200, json={"error": {"error_code": 6, "error_msg": "Too many requests per second"}})
            return httpx.Response(200, json={"response": {"count": 3, "items": mine[offset:offset + 2]}})
        key = (int(p["owner_id"]), int(p["album_id"]))
        if key == (7, 6):
            return httpx.Response(200, json={"error": {"error_code": 201, "error_msg": "Access denied"}})
        if key == (-33, 9):
            assert p["access_key"] == "k9"
        return httpx.Response(200, json={"response": {"count": len(lists[key]), "items": lists[key][offset:offset + 2]}})

    respx.get(vk.API + "audio.get").mock(side_effect=get)
    playlists = [
        {"id": 5, "owner_id": 7, "title": "В дорогу", "access_key": "k5", "photo": {"photo_300": "https://c/5.jpg"}},
        {"id": 6, "owner_id": 7, "title": "Закрытый", "access_key": "k6"},
        {"id": 8, "owner_id": 7, "title": "Альбом друзей",
         "original": {"owner_id": -33, "playlist_id": 9, "access_key": "k9"}},
    ]
    respx.get(vk.API + "audio.getPlaylists").mock(side_effect=lambda request: httpx.Response(200, json={"response": {
        "count": 3, "items": playlists[int(request.url.params["offset"]):int(request.url.params["offset"]) + 2]}}))

    snap = await vk.fetch("tok")
    assert snap.account == "Алён К" and hasty["n"] == 1
    assert [(c.kind, c.external_id, c.title, c.selected, [i.raw["title"] for i in c.items]) for c in snap.collections] == [
        ("playlist", "audios", "ВКонтакте", True, ["Группа крови", "Кукушка", "Пачка сигарет"]),
        ("playlist", "7_5", "В дорогу", True, ["Кукушка"]),
        ("playlist", "7_8", "Альбом друзей", False, ["Чужая"]),  # a followed one: read from its original, not ticked
    ]
    assert snap.collections[0].items[0].external_id == "7_1" and snap.collections[1].cover_url == "https://c/5.jpg"


@respx.mock
async def test_snapshot_says_when_the_sign_in_is_over():
    respx.get(vk.API + "users.get").mock(return_value=httpx.Response(200, json={
        "error": {"error_code": 5, "error_msg": "User authorization failed"}}))
    with pytest.raises(SourceError, match="войдите заново"):
        await vk.fetch("old")
