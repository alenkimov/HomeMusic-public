"""Uploads of own audio files from the phone (FR-5): stream to staging → edit tags → import into an album."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import UploadCommitIn, UploadCommitOut, UploadMatch, UploadMatchIn, UploadOut, UploadTwin
from app.auth import current_user
from app.config import get_settings
from app.covers import kept_path
from app.db import get_session
from app.events import bus
from app.jobs.downloads import enqueue_scan, owe_cover
from app.jobs.queue import notify
from app.library import acoustic, artists, probe, quality, recognize, tagger
from app.library.artwork import cover_info
from app.library.credits import main_artist, split_artists
from app.library.importer import ImportSpec, copy_limited, import_file
from app.library.owned import find_owned
from app.library.tracks import current_file
from app.models import Track, Upload, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

_better = quality.better


def _match(tags: dict) -> UploadMatch | None:
    """What the file was told to be (`_match` of its tags), as the form shows it."""
    m = tags.get("_match")
    if not m:
        return None
    from app.library import titles

    meta = m.get("meta") or {}
    # the record's name as the file will be filed under it: without the credits written into it
    name, guests, _ = titles.clean(meta.get("title"))
    _, credited = titles.with_guests(meta.get("artist"), meta.get("artists") or [meta.get("artist") or ""], guests)
    return UploadMatch(state=m["state"], why=m.get("why"), by_hand=bool(m.get("by_hand")), ref=meta.get("track_ref"),
                       title=name if meta.get("title") else None, artist=meta.get("artist"), artists=[a for a in credited if a],
                       album=meta.get("album"), album_artist=meta.get("album_artist"), album_artists=meta.get("album_artists") or [],
                       album_ref=f"dz:{meta['deezer_album_id']}" if meta.get("deezer_album_id") else None,
                       record_type=meta.get("record_type"),
                       year=meta.get("year") or (int(str(meta["release_date"])[:4]) if meta.get("release_date") else None),
                       duration_s=meta.get("duration_s"), cover_url=meta.get("cover_url"), track_no=meta.get("track_no"))


async def _find_twin(s: AsyncSession, u: Upload) -> dict | None:
    """Look for the library's track this file is of already: by its sound; where the sound finds nothing — by the
    same names and length, as a download is told to be owned. {track_id, by, likeness}."""
    tags = u.probed_tags_json or {}
    hit = await acoustic.twin(s, await _chroma(s, u), u.duration_s)
    if hit is not None:
        return {"track_id": hit[0].id, "by": "sound", "likeness": round(hit[2], 3)}
    if tags.get("artist") and tags.get("title"):
        track = await find_owned(s, artist=str(tags["artist"]), title=str(tags["title"]),
                                 duration_s=round(u.duration_s) if u.duration_s else None)
        if track is not None:
            return {"track_id": track.id, "by": "name", "likeness": None}
    return None


async def _twin(s: AsyncSession, u: Upload) -> UploadTwin | None:
    """The library's track this file is of already (`_find_twin`), as the form shows it. It is looked for once, when
    the file arrives, and kept with the upload (`_twin` of its tags): a list of uploads does not go through the
    library again. An upload from before that is looked for when it is first shown."""
    tags = dict(u.probed_tags_json or {})
    if "_twin" not in tags:
        if await _chroma(s, u) is None and _abs(u).is_file():
            sound = await acoustic.of_file(_abs(u))
            if sound:
                u.chroma, u.duration_s = sound[0], u.duration_s or sound[1]
                await s.flush()
        tags["_twin"] = await _find_twin(s, u)
        u.probed_tags_json = tags
        await s.flush()
    found = tags.get("_twin")
    track = await s.get(Track, found["track_id"]) if found else None
    if track is None:
        return None
    f = await current_file(s, track.id)
    if f is None:
        return None
    sure = found["by"] == "name" or (found.get("likeness") or 0) >= acoustic.SAME
    return UploadTwin(track_id=track.id, title=track.title, artist=track.artist, album=track.album,
                      codec=f.codec, bitrate_kbps=f.bitrate_kbps, by=found["by"], sure=sure,
                      better=sure and _better(tags.get("codec"), tags.get("bitrate_kbps"), f.codec, f.bitrate_kbps))


async def _chroma(s: AsyncSession, u: Upload) -> bytes | None:
    return (await s.execute(select(Upload.chroma).where(Upload.id == u.id))).scalar_one_or_none()


async def _as_here(s: AsyncSession, m: UploadMatch | None) -> UploadMatch | None:
    """The record a file was told to be, as the Hub calls it: a song the listeners put right (app/library/edits.py)
    has the name they gave it — that is what the file will be filed under, not what the catalog says still."""
    from app.library.tracks import by_ref

    t = await by_ref(s, m.ref) if m is not None and m.ref else None
    if m is None or t is None or t.renamed_at is None:
        return m
    return m.model_copy(update={"title": t.title, "artist": split_artists(t.artist)[0] if split_artists(t.artist) else t.artist,
                                "artists": split_artists(t.artist) or [t.artist]})


async def upload_out(s: AsyncSession, u: Upload) -> UploadOut:
    twin = await _twin(s, u) if u.status == "uploaded" else None
    kept = dict(u.probed_tags_json or {})
    if kept.get("has_cover") and "cover_info" not in kept and _abs(u).is_file():  # an upload from before the mark
        kept["cover_info"] = cover_info(await asyncio.to_thread(tagger.read_cover, _abs(u)))
        u.probed_tags_json = kept
        await s.flush()
    tags = {k: v for k, v in kept.items() if not k.startswith("_")}
    artist, title = recognize.names_of(tags, u.orig_name)
    from app.library import titles

    title, guests, _ = titles.clean(title)  # the file's own name, as it will be filed: the guests are artists
    artist, _ = titles.with_guests(artist, None, guests)
    return UploadOut(id=u.id, orig_name=u.orig_name, size=u.size, status=u.status, tags=tags,
                     error=u.error, created_at=u.created_at, artists=split_artists(artist),
                     codec=tags.get("codec"), bitrate_kbps=tags.get("bitrate_kbps"), twin=twin, match=await _as_here(s, _match(kept)),
                     weak=quality.weak(tags.get("codec"), tags.get("bitrate_kbps")), title=title, artist=artist,
                     single=recognize.single_album(tags.get("album"), title), duration_s=u.duration_s)


_catalog_covers: dict[str, dict | None] = {}


def _upload_dir(user_id: int) -> Path:
    d = get_settings().staging_dir / "uploads" / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _abs(u: Upload) -> Path:
    return get_settings().staging_dir / Path(u.staging_path)


@router.post("", response_model=list[UploadOut])
async def upload(files: list[UploadFile] = File(...), user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    cfg = get_settings().cfg.uploads
    limit = cfg.max_file_mb * 1024 * 1024
    out = []
    for f in files:
        name = Path(f.filename or "file").name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in cfg.extensions:
            raise HTTPException(415, f"Формат не поддерживается: {name}")
        rel = Path("uploads") / str(user.id) / f"{uuid.uuid4().hex}.{ext}"
        dest = get_settings().staging_dir / rel
        _upload_dir(user.id)
        try:
            size = await asyncio.to_thread(copy_limited, f.file, dest, limit)
        except ValueError:
            dest.unlink(missing_ok=True)
            raise HTTPException(413, f"Файл больше {cfg.max_file_mb} МБ: {name}") from None
        try:
            tags = await asyncio.to_thread(tagger.read_tags, dest)
        except Exception as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"Не удалось прочитать файл {name}: {e}") from e
        # what the file is — its codec, its bitrate, its length — and the picture of its sound: by them it is told
        # to be a recording the library has, and whether it is the better file of the two
        try:
            info = await probe.probe(dest)
            tags["codec"], tags["bitrate_kbps"] = info.codec, info.bitrate_kbps
        except Exception as e:
            log.info("upload %s not probed: %r", name, e)
            info = None
        # the mark of the file's picture: files of one album whose pictures differ are told by it
        tags["cover_info"] = cover_info(await asyncio.to_thread(tagger.read_cover, dest)) if tags.get("has_cover") else None
        sound = await acoustic.of_file(dest)
        duration = (info.duration_s if info else None) or (sound[1] if sound else None)
        tags["_match"] = await recognize.of_file(tags, name, duration)
        u = Upload(user_id=user.id, staging_path=rel.as_posix(), orig_name=name, size=size,
                   probed_tags_json=json.loads(json.dumps(tags, default=str)),
                   chroma=sound[0] if sound else None,
                   duration_s=duration)
        s.add(u)
        await s.flush()
        out.append(await upload_out(s, u))
    return out


@router.get("", response_model=list[UploadOut])
async def list_uploads(status: str | None = "uploaded", user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    stmt = select(Upload).where(Upload.user_id == user.id)
    if status:
        stmt = stmt.where(Upload.status == status)
    rows = list((await s.execute(stmt.order_by(Upload.id).limit(5000))).scalars())
    return [await upload_out(s, u) for u in rows]


async def _own(s: AsyncSession, user: User, upload_id: int) -> Upload:
    u = await s.get(Upload, upload_id)
    if u is None or u.user_id != user.id:
        raise HTTPException(404, "Файл не найден")
    return u


@router.get("/{upload_id}/cover")
async def cover(upload_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    u = await _own(s, user, upload_id)
    p = _abs(u)
    data = await asyncio.to_thread(tagger.read_cover, p) if p.is_file() else None
    if not data:
        raise HTTPException(404, "Нет обложки")
    return Response(data, media_type="image/jpeg")


@router.get("/{upload_id}/audio")
async def audio(upload_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The uploaded file itself, to listen to before it is brought in."""
    p = _abs(await _own(s, user, upload_id))
    if not p.is_file():
        raise HTTPException(404, "Файла нет")
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@router.get("/{upload_id}/preview")
async def preview(upload_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> dict:
    """Half a minute of the catalog's record the file was told to be: where it is heard from."""
    from app.services import svc

    u = await _own(s, user, upload_id)
    ident = (((u.probed_tags_json or {}).get("_match") or {}).get("meta") or {}).get("deezer_track_id")
    url = await svc.deezer.preview_url(ident) if ident and svc.deezer else None
    if not url:
        raise HTTPException(404, "У каталога нет фрагмента этой записи")
    return {"url": url}


@router.get("/{upload_id}/catalog-cover")
async def catalog_cover(upload_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")) -> dict:
    """Measure the picture from the catalog once."""
    from app.covers import fetch_cover

    u = await _own(s, user, upload_id)
    url = ((((u.probed_tags_json or {}).get("_match") or {}).get("meta") or {}).get("cover_url"))
    if not url:
        raise HTTPException(404, "У записи каталога нет обложки")
    if url not in _catalog_covers:
        if len(_catalog_covers) > 2000:
            _catalog_covers.clear()
        info = cover_info(await fetch_cover(url))
        if info is None:  # the catalog did not give it just now: asked again the next time
            return {}
        _catalog_covers[url] = info
    return _catalog_covers[url] or {}


@router.post("/{upload_id}/match", response_model=UploadOut)
async def match(upload_id: int, body: UploadMatchIn, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    """Which record of the catalog the file is: the one picked by hand (`ref`), or looked for again."""
    u = await _own(s, user, upload_id)
    tags = dict(u.probed_tags_json or {})
    try:
        tags["_match"] = await recognize.of_ref(body.ref) if body.ref else await recognize.of_file(tags, u.orig_name, u.duration_s)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"Каталог не ответил: {e}") from e
    u.probed_tags_json = json.loads(json.dumps(tags, default=str))
    await s.flush()
    return await upload_out(s, u)


@router.delete("/{upload_id}")
async def remove(upload_id: int, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    u = await _own(s, user, upload_id)
    _abs(u).unlink(missing_ok=True)
    u.status = "discarded"
    return {"ok": True}


def _own_spec(u: Upload, tr, tags: dict) -> ImportSpec:
    """The file under its own names, as the form has them: an «album» that is no album makes a single."""
    title = tr.title.strip() or recognize.names_of(tags, u.orig_name)[1]
    line = tr.artist.strip() or recognize.names_of(tags, u.orig_name)[0] or "Неизвестный исполнитель"
    single = recognize.single_album(tr.album, title)
    return ImportSpec(
        artist=line, artists=split_artists(line), title=title,
        album=title if single else " ".join((tr.album or "").split()),
        album_artist=(tr.album_artist or "").strip() or main_artist(line),
        year=tr.year, release_type="single" if single else (tags.get("release_type") or "album"),
        track_no=1 if single else (tr.track_no or tags.get("track_no")), disc_no=1 if single else (tags.get("disc_no") or 1),
        genre=tags.get("genre"), isrc=tags.get("isrc"), explicit=tags.get("explicit"),
    )


async def _catalog_spec(meta: dict, *, with_cover: bool = True) -> ImportSpec:
    """The file as the song of the catalog it is: what a download of that song would be filed as."""
    from app.covers import fetch_cover
    from app.jobs.downloads import _parse_date

    released = _parse_date(meta.get("release_date"))
    single = not meta.get("album")
    ref = str(meta.get("track_ref") or "")
    return ImportSpec(
        artist=meta.get("artist") or "", title=meta.get("title") or "", artists=meta.get("artists") or [],
        artist_refs=meta.get("artist_refs") or [],
        album=meta.get("album") or meta.get("title"), album_artist=meta.get("album_artist") or meta.get("artist"),
        year=meta.get("year") or (released.year if released else None), release_date=released,
        release_type={"compile": "compilation"}.get(meta.get("record_type", ""), meta.get("record_type"))
        or ("single" if single else "album"),
        track_no=meta.get("track_no") or (1 if single else None), track_total=meta.get("track_total"),
        disc_no=meta.get("disc_no") or 1, genre=meta.get("genre"), genre_source=meta.get("genre_source"),
        catalog_album_ref=meta.get("album_ref"), isrc=meta.get("isrc"), explicit=meta.get("explicit"),
        deezer_track_id=meta.get("deezer_track_id"), ref=ref or None, deezer_album_id=meta.get("deezer_album_id"),
        upc=meta.get("upc"), cover=await fetch_cover(meta.get("cover_url")) if with_cover else None,
    )


async def _album_cover(s: AsyncSession, user: User, a) -> bytes | None:
    """The picture chosen for an album: the catalog's of an upload's record, or the one inside an uploaded file.
    None: none was chosen, or its file is in the library already (an earlier part of the same import wrote the
    album's cover from it) — the album keeps the cover it has."""
    from app.covers import fetch_cover

    if a.cover_of_match:
        u = await _own(s, user, a.cover_of_match)
        return await fetch_cover((((u.probed_tags_json or {}).get("_match") or {}).get("meta") or {}).get("cover_url"))
    if a.cover_of_file:
        src = _abs(await _own(s, user, a.cover_of_file))
        return await asyncio.to_thread(tagger.read_cover, src) if src.is_file() else None
    return None


@router.post("/commit", response_model=UploadCommitOut)
async def commit(body: UploadCommitIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    """Bring the files into the library, each as what it is: the song of the catalog it was told to be, or under
    its own names. An album is what the listener chose for it (`body.albums`) — one name, one artist, one year and
    one cover for all its files; a file with no album is a single. A file the catalog only has something like is
    filed under its own names, and whether the record is its own is settled by the sound (`library/upgrade.py`)."""
    from app.jobs.downloads import _parse_date
    from app.library import upgrade

    if not body.tracks:
        raise HTTPException(400, "Не выбраны файлы")
    albums = {a.key: a for a in body.albums}
    covers: dict[str, bytes | None] = {}
    releases: dict[str, dict] = {}
    for a in body.albums:
        covers[a.key] = await _album_cover(s, user, a)
        if a.release_of:
            releases[a.key] = (((await _own(s, user, a.release_of)).probed_tags_json or {}).get("_match") or {}).get("meta") or {}
    imported = weak = unsure = 0
    versions_of: list[tuple[int, str]] = []  # (a track brought in, the catalog's record it is another version of)
    owed: dict[int, str] = {}  # albums whose catalog picture did not come just now: album id -> the picture's address
    track_ids: list[int] = []
    album_ids: list[int] = []
    for tr in body.tracks:
        u = await _own(s, user, tr.upload_id)
        if u.status != "uploaded":
            continue
        src = _abs(u)
        if not src.is_file():
            u.status, u.error = "failed", "файл пропал из staging"
            continue
        tags = u.probed_tags_json or {}
        matched = tags.get("_match") or {}
        meta = matched.get("meta") if tr.catalog else None
        try:
            info = await probe.probe(src)
        except Exception as e:
            log.warning("probe failed for upload %s: %r", u.id, e)
            info = None
        album = albums.get(tr.album_key) if tr.album_key else None
        own = await asyncio.to_thread(tagger.read_cover, src)
        spec = await _catalog_spec(meta, with_cover=album is None and tr.cover != "file") if meta else _own_spec(u, tr, tags)
        if meta:
            # where the catalog only adds to the file's artists, the file's own stay first: a track of GONE.Fludd
            # the catalog credits to «M00NCHILD, GONE.Fludd» is GONE.Fludd's still, with M00NCHILD beside him
            mine = [a.casefold() for a in split_artists(recognize.names_of(tags, u.orig_name)[0])]
            if mine and all(a in [x.casefold() for x in spec.artists] for a in mine):
                refs = [*spec.artist_refs, *([None] * len(spec.artists))][:len(spec.artists)]  # each name's own record
                pairs = sorted(zip(spec.artists, refs), key=lambda p: mine.index(p[0].casefold()) if p[0].casefold() in mine else len(mine))
                spec.artists, spec.artist_refs = [p[0] for p in pairs], [p[1] for p in pairs]
                spec.artist = spec.artists[0]
            # a name the catalog only writes in other letters' case is the file's name: «Утром», not «утром»
            mine = recognize.names_of(tags, u.orig_name)[1]
            if mine.casefold() == spec.title.casefold():
                spec.title = mine
        if album is not None:  # the album is said once, for all its files
            rel = releases.get(album.key) or {}
            released = _parse_date(rel.get("release_date"))
            spec.album, spec.album_artist, spec.year = album.title.strip(), album.album_artist.strip(), album.year
            spec.deezer_album_id, spec.upc = rel.get("deezer_album_id"), rel.get("upc")
            spec.release_date = released if released and released.year == album.year else None
            spec.release_type = {"compile": "compilation"}.get(rel.get("record_type", ""), rel.get("record_type")) or ("single" if album.single else "album")
            spec.track_total = rel.get("track_total")
            if not spec.genre:
                spec.genre, spec.genre_source = rel.get("genre"), rel.get("genre_source")
            # its place in the album: the catalog's when its record stands on this very release, else the file's
            there = bool(meta and rel.get("deezer_album_id") and meta.get("deezer_album_id") == rel.get("deezer_album_id"))
            spec.track_no = tr.track_no or (meta.get("track_no") if there and meta else None) or (1 if album.single else tags.get("track_no"))
            spec.disc_no = (meta.get("disc_no") if there and meta else None) or tags.get("disc_no") or 1
            spec.cover = covers.get(album.key)  # none: the cover the album has (`import_file`)
            wanted = (((await _own(s, user, album.cover_of_match)).probed_tags_json or {}).get("_match") or {}).get("meta", {}).get("cover_url") if album.cover_of_match else None
        else:  # a single: its own picture — the catalog's, or the file's where that was chosen or the catalog has none
            wanted = meta.get("cover_url") if meta and tr.cover != "file" else None
            if spec.cover is None:
                spec.cover = own
        # the file of a track the library has (the upload is the better one): it becomes that track's file
        replaced = await s.get(Track, tr.replace_track_id) if tr.replace_track_id else None
        spec.track_id = replaced.id if replaced is not None else None
        spec.chroma = await _chroma(s, u)
        spec.source, spec.source_url, spec.added_by = "upload", u.orig_name, user.id
        spec.duration_s = round(info.duration_s) if info and info.duration_s else None
        spec.bitrate_kbps, spec.codec = (info.bitrate_kbps, info.codec) if info else (None, None)
        try:
            track = await import_file(s, src, spec)
        except Exception as e:
            log.exception("upload import failed")
            u.status, u.error = "failed", str(e)[:500]
            continue
        u.status, u.track_id = "imported", track.id
        imported += 1
        if tr.also_title:  # the name the file had is one the song is known by, the listener says
            await artists.add_title(s, track, tr.also_title, "file")
        weak += quality.weak(spec.codec, spec.bitrate_kbps)
        track_ids.append(track.id)
        if track.album_id and track.album_id not in album_ids:
            album_ids.append(track.album_id)
        if wanted and track.album_id and track.album_id not in owed and not kept_path(wanted).is_file():
            owed[track.album_id] = wanted  # the catalog did not give the picture: it is put on the album when it does
        record = str((matched.get("meta") or {}).get("track_ref") or "")
        if tr.version_of_match and not tr.catalog and record:
            versions_of.append((track.id, record))  # tied once the import is written down (below)
        elif not tr.catalog and not tr.no_check and matched.get("state") == "doubt" and matched.get("meta"):
            await upgrade.start_identify(s, user.id, track, matched["meta"])  # is the record its own: by the sound
            unsure += 1
    for album_id, url in owed.items():
        await owe_cover(s, album_id, url)
    await enqueue_scan(s)
    if body.tag_ids and track_ids:
        from app import tags

        await tags.assign_tracks(s, user.id, track_ids, body.tag_ids)
    playlist_ids = list(dict.fromkeys(body.playlist_ids))
    if (playlist_ids or body.keep_in) and track_ids:  # the playlists are the Hub's: the tracks are in them at once
        from app import playlists
        from app.jobs.downloads import into_playlists

        if body.keep_in and body.keep_in.strip():
            playlist_ids.append(str((await playlists.named(s, user.id, body.keep_in)).id))
        await into_playlists(s, user.id, playlist_ids, track_ids)
    if body.star and track_ids:
        from app.api.schemas import FavoriteTarget
        from app.favorites import command, project_liked

        # the like is the track's own: Navidrome gets the ♥ when its scan has found the file (`deliver_pending`)
        await command(s, user, [FavoriteTarget(track_id=t) for t in track_ids], liked=True, client="hub",
                      context="upload")
        await project_liked(s, user.id)
    await s.commit()
    # another recording of the song the catalog took a file for: tied to that record as a version — the record, the one
    # the file was joined to, stays the preferred one. After the import is written down: meeting the record asks the
    # catalog, which keeps its answers through a connection of its own
    if versions_of:
        from app.library import versions
        from app.lyrics.service import track_for

        for track_id, record in versions_of:
            theirs = await track_for(s, ref=record, user_id=user.id)
            await s.commit()
            mine = await s.get(Track, track_id)
            if theirs is not None and mine is not None and theirs.id != mine.id:
                await versions.link(s, user.id, theirs, mine)
                await s.commit()
    notify()
    bus.publish(user.id, "library", {"kind": "imported", "album_id": album_ids[0] if album_ids else None})
    return UploadCommitOut(album_ids=album_ids, imported=imported, track_ids=track_ids, weak=weak, unsure=unsure)


def purge_stale_uploads(days: int = 7) -> int:
    """Remove staged upload files never committed (called by the daily housekeeping)."""
    import time

    root = get_settings().staging_dir / "uploads"
    if not root.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
            n += 1
    shutil.rmtree(root / "_tmp", ignore_errors=True)
    return n
