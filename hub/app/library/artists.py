"""Who an artist is — whatever the name is written as, whichever page of a catalog it is — and what else a song is
called.

An artist (`Artist`) is somebody the Hub knows, with one name everyone sees. Who a track or an album is by is said
by links to artists (`TrackArtist`, `AlbumArtist`), in the order of the credits; the lines written in the rows and
in the files' tags («ЛСП, Pharaoh») are those artists' names, so a new name, or two artists found to be one,
rewrites the lines of exactly their music (`rewrite`, a job) — nobody else's, whatever they are called.

An artist is told in two ways, and they are kept apart:

* by a catalog's own record of them (`ArtistRef`: "dz:8874422") — exact. A catalog may keep several records of
  one artist (the old releases under «LSP», the new under «ЛСП»): each is tied to the artist here, and their page
  is drawn of all of them. A record is tied by itself only when it is the first the artist has; a second record
  under a name the artist is spelt by may as well be a namesake's, so it becomes an artist of its own, and the Hub
  only says the two look alike (`suggestions`) — a listener joins them (`merge`) or says «это другой».
* by a bare spelling (`ArtistName`: a dictionary, one artist per spelling, the letters' case aside) — for music
  that says only a name: an uploaded file, a line typed by hand. A namesake of whoever holds a spelling is still
  an artist, with that very name: only bare text of that spelling is not taken for them.

A side project is not a spelling: «M00NCHILD» has releases of its own and stays an artist of its own — the two are
only linked (`ArtistLink`), and the projects of one person are one group.

A track, too, has one name and others it is known by (`TrackName`): the name it had before it was put right, what
its file called it, one a listener adds («Марафоны» for «Спорт»). The search reads them all (`found`).
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.library.credits import split_artists
from app.matching.normalize import _clean, _prep, fingerprint, to_latin
from app.models import (
    Album,
    AlbumArtist,
    Artist,
    ArtistApart,
    ArtistLink,
    ArtistName,
    ArtistRef,
    Edit,
    Track,
    TrackArtist,
    TrackFile,
    TrackName,
    utcnow,
)


class Taken(Exception):
    """The spelling is another artist's: the two may be one (`artist_id`, `name`)."""

    def __init__(self, artist_id: int, name: str):
        super().__init__(name)
        self.artist_id, self.name = artist_id, name


def key(name: str | None) -> str:
    """What makes two spellings the same one: the letters' case and the spaces do not."""
    return " ".join((name or "").split()).casefold()


def fold(text: str | None) -> str:
    """A name as it reads in Latin letters, without punctuation: «Деньги не проблема» and «Den'gi ne problema»,
    «ЛСП» and «LSP» fold to the same."""
    return to_latin(_clean(_prep(text)))


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


# ---------------------------------------------------------------- who an artist is


async def holder(s: AsyncSession, name: str | None) -> Artist | None:
    """Whose a bare spelling is (none: nobody's yet)."""
    k = key(name)
    if not k:
        return None
    return (await s.execute(select(Artist).join(ArtistName, ArtistName.artist_id == Artist.id).where(ArtistName.key == k))).scalar_one_or_none()


async def by_ref(s: AsyncSession, ref: str | None) -> Artist | None:
    if not ref:
        return None
    from app.catalog.registry import numeric_id

    if ref.startswith("artist:") and (ident := numeric_id(ref[7:])) is not None:
        return await by_id(s, ident)
    return (await s.execute(select(Artist).join(ArtistRef, ArtistRef.artist_id == Artist.id).where(ArtistRef.ref == ref))).scalar_one_or_none()


async def by_id(s: AsyncSession, ident: int) -> Artist | None:
    artist = await s.get(Artist, ident)
    return await s.get(Artist, artist.merged_into_id) if artist and artist.merged_into_id else artist


async def tie_profile(s: AsyncSession, artist_id: int, ref: str) -> None:
    """Associate an independently verified profile without moving someone else's profile."""
    await s.execute(insert(ArtistRef).values(ref=ref, artist_id=artist_id).on_conflict_do_nothing())


async def profiles(s: AsyncSession, artist_id: int) -> list[str]:
    """The catalog's records of the artist, the first one tied first."""
    return list((await s.execute(select(ArtistRef.ref).where(ArtistRef.artist_id == artist_id).order_by(ArtistRef.tied_at, ArtistRef.ref))).scalars())


async def addresses(s: AsyncSession, refs: Iterable[str | None]) -> dict[str, str]:
    """A catalog's records -> the one each artist is addressed by: the first tied of that catalog's records of them.
    Whatever is said of an artist by a record — their page, a follow, a pick — is said at this one address, so
    the several records a catalog keeps of one artist never look like several artists. A record nobody is tied
    to is its own address."""
    refs = {r for r in refs if r}
    if not refs:
        return {}
    whose = dict((await s.execute(select(ArtistRef.ref, ArtistRef.artist_id).where(ArtistRef.ref.in_(refs)))).all())
    first: dict[tuple[int, str], str] = {}
    if whose:
        for id_, ref in (await s.execute(select(ArtistRef.artist_id, ArtistRef.ref).where(
                ArtistRef.artist_id.in_(set(whose.values()))).order_by(ArtistRef.tied_at, ArtistRef.ref))).all():
            first.setdefault((id_, ref.partition(":")[0]), ref)
    return {r: first[(whose[r], r.partition(":")[0])] if r in whose else r for r in refs}


async def records(s: AsyncSession) -> list[list[str]]:
    """Each artist's verified catalog records, followed by their Hub addresses."""
    by: dict[int, list[str]] = {}
    for id_, ref in (await s.execute(select(ArtistRef.artist_id, ArtistRef.ref).order_by(ArtistRef.tied_at, ArtistRef.ref))).all():
        by.setdefault(id_, []).append(ref)
    for ident, merged in (await s.execute(select(Artist.id, Artist.merged_into_id).order_by(Artist.id))).all():
        by.setdefault(merged or ident, []).append(f"artist:{ident}")
    return [refs for refs in by.values() if len(refs) > 1]


async def _new(s: AsyncSession, name: str, source: str, *, spelling: bool) -> Artist:
    a = Artist(name=" ".join(name.split()))
    s.add(a)
    await s.flush()
    if spelling:
        s.add(ArtistName(artist_id=a.id, name=a.name, key=key(a.name), source=source))
        await s.flush()
    return a


async def who(s: AsyncSession, name: str | None, ref: str | None = None, source: str = "library", *,
              allow_name_match: bool = True) -> Artist | None:
    """The artist that a name — and, where a catalog said it, the catalog's record — stands for; somebody not met
    yet becomes an artist. None: neither was given.

    A record the Hub has tied says who it is, whatever the name. One not tied yet is the artist's of that
    spelling when they have no record in that catalog so far (their first there), and a new artist's otherwise —
    perhaps a namesake: a catalog that keeps two records under one name may well mean two artists."""
    a = await by_ref(s, ref)
    if a is not None:
        return a
    if not key(name):
        return None
    a = await holder(s, name)
    if ref is None:
        return a or await _new(s, name, source, spelling=True)
    if a is None:
        a = await _new(s, name, source, spelling=True)
    elif (not allow_name_match or ref.startswith("sp:")
          or any(r.partition(":")[0] == ref.partition(":")[0] for r in await profiles(s, a.id))):
        a = await _new(s, name, source, spelling=False)
    s.add(ArtistRef(ref=ref, artist_id=a.id))
    await s.flush()
    return a


async def whole(s: AsyncSession, ref: str | None, name: str | None = None) -> tuple[list[str], list[str]]:
    """Everything an artist is known by — every record a catalog keeps of them, and their name with every
    spelling — for the artist a record (or, with none, a name) stands for. What is said of an artist (they are
    not to be played, say) is said of all of this. Somebody the Hub does not know is what was given."""
    a = await by_ref(s, ref) if ref else await holder(s, name)
    if a is None:
        return ([ref] if ref else []), ([name] if name else [])
    refs = await profiles(s, a.id)
    spelt = (await s.execute(select(ArtistName.name).where(ArtistName.artist_id == a.id).order_by(ArtistName.id))).scalars().all()
    names_: list[str] = []
    for n in (a.name, *spelt):
        if key(n) not in {key(x) for x in names_}:
            names_.append(n)
    refs = list(dict.fromkeys([*refs, f"artist:{a.id}", *([ref] if ref else [])]))
    return refs, names_


class Reader:
    """Names as they are here, for what a catalog says (`reader`): nothing is made, nobody is tied."""

    def __init__(self, tied: dict[str, tuple[int, str]], held: dict[str, tuple[int, str, set[str]]]):
        self.tied, self.held = tied, held  # `held`: spelling -> (artist, name, the catalogs they have a record in)

    def artist(self, name: str | None, ref: str | None = None) -> tuple[int, str] | None:
        """(the artist's id, their name) for a name and the catalog's record of them; None: not somebody known —
        or a record not tied, under a spelling of an artist who has a record of their own in that catalog (a
        namesake, perhaps)."""
        if ref and ref in self.tied:
            return self.tied[ref]
        hit = self.held.get(key(name))
        if hit is None or (ref and ref.partition(":")[0] in hit[2]):
            return None
        return hit[0], hit[1]

    def name(self, name: str | None, ref: str | None = None) -> str | None:
        hit = self.artist(name, ref)
        return hit[1] if hit else name


async def reader(s: AsyncSession, names_: Iterable[str | None], refs: Iterable[str | None] = ()) -> Reader:
    keys = {key(n) for n in names_ if n} - {""}
    refs = {r for r in refs if r}
    tied = {r: (i, n) for r, i, n in (await s.execute(
        select(ArtistRef.ref, Artist.id, Artist.name).join(Artist, Artist.id == ArtistRef.artist_id).where(ArtistRef.ref.in_(refs)))).all()} if refs else {}
    held: dict[str, tuple[int, str, set[str]]] = {}
    if keys:
        rows = (await s.execute(select(ArtistName.key, Artist.id, Artist.name).join(Artist, Artist.id == ArtistName.artist_id).where(ArtistName.key.in_(keys)))).all()
        catalogs: dict[int, set[str]] = {}
        if rows:
            for i, r in (await s.execute(select(ArtistRef.artist_id, ArtistRef.ref).where(ArtistRef.artist_id.in_({i for _, i, _ in rows})))).all():
                catalogs.setdefault(i, set()).add(r.partition(":")[0])
        held = {k: (i, n, catalogs.get(i, set())) for k, i, n in rows}
    return Reader(tied, held)


def put_right(o: Any, r: Reader) -> None:
    """What a catalog says of a track or an album, in the artists' names here."""
    main_ref = getattr(o, "artist_ref", None)
    main = getattr(o, "artist", None)
    if isinstance(main, str):
        o.artist = r.name(main, main_ref)
        if hasattr(o, "hub_artist_id") and (hit := r.artist(main, main_ref)):
            o.hub_artist_id = hit[0]
        own = getattr(o, "album_artist", None)
        if isinstance(own, str):
            o.album_artist = r.name(own, main_ref if key(own) == key(main) else None)
    names_ = getattr(o, "artists", None)
    if isinstance(names_, list) and names_:
        refs = list(getattr(o, "artist_refs", None) or [])
        refs += [None] * (len(names_) - len(refs))
        if hasattr(o, "hub_artist_ids"):
            o.hub_artist_ids = [hit[0] if isinstance(n, str) and (hit := r.artist(n, ref)) else None
                                for n, ref in zip(names_, refs, strict=False)]
        o.artists = [r.name(n, ref) if isinstance(n, str) else n for n, ref in zip(names_, refs, strict=False)]


async def shown(s: AsyncSession, found_: list[Any], *, exact: bool = False) -> list[Any]:
    """A catalog's artists (each with a `ref` and a `name`) as they are here: under their names, and once each —
    the several records a catalog keeps of one artist are one card, that of the record with the most followers,
    at the artist's one address (`addresses`): the same artist is the same `ref` wherever they are shown."""
    if not found_:
        return found_
    at_ = await addresses(s, [a.ref for a in found_])
    r = await reader(s, [a.name for a in found_], [a.ref for a in found_])
    from app.library import looks

    out: list[Any] = []
    at: dict[int, int] = {}
    photos: dict[int, tuple[str, str] | None] = {}  # the photo chosen for an artist
    for a in found_:
        hit = r.tied.get(a.ref) if exact else r.artist(a.name, a.ref)
        if hit is None:
            out.append(a)
            continue
        card_ = a.model_copy(update={"name": hit[1], "hub_artist_id": hit[0]})
        if (addr := at_.get(a.ref, a.ref)) != a.ref:
            card_ = card_.model_copy(update={"ref": addr, **({"id": addr.partition(":")[2]} if hasattr(a, "id") else {})})
        if hit[0] not in photos:
            who_ = await s.get(Artist, hit[0])
            photos[hit[0]] = await looks.photo(s, who_, chosen_only=True) if who_ is not None else None
        if photos[hit[0]]:
            card_ = card_.model_copy(update={"picture_url": photos[hit[0]][0], "picture_small_url": photos[hit[0]][1]})
        if hit[0] not in at:
            at[hit[0]] = len(out)
            out.append(card_)
        elif (getattr(a, "nb_fans", None) or 0) > (getattr(out[at[hit[0]]], "nb_fans", None) or 0):
            out[at[hit[0]]] = card_
    return out


# ---------------------------------------------------------------- who the music is by


def beside(names_: list[str] | None, refs: list[str | None] | None) -> dict[str, str]:
    """Spelling (as `key`) -> the catalog's record that stands beside it. The records stand beside the names one to
    one, or say nothing: a list of another length is of other names (a service's names by the catalog's records),
    and would give an artist somebody else's record."""
    names_, refs = names_ or [], refs or []
    return {key(n): r for n, r in zip(names_, refs) if n and r} if len(names_) == len(refs) else {}


def refs_of(catalog: dict[str, Any] | None) -> dict[str, str]:
    """Spelling (as `key`) -> the catalog's record, as the catalog's word of a track says."""
    c = catalog or {}
    out = beside(c.get("artists"), c.get("artist_refs"))
    if c.get("artist") and c.get("artist_ref"):
        out.setdefault(key(c["artist"]), c["artist_ref"])
    return out


def _line(text: str | None, parts: list[str], theirs: list[Artist]) -> str:
    """A line of credits in the artists' names; one that names them rightly already stays as it was written."""
    if all(a.name == p for a, p in zip(theirs, parts)):
        return (text or "").strip()
    out: list[str] = []
    for a in theirs:
        if key(a.name) not in {key(x) for x in out}:
            out.append(a.name)
    return ", ".join(out)


async def credits(s: AsyncSession, text: str | None, refs: dict[str, str] | None = None, source: str = "library") -> tuple[str, list[Artist]]:
    """Who a line of credits names, and the line in their names. `refs`: the catalog's records by spelling."""
    parts = split_artists(text)
    theirs = [a for a in [await who(s, p, (refs or {}).get(key(p)), source) for p in parts] if a is not None]
    if len(theirs) != len(parts) or not parts:
        return (text or "").strip(), theirs
    return _line(text, parts, theirs), theirs


async def _set(s: AsyncSession, table: Any, column: Any, owner_id: int, theirs: list[Artist]) -> None:
    await s.execute(delete(table).where(column == owner_id))
    seen: set[int] = set()
    for a in theirs:
        if a.id not in seen:
            s.add(table(**{column.key: owner_id, "position": len(seen), "artist_id": a.id}))
            seen.add(a.id)
    await s.flush()


async def credit_track(s: AsyncSession, track: Track, theirs: list[Artist] | None = None) -> None:
    """Say who the track is by — as given, or as its line and the catalog's word of it tell."""
    if theirs is None:
        _, theirs = await credits(s, track.artist, refs_of(track.catalog))
    await _set(s, TrackArtist, TrackArtist.track_id, track.id, theirs)


async def credit_album(s: AsyncSession, album: Album, theirs: list[Artist] | None = None) -> None:
    if theirs is None:
        _, theirs = await credits(s, album.album_artist)
    await _set(s, AlbumArtist, AlbumArtist.album_id, album.id, theirs)


async def seed(s: AsyncSession) -> int:
    """Every track and album of the library says who it is by (run when the Hub starts: it heals what was made
    before the links, and costs nothing once all are said)."""
    n = 0
    said = select(TrackArtist.track_id).where(TrackArtist.track_id == Track.id).exists()
    for t in (await s.execute(select(Track).where(~said, Track.artist != ""))).scalars().all():
        await credit_track(s, t)
        n += 1
    said = select(AlbumArtist.album_id).where(AlbumArtist.album_id == Album.id).exists()
    for a in (await s.execute(select(Album).where(~said, Album.album_artist != ""))).scalars().all():
        # an album is by whom its tracks say, where they name the same artist
        own = (await s.execute(
            select(Artist).join(TrackArtist, TrackArtist.artist_id == Artist.id).join(Track, Track.id == TrackArtist.track_id)
            .where(Track.album_id == a.id))).scalars().all()
        by = {key(x.name): x for x in own}
        parts = split_artists(a.album_artist)
        theirs = [by.get(key(p)) or await who(s, p) for p in parts]
        await credit_album(s, a, [x for x in theirs if x is not None])
        n += 1
    return n


# ---------------------------------------------------------------- an artist's card


async def _linked(s: AsyncSession, artist_id: int) -> set[int]:
    """The other projects of who the artist is: everyone linked to them, straight or through another project —
    the projects of one person are one group, whichever two of them were named together."""
    near: dict[int, set[int]] = {}
    for low, high in (await s.execute(select(ArtistLink.low_id, ArtistLink.high_id))).all():
        near.setdefault(low, set()).add(high)
        near.setdefault(high, set()).add(low)
    seen, todo = {artist_id}, [artist_id]
    while todo:
        for i in near.get(todo.pop(), ()):
            if i not in seen:
                seen.add(i)
                todo.append(i)
    return seen - {artist_id}


async def _apart(s: AsyncSession, artist_id: int) -> set[int]:
    rows = (await s.execute(select(ArtistApart.low_id, ArtistApart.high_id).where(or_(ArtistApart.low_id == artist_id, ArtistApart.high_id == artist_id)))).all()
    return {x for r in rows for x in r if x != artist_id}


async def suggestions(s: AsyncSession, artist: Artist) -> list[Artist]:
    """Artists that look like this one: a name of theirs reads the same in Latin letters. Not the ones linked as
    projects, nor those a listener said are somebody else."""
    rows = [*(await s.execute(select(ArtistName.artist_id, ArtistName.name))).all(),
            *(await s.execute(select(Artist.id, Artist.name).where(Artist.merged_into_id.is_(None)))).all()]
    mine = {fold(n) for i, n in rows if i == artist.id} - {""}
    skip = {artist.id} | await _linked(s, artist.id) | await _apart(s, artist.id)
    ids = {i for i, n in rows if i not in skip and fold(n) in mine}
    return list((await s.execute(select(Artist).where(Artist.id.in_(ids)).order_by(Artist.name))).scalars()) if ids else []


async def _catalog(ref: str | None) -> Any | None:
    """The catalog's own card of a record (kept by the catalog's client); None: no such record, or the catalog is away."""
    from app.library.looks import profile_card

    return await profile_card(ref)


async def _picture(s: AsyncSession, artist: Artist, ref: str | None) -> str | None:
    """A small picture of the artist: the one chosen for them (app/library/looks.py), else the catalog's, else the
    cover of an album of theirs here."""
    from app.library import looks

    chosen = await looks.photo(s, artist, chosen_only=True)
    if chosen:
        return chosen[1]
    a = await _catalog(ref)
    if a is not None and (a.picture_small_url or a.picture_url):
        return a.picture_small_url or a.picture_url
    result = await looks.photo(s, artist)
    return result[1] if result else None


async def _brief(s: AsyncSession, a: Artist) -> dict[str, Any]:
    refs = await profiles(s, a.id)
    cat = await _catalog(refs[0]) if refs else None
    return {"id": a.id, "name": a.name, "ref": refs[0] if refs else None, "nb_fans": getattr(cat, "nb_fans", None),
            "picture_url": await _picture(s, a, refs[0] if refs else None)}


async def card(s: AsyncSession, artist: Artist) -> dict[str, Any]:
    """What an artist's page says of who they are: the name and the spellings, the catalog's records, the other
    projects, and who looks like the same artist."""
    rows = (await s.execute(select(ArtistName).where(ArtistName.artist_id == artist.id).order_by(ArtistName.id))).scalars().all()
    main = key(artist.name)
    names_ = [{"id": r.id, "name": r.name, "source": r.source, "main": r.key == main} for r in sorted(rows, key=lambda r: r.key != main)]
    held_by = None
    if not any(n["main"] for n in names_):  # a namesake of whoever holds the spelling: the name is theirs all the same
        other = await holder(s, artist.name)
        held_by = other.name if other is not None else None
        names_.insert(0, {"id": None, "name": artist.name, "source": "catalog", "main": True})
    projects = list((await s.execute(select(Artist).where(Artist.id.in_(await _linked(s, artist.id))).order_by(Artist.name))).scalars())
    out_projects = []
    for p in projects:
        also = (await s.execute(select(ArtistName.name).where(ArtistName.artist_id == p.id).order_by(ArtistName.id))).scalars().all()
        out_projects.append({**await _brief(s, p), "also": [n for n in also if key(n) != key(p.name)]})
    return {
        "id": artist.id, "name": artist.name, "names": names_, "held_by": held_by,
        "profiles": await profiles(s, artist.id), "projects": out_projects,
        "suggestions": [await _brief(s, a) for a in await suggestions(s, artist)],
    }


async def find(s: AsyncSession, q: str, limit: int = 12) -> list[dict[str, Any]]:
    """Artists by any spelling, read in Latin letters too."""
    want = fold(q)
    if not want:
        return []
    rows = [*(await s.execute(select(ArtistName.artist_id, ArtistName.name, Artist.name).join(Artist, Artist.id == ArtistName.artist_id))).all(),
            *(await s.execute(select(Artist.id, Artist.name, Artist.name).where(Artist.merged_into_id.is_(None)))).all()]
    out: dict[int, dict[str, Any]] = {}
    for i, n, main in sorted(rows, key=lambda r: (not fold(r[1]).startswith(want), len(r[1]))):
        if want in fold(n) and i not in out:
            out[i] = {"id": i, "name": main}
    return list(out.values())[:limit]


# ---------------------------------------------------------------- what a listener changes


def _journal(s: AsyncSession, user_id: int | None, artist_id: int, changes: dict[str, list[Any]]) -> None:
    s.add(Edit(target_type="artist", target_id=artist_id, user_id=user_id, changes_json=changes))


async def add_name(s: AsyncSession, user_id: int | None, artist: Artist, name: str) -> ArtistName:
    """One more spelling of the artist. `Taken`: it is somebody else's here — the two may be joined."""
    name = " ".join(name.split())
    if not name:
        raise ValueError("Пустое написание")
    row = (await s.execute(select(ArtistName).where(ArtistName.key == key(name)))).scalar_one_or_none()
    if row is not None:
        if row.artist_id == artist.id:
            return row
        other = await s.get(Artist, row.artist_id)
        raise Taken(row.artist_id, other.name if other else name)
    row = ArtistName(artist_id=artist.id, name=name, key=key(name), source="manual")
    s.add(row)
    _journal(s, user_id, artist.id, {"name": [None, name]})
    await s.flush()
    return row


async def set_main(s: AsyncSession, user_id: int | None, artist: Artist, name_id: int) -> bool:
    """A spelling becomes the artist's name; their music is then rewritten in it (the caller queues `rewrite`)."""
    row = await s.get(ArtistName, name_id)
    if row is None or row.artist_id != artist.id:
        raise ValueError("У исполнителя нет такого написания")
    if row.name == artist.name:
        return False
    _journal(s, user_id, artist.id, {"main": [artist.name, row.name]})
    artist.name = row.name
    await s.flush()
    return True


async def drop_name(s: AsyncSession, user_id: int | None, artist: Artist, name_id: int) -> Artist | None:
    """A spelling leaves the artist. One a listener wrote in is forgotten; one that came with music becomes an
    artist of its own again (returned) — what comes under it from now on is theirs; the music already the
    artist's stays so. The artist's own name stays."""
    row = await s.get(ArtistName, name_id)
    if row is None or row.artist_id != artist.id:
        raise ValueError("У исполнителя нет такого написания")
    if row.key == key(artist.name):
        raise ValueError("Это основное имя: сначала сделайте основным другое")
    _journal(s, user_id, artist.id, {"name": [row.name, None]})
    if row.source == "manual":
        await s.delete(row)
        await s.flush()
        return None
    own = Artist(name=row.name)
    s.add(own)
    await s.flush()
    row.artist_id = own.id
    s.add(ArtistApart(low_id=_pair(artist.id, own.id)[0], high_id=_pair(artist.id, own.id)[1]))
    await s.flush()
    return own


async def _hand_over(s: AsyncSession, table: Any, column: Any, into: int, other: int) -> None:
    """The music `other` is credited for is `into`'s — once, where it names them both."""
    both = select(column).where(table.artist_id == into)
    await s.execute(delete(table).where(table.artist_id == other, column.in_(both)))
    await s.execute(update(table).where(table.artist_id == other).values(artist_id=into))


async def merge(s: AsyncSession, user_id: int | None, into: Artist, other: Artist) -> None:
    """Two artists are one: `other`'s spellings, catalog records, music and projects are `into`'s (the caller
    queues `rewrite`, which writes that music in the name)."""
    if into.id == other.id:
        return
    await s.execute(update(ArtistName).where(ArtistName.artist_id == other.id).values(artist_id=into.id))
    if await holder(s, other.name) is None:  # a namesake held no spelling: now its name is one of the artist's
        s.add(ArtistName(artist_id=into.id, name=other.name, key=key(other.name), source="catalog"))
    await s.execute(update(ArtistRef).where(ArtistRef.artist_id == other.id).values(artist_id=into.id))
    await _hand_over(s, TrackArtist, TrackArtist.track_id, into.id, other.id)
    await _hand_over(s, AlbumArtist, AlbumArtist.album_id, into.id, other.id)
    theirs = (await _linked(s, other.id)) - {into.id}
    mine = await _linked(s, into.id)
    await s.execute(delete(ArtistLink).where(or_(ArtistLink.low_id == other.id, ArtistLink.high_id == other.id)))
    await s.execute(delete(ArtistApart).where(or_(ArtistApart.low_id == other.id, ArtistApart.high_id == other.id)))
    for i in theirs - mine:
        s.add(ArtistLink(low_id=_pair(into.id, i)[0], high_id=_pair(into.id, i)[1]))
    _journal(s, user_id, into.id, {"merged": [other.name, into.name]})
    await s.flush()
    await s.execute(update(Artist).where(Artist.merged_into_id == other.id).values(merged_into_id=into.id))
    other.merged_into_id = into.id
    await s.flush()


async def link(s: AsyncSession, user_id: int | None, a: Artist, b: Artist, on: bool = True) -> None:
    """`b` is another project of who `a` is — and so of every project of theirs. Or is not any more: `b` leaves
    the group, and the others stay each other's."""
    if a.id == b.id:
        raise ValueError("Это тот же исполнитель")
    group = await _linked(s, a.id)
    if on and b.id not in group:
        low, high = _pair(a.id, b.id)
        s.add(ArtistLink(low_id=low, high_id=high))
        _journal(s, user_id, a.id, {"project": [None, b.name]})
    elif not on and b.id in group:
        await s.execute(delete(ArtistLink).where(or_(ArtistLink.low_id == b.id, ArtistLink.high_id == b.id)))
        await s.flush()
        rest = sorted(group - {b.id})  # they were perhaps linked only through the one that left
        still = await _linked(s, a.id)
        for i in rest:
            if i not in still:
                low, high = _pair(a.id, i)
                s.add(ArtistLink(low_id=low, high_id=high))
        _journal(s, user_id, a.id, {"project": [b.name, None]})
    await s.flush()


async def apart(s: AsyncSession, a: Artist, b: Artist) -> None:
    """«Это другой»: the two are not offered to be joined again."""
    low, high = _pair(a.id, b.id)
    if a.id != b.id and (await s.execute(select(ArtistApart).where(ArtistApart.low_id == low, ArtistApart.high_id == high))).scalar_one_or_none() is None:
        s.add(ArtistApart(low_id=low, high_id=high))
        await s.flush()


async def detach_profile(s: AsyncSession, user_id: int, artist: Artist, ref: str, name: str) -> bool:
    """Give the catalog record its own identity and remember that the two artists are separate."""
    detached = await _new(s, name, "catalog", spelling=False)
    moved = await s.execute(update(ArtistRef).where(
        ArtistRef.ref == ref, ArtistRef.artist_id == artist.id,
    ).values(artist_id=detached.id, tied_at=utcnow()).returning(ArtistRef.ref))
    if moved.scalar_one_or_none() is None:
        return False  # the caller rolls back the provisional artist
    await s.execute(insert(ArtistName).values(
        artist_id=detached.id, name=detached.name, key=key(detached.name), source="catalog",
    ).on_conflict_do_nothing(index_elements=[ArtistName.key]))
    await apart(s, artist, detached)
    _journal(s, user_id, artist.id, {"profile": [ref, None], "detached_artist": [None, detached.id]})
    return True


async def _names_of(s: AsyncSession, table: Any, column: Any, owner_id: int) -> list[Artist]:
    return list((await s.execute(select(Artist).join(table, table.artist_id == Artist.id).where(column == owner_id).order_by(table.position))).scalars())


async def rewrite(s: AsyncSession, artist: Artist, user_id: int | None = None) -> int:
    """Write the artist's music in their name: the lines of the tracks and albums credited to them — the rows and
    the files' tags. An album that becomes the same as one the artist has is joined with it. Returns how many
    tracks and albums changed. It may be run again: what is already right is left."""
    from app.library import editor
    from app.library.importer import find_album

    changed = 0
    albums = (await s.execute(select(Album).join(AlbumArtist, AlbumArtist.album_id == Album.id).where(AlbumArtist.artist_id == artist.id))).scalars().all()
    for album in albums:
        parts = split_artists(album.album_artist)
        theirs = await _names_of(s, AlbumArtist, AlbumArtist.album_id, album.id)
        if len(theirs) != len(parts):
            continue
        new = _line(album.album_artist, parts, theirs)
        if new == album.album_artist:
            continue
        same = await find_album(s, title=album.title, album_artist=new, deezer_album_id=album.deezer_album_id)
        s.add(Edit(target_type="album", target_id=album.id, user_id=user_id, changes_json={"album_artist": [album.album_artist, new]}))
        if same is not None and same.id != album.id:
            await editor.merge_albums(s, [album.id], same)
        else:
            await editor.update_album(s, album, album_artist=new)
        changed += 1
    tracks = (await s.execute(select(Track).join(TrackArtist, TrackArtist.track_id == Track.id).where(TrackArtist.artist_id == artist.id))).scalars().all()
    for track in tracks:
        parts = split_artists(track.artist)
        theirs = await _names_of(s, TrackArtist, TrackArtist.track_id, track.id)
        if len(theirs) == len(parts):
            new = _line(track.artist, parts, theirs)
            if new != track.artist:
                s.add(Edit(target_type="track", target_id=track.id, user_id=user_id, changes_json={"artist": [track.artist, new]}))
                await editor.update_track(s, track, artist=new)
                track.fingerprint = fingerprint(track.artist, track.title)
                changed += 1
        if track.album_id is None and track.album_artist and key(track.album_artist) != key(artist.name):
            own = await holder(s, track.album_artist)
            if own is not None and own.id == artist.id:
                track.album_artist = artist.name
    await s.flush()
    return changed


# ---------------------------------------------------------------- what else a track is called


async def titles(s: AsyncSession, track: Track) -> list[dict[str, Any]]:
    """The other names of a track: the ones kept, and what the catalog calls it where that is not its name."""
    rows = (await s.execute(select(TrackName).where(TrackName.track_id == track.id).order_by(TrackName.id))).scalars().all()
    out = [{"id": r.id, "name": r.name, "source": r.source} for r in rows if r.key != key(track.title)]
    cat = (track.catalog or {}).get("title")
    if cat and key(cat) != key(track.title) and key(cat) not in {key(o["name"]) for o in out}:
        out.append({"id": None, "name": cat, "source": "catalog"})
    return out


async def add_title(s: AsyncSession, track: Track, name: str | None, source: str = "manual") -> bool:
    name = " ".join((name or "").split())
    if not name or key(name) == key(track.title):
        return False
    if (await s.execute(select(TrackName.id).where(TrackName.track_id == track.id, TrackName.key == key(name)))).first():
        return False
    s.add(TrackName(track_id=track.id, name=name, key=key(name), source=source))
    await s.flush()
    return True


async def drop_title(s: AsyncSession, track: Track, name_id: int) -> None:
    await s.execute(delete(TrackName).where(TrackName.track_id == track.id, TrackName.id == name_id))


async def renamed(s: AsyncSession, track: Track, was: str | None) -> None:
    """The track was given another name: the one it had is kept among the others, the new one is not one of them."""
    await s.execute(delete(TrackName).where(TrackName.track_id == track.id, TrackName.key == key(track.title)))
    await add_title(s, track, was, "renamed")


# ---------------------------------------------------------------- the library's search


async def found(s: AsyncSession, q: str, limit: int = 20, *, among: Iterable[int] | None = None) -> list[tuple[Track, list[str]]]:
    """The library's songs a query finds — tracks that have a file — by a track's name and its other names, its
    artists in every spelling, its album; Cyrillic and Latin read as one. With each: the other names and spellings
    it was found by (none: by what the row shows). `among`: these tracks are looked through instead, with a file
    or without one (what a listener keeps in their playlists)."""
    words = fold(q).split()
    if not words:
        return []
    other: dict[int, list[str]] = {}
    for tid, n in (await s.execute(select(TrackName.track_id, TrackName.name))).all():
        other.setdefault(tid, []).append(n)
    from app.library import album_names

    album_titles = await album_names.listed(s)
    spelt: dict[int, list[str]] = {}  # an artist -> every spelling of theirs
    for aid, n in (await s.execute(select(ArtistName.artist_id, ArtistName.name))).all():
        spelt.setdefault(aid, []).append(n)
    by: dict[int, list[int]] = {}  # a track -> who it is by
    for tid, aid in (await s.execute(select(TrackArtist.track_id, TrackArtist.artist_id).order_by(TrackArtist.position))).all():
        by.setdefault(tid, []).append(aid)
    has_file = select(TrackFile.id).where(TrackFile.track_id == Track.id).exists()
    hits: list[tuple[int, Track, list[str]]] = []
    ids = None if among is None else list(dict.fromkeys(among))
    rows: list[Track] = []
    if ids is None:
        rows = list((await s.execute(select(Track).where(has_file))).scalars())
    for k in range(0, len(ids or []), 500):
        rows += list((await s.execute(select(Track).where(Track.id.in_(ids[k:k + 500])))).scalars())  # type: ignore[index]
    for t in rows:
        shown_ = fold(f"{t.artist} {t.title} {t.album or ''}")
        also = [*other.get(t.id, []), *([(t.catalog or {}).get("title")] if (t.catalog or {}).get("title") else [])]
        also.extend(row.name for row in album_titles.get(t.album_id, []))
        for aid in by.get(t.id, []):
            also += spelt.get(aid, [])
        folded = [(n, fold(n)) for n in also]
        if not all(w in shown_ or any(w in f for _, f in folded) for w in words):
            continue
        told = []
        for n, f in folded:
            if any(w in f and w not in shown_ for w in words) and n not in told:
                told.append(n)
        # a name that begins with the query before one that only has it inside; what the row shows before the rest
        hits.append((int(bool(told)) * 2 + int(not fold(t.title).startswith(words[0])), t, told))
    hits.sort(key=lambda h: h[0])
    return [(t, told) for _, t, told in hits[:limit]]
