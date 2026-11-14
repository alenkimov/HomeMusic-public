"""What is already in the library: de-duplication before downloads and ownership badges for catalog pages.

Match order (PLAN 8.4 / 2.3): ISRC → Deezer track id → fingerprint (normalized artist+title) + duration ±3 s.
"""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AlbumOut, FileOut, TrackOut
from app.catalog.models import CatalogAlbum, CatalogTrack, split_ref
from app.matching.normalize import fingerprint, normalize_artist, normalize_title
from app.models import Album, DownloadItem, Track, TrackFile

ACTIVE_STATUSES = ("queued", "searching", "downloading", "tagging", "importing")


def quiet(query_json: dict[str, Any] | None) -> bool:
    """A download nobody asked for explicitly: queued by an import (`app.imports`), for a «Микс дня» (`app.mixes`)
    or a track of «Моя волна» played to its end (`app.mixes.wave`). Clients don't show it as a download."""
    qj = query_json or {}
    return bool(qj.get("import") or qj.get("mix") or qj.get("wave")) and not qj.get("requested")


# the remark of a file made of a poorer one (`jobs.downloads.try_candidates`)
_BLOWN_RE = re.compile(r"по звуку около (\d+)")


def file_out(f: TrackFile) -> FileOut:
    """A file as a list and the player say it."""
    blown = next((m for w in (f.warnings or []) if (m := _BLOWN_RE.search(str(w)))), None)
    return FileOut(source=f.source, codec=f.codec, bitrate_kbps=f.bitrate_kbps,
                   sounds_like_kbps=int(blown.group(1)) if blown else None)


async def with_files(s: AsyncSession, tracks: list[Any]) -> None:
    """The owned tracks of a list (`TrackOut`s) are told their files."""
    from app.library.tracks import current_files

    owned = [t for t in tracks if t.owned and t.library_track_id and t.file is None]
    if not owned:
        return
    ids = [t.library_track_id for t in owned]
    files = await current_files(s, ids)
    told = await with_journal(s, ids)
    for t in owned:
        if f := files.get(t.library_track_id):
            t.file = file_out(f)
            t.file.journal = t.library_track_id in told


async def with_journal(s: AsyncSession, track_ids: list[int]) -> set[int]:
    """The tracks whose file came by a download that kept its journal."""
    out: set[int] = set()
    for i in range(0, len(track_ids), 500):
        out.update((await s.execute(select(DownloadItem.track_id).where(
            DownloadItem.track_id.in_(track_ids[i:i + 500]), func.json_array_length(DownloadItem.log_json) > 0)
            .distinct())).scalars())
    return out


def _duration_ok(a: int | None, b: int | None, tol: int = 3) -> bool:
    return a is None or b is None or abs(a - b) <= tol


async def find_owned(
    s: AsyncSession,
    *,
    artist: str,
    title: str,
    duration_s: int | None = None,
    isrc: str | None = None,
    deezer_track_id: str | None = None,
) -> Track | None:
    """The track of this song that the library has a file of: by its ISRC, its catalog id, then by its names and
    length. A track the Hub knows but has no file of (it stands in a playlist, waiting) is not «in the library» —
    `library.tracks` is where a track is found whether it has a file or not."""
    newest = Track.id.desc()
    if isrc:
        t = (await s.execute(
            select(Track).where(Track.isrc == isrc.upper(), HAS_FILE).order_by(newest).limit(1))).scalar_one_or_none()
        if t:
            return t
    if deezer_track_id:
        t = (
            await s.execute(
                select(Track).where(Track.ref == f"dz:{deezer_track_id}", HAS_FILE).limit(1))
        ).scalar_one_or_none()
        if t:
            return t
    fp = fingerprint(artist, title)
    for t in (await s.execute(select(Track).where(Track.fingerprint == fp, HAS_FILE).order_by(newest))).scalars():
        if _duration_ok(t.duration_s, duration_s):
            return t
    return None


# the track has a file (`Track.path` is the path of its newest one)
HAS_FILE = Track.path.is_not(None)


def _dz_id(ref: str | None) -> str | None:
    if not ref:
        return None
    try:
        src, id_ = split_ref(ref)
    except ValueError:
        return None
    return id_ if src == "dz" else None


async def annotate_tracks(
    s: AsyncSession, tracks: Iterable[CatalogTrack], user_id: int | None = None
) -> list[TrackOut]:
    """Ownership and download state. A download queued only for ♥ is not «downloading»; for the user who liked the
    track any download that stars it is `star_pending` (the ♥ shows filled until the import stars the song)."""
    tracks = list(tracks)
    if not tracks:
        return []
    isrcs = {t.isrc.upper() for t in tracks if t.isrc}
    refs = {t.ref for t in tracks if t.source == "dz"}
    fps = {fingerprint(t.artist, t.title) for t in tracks}
    rows = (
        await s.execute(
            select(Track).where(
                or_(Track.isrc.in_(isrcs), Track.ref.in_(refs), Track.fingerprint.in_(fps))
            ).order_by(Track.id.desc())
        )
    ).scalars().all()
    # a track with a file before one without; of several with a file the newest wins, as in `find_owned`
    rows = sorted(rows, key=lambda r: (r.path is None, -r.id))
    by_isrc = {r.isrc: r for r in reversed(rows) if r.isrc}
    by_ref = {r.ref: r for r in rows if r.ref}
    by_fp: dict[str, list[Track]] = {}
    for r in rows:
        by_fp.setdefault(r.fingerprint, []).append(r)

    active = (
        await s.execute(
            select(DownloadItem.user_id, DownloadItem.query_json).where(DownloadItem.status.in_(ACTIVE_STATUSES))
        )
    ).all()
    downloading_refs: set[str | None] = set()
    downloading_fps: set[str] = set()
    for uid, q in active:
        q = q or {}
        ref = (q.get("meta") or {}).get("track_ref")
        if quiet(q):
            continue
        downloading_refs.add(ref)
        if q.get("query"):
            downloading_fps.add(fingerprint(q["query"]["artist"], q["query"]["title"]))

    out: list[TrackOut] = []
    for t in tracks:
        hit = (t.isrc and by_isrc.get(t.isrc.upper())) or (t.source == "dz" and by_ref.get(t.ref)) or None
        fp = fingerprint(t.artist, t.title)
        if hit is None:
            hit = next((r for r in by_fp.get(fp, []) if _duration_ok(r.duration_s, t.duration_s)), None)
        o = TrackOut(**t.model_dump())
        if hit is not None:
            o.library_track_id = hit.id  # the Hub knows the song; whether it has a file is `owned`
        if hit is not None and hit.path is not None:
            o.owned = True
            o.nd_song_id = hit.nd_song_id
        else:
            o.downloading = t.ref in downloading_refs or fp in downloading_fps
        out.append(o)
    await with_files(s, out)
    return out


def _album_key(artist: str, title: str) -> str:
    return f"{normalize_artist(artist).text}|{normalize_title(title).text}"


async def annotate_albums(
    s: AsyncSession, albums: Iterable[CatalogAlbum], user_id: int | None = None
) -> list[AlbumOut]:
    albums = list(albums)
    if not albums:
        return []
    dz_ids = {a.id for a in albums if a.source == "dz"}
    counts_dz = dict(
        (
            await s.execute(
                select(Album.deezer_album_id, func.count(Track.id))
                .join(Track, Track.album_id == Album.id)
                .where(Album.deezer_album_id.in_(dz_ids))
                .group_by(Album.deezer_album_id)
            )
        ).all()
    )
    # albums without a Deezer id (hitmoz catalog, own uploads): match by normalized artist + title
    lib_albums = (
        await s.execute(
            select(Album.album_artist, Album.title, func.count(Track.id))
            .join(Track, Track.album_id == Album.id)
            .group_by(Album.id)
        )
    ).all()
    counts_key: dict[str, int] = {}
    for artist, title, n in lib_albums:
        k = _album_key(artist, title)
        counts_key[k] = max(counts_key.get(k, 0), n)

    out: list[AlbumOut] = []
    for a in albums:
        d = a.model_dump(exclude={"tracks"})
        o = AlbumOut(**d)
        n = counts_dz.get(a.id, 0) if a.source == "dz" else 0
        if not n:
            n = counts_key.get(_album_key(a.artist, a.title), 0)
        o.owned_count = n
        total = a.nb_tracks or (len(a.tracks) if a.tracks else None)
        if n and total and n >= total:
            o.owned_status = "full"
        elif n:
            o.owned_status = "partial"
        if a.tracks is not None:
            o.tracks = await annotate_tracks(s, a.tracks, user_id)
            owned = sum(1 for t in o.tracks if t.owned)
            o.owned_count = owned
            o.owned_status = "full" if owned and owned >= len(o.tracks) else ("partial" if owned else "none")
        out.append(o)
    return out


__all__ = ["ACTIVE_STATUSES", "_dz_id", "annotate_albums", "annotate_tracks", "find_owned", "quiet"]
