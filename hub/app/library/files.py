"""A track's files: which of them is the main one, and the journal of what happened to them.

A track has its files in the library (`TrackFile`): one is the main one — the one that plays, goes to the offline
copies and is the track's song in Navidrome — and the others are kept beside it (a version without the censor's
cuts picked next to the old one). Everything that changes them goes through here, and each change is written in the
track's journal (`TrackFileEvent`):

- a file comes (`added`, `added_file`: downloaded, uploaded, picked, found better) — and is the main one: what was
  the main one is kept beside it;
- a file is made the main one (`make_main`);
- a file goes to the trash folder (`trash`): its row goes, the journal keeps the file as it was and where it lies;
  the main one gone, the newest of the others is the main one;
- a file comes back from the trash folder (`restore`) as the main one — as long as the trash folder keeps it;
- the main file is replaced (`replace_main`): the new one comes, and only the main one goes to the trash folder —
  the others stay. It is taken back from the journal (`restore`), as any file in the trash folder.

When the main file changes, a ♥ the listeners have on the track moves in Navidrome from the old file's song to the
new one's, once Navidrome has scanned it (`move_stars`, after each scan).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TrackFile, TrackFileEvent, utcnow

log = logging.getLogger(__name__)

# what a file is, kept in the journal: what it is brought back from
_FIELDS = ("uid", "path", "nd_song_id", "codec", "bitrate_kbps", "source", "source_url", "source_upload_date",
           "acquired_at", "locked", "warnings", "added_by")


class FileGone(ValueError):
    """The file is not there any more (taken elsewhere, purged from the trash folder)."""


def snapshot(f: TrackFile) -> dict[str, Any]:
    out = {}
    for k in _FIELDS:
        v = getattr(f, k)
        out[k] = v.isoformat() if isinstance(v, (date, datetime)) else v
    return out


async def main_of(s: AsyncSession, track_id: int) -> TrackFile | None:
    """The track's main file (None: it has no file)."""
    return (await s.execute(select(TrackFile).where(TrackFile.track_id == track_id, TrackFile.main))).scalar_one_or_none()


async def mains(s: AsyncSession, track_ids: list[int] | set[int]) -> dict[int, TrackFile]:
    """track id -> its main file, for the tracks that have one."""
    ids = list(dict.fromkeys(track_ids))
    out: dict[int, TrackFile] = {}
    for k in range(0, len(ids), 500):
        out.update((f.track_id, f) for f in (await s.execute(select(TrackFile).where(
            TrackFile.track_id.in_(ids[k:k + 500]), TrackFile.main))).scalars())
    return out


async def of_track(s: AsyncSession, track_id: int) -> list[TrackFile]:
    """The track's files: the main one first, then the newest."""
    return list((await s.execute(select(TrackFile).where(TrackFile.track_id == track_id)
                                 .order_by(TrackFile.main.desc(), TrackFile.id.desc()))).scalars())


async def note(s: AsyncSession, track_id: int, kind: str, *, file: TrackFile | None = None,
               data: dict[str, Any] | None = None, why: str, user_id: int | None = None, trash_path: str | None = None,
               star_from: str | None = None, download_item_id: int | None = None) -> TrackFileEvent:
    event = TrackFileEvent(track_id=track_id, at=utcnow(), kind=kind, file_id=file.id if file is not None else None,
                           file=data if data is not None else snapshot(file) if file is not None else {}, why=why,
                           trash_path=trash_path, star_from=star_from, download_item_id=download_item_id,
                           user_id=user_id)
    s.add(event)
    await s.flush()
    return event


async def added(s: AsyncSession, file: TrackFile, *, why: str, user_id: int | None,
                download_item_id: int | None = None) -> None:
    """A new file of its track (the row not written yet): written as the main one, what was the main one kept beside
    it. Called by the importer as it files it (`importer.import_file`)."""
    old = await main_of(s, file.track_id)
    if old is not None:
        old.main = False
        await s.flush()  # one main file a track: the old one is not before the new one is
    file.main = True
    s.add(file)
    await s.flush()
    await note(s, file.track_id, "added", file=file, why=why, user_id=user_id, download_item_id=download_item_id,
               star_from=old.nd_song_id if old is not None else None)


async def make_main(s: AsyncSession, file: TrackFile, *, why: str = "listener", user_id: int | None = None) -> None:
    """This file of its track plays from now on."""
    old = await main_of(s, file.track_id)
    if old is not None and old.id == file.id:
        return
    if old is not None:
        old.main = False
        await s.flush()
    file.main = True
    await s.flush()
    await note(s, file.track_id, "main", file=file, why=why, user_id=user_id,
               star_from=old.nd_song_id if old is not None else None)


def _trash_place(rel: str) -> Path:
    from app.config import get_settings
    from app.library.layout import unique_path

    dest = get_settings().trash_dir / datetime.now().strftime("%Y-%m-%d") / Path(*PurePosixPath(rel).parts)
    return unique_path(dest.parent, dest.name) if dest.exists() else dest


async def trash(s: AsyncSession, file: TrackFile, *, why: str, user_id: int | None = None,
                promote: bool = True) -> TrackFileEvent:
    """The file goes to the trash folder (kept there `library.trash_days`), its row with it; the journal keeps what
    it was and where it lies. The main one gone, the newest of the track's others is the main one (unless
    `promote` is False: one comes in its place)."""
    from app.library.importer import _move, library_abs, library_lock

    was_main, track_id, data = file.main, file.track_id, snapshot(file)
    src = library_abs(file.path)
    async with library_lock:
        dest: Path | None = None
        if await asyncio.to_thread(src.is_file):
            dest = _trash_place(file.path)
            await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(_move, src, dest)
            from app.library.editor import _remove_empty_dirs

            await asyncio.to_thread(_remove_empty_dirs, src.parent)
        await s.delete(file)
        await s.flush()
    event = await note(s, track_id, "trashed", data=data, why=why, user_id=user_id,
                       trash_path=str(dest.resolve()) if dest is not None else None)
    if was_main and promote:
        rest = await of_track(s, track_id)
        if rest:
            await make_main(s, rest[0], why=why, user_id=user_id)
    return event


async def lost(s: AsyncSession, file: TrackFile) -> None:
    """The file is not on the disk any more (a backup brought back without it): its row goes, the journal keeps
    what it was; the main one gone, the newest of the track's others is the main one."""
    was_main, track_id, data = file.main, file.track_id, snapshot(file)
    await s.delete(file)
    await s.flush()
    await note(s, track_id, "lost", data=data, why="missing")
    if was_main and (rest := await of_track(s, track_id)):
        await make_main(s, rest[0], why="missing")


def kept(event: TrackFileEvent) -> Path | None:
    """The file of a «trashed» event, while the trash folder keeps it (None: brought back, or purged)."""
    if event.kind != "trashed" or not event.trash_path:
        return None
    p = Path(event.trash_path)
    return p if p.is_file() else None


async def restore(s: AsyncSession, event: TrackFileEvent, *, why: str = "listener", user_id: int | None = None) -> TrackFile:
    """A file of the trash folder (its «trashed» event) back into the library, beside the track's other files, as
    the main one. `FileGone`: the trash folder keeps it no more."""
    from app.library.importer import _move, library_abs, library_lock
    from app.library.layout import unique_path
    from app.models import Track

    src = kept(event)
    if src is None:
        raise FileGone("Этого файла в корзине уже нет")
    track = await s.get(Track, event.track_id)
    if track is None:
        raise FileGone("Трека уже нет")
    data = dict(event.file or {})
    main = await main_of(s, track.id)
    # where it was; else beside the main file, named as the trash folder kept it
    rel = data.get("path")
    if not rel and main is not None:
        rel = (PurePosixPath(main.path).parent / src.name).as_posix()
    if not rel:
        raise FileGone("Неизвестно, куда вернуть файл: у трека нет других файлов")
    async with library_lock:
        dest = library_abs(rel)
        if await asyncio.to_thread(dest.exists):
            dest = unique_path(dest.parent, dest.name)
        await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(_move, src, dest)
        uid = data.get("uid")
        if not uid or (await s.execute(select(TrackFile.id).where(TrackFile.uid == uid))).first() is not None:
            uid = str(uuid.uuid4())
        acquired = data.get("acquired_at")
        uploaded = data.get("source_upload_date")
        file = TrackFile(track_id=track.id, uid=uid, path=dest.relative_to(library_abs("")).as_posix(), nd_song_id=None,
                         codec=data.get("codec"), bitrate_kbps=data.get("bitrate_kbps"), source=data.get("source"),
                         source_url=data.get("source_url"), locked=bool(data.get("locked")),
                         source_upload_date=date.fromisoformat(uploaded) if uploaded else None,
                         acquired_at=datetime.fromisoformat(acquired) if acquired else utcnow(),
                         warnings=list(data.get("warnings") or []), added_by=data.get("added_by"), main=False)
        s.add(file)
        await s.flush()
    event.trash_path = None  # it is not there any more: brought back
    await note(s, track.id, "restored", file=file, why=why, user_id=user_id)
    await make_main(s, file, why=why, user_id=user_id)
    return file


async def replace_main(s: AsyncSession, track_id: int, path: Path, spec: Any, *, why: str, user_id: int | None,
                       expected_file_uid: str | None = None) -> TrackFileEvent | None:
    """`path` (in the staging folder) comes in the place of the track's main file, which goes to the trash folder
    (the track's other files stay). Returns the main file's «trashed» event (None: the track had no file): what
    takes the replacement back (`restore`). If the new file does not come in, the old one is put back where it
    was, and the caller's rollback brings its row back. `expected_file_uid`: the main file the caller saw — another
    one there is `TrackAlreadyFiled`."""
    from app.library.importer import TrackAlreadyFiled, _move, import_file, library_abs, library_lock

    main = await main_of(s, track_id)
    if expected_file_uid is not None and (main is None or main.uid != expected_file_uid):
        raise TrackAlreadyFiled()
    gone = await trash(s, main, why=why, user_id=user_id, promote=False) if main is not None else None
    spec.why = why
    try:
        await import_file(s, path, spec)
    except Exception:
        if gone is not None and (src := kept(gone)) is not None:
            home = library_abs(gone.file["path"])
            async with library_lock:
                if not await asyncio.to_thread(home.exists):
                    await asyncio.to_thread(home.parent.mkdir, parents=True, exist_ok=True)
                    await asyncio.to_thread(_move, src, home)
        raise
    if gone is not None:
        # the new file is the main one in the old one's place: the ♥ moves from the old file's song to it
        came = (await s.execute(select(TrackFileEvent).where(TrackFileEvent.track_id == track_id,
                                                             TrackFileEvent.kind == "added")
                                .order_by(TrackFileEvent.id.desc()).limit(1))).scalar_one()
        came.star_from = came.star_from or (gone.file or {}).get("nd_song_id")
        await s.flush()
    return gone


async def history(s: AsyncSession, track_id: int) -> list[TrackFileEvent]:
    """The journal of the track's files, the latest first."""
    return list((await s.execute(select(TrackFileEvent).where(TrackFileEvent.track_id == track_id)
                                 .order_by(TrackFileEvent.at.desc(), TrackFileEvent.id.desc()))).scalars())


async def move_stars(s: AsyncSession) -> int:
    """The main files changed since Navidrome last scanned: a listener's ♥ on the track moves there from the old
    file's song to the new one's, once Navidrome has the new one (a change whose new song it has not got yet waits
    for the next scan). Returns how many changes were carried over."""
    from app import playlists
    from app.auth import user_navidrome
    from app.events import bus
    from app.models import Playlist, PlaylistItem, User

    pending = list((await s.execute(select(TrackFileEvent).where(TrackFileEvent.star_from.is_not(None))
                                    .order_by(TrackFileEvent.id))).scalars())
    done = 0
    for event in pending:
        main = await main_of(s, event.track_id)
        if main is None:
            event.star_from = None
            continue
        if not main.nd_song_id:
            continue  # Navidrome has not scanned it yet
        old, new = event.star_from, main.nd_song_id
        if old == new:
            event.star_from = None
            continue
        users = list((await s.execute(select(User).join(Playlist, Playlist.user_id == User.id)
                                      .join(PlaylistItem, PlaylistItem.playlist_id == Playlist.id)
                                      .where(Playlist.kind == playlists.LIKED, PlaylistItem.track_id == event.track_id)
                                      .distinct())).scalars())
        moved = True
        for user in users:
            nd = user_navidrome(user)
            try:
                await nd.star(song_ids=[new])
                await nd.unstar(song_ids=[old])
                bus.publish(user.id, "library", {"kind": "starred", "song_ids": [new]})
            except Exception as e:  # Navidrome away: tried again after the next scan
                moved = False
                log.warning("♥ of track %s not moved to its new file for user %s: %r", event.track_id, user.id, e)
            finally:
                await nd.aclose()
        if moved:
            event.star_from = None
            done += 1
    await s.flush()
    return done
