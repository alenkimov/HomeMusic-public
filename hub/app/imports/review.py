"""What a listener's transfers say of the tracks: a probable match to look over (a question of «Наведение порядка»,
`pending`), a song no file was found of (`unfound`)."""

from sqlalchemy import literal, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TrackOut
from app.models import Import, ImportCollection, ImportItem

_REVIEWABLE = ("done", "waiting", "failed")


def _items(user_id: int):
    return (select(ImportItem, Import.source)
            .join(Import, Import.id == ImportItem.import_id)
            .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
            .where(Import.user_id == user_id, ImportCollection.selected.is_(True),
                   ImportCollection.kind.in_(("playlist", "likes")), ImportItem.status != "skipped"))


async def mark_unavailable(s: AsyncSession, user_id: int, tracks: list[TrackOut]) -> None:
    """A track without a file a transfer of the user's did not find a file of: not to be had (`unfound`)."""
    failed = await unfound(s, user_id, {t.library_track_id for t in tracks if t.library_track_id is not None})
    for track in tracks:
        if not track.owned and track.library_track_id in failed:
            track.unavailable = True


async def unfound(s: AsyncSession, user_id: int, ids: set[int]) -> set[int]:
    """The tracks a transfer of the user's did not find a file of. (Its matches to look over are questions of
    «Наведение порядка»: `app.tidy.kinds`, «import_match».)"""
    if not ids:
        return set()
    return set((await s.execute(_items(user_id).with_only_columns(ImportItem.track_id).where(
        ImportItem.track_id.in_(ids), ImportItem.status == "failed"))).scalars())


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
                        .values(raw_json=ImportItem.raw_json.op("-")("match_reviewed")))


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
