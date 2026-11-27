"""One song, several recordings of it.

A track (`Track`) is a recording: the album cut, the older take with a guest, a remix, a live one. They are tracks
of their own — each in its album, with its artists, its length and its files — and nothing of one is written into
another. What ties them is the song they all are (`Song`): the tracks of one song are its versions (`SongTrack`),
each with a word for what it is («старая версия», «Deluxe», «remix»). A track that is nobody's version has no
song row: it is the song by itself.

Versions are one group: a track joined to any of them is a version of them all, and two songs whose tracks turn out
to be one song become one. One version of a song is the preferred one — what a list that shows the song once
shows, and what plays by itself. It is said by hand, for everyone here; until somebody says otherwise it is the
track the others were joined to. What a listener likes is their own matter, and is of a track, not of a song.
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.library import edits
from app.library.files import mains
from app.matching.normalize import split_version
from app.models import Album, Edit, Song, SongTrack, Track, TrackFile


async def _row(s: AsyncSession, track_id: int) -> SongTrack | None:
    return await s.get(SongTrack, track_id)


async def label_of(s: AsyncSession, track_id: int) -> str | None:
    return await s.scalar(select(SongTrack.label).where(SongTrack.track_id == track_id))


async def label_index(s: AsyncSession) -> list[dict[str, Any]]:
    """Identify labeled recordings by their exact aliases, including files still known by an older song id."""
    labeled = SongTrack.label.is_not(None) & (SongTrack.label != "")
    rows = (await s.execute(
        select(SongTrack.track_id, SongTrack.label, Track.ref, TrackFile.nd_song_id)
        .join(Track, Track.id == SongTrack.track_id)
        .outerjoin(TrackFile, TrackFile.track_id == Track.id)
        .where(labeled)
    )).all()
    out: dict[int, dict[str, Any]] = {}
    for track_id, label, ref, nd in rows:
        entry = out.setdefault(track_id, {
            "track_id": track_id, "label": label, "ref": ref, "nd_song_ids": set(), "source_urls": [],
        })
        if nd:
            entry["nd_song_ids"].add(nd)
    if out:
        # A provider's URL only identifies a recording when all its saved copies belong to that recording.
        labeled_urls = select(TrackFile.source_url).join(SongTrack, SongTrack.track_id == TrackFile.track_id).where(labeled)
        urls = (await s.execute(
            select(TrackFile.source_url, func.min(TrackFile.track_id))
            .where(TrackFile.source_url.in_(labeled_urls))
            .group_by(TrackFile.source_url)
            .having(func.count(func.distinct(TrackFile.track_id)) == 1)
        )).all()
        for url, track_id in urls:
            out[track_id]["source_urls"].append(url)
    return [{**entry, "nd_song_ids": sorted(entry["nd_song_ids"])} for entry in out.values()]


async def counts(s: AsyncSession, track_ids: Iterable[int]) -> dict[int, int]:
    """How many versions the song of each of these tracks has (only the tracks that are a version of something)."""
    ids = list({i for i in track_ids if i})
    if not ids:
        return {}
    mine = dict((await s.execute(select(SongTrack.track_id, SongTrack.song_id).where(SongTrack.track_id.in_(ids)))).all())
    if not mine:
        return {}
    size = dict((await s.execute(
        select(SongTrack.song_id, func.count(SongTrack.track_id)).where(SongTrack.song_id.in_(set(mine.values()))).group_by(SongTrack.song_id))).all())
    return {tid: size.get(sid, 1) for tid, sid in mine.items()}


async def songs_of(s: AsyncSession, track_ids: Iterable[int]) -> dict[int, tuple[int, int | None]]:
    """track -> (its song, the song's preferred track), for the tracks that are a version of something."""
    ids = list({i for i in track_ids if i})
    if not ids:
        return {}
    rows = (await s.execute(select(SongTrack.track_id, SongTrack.song_id, Song.preferred_track_id)
                            .join(Song, Song.id == SongTrack.song_id).where(SongTrack.track_id.in_(ids)))).all()
    return {tid: (sid, pref) for tid, sid, pref in rows}


async def mark(s: AsyncSession, outs: list[Any]) -> None:
    """Say on each track of a list how many versions its song has (`versions`; 0: it is nobody's version)."""
    many = await counts(s, [o.library_track_id for o in outs if getattr(o, "library_track_id", None)])
    for o in outs:
        o.versions = many.get(getattr(o, "library_track_id", None) or 0, 0)


async def once(s: AsyncSession, outs: list[Any], track_id=lambda o: getattr(o, "library_track_id", None),
               *, replace=lambda first, preferred: preferred) -> list[Any]:
    """A list that shows a song once (a search, an artist's most listened, what plays by itself): of the versions
    of one song that are in it only one stays — the preferred one where it is among them, else the first — in the
    place of the first of them. Tracks that are nobody's version stay as they are."""
    told = await songs_of(s, [track_id(o) for o in outs if track_id(o)])
    if not told:
        return outs
    at: dict[int, int] = {}  # a song -> where its one row stands
    out: list[Any] = []
    for o in outs:
        tid = track_id(o)
        hit = told.get(tid) if tid else None
        if hit is None:
            out.append(o)
        elif hit[0] not in at:
            at[hit[0]] = len(out)
            out.append(o)
        elif tid == hit[1]:
            pos = at[hit[0]]
            out[pos] = replace(out[pos], o)
    return out


def _guess(title: str) -> str | None:
    """A word for what a recording is, where its own name says so: «(Deluxe)», «- Live», «(Remix)»."""
    try:
        version = split_version(title)[1]
    except Exception:
        version = None
    return version.strip()[:64] if version and version.strip() else None


async def _cover(s: AsyncSession, t: Track) -> str | None:
    own = edits.cover_url(t)
    if own:
        return own
    if t.album_id:
        album = await s.get(Album, t.album_id)
        if album is not None and album.cover_path:
            return f"/api/library/albums/{album.id}/cover"
    cat = t.catalog or {}
    return cat.get("cover_small_url") or cat.get("cover_url")


async def _out(s: AsyncSession, t: Track, row: SongTrack | None, preferred: bool, file: Any) -> dict[str, Any]:
    return {
        "track_id": t.id, "title": t.title, "artist": t.artist, "album": t.album, "duration_s": t.duration_s,
        "label": row.label if row else None, "preferred": preferred, "cover_url": await _cover(s, t),
        "ref": t.ref, "nd_song_id": file.nd_song_id if file is not None else None,
        "codec": file.codec if file is not None else None, "bitrate_kbps": file.bitrate_kbps if file is not None else None,
        "has_file": file is not None,
    }


async def card(s: AsyncSession, track: Track) -> dict[str, Any]:
    """The song a track is, as the window shows it: every version — the preferred one first, then as they were
    joined — with what each is and what it plays from. A track that is nobody's version is the only one."""
    row = await _row(s, track.id)
    if row is None:
        files = await mains(s, [track.id])
        return {"track_id": track.id, "song_id": None, "title": track.title,
                "versions": [await _out(s, track, None, True, files.get(track.id))]}
    song = await s.get(Song, row.song_id)
    rows = (await s.execute(select(SongTrack, Track).join(Track, Track.id == SongTrack.track_id)
                            .where(SongTrack.song_id == row.song_id).order_by(SongTrack.joined_at, SongTrack.track_id))).all()
    preferred = song.preferred_track_id if song is not None and any(t.id == song.preferred_track_id for _, t in rows) else rows[0][1].id
    files = await mains(s, [t.id for _, t in rows])
    out = [await _out(s, t, r, t.id == preferred, files.get(t.id)) for r, t in rows]
    out.sort(key=lambda v: not v["preferred"])
    return {"track_id": track.id, "song_id": row.song_id, "title": next(v["title"] for v in out if v["preferred"]), "versions": out}


def _journal(s: AsyncSession, user_id: int | None, track_id: int, changes: dict[str, list[Any]]) -> None:
    s.add(Edit(target_type="track", target_id=track_id, user_id=user_id, changes_json=changes))


async def link(s: AsyncSession, user_id: int | None, of: Track, other: Track) -> None:
    """`other` is another recording of the song `of` is. A song is made of the two where neither had one (`of` is
    the preferred version then); where both had, the two songs are one from now on."""
    if of.id == other.id:
        raise ValueError("Это тот же трек")
    mine, theirs = await _row(s, of.id), await _row(s, other.id)
    if mine is None:
        song = Song(preferred_track_id=of.id)
        s.add(song)
        await s.flush()
        mine = SongTrack(track_id=of.id, song_id=song.id, label=_guess(of.title))
        s.add(mine)
        await s.flush()
    if theirs is None:
        s.add(SongTrack(track_id=other.id, song_id=mine.song_id, label=_guess(other.title)))
    elif theirs.song_id != mine.song_id:
        gone = theirs.song_id
        await s.execute(update(SongTrack).where(SongTrack.song_id == gone).values(song_id=mine.song_id))
        await s.execute(delete(Song).where(Song.id == gone))
    else:
        return
    _journal(s, user_id, other.id, {"version_of": [None, of.title]})
    await s.flush()


async def unlink(s: AsyncSession, user_id: int | None, track: Track) -> None:
    """The track is not a version of that song after all. A song left with one track is no song of several: the
    track is by itself again."""
    row = await _row(s, track.id)
    if row is None:
        return
    song_id = row.song_id
    await s.delete(row)
    await s.flush()
    left = (await s.execute(select(SongTrack.track_id).where(SongTrack.song_id == song_id).order_by(SongTrack.joined_at, SongTrack.track_id))).scalars().all()
    if len(left) < 2:
        await s.execute(delete(SongTrack).where(SongTrack.song_id == song_id))
        await s.execute(delete(Song).where(Song.id == song_id))
    else:
        song = await s.get(Song, song_id)
        if song is not None and song.preferred_track_id not in left:
            song.preferred_track_id = left[0]
    _journal(s, user_id, track.id, {"version_of": ["", None]})
    await s.flush()


async def prefer(s: AsyncSession, user_id: int | None, track: Track) -> None:
    """This version is the song's preferred one, for everyone here."""
    row = await _row(s, track.id)
    if row is None:
        raise ValueError("У трека нет других версий")
    song = await s.get(Song, row.song_id)
    if song is not None and song.preferred_track_id != track.id:
        _journal(s, user_id, track.id, {"preferred_version": [song.preferred_track_id, track.id]})
        song.preferred_track_id = track.id
        await s.flush()


async def label(s: AsyncSession, user_id: int | None, track: Track, text: str | None) -> None:
    """What the version is, in a word («старая версия»); nothing: what its name says, if it says."""
    row = await _row(s, track.id)
    if row is None:
        raise ValueError("У трека нет других версий")
    text = " ".join((text or "").split())[:64] or None
    if text != row.label:
        _journal(s, user_id, track.id, {"version_label": [row.label, text]})
        row.label = text
        await s.flush()


async def candidates(s: AsyncSession, track: Track, q: str, limit: int = 12) -> list[dict[str, Any]]:
    """Tracks of the library that may be joined to the song as its versions: found by the words, not its versions yet."""
    from app.library import artists

    row = await _row(s, track.id)
    taken = set((await s.execute(select(SongTrack.track_id).where(SongTrack.song_id == row.song_id))).scalars()) if row else {track.id}
    found = [t for t, _ in await artists.found(s, q, limit + len(taken)) if t.id not in taken][:limit]
    files = await mains(s, [t.id for t in found])
    return [await _out(s, t, None, False, files.get(t.id)) for t in found]
