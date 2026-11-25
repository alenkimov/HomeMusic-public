"""The look-through of «Наведение порядка»: what the rules find, then what the AI is asked, written down as
proposals (app/tidy/kinds.py `offer`). A job of the queue (`tidy_scan`): every night after the backup, and when
somebody asks («Проверить сейчас»). Each step is a transaction of its own — an AI call takes seconds, and what was
found before it is kept whatever comes after.

A subject that has an open proposal of its section is not asked about again until that one is decided: two
proposals about one name would undo each other.
"""

from __future__ import annotations

import difflib
import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.jobs.queue import handler
from app.library import artists as artists_
from app.library.artwork import album_cover_url
from app.library.quality import rank
from app.matching.normalize import has_cyrillic
from app.models import (
    Album,
    AlbumArtist,
    AppSetting,
    Artist,
    ArtistApart,
    ArtistLink,
    ArtistName,
    Job,
    Lyrics,
    SongTrack,
    TidyProposal,
    Track,
    TrackArtist,
    TrackFile,
    utcnow,
)
from app.tidy import ai, kinds, rules

log = logging.getLogger(__name__)

STATUS_KEY = "tidy"
JOB = "tidy_scan"
PRIORITY = -5  # the Hub's own work: never in the way of a listener's


# ---------------------------------------------------------------- what the page shows of a subject


def _track_cover(t: Track, albums: dict[int, Album]) -> str | None:
    from app.library.edits import cover_url

    album = albums.get(t.album_id or 0)
    cat = t.catalog or {}
    return cover_url(t) or (album_cover_url(album) if album else None) or cat.get("cover_small_url") or cat.get("cover_url")


def _track_show(t: Track, albums: dict[int, Album], **more: Any) -> dict[str, Any]:
    return {"what": "Трек", "line": t.artist, "cover": _track_cover(t, albums), "album_id": t.album_id, **more}


def _album_show(a: Album, **more: Any) -> dict[str, Any]:
    return {"what": "Альбом", "line": a.album_artist, "cover": album_cover_url(a), "album_id": a.id, **more}


def _tracks_word(n: int) -> str:
    tail = n % 100
    word = "трек" if tail % 10 == 1 and tail != 11 else "трека" if 2 <= tail % 10 <= 4 and not 12 <= tail <= 14 else "треков"
    return f"{n} {word}"


# ---------------------------------------------------------------- the library, as the steps read it


async def _busy(s: AsyncSession, section: str) -> set[tuple[str, int]]:
    rows = (await s.execute(select(TidyProposal.subject_type, TidyProposal.subject_id)
                            .where(TidyProposal.section == section, TidyProposal.state == "open"))).all()
    return {(t, i) for t, i in rows}


async def _albums(s: AsyncSession) -> dict[int, Album]:
    return {a.id: a for a in (await s.execute(select(Album))).scalars()}


async def _music_counts(s: AsyncSession) -> dict[int, int]:
    """Artist -> how many tracks they are credited on (the artists with music only)."""
    counts = dict((await s.execute(select(TrackArtist.artist_id, func.count(TrackArtist.track_id))
                                   .group_by(TrackArtist.artist_id))).all())
    for artist_id in (await s.execute(select(AlbumArtist.artist_id).distinct())).scalars():
        counts.setdefault(artist_id, 0)
    return counts


async def _named_freely(s: AsyncSession) -> list[Track]:
    """The tracks whose names may be put right: not the ones a listener named by hand. A name «Наведение порядка»
    gave is not theirs — the next look-through may put it right further (a note gone, then the Cyrillic)."""
    ours = {(i, name) for i, name in (await s.execute(
        select(TidyProposal.subject_id, TidyProposal.to_json["name"].astext)
        .where(TidyProposal.subject_type == "track", TidyProposal.state == "accepted",
               TidyProposal.kind.in_(("title", "cyrillic"))))).all()}
    return [t for t in (await s.execute(select(Track))).scalars()
            if t.renamed_at is None or (t.id, t.title) in ours]


async def _first_artist(s: AsyncSession) -> dict[int, int]:
    """Track -> whose it is (the first of its artists)."""
    return dict((await s.execute(select(TrackArtist.track_id, TrackArtist.artist_id).where(TrackArtist.position == 0))).all())


# ---------------------------------------------------------------- names


async def _titles(s: AsyncSession) -> int:
    """The notes of videos and sites in names, the artist and the number written into them (by rule). A name a
    listener wrote by hand is theirs: it is not offered to be put right."""
    busy = await _busy(s, "names")
    albums = await _albums(s)
    n = 0
    for t in await _named_freely(s):
        if ("track", t.id) in busy or not (c := rules.clean_title(t.title, t.artist, t.track_no)):
            continue
        n += await kinds.offer(s, kind="title", subject_type="track", subject_id=t.id, was={"name": t.title},
                               to={"name": c.title}, show=_track_show(t, albums), by="rule", sure=True, why=c.why)
    for a in albums.values():
        if ("album", a.id) in busy or not (c := rules.clean_title(a.title, a.album_artist)):
            continue
        n += await kinds.offer(s, kind="title", subject_type="album", subject_id=a.id, was={"name": a.title},
                               to={"name": c.title}, show=_album_show(a), by="rule", sure=True, why=c.why)
    return n


def _cyrillic_answer(item: dict[str, Any], was: str) -> str | None:
    name = " ".join(str(item.get("name") or "").split())
    return name if name and has_cyrillic(name) and name != was else None


async def _cyrillic(s: AsyncSession, budget: ai.Budget) -> int:
    """Russian names written in Latin letters: the artists first — whose music is Russian then tells which albums
    and tracks to ask about."""
    busy = await _busy(s, "names")
    music = await _music_counts(s)
    who = {a.id: a for a in (await s.execute(select(Artist).where(Artist.merged_into_id.is_(None), Artist.id.in_(list(music))))).scalars()}
    sample: dict[int, list[str]] = defaultdict(list)
    for artist_id, title in (await s.execute(select(TrackArtist.artist_id, Track.title).join(Track, Track.id == TrackArtist.track_id)
                                             .order_by(Track.id))).all():
        if len(sample[artist_id]) < 3:
            sample[artist_id].append(title)
    n = 0
    russian = {i for i, a in who.items() if has_cyrillic(a.name)}
    questions = [{"id": a.id, "type": "artist", "name": a.name, "tracks": sample.get(a.id, [])}
                 for a in who.values() if rules.needs_cyrillic(a.name) and ("artist", a.id) not in busy]
    for item in await ai.ask(s, budget, "cyrillic", questions):
        a = who[item["id"]]
        if not (name := _cyrillic_answer(item, a.name)):
            continue
        russian.add(a.id)
        n += await kinds.offer(s, kind="cyrillic", subject_type="artist", subject_id=a.id, was={"name": a.name},
                               to={"name": name}, show={"what": "Исполнитель", "line": _tracks_word(music.get(a.id, 0)),
                                                        "artist_id": a.id},
                               by="ai", sure=bool(item.get("sure")), why=str(item.get("why") or "Русское имя латиницей"))
    # an artist the AI was asked about before keeps telling: a proposal of their name, open or decided, says so
    russian |= set((await s.execute(select(TidyProposal.subject_id).where(TidyProposal.kind == "cyrillic",
                                                                           TidyProposal.subject_type == "artist"))).scalars())
    lyrics_ru = set((await s.execute(select(Lyrics.track_id).where(Lyrics.search_text.op("~")("[а-яё]{3}")))).scalars())
    first = await _first_artist(s)
    albums = await _albums(s)
    tracks = {t.id: t for t in await _named_freely(s)
              if rules.needs_cyrillic(t.title) and ("track", t.id) not in busy
              and (first.get(t.id) in russian or t.id in lyrics_ru or has_cyrillic(t.artist))}
    questions = [{"id": t.id, "type": "track", "name": t.title, "artist": t.artist} for t in tracks.values()]
    for item in await ai.ask(s, budget, "cyrillic", questions):
        t = tracks[item["id"]]
        if name := _cyrillic_answer(item, t.title):
            n += await kinds.offer(s, kind="cyrillic", subject_type="track", subject_id=t.id, was={"name": t.title},
                                   to={"name": name}, show=_track_show(t, albums), by="ai", sure=bool(item.get("sure")),
                                   why=str(item.get("why") or "Русское название латиницей"))
    owner = dict((await s.execute(select(AlbumArtist.album_id, AlbumArtist.artist_id).where(AlbumArtist.position == 0))).all())
    cands = {a.id: a for a in albums.values() if rules.needs_cyrillic(a.title) and ("album", a.id) not in busy
             and (owner.get(a.id) in russian or has_cyrillic(a.album_artist))}
    questions = [{"id": a.id, "type": "album", "name": a.title, "artist": a.album_artist} for a in cands.values()]
    for item in await ai.ask(s, budget, "cyrillic", questions):
        a = cands[item["id"]]
        if name := _cyrillic_answer(item, a.title):
            n += await kinds.offer(s, kind="cyrillic", subject_type="album", subject_id=a.id, was={"name": a.title},
                                   to={"name": name}, show=_album_show(a), by="ai", sure=bool(item.get("sure")),
                                   why=str(item.get("why") or "Русское название латиницей"))
    return n


# ---------------------------------------------------------------- versions and doubles


async def _songs(s: AsyncSession) -> dict[int, int]:
    return dict((await s.execute(select(SongTrack.track_id, SongTrack.song_id))).all())


def _version_show(t: Track, of: Track, label: str, albums: dict[int, Album]) -> dict[str, Any]:
    return _track_show(t, albums, of_title=of.title, of_artist=of.artist, label=label)


async def _versions(s: AsyncSession, budget: ai.Budget | None) -> int:
    """Tracks of one artist whose names are one song's: what the name says is the version (by rule); a name that
    is the other's with words a rule does not know (the AI, where it may ask)."""
    busy = await _busy(s, "versions")
    first = await _first_artist(s)
    song = await _songs(s)
    albums = await _albums(s)
    with_file = set((await s.execute(select(TrackFile.track_id).distinct())).scalars())
    tracks = {t.id: t for t in (await s.execute(select(Track).where(Track.id.in_(list(first))))).scalars()}

    def together(a: int, b: int) -> bool:
        return a in song and song.get(a) == song.get(b)

    def original(cands: list[Track]) -> Track:  # the one to join the others to: a song's already, with a file
        return min(cands, key=lambda t: (t.id not in song, t.id not in with_file, t.id))

    by_song: dict[tuple[int, str], list[Track]] = defaultdict(list)
    for t in tracks.values():
        if key := rules.song_key(t.title):
            by_song[(first[t.id], key)].append(t)
    n = 0
    for group in by_song.values():
        plain = [t for t in group if rules.version_of(t.title) is None]
        if not plain or len(group) < 2:
            continue
        of = original(plain)
        for t in group:
            v = rules.version_of(t.title)
            if v is None or together(of.id, t.id) or ("track", t.id) in busy:
                continue
            n += await kinds.offer(s, kind="version", subject_type="track", subject_id=t.id, was={"together": False},
                                   to={"of": of.id, "label": v.label}, show=_version_show(t, of, v.label, albums),
                                   by="rule", sure=True, why=f"В названии: «{v.label}»")
    if budget is None:
        return n
    # a name that goes on after the other's: «Trampoline slowed», «Song sped up version» — or «Song Part 2»
    by_artist: dict[int, list[tuple[str, Track]]] = defaultdict(list)
    for t in tracks.values():
        by_artist[first[t.id]].append((rules.song_key(t.title), t))
    pairs: list[tuple[Track, Track]] = []
    for items in by_artist.values():
        if len(items) < 2:
            continue
        for key, base in items:
            if not key or rules.version_of(base.title) is not None:
                continue
            for other_key, t in items:
                if (t.id != base.id and other_key.startswith(key + " ") and len(other_key) - len(key) <= 30
                        and rules.version_of(t.title) is None and not together(base.id, t.id) and ("track", t.id) not in busy):
                    pairs.append((base, t))
    questions = [{"id": i, "artist": t.artist, "title": base.title, "other": t.title} for i, (base, t) in enumerate(pairs)]
    for item in await ai.ask(s, budget, "version", questions):
        base, t = pairs[item["id"]]
        label = " ".join(str(item.get("label") or "").split())[:64]
        if not label:
            continue
        n += await kinds.offer(s, kind="version", subject_type="track", subject_id=t.id, was={"together": False},
                               to={"of": base.id, "label": label}, show=_version_show(t, base, label, albums),
                               by="ai", sure=bool(item.get("sure")), why=str(item.get("why") or f"Версия: {label}"))
    return n


async def _doubles(s: AsyncSession) -> int:
    """One recording in two files (told by the sound): the better file stays, the others go. Sure where they are
    one song in one album (or in none); a single and the album's track are perhaps both wanted."""
    from app.library import acoustic

    busy = await _busy(s, "versions")
    groups = await acoustic.doubles(s)
    if not groups:
        return 0
    ids = [i for g in groups for i in g]
    tracks = {t.id: t for t in (await s.execute(select(Track).where(Track.id.in_(ids)))).scalars()}
    files: dict[int, TrackFile] = {}
    for f in (await s.execute(select(TrackFile).where(TrackFile.track_id.in_(ids)).order_by(TrackFile.id))).scalars():
        files[f.track_id] = f  # the newest is the one that plays
    albums = await _albums(s)

    def worth(i: int) -> tuple:
        f = files.get(i)
        return (rank(f.codec, f.bitrate_kbps) or 0 if f else -1, tracks[i].album_id is not None, -i)

    n = 0
    for group in groups:
        group = [i for i in group if i in tracks and i in files]
        if len(group) < 2:
            continue
        keep = max(group, key=worth)
        k, kf = tracks[keep], files[keep]
        for i in group:
            if i == keep or ("track", i) in busy:
                continue
            t, f = tracks[i], files[i]
            same = rules.song_key(t.title) == rules.song_key(k.title) and (t.album_id == k.album_id or not t.album_id or not k.album_id)
            show = _track_show(t, albums, file=_file_word(f), keep_title=k.title, keep_file=_file_word(kf),
                               album=albums[t.album_id].title if t.album_id in albums else None,
                               keep_album=albums[k.album_id].title if k.album_id in albums else None)
            n += await kinds.offer(s, kind="double", subject_type="track", subject_id=t.id, was={"both": True},
                                   to={"keep": keep}, show=show, by="rule", sure=same,
                                   why="Звучат одинаково" if same else "Звучат одинаково, но лежат в разных альбомах")
    return n


def _file_word(f: TrackFile) -> str:
    codec = (f.codec or "").upper()
    return " · ".join(x for x in (codec, f"{f.bitrate_kbps} kbps" if f.bitrate_kbps else "") if x)


# ---------------------------------------------------------------- artists


async def _artists(s: AsyncSession, budget: ai.Budget | None) -> int:
    """Two artists who read the same in Latin letters are one (by rule); names that look alike are asked about."""
    busy = await _busy(s, "artists")
    music = await _music_counts(s)
    who = {a.id: a for a in (await s.execute(select(Artist).where(Artist.merged_into_id.is_(None), Artist.id.in_(list(music))))).scalars()}
    if len(who) < 2:
        return 0
    spellings: dict[int, set[str]] = defaultdict(set)
    for artist_id, name in (await s.execute(select(ArtistName.artist_id, ArtistName.name))).all():
        if artist_id in who:
            spellings[artist_id].add(name)
    for a in who.values():
        spellings[a.id].add(a.name)
    pairs_off = {tuple(r) for r in (await s.execute(select(ArtistApart.low_id, ArtistApart.high_id))).all()}
    pairs_off |= {tuple(r) for r in (await s.execute(select(ArtistLink.low_id, ArtistLink.high_id))).all()}

    def off(a: int, b: int) -> bool:
        return (min(a, b), max(a, b)) in pairs_off

    def into_of(a: int, b: int) -> tuple[int, int]:  # the one with more music stays
        x, y = sorted((a, b), key=lambda i: (-music.get(i, 0), not has_cyrillic(who[i].name), i))
        return x, y

    async def offer(into: int, other: int, by: str, sure: bool, why: str) -> int:
        if ("artist", other) in busy or ("artist", into) in busy:
            return 0
        busy.add(("artist", other))
        o, i = who[other], who[into]
        return await kinds.offer(
            s, kind="artist", subject_type="artist", subject_id=other, was={"apart": True}, to={"into": into},
            show={"what": "Исполнитель", "line": _tracks_word(music.get(other, 0)), "artist_id": other,
                  "into_name": i.name, "into_line": _tracks_word(music.get(into, 0)), "name": o.name},
            by=by, sure=sure, why=why)

    folded: dict[str, set[int]] = defaultdict(set)
    for artist_id, names in spellings.items():
        for name in names:
            if f := artists_.fold(name):
                folded[f].add(artist_id)
    n = 0
    joined: set[tuple[int, int]] = set()
    for ids in folded.values():
        if len(ids) < 2:
            continue
        ordered = sorted(ids, key=lambda i: (-music.get(i, 0), not has_cyrillic(who[i].name), i))
        into = ordered[0]
        for other in ordered[1:]:
            if off(into, other):
                continue
            joined.add((min(into, other), max(into, other)))
            if artists_.key(who[other].name) == artists_.key(who[into].name):
                # one name, two records of the catalogs: perhaps one artist, perhaps namesakes — a person decides
                n += await offer(into, other, "rule", False, "Два исполнителя с одним именем — возможно, это один")
            else:
                n += await offer(into, other, "rule", True, f"«{who[other].name}» и «{who[into].name}» читаются одинаково")
    if budget is None:
        return n
    # names that look alike: within the names that begin alike, those nearly the same
    keys = {i: artists_.fold(a.name) for i, a in who.items()}
    blocks: dict[str, list[int]] = defaultdict(list)
    for i, k in keys.items():
        if len(k) >= 3:
            blocks[k[:2]].append(i)
    pairs: list[tuple[int, int]] = []
    for ids in blocks.values():
        for x, a in enumerate(ids):
            for b in ids[x + 1:]:
                ka, kb = keys[a], keys[b]
                pair = (min(a, b), max(a, b))
                if ka == kb or pair in joined or off(a, b):
                    continue
                if ka.replace(" ", "") == kb.replace(" ", "") or difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.86:
                    pairs.append(into_of(a, b))
    sample: dict[int, list[str]] = defaultdict(list)
    wanted = {i for p in pairs for i in p}
    if wanted:
        for artist_id, title in (await s.execute(select(TrackArtist.artist_id, Track.title).join(Track, Track.id == TrackArtist.track_id)
                                                 .where(TrackArtist.artist_id.in_(wanted)).order_by(Track.id))).all():
            if len(sample[artist_id]) < 3:
                sample[artist_id].append(title)
    questions = [{"id": k, "a": who[a].name, "a_tracks": sample.get(a, []), "b": who[b].name, "b_tracks": sample.get(b, [])}
                 for k, (a, b) in enumerate(pairs)]
    for item in await ai.ask(s, budget, "artist", questions):
        into, other = pairs[item["id"]]
        n += await offer(into, other, "ai", bool(item.get("sure")), str(item.get("why") or "Похоже, один исполнитель"))
    return n


# ---------------------------------------------------------------- lyrics


async def _lyrics(s: AsyncSession, budget: ai.Budget | None) -> int:
    """The lines of a text that are not the song: an address or a site's line (by rule), a call to subscribe or an
    advert (the AI says which of the lines that look so are not sung). One proposal of a text: all its lines."""
    busy = await _busy(s, "lyrics")
    rows = (await s.execute(select(Lyrics).where(Lyrics.kind != "none"))).scalars().all()
    found: dict[int, tuple[list[str], list[rules.Junk]]] = {}
    for row in rows:
        if ("track", row.track_id) in busy:
            continue
        lines = kinds.text_lines(row)
        if junk := rules.lyric_junk(lines):
            found[row.track_id] = (lines, junk)
    if not found:
        return 0
    confirmed: dict[int, tuple[set[int], bool, str]] = {}
    if budget is not None:
        questions = []
        for track_id, (lines, junk) in found.items():
            weak = [j.index for j in junk if not j.sure]
            if not weak:
                continue
            near = sorted({k for i in weak for k in range(max(0, i - 2), min(len(lines), i + 3))})
            questions.append({"id": track_id, "lines": {str(k): lines[k] for k in near}, "suspects": weak})
        for item in await ai.ask(s, budget, "lyrics", questions):
            weak = {j.index for j in found[item["id"]][1] if not j.sure}
            drop = {int(i) for i in item.get("drop") or [] if isinstance(i, int) and i in weak}
            if drop:
                confirmed[item["id"]] = (drop, bool(item.get("sure")), str(item.get("why") or "Не часть песни"))
    tracks = {t.id: t for t in (await s.execute(select(Track).where(Track.id.in_(list(found))))).scalars()}
    albums = await _albums(s)
    n = 0
    texts = {row.track_id: row for row in rows}
    for track_id, (lines, junk) in found.items():
        strong = {j.index for j in junk if j.sure}
        more, sure, why = confirmed.get(track_id, (set(), True, ""))
        drop = sorted(strong | more)
        if not drop or track_id not in tracks:
            continue
        by = "ai" if more else "rule"
        why = why if more else "Ссылки и строки сайта"
        n += await kinds.offer(
            s, kind="lyrics", subject_type="track", subject_id=track_id, was={"text": kinds.text_hash(texts[track_id])},
            to={"drop": drop}, show=_track_show(tracks[track_id], albums, title=tracks[track_id].title,
                                                lines=[lines[i] for i in drop]),
            by=by, sure=sure if more else True, why=why)
    return n


LOOKUPS = 60  # censored texts whose other sources are asked in one look-through (each asks every source)


async def _uncensor(s: AsyncSession, budget: ai.Budget | None) -> int:
    """Words hidden by censorship («б***ь», «[цензура]»): put back from another source of the text, where the same
    line is whole there (by rule; sure when every word fits its mask letter for letter); what no source has, the AI
    reads off the mask and the line — never sure, a person decides. A text is looked up once (until it changes)."""
    from app.lyrics import parse
    from app.lyrics import service as lyrics_
    from app.lyrics.providers import TITLES as SOURCES

    busy = await _busy(s, "lyrics")
    found: dict[int, tuple[Lyrics, list[str], dict[int, list[str]]]] = {}
    for row in (await s.execute(select(Lyrics).where(Lyrics.kind != "none"))).scalars():
        if ("track", row.track_id) in busy:
            continue
        lines = kinds.text_lines(row)
        hidden = {i: m for i, line in enumerate(lines) if (m := rules.masks(line))}
        if hidden:
            found[row.track_id] = (row, lines, hidden)
    if not found:
        return 0
    questions = [{"id": tid, "text": kinds.text_hash(row)} for tid, (row, _, _) in found.items()]
    todo = (await ai.unasked(s, "uncensor-sources", questions))[:LOOKUPS]
    tracks = {t.id: t for t in (await s.execute(select(Track).where(Track.id.in_([q["id"] for q in todo])))).scalars()}
    fixes: dict[int, dict[int, tuple[list[str], bool, str]]] = defaultdict(dict)
    for q in todo:
        tid = q["id"]
        _row, lines, hidden = found[tid]
        if tid not in tracks:
            continue
        try:
            others = await lyrics_.candidates(s, tracks[tid])
        except Exception as e:  # a source away: asked again next time
            log.info("tidy: the lyrics sources of %s failed: %r", tid, e)
            continue
        for f in others:
            theirs = parse.plain_of(f.lines, f.plain).split("\n")
            for i, masks in hidden.items():
                if i not in fixes[tid] and (u := rules.unmask(lines[i], theirs)) and len(u.words) == len(masks):
                    fixes[tid][i] = (list(u.words), u.strict, f.source)
        await ai.mark_asked(s, "uncensor-sources", [q])
    if budget is not None:
        # what no source had: the AI, where the mask shows a letter (a bare «***» it would only guess)
        asked: list[tuple[int, int]] = []
        qs = []
        for q in todo:
            tid = q["id"]
            if tid not in tracks:
                continue
            _row, lines, hidden = found[tid]
            for i, masks in hidden.items():
                if i in fixes[tid] or not all(any(c.isalpha() for c in m) for m in masks):
                    continue
                qs.append({"id": len(asked), "artist": tracks[tid].artist, "title": tracks[tid].title,
                           "line": lines[i], "masks": masks, "context": lines[max(0, i - 2):i + 3]})
                asked.append((tid, i))
        for item in await ai.ask(s, budget, "uncensor", qs):
            tid, i = asked[item["id"]]
            words = [str(w).strip() for w in item.get("words") or []]
            masks = found[tid][2][i]
            if len(words) == len(masks) and all(rules.fits(m, w) for m, w in zip(masks, words, strict=True)):
                fixes[tid][i] = (words, False, "ai")
    albums = await _albums(s)
    n = 0
    for tid, fixed in fixes.items():
        if not fixed:
            continue
        row, lines, _hidden = found[tid]
        sources = {src for _, _, src in fixed.values()}
        named = [SOURCES.get(x, x) for x in sorted(sources - {"ai"})]
        why = ("Восстановил ИИ по маске" if sources == {"ai"}
               else f"Без цензуры в {', '.join(named)}" + (" и восстановил ИИ" if "ai" in sources else ""))
        sure = all(strict and src != "ai" for _, strict, src in fixed.values())
        pairs = [{"was": lines[i], "to": rules.put_words(lines[i], words)} for i, (words, _, _) in sorted(fixed.items())]
        n += await kinds.offer(
            s, kind="uncensor", subject_type="track", subject_id=tid, was={"text": kinds.text_hash(row)},
            to={"replace": {str(i): words for i, (words, _, _) in sorted(fixed.items())}},
            show=_track_show(tracks[tid], albums, title=tracks[tid].title, pairs=pairs),
            by="ai" if "ai" in sources else "rule", sure=sure, why=why)
    return n


# ---------------------------------------------------------------- the look-through


async def status(s: AsyncSession) -> dict[str, Any]:
    row = await s.get(AppSetting, STATUS_KEY)
    return dict(row.value_json) if row is not None else {}


async def _save_status(s: AsyncSession, value: dict[str, Any]) -> None:
    row = await s.get(AppSetting, STATUS_KEY)
    if row is None:
        s.add(AppSetting(key=STATUS_KEY, value_json=value))
    else:
        row.value_json = value
    await s.flush()


async def running(s: AsyncSession) -> bool:
    return (await s.scalar(select(func.count(Job.id)).where(Job.kind == JOB, Job.status.in_(("queued", "running"))))) > 0


async def request(s: AsyncSession) -> bool:
    """Queue a look-through, unless one is queued or going. Returns whether one was queued."""
    from app.jobs.queue import enqueue

    if await running(s):
        return False
    await enqueue(s, JOB, {}, priority=PRIORITY, max_attempts=1)
    return True


async def run() -> dict[str, Any]:
    """Look through the library; each step in a transaction of its own. Returns the status written down."""
    async with session_scope() as s:
        dropped = await kinds.drop_stale(s)
    found = 0
    for step in (_titles, _doubles):
        async with session_scope() as s:
            found += await step(s)
    use_ai = ai.available()
    async with session_scope() as s:
        budget = await ai.Budget.of_month(s) if use_ai else None
    for step in (_versions, _artists, _lyrics, _uncensor):
        async with session_scope() as s:
            found += await step(s, budget)
    if budget is not None:
        async with session_scope() as s:
            found += await _cyrillic(s, budget)
    ai_state = "off" if budget is None else "ok" if budget.left else "limit"
    value = {"checked_at": utcnow().isoformat(), "found": found, "dropped": dropped, "ai": ai_state}
    async with session_scope() as s:
        await _save_status(s, value)
    log.info("tidy: %s proposals found, %s dropped as stale, ai %s", found, dropped, ai_state)
    return value


@handler(JOB)
async def handle_scan(job: Job) -> None:
    from app.events import bus

    await run()
    bus.broadcast("tidy", {"kind": "checked"})
