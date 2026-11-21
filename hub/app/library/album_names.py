"""Alternate album titles kept by the local library."""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.library.artists import key
from app.models import Album, AlbumName


def catalog_uid(ref: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"homemusic:album:{ref}"))


async def listed(s: AsyncSession, ids: list[int] | None = None) -> dict[int, list[AlbumName]]:
    query = select(AlbumName).order_by(AlbumName.id)
    if ids is not None:
        query = query.where(AlbumName.album_id.in_(ids))
    result: dict[int, list[AlbumName]] = {}
    for row in (await s.scalars(query)).all():
        result.setdefault(row.album_id, []).append(row)
    return result


async def add(s: AsyncSession, album: Album, name: str, source: str) -> None:
    name = " ".join(name.split())
    if name and key(name) != key(album.title):
        stmt = insert(AlbumName).values(
            album_id=album.id, name=name, key=key(name), source=source,
        )
        if source == "catalog":
            stmt = stmt.on_conflict_do_update(index_elements=[AlbumName.album_id, AlbumName.key],
                                             set_={"source": source}, where=AlbumName.source != source)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=[AlbumName.album_id, AlbumName.key])
        await s.execute(stmt)


async def renamed(s: AsyncSession, album: Album, previous: str) -> None:
    await s.execute(delete(AlbumName).where(AlbumName.album_id == album.id, AlbumName.key == key(album.title)))
    await add(s, album, previous, "renamed")


async def drop(s: AsyncSession, album: Album, name_id: int) -> None:
    await s.execute(delete(AlbumName).where(
        AlbumName.album_id == album.id, AlbumName.id == name_id, AlbumName.source != "catalog",
    ))
