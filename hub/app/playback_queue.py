"""The account's manual playback queue. A device confirms a claim once sound starts."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.events import bus
from app.models import PlaybackQueueItem, Track, utcnow

QueueId = Annotated[int, Field(ge=1, le=2**53 - 1)]
ClaimToken = Annotated[str, Field(min_length=1, max_length=64)]


class Credit(BaseModel):
    name: str = Field(max_length=512)
    ref: str | None = Field(default=None, max_length=128)
    nd: str | None = Field(default=None, max_length=128)
    id: QueueId | None = None


class QueueTrack(BaseModel):
    ref: str = Field(max_length=512, pattern=r"^(?:(?:dz|ym|sp|hm|mb|nd|file):.+|lib:[1-9][0-9]{0,15})$")
    title: str = Field(max_length=1024)
    artist: str = Field(max_length=2048)
    library_track_id: QueueId | None = None
    nd_song_id: str | None = Field(default=None, max_length=128)
    album: str | None = Field(default=None, max_length=512)
    album_ref: str | None = Field(default=None, max_length=128)
    release_id: QueueId | None = None
    artist_ref: str | None = Field(default=None, max_length=128)
    hub_artist_id: QueueId | None = None
    artists: list[Credit] | None = Field(default=None, max_length=100)
    cover_url: str | None = Field(default=None, max_length=4096)
    cover_small_url: str | None = Field(default=None, max_length=4096)
    page_url: str | None = Field(default=None, max_length=4096)
    duration_s: float | None = Field(default=None, ge=0, le=86400)
    year: int | None = None
    isrc: str | None = Field(default=None, max_length=32)
    spotify_plays: int | None = None


class QueueEntry(BaseModel):
    id: QueueId
    track: QueueTrack


def from_track(track: Track) -> QueueTrack:
    from app.api.playlists import track_with_file

    drawn = track_with_file(track)
    data = drawn.model_dump()
    data.update(ref=f"lib:{track.id}", album=drawn.album_title, artists=[
        Credit(name=name, ref=drawn.artist_refs[i] if i < len(drawn.artist_refs) else None)
        for i, name in enumerate(drawn.artists)
    ])
    return QueueTrack.model_validate(data)


def _pending(user_id: int):
    return PlaybackQueueItem.user_id == user_id, PlaybackQueueItem.finished_at.is_(None)


def _ordered(user_id: int):
    return select(PlaybackQueueItem).where(*_pending(user_id)).order_by(
        func.coalesce(PlaybackQueueItem.position, PlaybackQueueItem.id), PlaybackQueueItem.id)


async def entries(s: AsyncSession, user_id: int) -> list[QueueEntry]:
    return [QueueEntry(id=row.id, track=QueueTrack.model_validate(row.track))
            for row in await s.scalars(_ordered(user_id))]


async def _lock_order(s: AsyncSession, user_id: int) -> None:
    await s.execute(select(func.pg_advisory_xact_lock(7013423, user_id)))


async def append(s: AsyncSession, user_id: int, tracks: list[QueueTrack], *, source_key: str | None = None) -> int:
    await _lock_order(s, user_id)
    await s.execute(delete(PlaybackQueueItem).where(
        PlaybackQueueItem.user_id == user_id, PlaybackQueueItem.finished_at < utcnow() - timedelta(days=2)))
    requested = [int(t.ref[4:]) if t.ref.startswith("lib:") else t.library_track_id for t in tracks]
    ids = {key for key in requested if key is not None and 0 < key < 2**53}
    known = set(await s.scalars(select(Track.id).where(Track.id.in_(ids)))) if ids else set()
    rows = []
    for index, (track, key) in enumerate(zip(tracks, requested, strict=True)):
        data = track.model_dump(exclude_none=True)
        track_id = key if key in known and not track.ref.startswith("file:") else None
        if track_id is None:
            data.pop("library_track_id", None)
            if track.ref.startswith("lib:"):
                continue
        else:
            data["library_track_id"] = track_id
        rows.append(dict(user_id=user_id, track_id=track_id, track=data,
                         source_key=f"{source_key}:{index}" if source_key else None))
    if not rows:
        return 0
    statement = insert(PlaybackQueueItem).values(rows).on_conflict_do_nothing(
        index_elements=["user_id", "source_key"]).returning(PlaybackQueueItem.id)
    return len((await s.scalars(statement)).all())


async def remove(s: AsyncSession, user_id: int, ids: list[int]) -> int:
    await _lock_order(s, user_id)
    statement = update(PlaybackQueueItem).where(
        *_pending(user_id), PlaybackQueueItem.id.in_(ids)).values(finished_at=utcnow()).returning(PlaybackQueueItem.id)
    return len((await s.scalars(statement)).all())


async def move(s: AsyncSession, user_id: int, entry_id: int, before_id: int | None) -> bool:
    if entry_id == before_id:
        return False
    await _lock_order(s, user_id)
    rows = list(await s.scalars(_ordered(user_id)))
    source = next((row for row in rows if row.id == entry_id), None)
    if source is None:
        return False
    rows.remove(source)
    at = len(rows) if before_id is None else next((i for i, row in enumerate(rows) if row.id == before_id), -1)
    if at < 0:
        return False
    rows.insert(at, source)
    for position, row in enumerate(rows):
        row.position = position
    return True


async def claim(s: AsyncSession, user_id: int, entry_id: int, token: str) -> bool:
    now = utcnow()
    result = await s.scalar(update(PlaybackQueueItem).where(
        *_pending(user_id), PlaybackQueueItem.id == entry_id,
        or_(PlaybackQueueItem.claim == token, PlaybackQueueItem.claim_until.is_(None),
            PlaybackQueueItem.claim_until <= now),
    ).values(claim=token, claim_until=now + timedelta(minutes=2)).returning(PlaybackQueueItem.id))
    return result is not None


async def release(s: AsyncSession, user_id: int, entry_id: int, token: str) -> None:
    await s.execute(update(PlaybackQueueItem).where(
        *_pending(user_id), PlaybackQueueItem.id == entry_id, PlaybackQueueItem.claim == token,
    ).values(claim=None, claim_until=None))


async def finish(s: AsyncSession, user_id: int, entry_id: int, token: str) -> bool:
    await _lock_order(s, user_id)
    result = await s.scalar(update(PlaybackQueueItem).where(
        *_pending(user_id), PlaybackQueueItem.id == entry_id, PlaybackQueueItem.claim == token,
    ).values(finished_at=utcnow()).returning(PlaybackQueueItem.id))
    return result is not None


def publish(user_id: int) -> None:
    bus.publish(user_id, "playback_queue", {})
