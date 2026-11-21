"""Stable Hub addresses for releases. External references identify catalog records."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import CatalogAlbum, CatalogTrack
from app.models import Album, CatalogRelease, CatalogReleaseRef, Track

write_lock = asyncio.Lock()


def numeric_id(address: str) -> int | None:
    return int(address) if address.isascii() and address.isdecimal() and len(address) <= 18 else None


async def by_address(s: AsyncSession, address: str) -> CatalogRelease | None:
    if (ident := numeric_id(address)) is not None:
        row = await s.get(CatalogRelease, ident)
        return await s.get(CatalogRelease, row.merged_into_id) if row and row.merged_into_id else row
    return await s.scalar(
        select(CatalogRelease).join(CatalogReleaseRef).where(CatalogReleaseRef.ref == address)
    )


async def refs(s: AsyncSession, release_id: int) -> list[str]:
    return list(await s.scalars(
        select(CatalogReleaseRef.ref).where(CatalogReleaseRef.release_id == release_id).order_by(CatalogReleaseRef.ref)
    ))


async def addresses(
    s: AsyncSession, catalog_refs: Iterable[str], album_ids: Iterable[int] = (),
) -> tuple[dict[str, int], dict[int, int]]:
    catalog, local = list(set(catalog_refs)), list(set(album_ids))
    out_refs, out_local = {}, {}
    for start in range(0, len(catalog), 500):
        out_refs.update((await s.execute(select(CatalogReleaseRef.ref, CatalogReleaseRef.release_id)
                                        .where(CatalogReleaseRef.ref.in_(catalog[start:start + 500])))).all())
    for start in range(0, len(local), 500):
        rows = await s.execute(select(CatalogRelease.album_id, CatalogRelease.id).where(
            CatalogRelease.album_id.in_(local[start:start + 500]), CatalogRelease.merged_into_id.is_(None),
        ).order_by(CatalogRelease.id))
        for aid, rid in rows:
            out_local.setdefault(aid, rid)
    return out_refs, out_local


async def remember(
    s: AsyncSession, album: CatalogAlbum, external: Iterable[str], album_id: int | None = None,
    *, create: bool = True,
) -> CatalogRelease | None:
    """Register an opened release, or merge already registered addresses of confirmed duplicates."""
    external = set(external)
    rows = list(await s.scalars(select(CatalogRelease).join(CatalogReleaseRef).where(
        CatalogReleaseRef.ref.in_(external), CatalogRelease.merged_into_id.is_(None),
    ).distinct().order_by(CatalogRelease.id)))
    if album_id is not None:
        # Share the unbound library address when the catalog record is identified.
        # An album assembled manually may contain several otherwise distinct releases.
        local = await s.scalar(select(CatalogRelease).where(
            CatalogRelease.album_id == album_id, CatalogRelease.merged_into_id.is_(None),
            ~select(CatalogReleaseRef.ref).where(CatalogReleaseRef.release_id == CatalogRelease.id).exists(),
        ).order_by(CatalogRelease.id).limit(1))
        if local and local not in rows:
            rows = sorted([*rows, local], key=lambda r: r.id)
    if not rows and not create:
        return None
    release = rows[0] if rows else CatalogRelease(album_id=album_id)
    if not rows:
        s.add(release)
        await s.flush()
    release.album_id = album_id if album_id is not None else next((r.album_id for r in rows if r.album_id), None)
    if album.tracks is not None or release.album_json is None:
        snapshot = album.model_dump(mode="json")
        for track in snapshot["tracks"] or []:
            track.pop("spotify_plays", None)
        release.album_json = snapshot
    retired = [r.id for r in rows[1:]]
    if retired:
        await s.execute(update(CatalogRelease).where(CatalogRelease.merged_into_id.in_(retired))
                        .values(merged_into_id=release.id))
        await s.execute(update(CatalogReleaseRef).where(CatalogReleaseRef.release_id.in_(retired))
                        .values(release_id=release.id))
        for row in rows[1:]:
            row.merged_into_id = release.id
    if external:
        await s.execute(insert(CatalogReleaseRef).on_conflict_do_nothing(), [
            {"ref": ref, "release_id": release.id} for ref in external
        ])
    await s.flush()
    return release


async def opened(s: AsyncSession, album: CatalogAlbum, external: Iterable[str], album_id: int | None) -> CatalogRelease:
    async with write_lock:
        row = await remember(s, album, external, album_id)
        await s.commit()
        assert row is not None
        return row


async def library_album(s: AsyncSession, album_id: int, *, with_tracks: bool = True) -> CatalogAlbum | None:
    album = await s.get(Album, album_id)
    if album is None:
        return None
    from app.library.artwork import album_cover_url

    rows = (await s.scalars(select(Track).where(Track.album_id == album_id)
                            .order_by(Track.disc_no, Track.track_no, Track.id))).all() if with_tracks else []
    kind = {"single": "single", "ep": "ep", "compilation": "compile"}.get(album.release_type, "album")
    return CatalogAlbum(
        ref=f"lib:{album.id}", id=str(album.id), source="nd", title=album.title, artist=album.album_artist,
        year=album.year, record_type=kind, genre=album.genre, upc=album.upc, cover_url=album_cover_url(album),
        tracks=[library_track(t) for t in rows] if with_tracks else None,
    )


def library_track(track: Track) -> CatalogTrack:
    saved = track.catalog or {}
    return CatalogTrack.model_validate({
        **saved, "ref": f"lib:{track.id}", "id": str(track.id), "source": "nd",
        "title": track.title, "artist": track.artist, "album_title": track.album,
        "album_ref": f"lib:{track.album_id}" if track.album_id else saved.get("album_ref"),
        "duration_s": track.duration_s, "track_no": track.track_no, "disc_no": track.disc_no, "isrc": track.isrc,
    })
