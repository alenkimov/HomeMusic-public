"""Probable matches awaiting the listener's confirmation."""

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ImportReviewOut, TrackOut
from app.models import Import, ImportCollection, ImportItem

_REVIEWED = "$.match_reviewed"
_REVIEWABLE = ("done", "waiting", "failed")


def _items(user_id: int):
    return (select(ImportItem, Import.source)
            .join(Import, Import.id == ImportItem.import_id)
            .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
            .where(Import.user_id == user_id, ImportCollection.selected.is_(True),
                   ImportCollection.kind.in_(("playlist", "likes")), ImportItem.status != "skipped"))


async def annotate(s: AsyncSession, user_id: int, tracks: list[TrackOut]) -> None:
    ids = {t.library_track_id for t in tracks if t.library_track_id is not None}
    if not ids:
        return
    pending = ((ImportItem.match == "probable") & ImportItem.status.in_(_REVIEWABLE)
               & func.json_extract(ImportItem.raw_json, _REVIEWED).is_(None))
    rows = await s.execute(
        _items(user_id).where(
            ImportItem.track_id.in_(ids),
            or_(ImportItem.status == "failed", pending),
        ).order_by(ImportItem.id.desc()))
    reviews, failed = {}, set()
    for item, source in rows:
        if item.status == "failed":
            failed.add(item.track_id)
        raw = item.raw_json or {}
        if item.match == "probable" and not raw.get("match_reviewed") and item.track_id not in reviews:
            reviews[item.track_id] = ImportReviewOut(
                id=item.id, source=source, title=raw.get("title") or "", artist=raw.get("artist") or "",
                duration_s=raw.get("duration_s"), cover_url=raw.get("cover_url"))
    for track in tracks:
        if not track.owned and track.library_track_id in failed:
            track.unavailable = True
        track.import_review = reviews.get(track.library_track_id)


async def acknowledge(s: AsyncSession, user_id: int, item_id: int, *, track_id: int | None = None) -> bool:
    """Remember a decision for this song from the same service across all of the listener's imports."""
    query = _items(user_id).where(ImportItem.match == "probable", ImportItem.track_id.is_not(None))
    found = (await s.execute(query.where(ImportItem.id == item_id))).one_or_none()
    if found is None:
        return False
    item, source = found
    if track_id is not None and item.track_id != track_id:
        return False
    same = query.with_only_columns(ImportItem.id).where(
        Import.source == source, ImportItem.external_id == item.external_id, ImportItem.track_id == item.track_id)
    await s.execute(update(ImportItem).where(ImportItem.id.in_(same))
                    .values(raw_json=func.json_set(ImportItem.raw_json, _REVIEWED, 1)))
    return True
