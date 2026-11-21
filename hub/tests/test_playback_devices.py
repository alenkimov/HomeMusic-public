import httpx
import pytest
from fastapi import FastAPI, Header

from app.api import playback_devices
from app.auth import current_user
from app.models import User

SOURCE = "source-device-01"
TARGET = "target-device-01"
TRANSFER = "playback-transfer-01"


@pytest.fixture
async def client(monkeypatch):
    app = FastAPI()
    app.include_router(playback_devices.router)
    app.dependency_overrides[current_user] = lambda x_test_user=Header(1): User(id=int(x_test_user))
    monkeypatch.setattr(playback_devices, "_devices", {})
    monkeypatch.setattr(playback_devices, "_transfers", {})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hub.test/api/playback/devices/") as http:
        yield http


async def presence(client, device, user=1):
    response = await client.post("presence", json={"id": device, "name": device, "mobile": False},
                                 headers={"X-Test-User": str(user)})
    assert response.status_code == 200
    return response.json()


def offer():
    track = {"ref": "lib:4", "title": "A track", "artist": "Artist"}
    return {
        "id": TRANSFER, "source": SOURCE, "target": TARGET,
        "snapshot": {"queue": [track, track], "index": 1, "context": "playlist", "page": "/playlist/1",
                     "position": 42.5, "autoplay": True, "shuffle": False, "repeat": "off", "smart": False},
    }


async def test_target_confirms_exact_queue_occurrence_and_position(client):
    await presence(client, SOURCE)
    assert [device["id"] for device in (await presence(client, TARGET))["devices"]] == [SOURCE]
    payload = offer()
    assert (await client.post("transfers", json=payload)).status_code == 204
    assert (await client.post("transfers", json=payload)).status_code == 204
    received, = (await presence(client, TARGET))["transfers"]
    assert received["status"] == "offered"
    assert received["snapshot"]["index"] == 1
    assert received["snapshot"]["position"] == 42.5
    assert len(received["snapshot"]["queue"]) == 2
    reply = f"transfers/{TRANSFER}"
    assert (await client.post(reply, json={"device": SOURCE, "status": "completed"})).status_code == 409
    assert (await client.post(reply, json={"device": TARGET, "status": "completed"})).status_code == 204
    assert (await client.post(reply, json={"device": TARGET, "status": "completed"})).status_code == 204
    status, = (await presence(client, SOURCE))["transfers"]
    assert status["status"] == "completed" and "snapshot" not in status


async def test_transfer_is_private_and_cancel_cannot_stop_source(client):
    await presence(client, SOURCE)
    assert (await presence(client, TARGET, user=2))["devices"] == []
    assert (await client.post("transfers", json=offer())).status_code == 409
    await presence(client, TARGET)
    assert (await client.post("transfers", json=offer())).status_code == 204
    reply = f"transfers/{TRANSFER}"
    assert (await presence(client, TARGET, user=2))["transfers"] == []
    assert (await client.post(reply, json={"device": TARGET, "status": "completed"},
                              headers={"X-Test-User": "2"})).status_code == 404
    assert (await client.post(reply, json={"device": SOURCE, "status": "cancelled"})).status_code == 204
    assert (await client.post(reply, json={"device": TARGET, "status": "completed"})).status_code == 409
