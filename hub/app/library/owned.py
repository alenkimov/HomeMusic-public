"""What is already in the library: de-duplication before downloads and ownership badges for catalog pages.

Match by known external reference, corroborated ISRC, or the same name and duration.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AlbumOut, FileOut, TrackOut
from app.catalog.identity import same_recording
from app.catalog.models import CatalogAlbum, CatalogTrack
from app.library.artwork import album_cover_url
from app.library.tracks import by_ref as track_by_ref, tracks_by_refs
from app.matching.normalize import fingerprint, normalize_artist, normalize_title
from app.models import Album, DownloadItem, Track, TrackFile, TrackLink
from app.providers.base import TrackQuery

ACTIVE_STATUSES = ("queued", "searching", "downloading", "tagging", "importing")


def quiet(query_json: dict[str, Any] | None) -> bool:
    """A download nobody asked for explicitly: queued by an import (`app.imports`), for a «Микс дня» (`app.mixes`)
    or a track of «Моя волна» played to its end (`app.mixes.wave`), or a better file looked for in the place of a
    weak one (`app.library.upgrade`). Clients don't show it as a download."""
    qj = query_json or {}
    return bool(qj.get("import") or qj.get("mix") or qj.get("wave") or qj.get("upgrade")) and not qj.get("requested")


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


async def _track_variants(s: AsyncSession, tracks: Iterable[CatalogTrack]) -> dict[str, list[str]]:
    from app.catalog import release_matches

    tracks = list(tracks)
    groups = await release_matches.groups_for(s, (t.album_ref for t in tracks if t.album_ref))
    return {t.ref: [other.ref for other in release_matches.track_versions(groups[t.album_ref], t.ref)
                    if other.ref != t.ref] for t in tracks if t.album_ref in groups}


async def with_recording_availability(s: AsyncSession, tracks: Iterable[TrackOut],
                                      aliases: dict[str, list[str]] | None = None) -> None:
    """Show the same recording check for a catalog row and the Hub track it resolves to."""
    from app.listen import recording_checks

    pending = [track for track in tracks if not track.owned and not track.unknown]
    if aliases is None:
        aliases = await _track_variants(s, pending)
    addresses = []
    for track in pending:
        refs = [track.ref, *aliases.get(track.ref, [])]
        if track.library_track_id is not None:
            refs.append(f"lib:{track.library_track_id}")
        addresses.append((track, refs))
    checks = await recording_checks(s, [ref for _, refs in addresses for ref in refs])
    for track, refs in addresses:
        latest = max((checks[ref] for ref in refs if ref in checks), default=None, key=lambda check: check[0])
        track.unavailable = latest is not None and not latest[1]


async def with_cover_fallbacks(s: AsyncSession, pairs: list[tuple[TrackOut, Track]]) -> None:
    """Prefer saved covers and fall back to the catalog when Navidrome has no image."""
    album_ids = list({t.album_id for _, t in pairs if t.album_id is not None and not t.cover_key})
    covered: dict[int, str] = {}
    for i in range(0, len(album_ids), 500):
        rows = (await s.execute(select(Album).where(
            Album.id.in_(album_ids[i:i + 500]), Album.cover_path.is_not(None)))).scalars()
        covered.update((a.id, url) for a in rows if (url := album_cover_url(a)))
    for out, track in pairs:
        if track.cover_key:
            out.cover_url = out.cover_small_url = f"/api/library/tracks/{track.id}/cover?v={track.cover_key}"
        elif track.album_id in covered:
            out.cover_url = covered[track.album_id]
            out.cover_small_url = f"{out.cover_url}&size=300"
        elif not out.cover_url:
            catalog = track.catalog or {}
            out.cover_url = catalog.get("cover_url") or catalog.get("cover_small_url")
            out.cover_small_url = catalog.get("cover_small_url") or out.cover_url


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
    catalog_ref: str | None = None,
) -> Track | None:
    """Return the matching Hub track if the library has a file for it."""
    if catalog_ref and (track := await track_by_ref(s, catalog_ref)) is not None and track.path:
        return track
    newest = Track.id.desc()
    fp = fingerprint(artist, title)
    if isrc:
        known = await s.scalars(select(Track).where(Track.isrc == isrc.upper(), HAS_FILE).order_by(newest))
        wanted = TrackQuery(artist=artist, title=title, duration_s=duration_s, isrc=isrc)
        if match := next((track for track in known if same_recording(wanted, track)), None):
            return match
    for t in (await s.execute(select(Track).where(Track.fingerprint == fp, HAS_FILE).order_by(newest))).scalars():
        if _duration_ok(t.duration_s, duration_s):
            return t
    return None


# the track has a file (`Track.path` is the path of its newest one)
HAS_FILE = Track.path.is_not(None)


def put_right(o: Any, t: Track) -> None:
    """What listeners put right of the track (app/library/edits.py) goes over what a catalog or Navidrome says of it:
    the name it was given by hand, and its own cover."""
    if t.renamed_at is not None:
        if o.artist != t.artist:
            from app.library.artists import key
            from app.library.credits import split_artists

            names = split_artists(t.artist)
            for field, main in (("artist_refs", "artist_ref"), ("hub_artist_ids", "hub_artist_id"),
                                ("nd_artist_ids", "nd_artist_id")):
                if not hasattr(o, field):
                    continue
                values = getattr(o, field)
                by_name = dict(zip(map(key, o.artists), values, strict=True)) if len(values) == len(o.artists) else {}
                if getattr(o, main) is not None:
                    by_name[key(o.artist)] = getattr(o, main)
                setattr(o, field, [by_name.get(key(name)) for name in names])
                setattr(o, main, by_name.get(key(t.artist)))
            o.artists = names
        o.title, o.artist = t.title, t.artist
    if t.cover_key:
        o.cover_url = o.cover_small_url = f"/api/library/tracks/{t.id}/cover?v={t.cover_key}"


async def annotate_tracks(
    s: AsyncSession, tracks: Iterable[CatalogTrack], user_id: int | None = None
) -> list[TrackOut]:
    """Ownership and download state. A download queued only for ♥ is not «downloading»; for the user who liked the
    track any download that stars it is `star_pending` (the ♥ shows filled until the import stars the song)."""
    tracks = list(tracks)
    if not tracks:
        return []
    from app.catalog import registry

    internal = {t.ref: ident for t in tracks if t.ref.startswith("lib:")
                and (ident := registry.numeric_id(t.ref[4:])) is not None}
    variants = await _track_variants(s, tracks)
    isrcs = {t.isrc.upper() for t in tracks if t.isrc}
    refs = {t.ref for t in tracks if t.source in ("dz", "sp", "ym")}
    refs.update(ref for peers in variants.values() for ref in peers)
    fps = {fingerprint(t.artist, t.title) for t in tracks}
    spotify_ids = {t.spotify_id for t in tracks if t.spotify_id}
    linked = dict((await s.execute(select(TrackLink.external_id, TrackLink.track_id)
        .where(TrackLink.service == "spotify", TrackLink.external_id.in_(spotify_ids)))).all()) if spotify_ids else {}
    by_ref = await tracks_by_refs(s, refs)
    rows = (
        await s.execute(
            select(Track).where(
                or_(Track.isrc.in_(isrcs), Track.fingerprint.in_(fps),
                    Track.id.in_({*linked.values(), *internal.values(), *(t.id for t in by_ref.values())}))
            ).order_by(Track.id.desc())
        )
    ).scalars().all()
    # a track with a file before one without; of several with a file the newest wins, as in `find_owned`
    rows = sorted(rows, key=lambda r: (r.path is None, -r.id))
    by_isrc: dict[str, list[Track]] = {}
    by_id = {r.id: r for r in rows}
    by_fp: dict[str, list[Track]] = {}
    for r in rows:
        by_fp.setdefault(r.fingerprint, []).append(r)
        if r.isrc:
            by_isrc.setdefault(r.isrc, []).append(r)

    active = (
        await s.execute(
            select(DownloadItem.user_id, DownloadItem.query_json).where(DownloadItem.status.in_(ACTIVE_STATUSES))
        )
    ).all()
    downloading_refs: set[str | None] = set()
    downloading_fps: set[str] = set()
    for _, q in active:
        q = q or {}
        ref = (q.get("meta") or {}).get("track_ref")
        if quiet(q):
            continue
        downloading_refs.add(ref)
        if q.get("query"):
            downloading_fps.add(fingerprint(q["query"]["artist"], q["query"]["title"]))

    from app.library import artists, titles

    spelt = await artists.reader(s, [n for t in tracks for n in (t.artist, t.album_artist, *(t.artists or []))],
                                 [r for t in tracks for r in (t.artist_ref, *(t.artist_refs or []))])
    release_by_ref, release_by_album = await registry.addresses(
        s, (t.album_ref for t in tracks if t.album_ref), (r.album_id for r in rows if r.album_id)
    )
    out: list[TrackOut] = []
    availability_refs: dict[str, list[str]] = {}
    for t in tracks:
        known = [row for ref in (t.ref, *variants.get(t.ref, [])) if (row := by_ref.get(ref))]
        hit = next((row for row in known if row.path is not None), None)
        coded = next((r for r in by_isrc.get(t.isrc.upper(), []) if same_recording(t, r)), None) if t.isrc else None
        hit = (by_id.get(internal.get(t.ref)) or hit or (known[0] if known else None)
               or by_id.get(linked.get(t.spotify_id)) or coded)
        fp = fingerprint(t.artist, t.title)
        if hit is None:
            hit = next((r for r in by_fp.get(fp, []) if _duration_ok(r.duration_s, t.duration_s)
                        and (not (t.isrc and r.isrc) or t.isrc.upper() == r.isrc.upper())
                        and (not t.album_title or not r.album or same_recording(t, r))), None)
        o = TrackOut(**t.model_dump())
        titles.put_right(o)  # the name without the credits written into it
        artists.put_right(o, spelt)
        if hit is not None:
            o.library_track_id = hit.id  # the Hub knows the song; whether it has a file is `owned`
            put_right(o, hit)
            o.unknown = not hit.ref and not hit.path
        o.release_id = release_by_ref.get(t.album_ref) or (release_by_album.get(hit.album_id) if hit else None)
        if hit is not None and hit.path is not None:
            o.owned = True
            o.nd_song_id = hit.nd_song_id
        else:
            o.downloading = t.ref in downloading_refs or fp in downloading_fps
            aliases = list(variants.get(t.ref, []))
            if hit is not None and hit.ref and hit.ref != t.ref:
                aliases.append(hit.ref)
            if aliases:
                availability_refs[t.ref] = aliases
        out.append(o)
    await with_files(s, out)
    await with_recording_availability(s, out, availability_refs)
    from app.library import versions

    # the song a track is may have other recordings (app/library/versions.py): a list says how many
    await versions.mark(s, out)
    return out


def _album_key(artist: str, title: str) -> tuple[str, str, str | None]:
    name = normalize_title(title)
    return normalize_artist(artist).text, name.text, name.version


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
    lib_albums = (await s.execute(select(Album, func.count(Track.id))
        .outerjoin(Track, Track.album_id == Album.id).group_by(Album.id).order_by(Album.id))).all()
    counts_key: dict[tuple[str, str, str | None], int] = {}
    by_deezer: dict[str, Album] = {}
    by_name: dict[tuple[str, str, str | None], Album] = {}
    unnamed: dict[tuple[str, str, str | None], Album] = {}
    by_id = {local.id: (local, n) for local, n in lib_albums}
    by_uid = {local.uid: local for local, _ in lib_albums}
    for local, n in lib_albums:
        k = _album_key(local.album_artist, local.title)
        counts_key[k] = max(counts_key.get(k, 0), n)
        by_name.setdefault(k, local)
        if local.deezer_album_id:
            by_deezer.setdefault(local.deezer_album_id, local)
        else:
            unnamed.setdefault(k, local)

    from app.catalog import registry, release_matches
    from app.library import album_names, artists
    from app.models import CatalogRelease, CatalogReleaseRef

    confirmed = await release_matches.groups_for(s, (a.ref for a in albums))
    album_refs = {a.ref for a in albums} | set(confirmed)
    release_by_ref, release_by_album = await registry.addresses(s, album_refs, by_id)
    saved_ref = Track.catalog["album_ref"].as_string()
    local_by_ref = dict((await s.execute(select(saved_ref, Track.album_id).where(
        saved_ref.in_(album_refs), Track.album_id.is_not(None),
    ).distinct().order_by(Track.album_id.desc()))).all())
    local_by_ref.update((await s.execute(select(CatalogReleaseRef.ref, CatalogRelease.album_id).join(CatalogRelease).where(
        CatalogReleaseRef.ref.in_(album_refs), CatalogRelease.album_id.is_not(None),
    ))).all())
    known_refs = {t.ref for group in confirmed.values() for a in group for t in a.tracks or []}
    local_refs = dict((await s.execute(select(Track.ref, Track.album_id).where(
        Track.ref.in_(known_refs), Track.album_id.is_not(None),
    ).order_by(Track.id.desc()))).all()) if known_refs else {}
    names = await album_names.listed(s)
    by_former: dict[str, list[Album]] = {}
    for local, _ in lib_albums:
        for name in names.get(local.id, []):
            if name.source in ("catalog", "renamed"):
                by_former.setdefault(name.key, []).append(local)

    spelt = await artists.reader(s, [n for a in albums for n in (a.artist, *(a.artists or []))],
                                 [r for a in albums for r in (a.artist_ref, *(a.artist_refs or []))])
    out: list[AlbumOut] = []
    for a in albums:
        d = a.model_dump(exclude={"tracks"})
        o = AlbumOut(**d)
        artists.put_right(o, spelt)
        k = _album_key(a.artist, a.title)
        group = confirmed.get(a.ref, [a])
        local = by_deezer.get(a.id) if a.source == "dz" else None
        if a.ref.startswith("lib:") and (aid := registry.numeric_id(a.ref[4:])) in by_id:
            local = by_id[aid][0]
        local = local or next((by_uid[uid] for item in group
                               if (uid := album_names.catalog_uid(item.ref)) in by_uid), None)
        local = local or next((by_id[aid][0] for item in group if (aid := local_by_ref.get(item.ref)) in by_id), None)
        if local is None and a.ref in confirmed:
            confirmed_ids = {local_refs[t.ref] for item in confirmed[a.ref] for t in item.tracks or []
                             if local_refs.get(t.ref) in by_id}
            if confirmed_ids:
                local = by_id[min(confirmed_ids)][0]
        if local is None:
            direct = unnamed.get(k) if a.source == "dz" else by_name.get(k)
            year = a.year or (a.release_date.year if a.release_date else None)
            options = ([direct] if direct else []) + by_former.get(artists.key(a.title), [])
            compatible = {row.id: row for row in options
                          if normalize_artist(row.album_artist).text == normalize_artist(a.artist).text
                          and not (year and row.year and year != row.year)
                          and not (a.source == "dz" and row.deezer_album_id and row.deezer_album_id != a.id)
                          and not (a.upc and row.upc and a.upc.lstrip("0") != row.upc.lstrip("0"))}
            if len(compatible) == 1:
                local = next(iter(compatible.values()))
        cover = None
        o.release_id = release_by_ref.get(a.ref)
        if local:
            o.library_album_id = local.id
            o.release_id = o.release_id or release_by_album.get(local.id)
            o.title = local.title
            if normalize_artist(local.album_artist).text != normalize_artist(o.artist).text:
                o.artist, o.artists = local.album_artist, [local.album_artist]
                o.artist_ref, o.artist_refs = None, []
                o.hub_artist_id, o.hub_artist_ids = None, []
            o.alternate_titles = release_matches.alternate_titles(o.title, [
                a.title, *o.alternate_titles, *(row.name for row in names.get(local.id, [])),
            ])
            if cover := album_cover_url(local):
                o.cover_url, o.cover_small_url = cover, f"{cover}&size=300"
        n = by_id[local.id][1] if local else (counts_dz.get(a.id, 0) if a.source == "dz" else 0)
        if not n:
            n = counts_key.get(k, 0)
        o.owned_count = n
        total = a.nb_tracks or (len(a.tracks) if a.tracks else None)
        if n and total and n >= total:
            o.owned_status = "full"
        elif n:
            o.owned_status = "partial"
        if a.tracks is not None:
            o.tracks = await annotate_tracks(s, a.tracks, user_id)
            if cover:
                for track in o.tracks:
                    if not (track.cover_url or "").startswith("/api/library/tracks/"):
                        track.cover_url, track.cover_small_url = o.cover_url, o.cover_small_url
            owned = sum(1 for t in o.tracks if t.owned)
            o.owned_count = owned
            o.owned_status = "full" if owned and owned >= len(o.tracks) else ("partial" if owned else "none")
        out.append(o)
    return out


__all__ = ["ACTIVE_STATUSES", "_dz_id", "annotate_albums", "annotate_tracks", "find_owned", "quiet"]
