"""The registry of tracks (ARCHITECTURE.md): a track and the files that play it."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TrackFile


async def current_file(s: AsyncSession, track_id: int) -> TrackFile | None:
    """The file that plays the track: the newest one."""
    return (await s.execute(select(TrackFile).where(TrackFile.track_id == track_id)
                            .order_by(TrackFile.id.desc()).limit(1))).scalar_one_or_none()


async def current_files(s: AsyncSession, track_ids: Iterable[int]) -> dict[int, TrackFile]:
    """track id -> the file that plays it, for the tracks that have one."""
    ids = list(dict.fromkeys(track_ids))
    out: dict[int, TrackFile] = {}
    for i in range(0, len(ids), 500):
        for f in (await s.execute(select(TrackFile).where(TrackFile.track_id.in_(ids[i:i + 500]))
                                  .order_by(TrackFile.id))).scalars():
            out[f.track_id] = f  # ordered by id: the newest stays
    return out


async def files_of(s: AsyncSession, track_id: int) -> list[TrackFile]:
    return list((await s.execute(select(TrackFile).where(TrackFile.track_id == track_id)
                                 .order_by(TrackFile.id))).scalars())
