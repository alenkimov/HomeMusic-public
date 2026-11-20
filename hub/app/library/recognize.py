"""What song of the catalog a file is.

A file brought from outside says what it is as it can: several artists in one line, «Single» for an album or the
track's own name, a year missing, a cover found somewhere. The catalog says the same of the song properly — each
artist by name, the release it came out on and what kind it is, the year, the cover. So a file is looked for in the
catalog by its artist, its name and its length (the names read out of the file's name where its tags are empty),
and when the song is found the file is filed as that song.

Three answers. `found`: one record stands out and nothing speaks against it. `doubt`: a record is like the file, but
not surely it — the names are only alike, the length is another, the file is a version (slowed, a remix) and the
record is not: it is shown, and used only when the listener says so. `none`: the catalog has nothing like it — the
file keeps its own names.
"""

from __future__ import annotations

import asyncio
import logging
import re
from itertools import zip_longest
from time import monotonic
from typing import Any

from app.catalog import lookup
from app.catalog.models import CatalogAlbum, CatalogArtist, CatalogTrack
from app.config import get_settings
from app.library.credits import main_artist
from app.matching.normalize import has_cyrillic, normalize_artist, split_version, to_latin, version_kind
from app.matching.score import artist_sim
from app.matching.select import classify_scores, match_catalog
from app.services import svc

log = logging.getLogger(__name__)

_NAME = re.compile(r"^\s*(?:\d{1,3}[.)]?\s+)?(?P<artist>.+?)\s+[-–—]\s+(?P<title>.+?)\s*$")
_EXT = re.compile(r"\.[A-Za-z0-9]{2,5}$")
SAME_LENGTH_S = 2  # two lengths this near are one recording's
DOUBT_LENGTH_S = 5  # a record whose length differs by more is not surely the file's recording


def names_of(tags: dict[str, Any], file_name: str) -> tuple[str, str]:
    """(artist, title) of a file: its tags; what they do not say — out of «Artist - Title» of its name."""
    artist, title = str(tags.get("artist") or "").strip(), str(tags.get("title") or "").strip()
    stem = _EXT.sub("", file_name or "")
    m = _NAME.match(stem)
    if m and (not artist or not title or title == stem):
        artist, title = artist or m["artist"].strip(), m["title"].strip() if not title or title == stem else title
    return artist, title or stem


def single_album(album: str | None, title: str) -> bool:
    """The file's «album» is no album: nothing, «Single», or the track's own name."""
    a = " ".join((album or "").split()).casefold()
    return not a or a in ("single", "singles", "сингл", "синглы") or a.endswith("- single") or a == " ".join(title.split()).casefold()


_JOINT = re.compile(r"\s+(?:&|x|X|и|and|vs\.?)\s+")
RELEASES_MAX = 80  # album lookups per artist across the enabled catalogs
_released: dict[tuple[str, tuple[str, ...]], tuple[float, list[CatalogTrack]]] = {}
_RELEASED_KEPT = 400


async def _released_by(line: str) -> list[CatalogTrack]:
    """Read a bounded part of the artist's releases across the available catalogs."""
    name = main_artist(line)
    sources = tuple(src for src in lookup.SOURCE_PRIORITY if lookup.source_available(f"{src}:"))
    key = (name.casefold(), sources)
    if (cached := _released.get(key)) and cached[0] > monotonic():
        return cached[1]
    if not sources:
        return []
    profiles: dict[str, list[CatalogArtist]] = {}
    # A release may be filed under the first artist in a joint name.
    for spelling in dict.fromkeys((name, _JOINT.split(name)[0].strip())):
        try:
            found = await lookup.search(_words(spelling), kinds=("artist",), limit=8)
        except lookup.CatalogUnavailable:
            continue
        who = normalize_artist(spelling)
        for src in sources:
            if src in profiles:
                continue
            matches = [a for a in found.artists if a.source == src and
                       artist_sim(who, normalize_artist(a.name)) >= get_settings().cfg.matching.artist_min_ratio]
            if matches:
                profiles[src] = matches[:2]
        if len(profiles) == len(sources):
            break

    async def albums_of(artist: CatalogArtist) -> tuple[list[CatalogAlbum], bool]:
        try:
            if artist.source == "sp" and svc.spotify is not None:
                albums, partial, pending = await svc.spotify.discography(artist.id)
                return albums, not (partial or pending)
            if artist.source == "dz" and svc.deezer is not None:
                return await svc.deezer.get_artist_albums(artist.id), True
            return (await svc.artist_page(artist.ref)).albums, True
        except Exception as error:
            log.info("recognize: releases of %s not read: %r", artist.ref, error)
            return [], False

    shelves = await asyncio.gather(*(albums_of(a) for src in sources for a in profiles.get(src, [])))
    # Interleave shelves so a large discography in one catalog cannot exhaust the lookup budget.
    albums = {album.ref: album for row in zip_longest(*(items for items, _ in shelves))
              for album in row if album is not None}
    gate = asyncio.Semaphore(4)

    async def tracks_of(album: CatalogAlbum) -> list[CatalogTrack] | None:
        async with gate:
            try:
                whole = album if album.tracks is not None else await svc.album(album.ref)
            except Exception as error:
                log.info("recognize: release %s not read: %r", album.ref, error)
                return None
        out = []
        for t in whole.tracks or []:
            _kinds[t.ref] = whole.record_type
            out.append(t.model_copy(update={"album_title": t.album_title or whole.title,
                                            "album_ref": t.album_ref or whole.ref,
                                            "album_artist": t.album_artist or whole.artist}))
        return out

    parts = await asyncio.gather(*(tracks_of(a) for a in list(albums.values())[:RELEASES_MAX]))
    out = [track for part in parts if part is not None for track in part]
    if out and all(complete for _, complete in shelves) and None not in parts:
        if key not in _released and len(_released) >= _RELEASED_KEPT:
            _released.pop(next(iter(_released)))
        ttl = 3600 if len(profiles) == len(sources) else 120
        _released[key] = (monotonic() + ttl, out)
    return out


TITLE_SURE = 0.85  # a name as like as this is the file's name, spelt a little otherwise
TITLE_FLOOR = 0.6  # a record named less like the file than this is not «like» it, whoever made it


def _unstarred(t: Any, title: str) -> Any:
    """The catalog hides rude words under stars («Д*******М — НЕТ!»). A name that is the file's under them is the
    file's name: the record is weighed, and filed, under the name written out."""
    if "*" not in t.title:
        return t
    plain = lambda s: " ".join(re.sub(r"[^\w*]+", " ", re.sub(r"\*+", "*", s)).split()).casefold()  # noqa: E731
    mask = "".join(".+?" if part == "*" else re.escape(part) for part in re.split(r"(\*)", plain(t.title)))
    return t.model_copy(update={"title": title}) if re.fullmatch(mask, plain(title)) else t


def _weigh(found: list[Any], artist: str, raw_title: str, album: str | None, duration: int | None, version: str | None,
           cfg: Any) -> tuple[list[tuple[Any, Any]], str]:
    """The records that may be the file's, the likest first, and what they come to. One song stands in the catalog
    several times — a single, its album, the album's new edition: those are not rivals of each other, so the
    likest record is «the one» when no *other song* comes near it."""
    scored = [(t, sc) for t, sc in match_catalog(main_artist(artist), raw_title, [_unstarred(t, raw_title) for t in found],
                                                album=album, duration=duration, version=version)
              if _theirs(t, sc, split_version(raw_title)[0] or raw_title, album, duration, cfg)]
    if not scored:
        return [], "not_found"
    scored.sort(key=lambda row: (-row[1].total, lookup.SOURCE_PRIORITY.get(row[0].source, len(lookup.SOURCE_PRIORITY))))
    top = scored[0][0]

    def same_song(t: Any) -> bool:
        return (_words(t.title).casefold() == _words(top.title).casefold()
                and bool(t.duration_s and top.duration_s and abs(t.duration_s - top.duration_s) <= SAME_LENGTH_S))

    rivals = [scored[0], *[(t, sc) for t, sc in scored[1:] if not same_song(t)]]
    verdict = classify_scores([sc.total for _, sc in rivals], cfg)
    if verdict == "not_found" and _on_the_album(top, scored[0][1], split_version(raw_title)[0] or raw_title, album):
        verdict = "confirm"  # a song of this name on the album the file names is worth a look, whatever it scores
    return scored, verdict


_kinds: dict[str, str] = {}  # a track's ref -> the kind of the release it stands on (as `_released_by` read it)
_RANKS = {"album": 0, "ep": 1, "compile": 2, "single": 3}


def _release_rank(t: Any) -> int:
    """How much a release is a song's home: an album before an EP, an EP before a single."""
    kind = _kinds.get(t.ref) or ("single" if _same_name(t.album_title, t.title) else "album")
    return _RANKS.get(kind, 1)


def _same_name(a: str | None, b: str | None) -> bool:
    return bool(a and b) and " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _of_album(scored: list[tuple[Any, Any]], album: str | None) -> Any:
    """The likest record — and, where the song stands on several releases (a single, then the album), the one on
    the release the file itself names: files of one album stay one album."""
    top = scored[0][0]
    if not album:
        return top
    name = " ".join(album.split()).casefold()
    for t, _ in scored:
        if (t.album_title and " ".join(t.album_title.split()).casefold() == name
                and _words(t.title).casefold() == _words(top.title).casefold()
                and bool(t.duration_s and top.duration_s and abs(t.duration_s - top.duration_s) <= SAME_LENGTH_S)):
            return t
    return top


def _theirs(t: Any, sc: Any, title: str, album: str | None, duration: int | None, cfg: Any) -> bool:
    """A record that may be the file's at all: of the file's artist — or, under another name of theirs (an album
    put out as «M00NCHILD»), the same name on the same album over the same length. A song of the same name by
    somebody else is not a record «like» the file: it is another song."""
    goes_on = _goes_on(title, t.title)  # the file's name with something after it: the same name, for this
    if not goes_on:
        if sc.title < TITLE_FLOOR:
            return False
        # a name only somewhat like the file's, over another length, is another song of the artist's («Не надо» is
        # not «Не знаю»)
        if sc.title < TITLE_SURE and duration and t.duration_s and abs(duration - t.duration_s) > DOUBT_LENGTH_S:
            return False
    if sc.artist >= cfg.artist_min_ratio:
        return True
    return _on_the_album(t, sc, title, album)


def _other_name(t: Any, sc: Any, title: str, album: str | None, duration: int | None) -> bool:
    """The record is the file's under another name of its artist: the same name of the song, on the album the file
    names, over the same length — three things that do not agree by chance."""
    return _on_the_album(t, sc, title, album) and bool(duration and t.duration_s and abs(duration - t.duration_s) <= SAME_LENGTH_S)


def _on_the_album(t: Any, sc: Any, title: str, album: str | None) -> bool:
    """A song of the file's name on the album the file names: likely the file's song, whoever the catalog credits
    it to and whatever its length — if not surely (`_other_name`), then to be settled by the sound."""
    return _same_name(t.album_title, album) and (sc.title >= 0.95 or _goes_on(title, t.title))


def _goes_on(title: str, theirs: str) -> bool:
    """The catalog's name is the file's with something after it: «(prod. by …)», «(feat. …)»."""
    mine = _words(title).casefold()
    return bool(mine) and (_words(theirs).casefold() + " ").startswith(mine + " ")


def _numbers(s: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", s))


def _words(s: str) -> str:
    """The words of a name, as the catalog's search takes them: letters, digits and «$» (May Wave$)."""
    return " ".join(re.sub(r"[^\w$]+", " ", s, flags=re.UNICODE).split())


def _mmss(s: int | float | None) -> str:
    return f"{int(s) // 60}:{int(s) % 60:02d}" if s else "?"


async def of_file(tags: dict[str, Any], file_name: str, duration_s: float | None) -> dict[str, Any]:
    """{state: "found" | "doubt" | "none", why, meta}: `meta` — the record as a download carries it
    (`jobs.downloads.meta_from_catalog`), with its title and artist line."""
    artist, raw_title = names_of(tags, file_name)
    if not artist or not raw_title:
        return {"state": "none", "why": None, "meta": None}
    title, version = split_version(raw_title)
    duration = round(duration_s) if duration_s else None
    # The catalog's search takes plain words, and is asked several ways, each only if the one before it did not
    # settle the matter: the first artist with the name; the whole line of artists; the name spelt in Latin letters
    # (the catalog writes many Russian names so: «Nikto Ne Nuzhen»); the name alone (the catalog may spell the
    # artist otherwise: «ЛСП» for the file's «LSP»). What it gives is weighed together — a record is the file's by
    # its artist, name and length, however it was found. Signs go out of the words: an apostrophe finds nothing.
    wanted, first = _words(title or raw_title), _words(main_artist(artist))
    asks = [f"{first} {wanted}", f"{_words(artist)} {wanted}"]
    if has_cyrillic(wanted):
        asks.append(f"{first} {to_latin(wanted)}")
    asks.append(wanted)
    # the album the file names, to look for the song on: a name of its own — or the track's (an album has a song
    # of its name), only not «Single» and the like, which name nothing
    album = None if single_album(tags.get("album"), "") else str(tags.get("album"))
    # …and the album it names as its release: not the track's own name — that is how a single is tagged, and a
    # single's song is filed on its album where it has one
    release = None if single_album(tags.get("album"), raw_title) else album
    cfg = get_settings().cfg.matching
    found, seen, failed, scored, verdict = [], set(), None, [], "not_found"
    for ask in dict.fromkeys(a for a in asks if a.strip()):
        try:
            got, _ = await lookup.track_page(ask, 0, 25 if ask == wanted else 10)
        except lookup.CatalogUnavailable as e:
            log.info("recognize: catalog search failed for %r: %r", ask, e)
            failed = e
            continue
        new = [t for t in got if t.ref not in seen]
        if not new:
            continue
        seen.update(t.ref for t in new)
        found.extend(new)
        scored, verdict = _weigh(found, artist, raw_title, album, duration, version, cfg)
        if verdict == "auto":
            break
    release_tracks = None
    if verdict != "auto":
        # the search did not settle it — it misses a good deal of what the catalog has. The artist's own releases
        # are gone through then: the song is looked for among everything they put out
        release_tracks = await _released_by(artist)
        more = [t for t in release_tracks if t.ref not in seen]
        if more:
            found.extend(more)
            seen.update(t.ref for t in more)
            scored, verdict = _weigh(found, artist, raw_title, album, duration, version, cfg)
    if verdict == "not_found":
        return {"state": "none", "why": "каталог не ответил" if failed and not found else None, "meta": None}
    top = _of_album(scored, release)
    better_release_needed = not _same_name(top.album_title, release) if release else _release_rank(top) > _RANKS["album"]
    if better_release_needed:
        # The record found stands on a release the file does not name — the search gives a song's single before its
        # album. The song's other releases are gone through, among everything the artist put out: the one the file
        # names is the file's; where it names none of them (no album in its tags, or one the catalog does not
        # know), an album is taken over a single — a song belongs with the album it came out on.
        if release_tracks is None:
            release_tracks = await _released_by(artist)
        more = [t for t in release_tracks if t.ref not in seen]
        again, verdict_again = scored, verdict
        if more:
            found.extend(more)
            seen.update(t.ref for t in more)
            again, verdict_again = _weigh(found, artist, raw_title, album, duration, version, cfg)
        twins = [t for t, _ in again if _words(t.title).casefold() == _words(top.title).casefold()
                 and bool(t.duration_s and top.duration_s and abs(t.duration_s - top.duration_s) <= SAME_LENGTH_S)]
        named = next((t for t in twins if _same_name(t.album_title, release)), None)
        best = named or min(twins, key=_release_rank, default=top)
        if best is not top and (named is not None or _release_rank(best) < _release_rank(top)):
            scored, verdict, top = again, verdict_again if verdict_again != "not_found" else verdict, best
    why: str | None = None
    close = bool(duration and top.duration_s and abs(duration - top.duration_s) <= SAME_LENGTH_S)
    # a name that only goes on in the catalog — «(prod. by …)», «(feat. …)» — over the same length is the same song
    longer = _goes_on(title or raw_title, top.title)
    mark = next(sc for x, sc in scored if x is top)
    # …and so is the song on the file's album under another name of its artist («M00NCHILD» for GONE.Fludd)
    if verdict != "auto" and not (close and longer and mark.artist >= cfg.artist_min_ratio)             and not _other_name(top, mark, title or raw_title, album, duration):
        why = "названия совпали не полностью"
    # «Отдай кота 2.0» is not «Отдай кота»: the numbers of a name are part of what it names
    if _numbers(raw_title) != _numbers(top.title):
        why = "в названии другой номер"
    theirs = split_version(top.title)[1]
    if version_kind(version) != version_kind(theirs):
        why = f"в файле версия «{version}», в каталоге — обычная запись" if version else f"в каталоге версия «{theirs}», в файле — обычная запись"
    elif duration and top.duration_s and abs(duration - top.duration_s) > DOUBT_LENGTH_S:
        why = f"длительность другая: {_mmss(duration)} в файле, {_mmss(top.duration_s)} в каталоге"
    return {"state": "doubt" if why else "found", "why": why, "meta": await meta_of(top)}


async def of_ref(ref: str) -> dict[str, Any]:
    """The record the listener picked by hand: it is the file's, no doubt about it."""
    src, _, ident = ref.partition(":")
    if src not in lookup.SOURCE_PRIORITY or not ident:
        raise ValueError("Каталог не знает такой записи")
    return {"state": "found", "why": None, "meta": await meta_of(await svc.track(ref)), "by_hand": True}


async def meta_of(t: Any) -> dict[str, Any]:
    from app.jobs.downloads import complete_meta, meta_from_catalog

    album = None
    if t.source == "dz":
        # The Deezer tracklist may only credit the main artist.
        if svc.deezer is not None:
            try:
                whole = await svc.deezer.get_track(t.id)
                t = t.model_copy(update={"artists": whole.artists or t.artists, "artist_refs": whole.artist_refs or t.artist_refs})
            except Exception as error:
                log.info("recognize: credits of %s not read: %r", t.ref, error)
        meta = await complete_meta(meta_from_catalog(t))
    else:
        if t.album_ref:
            try:
                async with asyncio.timeout(10):
                    album = await svc.album(t.album_ref)
            except Exception as error:
                log.info("recognize: release metadata of %s not read: %r", t.ref, error)
            if album and (listed := next((row for row in album.tracks or [] if row.ref == t.ref), None)):
                t = t.model_copy(update={"track_no": t.track_no or listed.track_no, "disc_no": t.disc_no or listed.disc_no})
        meta = meta_from_catalog(t, album)
    meta["title"], meta["artist"] = t.title, t.artist
    meta["artists"] = t.artists or [t.artist]
    return meta
