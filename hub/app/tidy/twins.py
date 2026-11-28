"""«Двойники» of «Наведение порядка»: one person under two names, with their albums and tracks — one case, not
fifteen questions apart.

What tells it (the clues of a pair of artists):

- an alias in the name: «Алексей Хвостенко (Хвост)» — the part in brackets is the other's name;
- one recording under both (told by the sound, `acoustic.doubles`): a track of one and a track of the other sound the
  same, where nobody else's credit tells them apart;
- one album under both: an album of each of the same name (the same cover said so, where both have one) — or
  named otherwise (a catalog's own language: «DecoMiku LIVE» / «デコミク LIVE») but the same by its signature: as
  many tracks, each as long as the one in its place, under the same cover.

A pair with enough of them is a case (`find`). What joining it does is drawn from the library as it is when it is
looked at (`plan`): the artist, then each album of the one that goes into the album of that name of the one that
stays, then each recording of both into one track — the better file plays. It is done in that order, in one
transaction, each step what it would be on its own (`artists.merge`, `editor.join_albums`, `tracks.join`), each
written down, and taken back the other way round (`revert`). The listener may leave steps out (`skip`).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.library import artists as artists_
from app.library.quality import rank
from app.matching.normalize import has_cyrillic, normalize_title
from app.models import Album, AlbumArtist, Artist, ArtistApart, ArtistLink, ArtistName, Track, TrackArtist, TrackFile

# «Алексей Хвостенко (Хвост)», «Yegor Letov [Егор Летов]»: a name and, in brackets, another one of the same person
ALIAS = re.compile(r"^(.+?)\s*[(\[]\s*([^()\[\]]+?)\s*[)\]]\s*$")
SOUND_ALONE = 2  # recordings under both that make a case by themselves


def better(f: TrackFile | None, t: Track) -> tuple:
    """Which of the files of one recording plays when their tracks are one: the better sound, then one in an
    album, then the older track."""
    return (rank(f.codec, f.bitrate_kbps) or 0 if f else -1, t.album_id is not None, -t.id)


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def album_key(title: str | None) -> str:
    return normalize_title(title).text


async def _credits(s: AsyncSession, track_ids: set[int]) -> dict[int, set[int]]:
    out: dict[int, set[int]] = defaultdict(set)
    for track_id, artist_id in (await s.execute(select(TrackArtist.track_id, TrackArtist.artist_id)
                                                .where(TrackArtist.track_id.in_(track_ids)))).all():
        out[track_id].add(artist_id)
    return out


def _recordings(groups: list[list[int]], credits: dict[int, set[int]]) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """Pairs of artists -> the pairs of tracks, one of each, that are one recording: credited alike but for these
    two (the rest of the credits — a band they play with — the same)."""
    out: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for group in groups:
        for i, x in enumerate(group):
            for y in group[i + 1:]:
                a, b = credits.get(x, set()), credits.get(y, set())
                only_a, only_b = a - b, b - a
                if len(only_a) == 1 and len(only_b) == 1:
                    (ia,), (ib,) = only_a, only_b
                    out[_pair(ia, ib)].append((x, y) if ia < ib else (y, x))
    return out


async def _cover(album: Album) -> bytes | None:
    """The album's cover as covers are compared (`release_matches.pixels`); None: it has none."""
    import asyncio

    from app.catalog.release_matches import pixels
    from app.library.importer import album_reldir, library_abs

    p = library_abs(album_reldir(album)) / "cover.jpg"
    if not await asyncio.to_thread(p.is_file):
        return None
    try:
        return await asyncio.to_thread(pixels, await asyncio.to_thread(p.read_bytes))
    except (OSError, ValueError):
        return None


async def _lengths(s: AsyncSession, album_ids: list[int]) -> dict[int, list[int | None]]:
    """Album -> the lengths of its tracks in their order (disc, number)."""
    out: dict[int, list[int | None]] = defaultdict(list)
    for album_id, length in (await s.execute(select(Track.album_id, Track.duration_s).where(Track.album_id.in_(album_ids))
                                             .order_by(Track.album_id, Track.disc_no, Track.track_no, Track.id))).all():
        out[album_id].append(length)
    return out


def _same_lengths(x: list[int | None], y: list[int | None]) -> bool:
    """Two tracklists of one release: as many tracks (two at least), each as long as the one in its place."""
    return len(x) == len(y) >= 2 and all(a and b and abs(a - b) <= 2 for a, b in zip(x, y, strict=True))


async def same_albums(s: AsyncSession, goes: list[Album], stays: list[Album]) -> list[tuple[Album, Album, bool]]:
    """Each album of `goes` and the album of `stays` it is — `(goes, stays, same_cover)`: by its name (of several,
    the one with more tracks), else by its signature (as many tracks, each as long, under the same cover)."""
    from app.catalog.release_matches import same_picture

    ids = [a.id for a in (*goes, *stays)]
    lengths = await _lengths(s, ids) if ids else {}
    covers: dict[int, bytes | None] = {}

    async def cover(a: Album) -> bytes | None:
        if a.id not in covers:
            covers[a.id] = await _cover(a)
        return covers[a.id]

    named: dict[str, Album] = {}
    for a in sorted(stays, key=lambda a: (len(lengths.get(a.id, [])), -a.id)):
        named[album_key(a.title)] = a
    out = []
    for a in sorted(goes, key=lambda a: a.id):
        if (b := named.get(album_key(a.title))) is not None and b.id != a.id:
            out.append((a, b, same_picture(await cover(a), await cover(b))))
            continue
        for b in stays:
            if (b.id != a.id and _same_lengths(lengths.get(a.id, []), lengths.get(b.id, []))
                    and same_picture(await cover(a), await cover(b))):
                out.append((a, b, True))
                break
    return out


async def find(s: AsyncSession, groups: list[list[int]], music: dict[int, int]) -> list[dict[str, Any]]:
    """The cases of the library: `{"into", "other", "clues": [{"kind", "text"}], "sure"}` — `into` the artist who
    stays (more music; a name without brackets). `groups`: the recordings told by the sound; `music`: tracks per
    artist. Pairs said to be two people, or two projects of one, are not cases."""
    who = {a.id: a for a in (await s.execute(select(Artist).where(Artist.merged_into_id.is_(None),
                                                                  Artist.id.in_(list(music))))).scalars()}
    if len(who) < 2:
        return []
    off = {tuple(r) for r in (await s.execute(select(ArtistApart.low_id, ArtistApart.high_id))).all()}
    off |= {tuple(r) for r in (await s.execute(select(ArtistLink.low_id, ArtistLink.high_id))).all()}
    clues: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)

    # an alias in the name, which is the other's name (or one of its spellings)
    holders: dict[str, set[int]] = defaultdict(set)
    for artist_id, name_key in (await s.execute(select(ArtistName.artist_id, ArtistName.key))).all():
        if artist_id in who:
            holders[name_key].add(artist_id)
    for a in who.values():
        holders[artists_.key(a.name)].add(a.id)
    for a in who.values():
        if (m := ALIAS.match(a.name)) is None:
            continue
        for part in m.groups():
            for b in holders.get(artists_.key(part), set()) - {a.id}:
                clues[_pair(a.id, b)].append({"kind": "alias", "text": f"В имени «{a.name}» — «{who[b].name}»"})

    # one recording under both
    ids = {i for g in groups for i in g}
    sound = _recordings(groups, await _credits(s, ids)) if ids else {}
    for pair, tracks in sound.items():
        if all(i in who for i in pair):
            n = len(tracks)
            clues[pair].append({"kind": "sound", "text": f"{n} {_records(n)} звучат одинаково", "n": n})

    # one album under both
    by_artist: dict[int, list[Album]] = defaultdict(list)
    for album, artist_id in (await s.execute(select(Album, AlbumArtist.artist_id)
                                             .join(AlbumArtist, AlbumArtist.album_id == Album.id))).all():
        if artist_id in who:
            by_artist[artist_id].append(album)
    for pair in list(clues):
        a, b = pair
        for x, y, same_cover in (await same_albums(s, by_artist.get(a, []), by_artist.get(b, [])))[:1]:
            if album_key(x.title) == album_key(y.title):
                text = f"Альбом «{y.title}»: " + ("та же обложка и название" if same_cover else "то же название")
            else:
                text = f"«{x.title}» и «{y.title}» — один альбом: те же треки по длительности и обложка"
            clues[pair].append({"kind": "album", "text": text})

    out = []
    for (a, b), found in clues.items():
        if (a, b) in off:
            continue
        kinds = {c["kind"] for c in found}
        n = next((c["n"] for c in found if c["kind"] == "sound"), 0)
        if not (n >= SOUND_ALONE or (n and len(kinds) >= 2) or {"alias", "album"} <= kinds):
            continue
        into, other = sorted((a, b), key=lambda i: (-music.get(i, 0), bool(ALIAS.match(who[i].name)),
                                                     not has_cyrillic(who[i].name), i))
        out.append({"into": into, "other": other, "clues": sorted(found, key=lambda c: ("alias", "sound", "album").index(c["kind"])),
                    "sure": len(kinds) == 3})
    return out


def _records(n: int) -> str:
    return "запись" if n % 10 == 1 and n % 100 != 11 else "записи" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "записей"


async def plan(s: AsyncSession, other: int, into: int, groups: list[list[int]]) -> dict[str, Any]:
    """What joining the two does to the library as it is now: `{"albums": [{"source", "target"}], "tracks":
    [{"gone", "keep"}]}` — each album of `other` into the album of `into` it is (`same_albums`), each recording of
    both into one track (the better file stays)."""
    from app.library import files as library_files

    owned: dict[int, list[Album]] = defaultdict(list)
    for album, artist_id in (await s.execute(select(Album, AlbumArtist.artist_id).join(AlbumArtist, AlbumArtist.album_id == Album.id)
                                             .where(AlbumArtist.artist_id.in_((other, into))))).all():
        owned[artist_id].append(album)
    mine = {a.id for a in owned.get(into, [])}
    albums = [{"source": a.id, "target": b.id}
              for a, b, _ in await same_albums(s, [a for a in owned.get(other, []) if a.id not in mine], owned.get(into, []))]

    ids = {i for g in groups for i in g}
    pairs = _recordings(groups, await _credits(s, ids)).get(_pair(other, into), []) if ids else []
    tracks = []
    if pairs:
        rows = {t.id: t for t in (await s.execute(select(Track).where(Track.id.in_({i for p in pairs for i in p})))).scalars()}
        files = await library_files.mains(s, list(rows))
        taken: set[int] = set()
        for x, y in pairs:
            if x not in rows or y not in rows or x in taken or y in taken:
                continue
            keep, gone = sorted((x, y), key=lambda i: better(files.get(i), rows[i]), reverse=True)
            if gone in files and keep in files:
                tracks.append({"gone": gone, "keep": keep})
                taken |= {x, y}
    return {"albums": albums, "tracks": tracks}


async def apply(s: AsyncSession, user_id: int, other: Artist, into: Artist, steps: dict[str, Any],
                skip: set[str]) -> dict[str, Any]:
    """Join the case, all of it but the `skip`ped steps («artist», «album:<id>», «track:<id>»), in order: the
    artist, the albums, the tracks. Returns what `revert` takes back."""
    from app.library import editor
    from app.library import tracks as registry

    undo: dict[str, Any] = {"artist": None, "albums": [], "tracks": []}
    if "artist" not in skip:
        undo["artist"] = await artists_.merge(s, user_id, into, other)
    for step in steps["albums"]:
        if f"album:{step['source']}" in skip:
            continue
        source, target = await s.get(Album, step["source"]), await s.get(Album, step["target"])
        if source is not None and target is not None:
            undo["albums"].append({"target": target.id, "joined": await editor.join_albums(s, source, target)})
    for step in steps["tracks"]:
        if f"track:{step['gone']}" in skip:
            continue
        gone, keep = await s.get(Track, step["gone"]), await s.get(Track, step["keep"])
        if gone is not None and keep is not None:
            undo["tracks"].append({"keep": keep.id, "joined": await registry.join(s, gone, keep, user_id=user_id)})
    return undo


async def revert(s: AsyncSession, user_id: int, other: Artist, into: Artist, undo: dict[str, Any]) -> set[int]:
    """`apply` taken back, the other way round: the tracks parted, the albums made again, the artists apart.
    `tracks.Parted` / ValueError: a step can no longer be taken back as it was. Returns the playlists changed."""
    from app.library import editor
    from app.library import tracks as registry

    playlists: set[int] = set()
    for step in reversed(undo.get("tracks") or []):
        keep = await s.get(Track, step["keep"])
        if keep is None:
            raise registry.Parted("Трека, с которым объединили копию, уже нет")
        _back, changed = await registry.part(s, keep, step["joined"], user_id=user_id)
        playlists |= changed
    for step in reversed(undo.get("albums") or []):
        target = await s.get(Album, step["target"])
        if target is None:
            raise ValueError("Альбома, с которым объединили, уже нет")
        await editor.part_albums(s, target, step["joined"])
    if undo.get("artist") is not None:
        await artists_.unmerge(s, user_id, into, other, undo["artist"])
    return playlists
