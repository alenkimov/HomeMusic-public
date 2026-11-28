"""The registry of tracks (ARCHITECTURE.md): the songs the Hub knows and the files that play them.

A catalog track becomes a track here the moment it matters to someone — it is put into a playlist, tagged,
downloaded, listened to. `of_catalog` is that moment: it gives the track of a catalog track, making it if the Hub
has not met the song yet. The track has no file then; a download adds one later (`library.importer`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.identity import same_recording
from app.catalog.models import CATALOGS, CatalogTrack
from app.matching.normalize import fingerprint
from app.models import Track, TrackCatalogRef, TrackFile
from app.providers.base import TrackQuery


def _same_length(a: int | None, b: int | None, tol: int = 3) -> bool:
    return a is None or b is None or abs(a - b) <= tol


async def names_of(s: AsyncSession, track_id: int) -> tuple[str | None, str | None] | None:
    """What a track is called outside the Hub: (its catalog ref, the Navidrome song of its newest file). None: no
    such track."""
    row = (await s.execute(select(Track.ref, Track.nd_song_id).where(Track.id == track_id))).first()
    return (row[0], row[1]) if row is not None else None


async def id_of(s: AsyncSession, ref: str | None, nd_song_id: str | None) -> int | None:
    """The track that has this Navidrome song among its files, else the one of this catalog ref."""
    from app.models import TrackFile

    if nd_song_id:
        found = (await s.execute(select(TrackFile.track_id).where(TrackFile.nd_song_id == nd_song_id)
                                 .order_by(TrackFile.id.desc()).limit(1))).scalar_one_or_none()
        if found is not None:
            return found
    track = await by_ref(s, ref) if ref else None
    return track.id if track is not None else None


def _names(c: CatalogTrack) -> dict:
    """What a track says of its song, as the catalog tells it — the name without the credits written into it
    (app/library/titles.py): the catalog's own word is kept whole in `Track.catalog`."""
    from app.library import titles

    title, _, producers = titles.clean(c.title)
    return dict(
        producer=", ".join(producers) or None,
        artist=c.artist, title=title, album=c.album_title, album_artist=c.album_artist,
        track_no=c.track_no, disc_no=c.disc_no, year=c.release_date.year if c.release_date else None,
        duration_s=c.duration_s, isrc=c.isrc.upper() if c.isrc else None, explicit=c.explicit,
        fingerprint=fingerprint(c.artist, title))


async def _unnamed(s: AsyncSession, *, artist: str, title: str, duration_s: int | None, isrc: str | None) -> Track | None:
    """A track the Hub has under no catalog name that is this very song: the same ISRC, or the same names and
    length."""
    unnamed = select(Track).where(Track.ref.is_(None)).order_by(Track.id.desc())
    if isrc:
        wanted = TrackQuery(artist=artist, title=title, duration_s=duration_s, isrc=isrc)
        for track in await s.scalars(unnamed.where(Track.isrc == isrc.upper())):
            if same_recording(wanted, track):
                return track
    for t in (await s.execute(unnamed.where(Track.fingerprint == fingerprint(artist, title)))).scalars():
        if _same_length(t.duration_s, duration_s) and (not (isrc and t.isrc) or t.isrc == isrc.upper()):
            return t
    return None


async def by_name(s: AsyncSession, *, artist: str, title: str, album: str | None = None, duration_s: int | None = None,
                  isrc: str | None = None, user_id: int | None = None) -> Track:
    """The track of a song known by its name alone — a row of a transfer the catalog has no record of: the one the
    Hub has under no catalog name that is this song, else a new one. With no file and no record of the catalog there
    is nothing of it to play, yet it is a track like any other: it has its places in playlists, its ♥ and its tags.
    It plays once it is given a file (`ImportSpec.track_id`), and takes the catalog's name once it is matched with
    the catalog (`of_catalog`)."""
    from app.library import artists

    track = await _unnamed(s, artist=artist, title=title, duration_s=duration_s, isrc=isrc)
    if track is not None:
        return track
    track = Track(artist=artist, title=title, album=album, duration_s=duration_s, isrc=isrc.upper() if isrc else None,
                  fingerprint=fingerprint(artist, title), created_by=user_id)
    s.add(track)
    await s.flush()
    await s.refresh(track)
    await artists.credit_track(s, track)
    return track


async def of_catalog(s: AsyncSession, c: CatalogTrack, *, user_id: int | None = None, word: bool = True) -> Track:
    """The track of this catalog track; made if the Hub does not know the song. A track the Hub has under no
    catalog name yet (an uploaded file, say) that is this very song — the same ISRC, or the same names and length —
    takes the name instead of a second track being made. `word` False: `c` is what somebody else called the song
    (a music service's row matched with the catalog's track), not the catalog's record of it — the track is named
    by it, and the catalog's own word is asked for later (`fill_catalog`)."""
    from app.catalog import release_matches
    from app.library import artists

    ref = c.ref if c.source != "nd" else None
    variants = []
    if c.album_ref and ref:
        group = (await release_matches.groups_for(s, [c.album_ref])).get(c.album_ref)
        if group:
            variants = release_matches.track_versions(group, ref)
    track = await by_ref(s, ref)
    if track is None and variants:
        known = await tracks_by_refs(s, [t.ref for t in variants])
        track = min(known.values(), key=lambda t: (t.path is None, t.id), default=None)
        if track is None:
            c = release_matches.combined_track(variants)
            ref = c.ref
    said = c.model_dump(mode="json") if word else None
    if track is not None:
        await bind_refs(s, track, [ref, *(t.ref for t in variants)])
        if (track.title == ref or not track.artist) and track.ref == ref:
            # met by its ref alone (a like that said nothing of the song): now it is known what the song is
            for k, v in _names(c).items():
                setattr(track, k, v)
            track.catalog = said or track.catalog
            await artists.credit_track(s, track)
        for other in variants:
            await artists.add_title(s, track, other.title, "catalog")
        return track
    track = await _unnamed(s, artist=c.artist, title=c.title, duration_s=c.duration_s, isrc=c.isrc)
    if track is not None:
        if ref:
            track.ref = ref
        track.catalog = track.catalog or said
        await bind_refs(s, track, [ref, *(t.ref for t in variants)])
        return track
    track = Track(ref=ref, **_names(c), catalog=said, created_by=user_id)
    s.add(track)
    await s.flush()
    await s.refresh(track)  # what is read with a track (its song, its path) is there to be read: none, for now
    await bind_refs(s, track, [ref, *(t.ref for t in variants)])
    await artists.credit_track(s, track)
    for other in variants:
        await artists.add_title(s, track, other.title, "catalog")
    return track


async def fill_catalog(s: AsyncSession, tracks: Iterable[Track] | None = None, limit: int = 60) -> int:
    """Tracks the catalog knows (`ref`) that do not keep what the catalog says of them get it now: who their artists
    are there, their album, their cover. Such are the tracks made before a track kept it, and the ones made of what
    somebody else called the song — a music service's row, a like that knew only the name: what they keep does not
    say whose record the artist is. A track with no file yet is by whom the catalog says from then on (one with a
    file was filed by the catalog's word already). `tracks`: these ones (an artist's page needs its few at once);
    none: any that lack it, `limit` at a time. Returns how many were filled."""
    from app.library import artists
    from app.mixes.resolve import catalog_track

    def lacks(t: Track) -> bool:
        return bool(t.ref and t.ref.partition(":")[0] in CATALOGS) and not (t.catalog or {}).get("artist_ref")

    rows = [t for t in tracks if lacks(t)] if tracks is not None else list(
        (await s.execute(select(Track).where(
            or_(*(Track.ref.like(f"{source}:%") for source in CATALOGS)),
            or_(Track.catalog.is_(None), Track.catalog["artist_ref"].as_string().is_(None)))
            .order_by(Track.id.desc()).limit(limit))).scalars())
    filled: list[Track] = []
    for t in rows[:limit]:
        c = await catalog_track(t.ref)
        if c is not None and c.artist_ref:
            t.catalog = c.model_dump(mode="json")
            filled.append(t)
    for t in filled:
        if not await has_file(s, t.id):
            await artists.credit_track(s, t)
    if filled:
        await s.flush()
    return len(filled)


async def has_file(s: AsyncSession, track_id: int) -> bool:
    return (await s.execute(select(TrackFile.id).where(TrackFile.track_id == track_id).limit(1))).first() is not None


def dz_ref(deezer_id: str | int | None) -> str | None:
    return f"dz:{deezer_id}" if deezer_id else None


def dz_id(ref: str | None) -> str | None:
    """The Deezer id of a catalog ref, if it is Deezer's."""
    return ref[3:] if ref and ref.startswith("dz:") else None


async def by_ref(s: AsyncSession, ref: str | None) -> Track | None:
    """Resolve a Hub track address or its known catalog reference."""
    if not ref:
        return None
    from app.catalog.registry import numeric_id

    if ref.startswith("lib:") and (ident := numeric_id(ref[4:])) is not None:
        return await s.get(Track, ident)
    return (await tracks_by_refs(s, [ref])).get(ref)


async def tracks_by_refs(s: AsyncSession, refs: Iterable[str]) -> dict[str, Track]:
    refs = set(refs)
    if not refs:
        return {}
    linked = await s.execute(select(TrackCatalogRef.ref, Track).join(Track).where(TrackCatalogRef.ref.in_(refs)))
    out = {ref: track for ref, track in linked}
    missing = refs - out.keys()
    if missing:
        out.update((t.ref, t) for t in await s.scalars(select(Track).where(Track.ref.in_(missing))))
    return out


async def bind_refs(s: AsyncSession, track: Track, refs: Iterable[str | None]) -> None:
    external = {ref for ref in refs if ref and ref.partition(":")[0] in CATALOGS}
    if not external:
        return
    existing = await tracks_by_refs(s, external)
    rows = [{"ref": ref, "track_id": track.id} for ref in external
            if ref not in existing or existing[ref].id == track.id]
    if rows:
        await s.execute(insert(TrackCatalogRef).on_conflict_do_nothing(), rows)


async def merge_into(s: AsyncSession, gone: Track, keep: Track) -> dict[str, Any]:
    """`gone` is `keep`: one song under two of the Hub's tracks (a copy by the sound, a track without a file of a
    record whose song the library has). Everything of `gone` that is the listener's goes to `keep`, and what moved is
    written down (the record returned) to be taken back (`unmerge`):

    - its places in playlists: where `keep` is not, `gone`'s place is `keep`'s now; where both are, one place stays —
      the earlier one of the two (the listener's order), and `gone`'s goes with it (a playlist holds a track once);
    - its own tags (not the ones made by rule);
    - its listens, its transfers' and downloads' rows;
    - its catalog records — written down here; they are `keep`'s once `gone` is no more (`bind_merged`).

    The caller then deletes `gone` (or puts it in the trash) and calls `bind_merged`."""
    from sqlalchemy import update

    from app.models import DownloadItem, ImportItem, PlayEvent, PlaylistItem, TrackTag

    record: dict[str, Any] = {"moved": [], "shifted": [], "tags": [], "events": [], "imports": [], "downloads": [],
                              "ref": gone.ref, "refs": [], "playlists": []}
    mine = {i.playlist_id: i for i in (await s.execute(select(PlaylistItem).where(PlaylistItem.track_id == keep.id))).scalars()}
    for item in (await s.execute(select(PlaylistItem).where(PlaylistItem.track_id == gone.id))).scalars():
        record["playlists"].append(item.playlist_id)
        there = mine.get(item.playlist_id)
        if there is None:
            item.track_id = keep.id
            mine[item.playlist_id] = item
            record["moved"].append(item.id)
        elif item.position < there.position:  # the copy stood earlier: the one that stays takes its place
            record["shifted"].append([there.id, there.position])
            there.position = item.position
    await s.flush()
    have = set((await s.execute(select(TrackTag.tag_id).where(TrackTag.track_id == keep.id))).scalars())
    for tag_id, auto in (await s.execute(select(TrackTag.tag_id, TrackTag.auto).where(TrackTag.track_id == gone.id))).all():
        if tag_id not in have and not auto:
            s.add(TrackTag(track_id=keep.id, tag_id=tag_id, auto=False))
            record["tags"].append(tag_id)
    for key, model in (("events", PlayEvent), ("imports", ImportItem), ("downloads", DownloadItem)):
        ids = list((await s.execute(update(model).where(model.track_id == gone.id).values(track_id=keep.id)
                                    .returning(model.id))).scalars())
        record[key] = ids
    record["refs"] = sorted({r for r in (gone.ref, *(await s.execute(
        select(TrackCatalogRef.ref).where(TrackCatalogRef.track_id == gone.id))).scalars()) if r})
    record["playlists"] = sorted(set(record["playlists"]))
    await s.flush()
    return record


async def bind_merged(s: AsyncSession, keep: Track, record: dict[str, Any]) -> None:
    """After `merge_into`, once the track that went is no more: its catalog records name `keep` — a like, a
    transfer's row, a page of the catalog finds the library's track, not a new one without a file."""
    before = set((await s.execute(select(TrackCatalogRef.ref).where(TrackCatalogRef.track_id == keep.id))).scalars())
    await bind_refs(s, keep, record.get("refs") or [])
    after = set((await s.execute(select(TrackCatalogRef.ref).where(TrackCatalogRef.track_id == keep.id))).scalars())
    record["bound"] = sorted(after - before)


async def unmerge(s: AsyncSession, back: Track, keep: Track, record: dict[str, Any]) -> set[int]:
    """`merge_into` taken back: `back` (the track that went, brought back) has its own again — its places in the
    playlists (`keep`'s moved back to theirs), tags, listens, rows and catalog records. Returns the playlists."""
    from sqlalchemy import delete, update

    from app.models import DownloadItem, ImportItem, PlayEvent, PlaylistItem, TrackTag

    for item in (await s.execute(select(PlaylistItem).where(PlaylistItem.id.in_(record.get("moved") or []),
                                                            PlaylistItem.track_id == keep.id))).scalars():
        item.track_id = back.id
    for item_id, position in record.get("shifted") or []:
        if (item := await s.get(PlaylistItem, item_id)) is not None and item.track_id == keep.id:
            item.position = position
    if record.get("tags"):
        await s.execute(delete(TrackTag).where(TrackTag.track_id == keep.id, TrackTag.tag_id.in_(record["tags"]),
                                               TrackTag.auto.is_(False)))
    for key, model in (("events", PlayEvent), ("imports", ImportItem), ("downloads", DownloadItem)):
        if record.get(key):
            await s.execute(update(model).where(model.id.in_(record[key]), model.track_id == keep.id)
                            .values(track_id=back.id))
    if record.get("bound"):
        await s.execute(delete(TrackCatalogRef).where(TrackCatalogRef.track_id == keep.id,
                                                      TrackCatalogRef.ref.in_(record["bound"])))
    await s.flush()
    if record.get("ref") and not back.ref and (await by_ref(s, record["ref"])) is None:
        back.ref = record["ref"]
    await bind_refs(s, back, [r for r in record.get("refs") or [] if r != back.ref])
    await s.flush()
    return set(record.get("playlists") or [])


class Parted(ValueError):
    """A join of two tracks that can no longer be taken back as it was: what it says."""


async def join(s: AsyncSession, gone: Track, keep: Track, *, user_id: int | None) -> dict[str, Any]:
    """Two tracks that are one recording (a copy told by the sound) become one: `keep`. Nothing of the listener's is
    deleted — all of `gone` that is theirs is `keep`'s (`merge_into`: the ♥, the places in playlists, tags, listens);
    its files are `keep`'s and go to the trash folder at once, as files of `keep` (the journal of `keep`'s files says
    so and brings one back from there, `app.library.files`); the journal of its files is `keep`'s; its own picture is
    `keep`'s where `keep` has none. Then the row of `gone` is no more — its album with it, when it was the album's
    last track. Returns what `part` takes back."""
    from sqlalchemy import func, update

    from app.library import files, trash
    from app.models import Album, TrackCover, TrackFileEvent

    merged = await merge_into(s, gone, keep)
    record: dict[str, Any] = {"merged": merged, "track": trash.row_of(gone), "album": None, "cover": False}
    record["journal"] = list((await s.execute(update(TrackFileEvent).where(TrackFileEvent.track_id == gone.id)
                                              .values(track_id=keep.id).returning(TrackFileEvent.id))).scalars())
    trashed = []
    for f in await files.of_track(s, gone.id):
        f.track_id, f.main = keep.id, False
        await s.flush()
        trashed.append((await files.trash(s, f, why="double", user_id=user_id, promote=False)).id)
    record["files"] = trashed
    if gone.cover_key and not keep.cover_key:
        await s.execute(update(TrackCover).where(TrackCover.track_id == gone.id).values(track_id=keep.id))
        keep.cover_key, record["cover"] = gone.cover_key, True
    # what stays on it after `merge_into` (its places where `keep` stands too, its tags) goes with its row: written down
    record["hung"] = await trash.hung_on(s, gone.id)
    album = None
    if gone.album_id is not None and not (await s.execute(select(func.count(Track.id)).where(
            Track.album_id == gone.album_id, Track.id != gone.id))).scalar_one():
        album = await s.get(Album, gone.album_id)
        record["album"] = trash.row_of(album) if album is not None else None
    await s.delete(gone)
    await s.flush()
    if album is not None:  # its last track is gone: the album goes with it
        await s.delete(album)
        await s.flush()
    await bind_merged(s, keep, merged)  # what it bound is in `merged`: taken back with it
    return record


async def part(s: AsyncSession, keep: Track, record: dict[str, Any], *, user_id: int | None) -> tuple[Track, set[int]]:
    """`join` taken back: the track that went is again, its files back from the trash folder, its journal and all
    that was its own its again (`unmerge`). `Parted`: a file of it is not in the trash folder any more (brought back
    to `keep` from its journal, or stirred out). Returns the track and the playlists that changed."""
    from sqlalchemy import update

    from app.library import files, trash
    from app.models import TrackCover, TrackFileEvent

    events = [await s.get(TrackFileEvent, i) for i in record.get("files") or []]
    if not events or any(e is None or e.track_id != keep.id or files.kept(e) is None for e in events):
        raise Parted("Файла копии уже нет в корзине: его вернули к этому треку или он стёрт")
    from app.library.importer import lock_library

    await lock_library(s)
    back = await trash.revive(s, record["track"], record.get("album"))
    await s.execute(update(TrackFileEvent).where(TrackFileEvent.id.in_(record.get("journal") or []),
                                                 TrackFileEvent.track_id == keep.id).values(track_id=back.id))
    for e in events:
        e.track_id = back.id
        await s.flush()
        await files.restore(s, e, why="revert", user_id=user_id)
    if record.get("cover"):
        await s.execute(update(TrackCover).where(TrackCover.track_id == keep.id).values(track_id=back.id))
        keep.cover_key = None
    elif back.cover_key:
        back.cover_key = None  # its picture was not kept: `keep` had one of its own
    await trash.hang_back(s, back, record.get("hung") or {})
    playlists = await unmerge(s, back, keep, record["merged"])
    return back, playlists


async def fold(s: AsyncSession, stub: Track, into: Track) -> set[int]:
    """A track without a file (made of a catalog's record: a like, a transfer's row) found to be a track the library
    has (its download found the song there already): it becomes that track (`merge_into`) and is gone. Returns the
    playlists that changed. A track with a file is not folded."""
    if stub.id == into.id or await has_file(s, stub.id):
        return set()
    record = await merge_into(s, stub, into)
    await s.delete(stub)
    await s.flush()
    await bind_merged(s, into, record)
    return set(record["playlists"])


async def fold_known() -> int:
    """The tracks without a file whose song a download or a transfer's row found in the library under another track
    (before `fold` was done there): folded into it. Quick — by the rows that say so — and safe to run again: at the
    worker's start."""
    from sqlalchemy import text

    from app.db import session_scope

    async with session_scope() as s:
        pairs = (await s.execute(text("""
            SELECT DISTINCT ON (stub.id) stub.id, x.track_id FROM tracks stub
            JOIN (SELECT ref, track_id FROM import_items WHERE ref IS NOT NULL AND track_id IS NOT NULL
                  UNION SELECT query_json->'meta'->>'track_ref', track_id FROM download_items
                        WHERE status = 'done' AND track_id IS NOT NULL AND query_json->'meta'->>'track_ref' IS NOT NULL) x
              ON x.ref = stub.ref AND x.track_id <> stub.id
            WHERE NOT EXISTS (SELECT 1 FROM track_files f WHERE f.track_id = stub.id)
              AND EXISTS (SELECT 1 FROM track_files f WHERE f.track_id = x.track_id)
            ORDER BY stub.id, x.track_id"""))).all()
        n, changed = 0, set()
        for stub_id, into_id in pairs:
            stub, into = await s.get(Track, stub_id), await s.get(Track, into_id)
            if stub is None or into is None:
                continue
            changed |= await fold(s, stub, into)
            n += 1
        if changed:
            from app.models import Playlist
            from app.playlists_mirror import enqueue_mirror

            for p in (await s.execute(select(Playlist).where(Playlist.id.in_(changed), Playlist.kind == "user"))).scalars():
                await enqueue_mirror(s, p.id)
        await s.commit()
    return n
