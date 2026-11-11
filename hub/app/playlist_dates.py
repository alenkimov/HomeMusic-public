"""When a track was added to a playlist (`models.PlaylistDate`): the «Дата добавления» column.

Navidrome keeps no such date, so the Hub does: a track added from the Hub is dated then; a playlist that came with
an import keeps the dates the service had (Spotify's «Added At») — they are copied here, so they outlive the import.
A track without a date of its own shows the day it came into the library.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.favorites import parse_time
from app.models import Import, ImportCollection, ImportItem, PlaylistDate, utcnow


def naive(value: datetime | None) -> datetime | None:
    """UTC without a zone, the way the Hub keeps its times."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


async def _put(s: AsyncSession, rows: list[dict[str, Any]]) -> None:
    for i in range(0, len(rows), 200):
        await s.execute(insert(PlaylistDate).values(rows[i:i + 200]).on_conflict_do_nothing())


async def stamp(s: AsyncSession, playlist_id: str, track_ids: list[int]) -> None:
    """These tracks are being added to the playlist now (one already dated keeps its date)."""
    now = utcnow()
    await _put(s, [{"nd_playlist_id": playlist_id, "track_id": t, "added_at": now, "source": "hub"}
                   for t in dict.fromkeys(track_ids)])


async def collect(s: AsyncSession, user_id: int, playlist_id: str, name: str) -> None:
    """Copy the dates of the user's import this playlist came from: the import's playlist that made it, or one of
    the same name (a CSV imported by hand)."""
    q = (select(ImportItem.track_id, ImportItem.raw_json)
         .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
         .join(Import, Import.id == ImportItem.import_id)
         .where(Import.user_id == user_id, ImportCollection.kind == "playlist", ImportItem.track_id.is_not(None),
                or_(ImportCollection.nd_playlist_id == playlist_id,
                    func.lower(ImportCollection.title) == name.strip().lower())))
    rows = []
    seen: set[int] = set()
    for track_id, raw in (await s.execute(q)).all():
        at = naive(parse_time((raw or {}).get("liked_at")))
        if at is not None and track_id not in seen:
            seen.add(track_id)
            rows.append({"nd_playlist_id": playlist_id, "track_id": track_id, "added_at": at, "source": "import"})
    await _put(s, rows)


async def of(s: AsyncSession, playlist_id: str) -> dict[int, datetime]:
    """track id -> when it was added to the playlist."""
    return dict((await s.execute(select(PlaylistDate.track_id, PlaylistDate.added_at)
                                 .where(PlaylistDate.nd_playlist_id == playlist_id))).all())
