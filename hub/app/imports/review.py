"""Probable matches awaiting the listener's confirmation."""

from sqlalchemy import literal, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ImportReviewOut, TrackOut
from app.models import Import, ImportCollection, ImportItem

_REVIEWABLE = ("done", "waiting", "failed")


def _items(user_id: int):
    return (select(ImportItem, Import.source)
            .join(Import, Import.id == ImportItem.import_id)
            .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
            .where(Import.user_id == user_id, ImportCollection.selected.is_(True),
                   ImportCollection.kind.in_(("playlist", "likes")), ImportItem.status != "skipped"))


async def annotate(s: AsyncSession, user_id: int, tracks: list[TrackOut]) -> None:
    """What the user's transfers say of these tracks (`word`), on each of them."""
    reviews, failed = await word(s, user_id, {t.library_track_id for t in tracks if t.library_track_id is not None})
    for track in tracks:
        if not track.owned and track.library_track_id in failed:
            track.unavailable = True
        track.import_review = reviews.get(track.library_track_id)


async def word(s: AsyncSession, user_id: int, ids: set[int]) -> tuple[dict[int, ImportReviewOut], set[int]]:
    """What the user's transfers say of these tracks: a match to look over (by track), and the tracks a transfer of
    theirs did not find a file of (such a track without a file is not to be had)."""
    if not ids:
        return {}, set()
    pending = ((ImportItem.match == "probable") & ImportItem.status.in_(_REVIEWABLE)
               & ImportItem.raw_json["match_reviewed"].as_boolean().is_(None))
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
    return reviews, failed


async def acknowledge(s: AsyncSession, user_id: int, item_id: int, *, track_id: int | None = None) -> list[int]:
    """Remember a decision for this song from the same service across all of the listener's imports. Returns the
    rows marked (none: no such match to look over)."""
    query = _items(user_id).where(ImportItem.match == "probable", ImportItem.track_id.is_not(None))
    found = (await s.execute(query.where(ImportItem.id == item_id))).one_or_none()
    if found is None:
        return []
    item, source = found
    if track_id is not None and item.track_id != track_id:
        return []
    same = list((await s.execute(query.with_only_columns(ImportItem.id).where(
        Import.source == source, ImportItem.external_id == item.external_id,
        ImportItem.track_id == item.track_id))).scalars())
    await s.execute(update(ImportItem).where(ImportItem.id.in_(same))
                    .values(raw_json=ImportItem.raw_json.concat(literal({"match_reviewed": True}, JSONB))))
    return same


async def unacknowledge(s: AsyncSession, item_ids: list[int]) -> None:
    """A confirmation taken back («Наведение порядка»: «Откатить»): the matches are to be looked over again."""
    if item_ids:
        await s.execute(update(ImportItem).where(ImportItem.id.in_(item_ids))
                        .values(raw_json=ImportItem.raw_json - "match_reviewed"))


def pending():
    """The matches a transfer made that the listener has not looked over: probable ones, of a playlist or the likes
    taken over, with a track (whose ones: `Import.user_id`)."""
    return (_items_all().where(ImportItem.match == "probable", ImportItem.status.in_(_REVIEWABLE),
                               ImportItem.track_id.is_not(None),
                               ImportItem.raw_json["match_reviewed"].as_boolean().is_(None)))


def _items_all():
    return (select(ImportItem, Import.source, Import.user_id)
            .join(Import, Import.id == ImportItem.import_id)
            .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
            .where(ImportCollection.selected.is_(True), ImportCollection.kind.in_(("playlist", "likes")),
                   ImportItem.status != "skipped"))
