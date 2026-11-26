"""A better file in the place of a weak one — the second, slow step of bringing music in.

A file brought from outside may be poor: an MP3 at 128 found somewhere. Once it is in the library (the first step
files it by what it is: `library/recognize.py`), the song is looked for anew through the sources, as any download —
by the catalog's record of it where there is one, else by the track's own names — and every file found is weighed
against the one the track has:

- it must be the better file of the two (`quality.better`), and not one blown up from a poorer;
- it must be the same recording, told by its sound. As alike as one recording encoded twice (`acoustic.SAME`): it
  takes the old file's place at once. Only alike (`acoustic.ALIKE` — a version, a remaster): it is kept aside and
  the listener says — they can hear both. Less alike: it is another recording, and is dropped.

Nothing better: the old file stays, and nobody is asked anything. What became of each track is written down
(`upgrades`) and shown on «Качество звука».

The same step settles what the first could not. A file the catalog only has something *like* — another length,
«demo» in its name — was filed under its own names, with the record kept (`start_identify`). The record's file is
downloaded and compared with the track's by the sound: the same recording — the track is the record's song after
all, and takes its names, its release and its cover (`adopt`), and the file too when it is the better one; only
alike — the listener hears both and says; another recording — the track stays as it is.

The track stays the track: its places in playlists, its likes, its lyrics, its tags and its names are its own; only
the file under it changes. The old file goes to the trash folder with the rest of what is deleted — and while it
lies there the listener can hear the two side by side and take the old one back (`revert`): the sound tells one
recording from another, not a censored copy from the one with the words in it.
"""

from __future__ import annotations

import asyncio
import logging
from array import array
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.config import get_settings
from app.db import session_scope
from app.library import acoustic, quality
from app.library.credits import split_artists
from app.library.importer import ImportSpec, TrackAlreadyFiled, _move, import_file, library_abs, library_lock
from app.library.layout import unique_path
from app.library.tracks import current_files
from app.models import DownloadBatch, DownloadItem, Track, TrackCover, TrackFile, Upgrade

log = logging.getLogger(__name__)

OPEN = ("queued", "searching", "found")  # states in which a track is not looked for again
SAME_LENGTH_S = 3  # two files this near in length, sounding the same, are one recording


def wanted(file: TrackFile | None) -> bool:
    """A track worth looking for anew: the file it has is weak."""
    return file is not None and quality.weak(file.codec, file.bitrate_kbps)


async def start(s: AsyncSession, user_id: int, track_ids: list[int]) -> int:
    """Queue the search of a better file for these tracks — the weak ones among them that are not being looked for
    already. The songs the catalog knows go first: they are found sooner and surer. How many were queued."""
    from app.catalog.models import CATALOGS
    from app.jobs.queue import enqueue as enqueue_job
    from app.providers.base import TrackQuery

    ids = list(dict.fromkeys(track_ids))
    busy = set((await s.execute(select(Upgrade.track_id).where(Upgrade.state.in_(OPEN)))).scalars())
    files = await current_files(s, ids)
    tracks = [t for t in [await s.get(Track, i) for i in ids if i not in busy] if t is not None and wanted(files.get(t.id))]
    tracks.sort(key=lambda t: (t.ref or "").partition(":")[0] not in CATALOGS)
    if not tracks:
        return 0
    batch = DownloadBatch(user_id=user_id, origin="upgrade", title="Качество звука")
    s.add(batch)
    await s.flush()
    for t in tracks:
        f = files[t.id]
        row = Upgrade(user_id=user_id, track_id=t.id, was_codec=f.codec, was_kbps=f.bitrate_kbps)
        s.add(row)
        await s.flush()
        meta: dict[str, Any] = {"upgrade_of": t.id, "upgrade_row": row.id}
        if t.ref and (source := t.ref.partition(":")[0]) in CATALOGS:
            meta["track_ref"] = t.ref
            if source == "dz":
                meta["deezer_track_id"] = t.ref.split(":", 1)[1]
        q = TrackQuery(artist=t.artist, title=t.title, duration_s=t.duration_s, isrc=t.isrc, explicit=t.explicit)
        item = DownloadItem(batch_id=batch.id, user_id=user_id, force=True, query_json={
            "query": q.model_dump(), "upgrade": row.id, "replaces_song": f.nd_song_id, "meta": meta})
        s.add(item)
        await s.flush()
        row.item_id = item.id
        await enqueue_job(s, "download_item", {"item_id": item.id})
    return len(tracks)


async def start_identify(s: AsyncSession, user_id: int, track: Track, meta: dict[str, Any]) -> None:
    """Queue the settling of what the track is: `meta` — the catalog's record it may be (a download's meta)."""
    from app.jobs.queue import enqueue as enqueue_job
    from app.providers.base import TrackQuery

    f = (await current_files(s, [track.id])).get(track.id)
    batch = DownloadBatch(user_id=user_id, origin="upgrade", title="Качество звука")
    s.add(batch)
    await s.flush()
    row = Upgrade(user_id=user_id, track_id=track.id, kind="identify", meta_json=meta,
                  was_codec=f.codec if f else None, was_kbps=f.bitrate_kbps if f else None)
    s.add(row)
    await s.flush()
    q = TrackQuery(artist=str(meta.get("artist") or track.artist), title=str(meta.get("title") or track.title),
                   duration_s=meta.get("duration_s"), isrc=meta.get("isrc"), explicit=meta.get("explicit"))
    item = DownloadItem(batch_id=batch.id, user_id=user_id, force=True, query_json={
        "query": q.model_dump(), "upgrade": row.id, "replaces_song": f.nd_song_id if f else None,
        "meta": {**meta, "upgrade_of": track.id, "upgrade_row": row.id, "identify": True}})
    s.add(item)
    await s.flush()
    row.item_id = item.id
    await enqueue_job(s, "download_item", {"item_id": item.id})


def of_meta(meta: dict[str, Any]) -> int | None:
    """The track a download is looking for a better file of (None: an ordinary download)."""
    v = meta.get("upgrade_of")
    return int(v) if v else None


async def mark(meta: dict[str, Any], **fields: Any) -> None:
    """Write down what became of the search a download carries."""
    row_id = meta.get("upgrade_row")
    if not row_id:
        return
    async with session_scope() as s:
        row = await s.get(Upgrade, int(row_id))
        if row is not None:
            for k, v in fields.items():
                setattr(row, k, v)


async def judge(s: AsyncSession, track_id: int, path: Path, codec: str | None, kbps: int | None,
                duration_s: float | None) -> tuple[str, str, float | None]:
    """What a file found is to the track's own: ("replace" | "ask" | "no", in words, how alike they sound)."""
    old = (await s.execute(select(TrackFile).where(TrackFile.track_id == track_id).options(undefer(TrackFile.chroma))
                           .order_by(TrackFile.id.desc()).limit(1))).scalar_one_or_none()
    if old is None:
        return "no", "у трека нет файла", None
    if not quality.better(codec, kbps, old.codec, old.bitrate_kbps):
        return "no", f"не лучше имеющегося ({(old.codec or '?').upper()} {old.bitrate_kbps or '?'})", None
    mine = old.chroma
    if not mine:  # a file from before the pictures of sound, or one brought back from the trash
        drawn = await acoustic.of_file(library_abs(old.path))
        mine = drawn[0] if drawn else None
        old.chroma = mine or b""
    got = await acoustic.of_file(path)
    if not mine or not got:
        return "no", "не удалось сравнить по звуку", None
    alike = round(await asyncio.to_thread(acoustic.likeness, got[0], mine), 3)
    if alike < acoustic.ALIKE:
        return "no", f"это другая запись (сходство по звуку {alike:.2f})", alike
    track = await s.get(Track, track_id)
    near = not (duration_s and track and track.duration_s) or abs(duration_s - track.duration_s) <= SAME_LENGTH_S
    if alike >= acoustic.SAME and near:
        # the same recording — yet a copy with words cut out is that too: where the two part at some places, the
        # listener hears them and says
        gaps = await acoustic.differences(library_abs(old.path), path)
        if gaps:
            return "ask", f"та же запись (сходство {alike:.2f}), но файлы расходятся {places(gaps)}", alike
        return "replace", f"та же запись (сходство по звуку {alike:.2f})", alike
    return "ask", f"похожая запись (сходство по звуку {alike:.2f}) — возможно, другая версия", alike


async def take(meta: dict[str, Any], path: Path, check: Any, cand: Any, user_id: int) -> tuple[str | None, str]:
    """Weigh a downloaded file against the track's own and do what follows: ("replaced" | "found" | None, in
    words). None: the file is no use — the caller drops it and tries the next one."""
    track_id = of_meta(meta)
    assert track_id is not None
    info = check.info
    codec, kbps = (info.codec, info.bitrate_kbps) if info else (None, getattr(cand, "bitrate_kbps", None))
    # a file made of a poorer one is worth what it sounds like, not what it says: it is not «the better file»
    if sounds := getattr(check, "blown_from_kbps", None):
        kbps = min(kbps, sounds) if kbps else sounds
    async with session_scope() as s:
        if meta.get("identify"):
            return await _settle(s, meta, track_id, path, codec, kbps, info.duration_s if info else None, cand, user_id)
        verdict, words, alike = await judge(s, track_id, path, codec, kbps, info.duration_s if info else None)
        if verdict == "no":
            return None, words
        found = dict(codec=codec, bitrate_kbps=kbps, likeness=alike, provider=getattr(cand, "provider", None),
                     source_url=getattr(cand, "page_url", None), note=words)
        if verdict == "replace":
            was = await put(s, track_id, path, user_id=user_id, **{k: found[k] for k in ("codec", "bitrate_kbps", "provider", "source_url")},
                            duration_s=info.duration_s if info else None)
            await mark_in(s, meta, state="replaced", staged_path=None, was_path=was, **found)
            return "replaced", words
        rel = Path("upgrades") / f"{meta.get('upgrade_row') or track_id}{path.suffix.lower()}"
        dest = get_settings().staging_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.unlink(missing_ok=True)
        await asyncio.to_thread(_move, path, dest)
        await mark_in(s, meta, state="found", staged_path=rel.as_posix(), **found)
        return "found", words


async def _settle(s: AsyncSession, meta: dict[str, Any], track_id: int, path: Path, codec: str | None, kbps: int | None,
                  duration_s: float | None, cand: Any, user_id: int) -> tuple[str | None, str]:
    """The file of the record the track may be, against the track's own: see the module's last paragraph."""
    old = (await s.execute(select(TrackFile).where(TrackFile.track_id == track_id).options(undefer(TrackFile.chroma))
                           .order_by(TrackFile.id.desc()).limit(1))).scalar_one_or_none()
    track = await s.get(Track, track_id)
    if old is None or track is None:
        return None, "у трека нет файла"
    mine = old.chroma
    if not mine:
        drawn = await acoustic.of_file(library_abs(old.path))
        mine = drawn[0] if drawn else None
    got = await acoustic.of_file(path)
    if not mine or not got:
        return None, "не удалось сравнить по звуку"
    alike = round(await asyncio.to_thread(acoustic.likeness, got[0], mine), 3)
    if alike < acoustic.ALIKE:
        return None, f"по звуку это другая запись (сходство {alike:.2f})"
    better = quality.better(codec, kbps, old.codec, old.bitrate_kbps)
    found = dict(codec=codec, bitrate_kbps=kbps, likeness=alike, provider=getattr(cand, "provider", None),
                 source_url=getattr(cand, "page_url", None))
    near = not (duration_s and track.duration_s) or abs(duration_s - track.duration_s) <= SAME_LENGTH_S
    # where the two part at some places — whichever is the better file: a record is not taken for the track's own
    # by itself while its file differs from the track's
    gaps = await acoustic.differences(library_abs(old.path), path) if alike >= acoustic.SAME and near else []
    if alike >= acoustic.SAME and near and not gaps:
        await adopt(s, track, meta)
        was = None
        if better:
            was = await put(s, track_id, path, user_id=user_id, **{k: found[k] for k in ("codec", "bitrate_kbps", "provider", "source_url")},
                            duration_s=duration_s)
        else:
            path.unlink(missing_ok=True)
        words = f"та же запись (сходство по звуку {alike:.2f}): взяты данные каталога" + (", файл заменён лучшим" if better else ", файл оставлен ваш")
        await mark_in(s, meta, state="replaced", staged_path=None, note=words, was_path=was, **found)
        return "replaced", words
    rel = Path("upgrades") / f"{meta.get('upgrade_row') or track_id}{path.suffix.lower()}"
    dest = get_settings().staging_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    await asyncio.to_thread(_move, path, dest)
    words = (f"похожая запись (сходство по звуку {alike:.2f})" if alike < acoustic.SAME
             else f"та же запись (сходство {alike:.2f}), но файлы расходятся {places(gaps)}" if gaps
             else f"звучит так же (сходство {alike:.2f}), но длина другая")
    await mark_in(s, meta, state="found", staged_path=rel.as_posix(), note=words, **found)
    return "found", words


async def adopt(s: AsyncSession, track: Track, meta: dict[str, Any]) -> None:
    """The track is the catalog's record `meta` after all: it takes the record's names, its number, its release and
    the release's cover — as a file found in the catalog is filed in the first step."""
    from app.covers import fetch_cover
    from app.jobs.downloads import _parse_date
    from app.library import editor
    from app.library.importer import _write_cover_file, album_reldir, get_or_create_album
    from app.library.tracks import by_ref

    released = _parse_date(meta.get("release_date"))
    single = not meta.get("album")
    spec = ImportSpec(
        artist=meta.get("artist") or track.artist, title=meta.get("title") or track.title, artists=meta.get("artists") or [],
        album=meta.get("album") or meta.get("title") or track.title, album_artist=meta.get("album_artist") or meta.get("artist"),
        year=meta.get("year") or (released.year if released else None), release_date=released,
        release_type={"compile": "compilation"}.get(meta.get("record_type", ""), meta.get("record_type")) or ("single" if single else "album"),
        deezer_album_id=meta.get("deezer_album_id"), upc=meta.get("upc"), genre=meta.get("genre"))
    async with library_lock:
        album = await get_or_create_album(s, spec)
        await s.flush()
        folder = library_abs(album_reldir(album))
        if not (folder / "cover.jpg").is_file():
            cover = await fetch_cover(meta.get("cover_url"))
            if cover:
                from app.library import tagger

                await asyncio.to_thread(lambda: _write_cover_file(folder, tagger.prepare_cover(cover)))
                album.cover_path = (album_reldir(album) / "cover.jpg").as_posix()
    # a name the catalog only writes in other letters' case stays as the file has it
    if spec.title.casefold() != track.title.casefold():
        track.title = spec.title
    track.artist = spec.artist
    track.track_no = meta.get("track_no") or (1 if single else track.track_no)
    track.disc_no = meta.get("disc_no") or track.disc_no
    track.isrc = (str(meta["isrc"]).upper() if meta.get("isrc") else track.isrc)
    ref = str(meta.get("track_ref") or "")
    if ref and await by_ref(s, ref) is None:
        track.ref = ref
    track.catalog = {**(track.catalog or {}), "artists": meta.get("artists") or [], "cover_url": meta.get("cover_url")}
    if track.album_id != album.id:
        await editor.move_tracks(s, [track.id], album)
    else:
        await editor.update_track(s, track, title=track.title)
    await s.flush()


async def mark_in(s: AsyncSession, meta: dict[str, Any], **fields: Any) -> None:
    row = await s.get(Upgrade, int(meta["upgrade_row"])) if meta.get("upgrade_row") else None
    if row is not None:
        for k, v in fields.items():
            setattr(row, k, v)


async def put(s: AsyncSession, track_id: int, path: Path, *, user_id: int | None, codec: str | None,
              bitrate_kbps: int | None, provider: str | None, source_url: str | None, duration_s: float | None = None,
              attach: bool = False, expected_file_uid: str | None = None,
              fallback_cover: bytes | None = None) -> str | None:
    """Put the file under the track, in the place of the files it has: tagged as the track is named, in the track's
    album. The old files go to the trash folder; if the new one does not come in, they are put back."""
    track = await s.get(Track, track_id)
    if track is None:
        raise ValueError("трек удалён")
    cover = (await s.execute(select(TrackCover.image).where(TrackCover.track_id == track_id))).scalar_one_or_none()
    spec = ImportSpec(
        artist=track.artist, title=track.title, artists=(track.catalog or {}).get("artists") or split_artists(track.artist),
        album=track.album, album_artist=track.album_artist, album_id=track.album_id, year=track.year,
        track_no=track.track_no, disc_no=track.disc_no, genre=track.genre, isrc=track.isrc, explicit=track.explicit,
        track_id=track.id, cover=cover, source=provider, source_url=source_url,
        duration_s=round(duration_s) if duration_s else track.duration_s, bitrate_kbps=bitrate_kbps, codec=codec,
        added_by=user_id)
    if attach:
        await import_file(s, path, spec, require_missing=True, fallback_cover=fallback_cover)
        return None
    moved = await shelve(s, track_id, expected_file_uid=expected_file_uid)
    try:
        await import_file(s, path, spec)
    except Exception:
        await unshelve(moved)
        raise
    return str(moved[-1][0]) if moved else None  # where the file it had lies now


async def shelve(s: AsyncSession, track_id: int, *, expected_file_uid: str | None = None) -> list[tuple[Path, Path]]:
    """Take the track's files out of the library into the trash folder, their rows with them. [(where it went,
    where it was)]: `unshelve` puts them back if the new file does not come."""
    moved: list[tuple[Path, Path]] = []
    async with library_lock:
        files = (await s.execute(select(TrackFile).where(TrackFile.track_id == track_id).order_by(TrackFile.id))).scalars().all()
        if expected_file_uid is not None and (not files or files[-1].uid != expected_file_uid):
            raise TrackAlreadyFiled()
        for f in files:
            src = library_abs(f.path)
            if src.is_file():
                dest = get_settings().trash_dir / datetime.now().strftime("%Y-%m-%d") / Path(*PurePosixPath(f.path).parts)
                if dest.exists():
                    dest = unique_path(dest.parent, dest.name)
                await asyncio.to_thread(_move, src, dest)
                moved.append((dest, src))
            await s.delete(f)
        await s.flush()
    return moved


async def unshelve(moved: list[tuple[Path, Path]]) -> None:
    for trashed, home in moved:
        if trashed.is_file() and not home.exists():
            await asyncio.to_thread(_move, trashed, home)


async def likeness_to(s: AsyncSession, track_id: int, path: Path) -> float | None:
    """How alike a file sounds to the one the track has, 0…1 (None: it could not be told)."""
    old = (await s.execute(select(TrackFile).where(TrackFile.track_id == track_id).options(undefer(TrackFile.chroma))
                           .order_by(TrackFile.id.desc()).limit(1))).scalar_one_or_none()
    if old is None:
        return None
    mine = old.chroma
    if not mine:  # a file from before the pictures of sound, or one brought back from the trash
        drawn = await acoustic.of_file(library_abs(old.path))
        mine = drawn[0] if drawn else None
        old.chroma = mine or b""
    got = await acoustic.of_file(path)
    if not mine or not got:
        return None
    return round(await asyncio.to_thread(acoustic.likeness, got[0], mine), 3)


async def take_picked(s: AsyncSession, user_id: int, track: Track, path: Path, *, codec: str | None, bitrate_kbps: int | None,
                      provider: str | None, source_url: str | None, duration_s: float | None, likeness: float | None,
                      expected_file_uid: str) -> Upgrade:
    """Put a file the listener picked themself in the place of the track's own — whatever it sounds like: a version
    without the censor's cuts, another master, are picked on purpose. It is written down as any replacement, so the
    two can be heard side by side and the old one taken back («Качество звука» → «Заменено»)."""
    old = (await current_files(s, [track.id])).get(track.id)
    row = Upgrade(user_id=user_id, track_id=track.id, kind="picked", state="replaced", codec=codec, bitrate_kbps=bitrate_kbps,
                  likeness=likeness, provider=provider, source_url=source_url, note="файл выбран вручную",
                  was_codec=old.codec if old else None, was_kbps=old.bitrate_kbps if old else None)
    s.add(row)
    row.was_path = await put(s, track.id, path, user_id=user_id, codec=codec, bitrate_kbps=bitrate_kbps, provider=provider,
                             source_url=source_url, duration_s=duration_s, expected_file_uid=expected_file_uid)
    await s.flush()
    return row


def places(gaps: list[dict[str, Any]]) -> str:
    """Where two files part, in words: «на 1:02», «в 3 местах (0:41, 1:02, 2:10)»."""
    at = [acoustic.clock(g["at"]) for g in gaps]
    return f"на {at[0]}" if len(at) == 1 else f"в {len(at)} местах ({', '.join(at[:4])}{'…' if len(at) > 4 else ''})"


def staged(row: Upgrade) -> Path | None:
    p = get_settings().staging_dir / Path(row.staged_path) if row.staged_path else None
    return p if p is not None and p.is_file() else None


async def accept(s: AsyncSession, row: Upgrade, user_id: int, *, take_file: bool = True) -> None:
    """The listener's yes. To «a better file was found»: it takes the old one's place. To «is it that record?»:
    the track takes the record's names — and the file found takes the old one's place only where the listener
    said that too (`take_file`) and it is the better file; else the track keeps its own."""
    path = staged(row)
    if row.state != "found" or path is None:
        raise ValueError("Файл уже не ждёт решения")
    track = await s.get(Track, row.track_id)
    old = (await current_files(s, [row.track_id])).get(row.track_id)
    if row.kind == "identify" and track is not None:  # «это она»: the record's names — and its file if it is better
        await adopt(s, track, row.meta_json or {})
        if not take_file or (old is not None and not quality.better(row.codec, row.bitrate_kbps, old.codec, old.bitrate_kbps)):
            path.unlink(missing_ok=True)
            row.state, row.staged_path = "replaced", None
            row.note = "вы подтвердили запись: взяты данные каталога, файл оставлен ваш"
            return
    row.was_path = await put(s, row.track_id, path, user_id=user_id, codec=row.codec, bitrate_kbps=row.bitrate_kbps,
                             provider=row.provider, source_url=row.source_url)
    row.state, row.staged_path = "replaced", None


def was(row: Upgrade) -> Path | None:
    """The file the track had before, while it is kept in the trash folder."""
    p = Path(row.was_path) if row.was_path else None
    return p if p is not None and p.is_file() else None


async def revert(s: AsyncSession, row: Upgrade, user_id: int) -> None:
    """The listener takes back the file the track had: it comes out of the trash folder into the new one's place
    (the new one goes there in its turn)."""
    path = was(row)
    if row.state != "replaced" or path is None:
        raise ValueError("Прежнего файла уже нет — он хранится 30 дней")
    await put(s, row.track_id, path, user_id=user_id, codec=row.was_codec, bitrate_kbps=row.was_kbps, provider="upload",
              source_url=None)
    row.state, row.was_path, row.note = "kept", None, "вы вернули свой файл"


async def decline(s: AsyncSession, row: Upgrade) -> None:
    """The listener keeps the file the track has: the one found is thrown away."""
    if row.state != "found":
        raise ValueError("Файл уже не ждёт решения")
    if (path := staged(row)) is not None:
        path.unlink(missing_ok=True)
    row.state, row.staged_path = "kept", None


async def other_version(s: AsyncSession, user_id: int, row: Upgrade) -> None:
    """Neither «it is that record» nor «another song»: the track is another recording of the song the catalog's
    record is. The track keeps its file and its names, and the two are versions of one song from now on
    (app/library/versions.py) — the catalog's record the preferred one, as the one the other was joined to."""
    from app.library import versions
    from app.lyrics.service import track_for

    if row.kind != "identify":
        raise ValueError("Это не вопрос о записи каталога")
    ref = str((row.meta_json or {}).get("track_ref") or "")
    track = await s.get(Track, row.track_id)
    theirs = await track_for(s, ref=ref, user_id=user_id) if ref else None
    if track is None or theirs is None:
        raise ValueError("Каталог не отдаёт эту запись")
    if row.state == "found":
        await decline(s, row)
    else:
        row.state = "kept"
    if theirs.id != track.id:
        await versions.link(s, user_id, theirs, track)
    row.note = "другая версия той же песни"


_peaks: dict[tuple[str, float], list[int]] = {}


async def peaks(path: Path, n: int = 160) -> list[int]:
    """The file's loudness along its length, `n` numbers 0…100 — the picture a listener moves through it by."""
    key = (str(path), path.stat().st_mtime)
    if key in _peaks:
        return _peaks[key]
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "2000", "-f", "s16le", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    raw, _ = await proc.communicate()

    def fold() -> list[int]:
        a = array("h")
        a.frombytes(raw[: len(raw) // 2 * 2])
        if not a:
            return []
        step = max(1, len(a) // n)
        tops = [max(abs(v) for v in a[i:i + step]) for i in range(0, step * n, step) if a[i:i + step]]
        peak = max(tops) or 1
        return [round(100 * v / peak) for v in tops]

    out = await asyncio.to_thread(fold)
    if len(_peaks) > 200:
        _peaks.clear()
    _peaks[key] = out
    return out


async def purge(s: AsyncSession, days: int = 7) -> int:
    """Files found that nobody said anything about in a week are thrown away: the track keeps its own."""
    from datetime import timedelta

    from app.models import utcnow

    rows = list((await s.execute(select(Upgrade).where(Upgrade.state == "found", Upgrade.updated_at < utcnow() - timedelta(days=days)))).scalars())
    for row in rows:
        await decline(s, row)
    return len(rows)


