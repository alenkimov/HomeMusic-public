"""Short-lived handoffs between web players on the same Hub account."""

from dataclasses import dataclass
from time import monotonic
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, model_validator

from app.auth import current_user
from app.events import bus
from app.models import User
from app.playback_queue import QueueTrack

router = APIRouter(prefix="/api/playback/devices", tags=["playback"])

PRESENCE_SECONDS = 45
TRANSFER_SECONDS = 90
DeviceId = Annotated[str, Field(pattern=r"^[a-zA-Z0-9-]{12,64}$")]
Status = Literal["offered", "completed", "cancelled"]


class Recommendation(BaseModel):
    label: str = Field(max_length=512)
    session: str = Field(max_length=128)


class PlaybackTrack(QueueTrack):
    queued: bool = False
    rec: Recommendation | None = None


class PlaybackSnapshot(BaseModel):
    queue: list[PlaybackTrack] = Field(min_length=1, max_length=20000)
    index: int = Field(ge=0)
    context: str = Field(max_length=256)
    page: str = Field(max_length=2048, pattern=r"^(?:|/(?:[^/\r\n\\][^\r\n\\]*)?)$")
    position: float = Field(ge=0, le=86400)
    autoplay: bool
    shuffle: bool
    smart: bool
    repeat: Literal["off", "all", "one"]
    originalOrder: list[int] | None = Field(default=None, max_length=20000)

    @model_validator(mode="after")
    def check_indices(self):
        size = len(self.queue)
        if self.index >= size or any(i < 0 or i >= size for i in self.originalOrder or []):
            raise ValueError("Неверная позиция в очереди")
        return self


class Presence(BaseModel):
    id: DeviceId
    name: str = Field(min_length=1, max_length=96)
    mobile: bool


class Offer(BaseModel):
    id: DeviceId
    source: DeviceId
    target: DeviceId
    snapshot: PlaybackSnapshot


class Answer(BaseModel):
    device: DeviceId
    status: Literal["completed", "cancelled"]


@dataclass
class Transfer:
    offer: Offer
    source_name: str
    created: float
    status: Status = "offered"


_devices: dict[tuple[int, str], tuple[Presence, float]] = {}
_transfers: dict[tuple[int, str], Transfer] = {}


def _prune(now: float) -> None:
    for key, (_, seen) in list(_devices.items()):
        if now - seen >= PRESENCE_SECONDS:
            del _devices[key]
    for key, transfer in list(_transfers.items()):
        if now - transfer.created >= TRANSFER_SECONDS:
            del _transfers[key]


def _notice(user_id: int) -> None:
    bus.publish(user_id, "playback_devices", {})


@router.post("/presence")
async def presence(body: Presence, response: Response, user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    now = monotonic()
    _prune(now)
    key = (user.id, body.id)
    first = key not in _devices
    if first and sum(uid == user.id for uid, _ in _devices) >= 24:
        raise HTTPException(429, "Слишком много открытых плееров")
    _devices[key] = (body, now)
    if first:
        _notice(user.id)
    devices = [device for (uid, did), (device, _) in _devices.items() if uid == user.id and did != body.id]
    transfers = []
    for (uid, _), transfer in _transfers.items():
        offer = transfer.offer
        if uid != user.id or body.id not in (offer.source, offer.target):
            continue
        incoming = body.id == offer.target
        row = {
            "id": offer.id, "incoming": incoming, "status": transfer.status, "sourceName": transfer.source_name,
            "age": now - transfer.created, "expiresIn": TRANSFER_SECONDS - (now - transfer.created),
        }
        if incoming and transfer.status == "offered":
            row["snapshot"] = offer.snapshot.model_dump(exclude_none=True)
        transfers.append(row)
    return {"devices": devices, "transfers": transfers}


@router.post("/transfers", status_code=204)
async def offer_transfer(body: Offer, user: User = Depends(current_user)):
    now = monotonic()
    _prune(now)
    key = (user.id, body.id)
    if key in _transfers:
        if _transfers[key].offer != body:
            raise HTTPException(409, "Передача уже существует")
        return
    source = _devices.get((user.id, body.source))
    if source is None or (user.id, body.target) not in _devices or body.source == body.target:
        raise HTTPException(409, "Устройство недоступно")
    participants = {body.source, body.target}
    active = [t.offer for (uid, _), t in _transfers.items() if uid == user.id and t.status == "offered"]
    if any(participants.intersection((other.source, other.target)) for other in active):
        raise HTTPException(409, "Одна из передач уже выполняется")
    _transfers[key] = Transfer(body, source[0].name, now)
    _notice(user.id)


@router.post("/transfers/{transfer_id}", status_code=204)
async def answer_transfer(transfer_id: DeviceId, body: Answer, user: User = Depends(current_user)):
    _prune(monotonic())
    transfer = _transfers.get((user.id, transfer_id))
    if transfer is None or body.device not in (transfer.offer.source, transfer.offer.target):
        raise HTTPException(404, "Передача больше недоступна")
    if body.status == "completed" and body.device != transfer.offer.target:
        raise HTTPException(409, "Воспроизведение подтверждает принимающее устройство")
    if transfer.status == body.status:
        return
    if transfer.status != "offered":
        raise HTTPException(409, "Передача уже завершена")
    transfer.status = body.status
    _notice(user.id)
