"""How each kind of proposal is done and taken back — with the functions a listener's own changes go through, so
the journal of edits, the files' tags and the search follow as for a change by hand.

Each kind says what its subject is now (`now`): a proposal holds while that is what it was made of (`was_json`),
and an accepted one can be taken back while the subject is what it made of it (`after`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Album,
    Artist,
    ArtistName,
    Import,
    ImportItem,
    Lyrics,
    PlaylistItem,
    SongTrack,
    TidyProposal,
    Track,
    TrackTag,
    TrashedTrack,
    User,
    utcnow,
)

# the sections of the page, in its order («Альбомы» is the page of «Объединение альбомов», not proposals)
SECTIONS = ("names", "versions", "artists", "albums", "lyrics", "imports")
TITLES = {"names": "Названия", "versions": "Версии и дубли", "artists": "Исполнители", "albums": "Альбомы",
          "lyrics": "Тексты", "imports": "Импорт"}


class Changed(ValueError):
    """The subject is not what the proposal is about any more (somebody changed it, or it is gone)."""


# ---------------------------------------------------------------- what is done after (jobs, the player's apps)


@dataclass
class After:
    """What has to follow a change once it is committed: the library written in an artist's name, a scan of the
    files for Navidrome, the playlists mirrored."""

    rewrite: set[int]
    scan: bool = False
    playlists: set[int] | None = None

    async def queue(self, s: AsyncSession, user: User | None) -> None:
        from app.jobs.downloads import enqueue_scan
        from app.jobs.queue import enqueue
        from app.playlists_mirror import enqueue_mirror

        for artist_id in sorted(self.rewrite):
            await enqueue(s, "artist_rewrite", {"artist_id": artist_id, "user_id": user.id if user else None})
        for playlist_id in sorted(self.playlists or ()):
            await enqueue_mirror(s, playlist_id)
        if self.scan:
            await enqueue_scan(s)


def text_lines(row: Lyrics | None) -> list[str]:
    """A text as the lines a proposal names by their places: the timed lines, else the plain text's."""
    if row is None:
        return []
    if row.lines_json:
        return [ln.get("text", "") for ln in row.lines_json]
    return (row.plain or "").split("\n")


def text_hash(row: Lyrics | None) -> str | None:
    if row is None or row.kind == "none":
        return None
    return hashlib.sha1("\n".join(text_lines(row)).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- names: a track, an album, an artist


async def _named(s: AsyncSession, subject_type: str, subject_id: int) -> Track | Album | Artist | None:
    model = {"track": Track, "album": Album, "artist": Artist}[subject_type]
    obj = await s.get(model, subject_id)
    if isinstance(obj, Artist) and obj.merged_into_id is not None:
        return None
    return obj


def _name_of(obj: Track | Album | Artist) -> str:
    return obj.name if isinstance(obj, Artist) else obj.title


async def _name_now(s: AsyncSession, p: TidyProposal) -> dict[str, Any] | None:
    obj = await _named(s, p.subject_type, p.subject_id)
    return {"name": _name_of(obj)} if obj is not None else None


async def _rename(s: AsyncSession, user: User, subject_type: str, obj: Any, name: str, after: After,
                  *, dropping: int | None = None) -> int | None:
    """Give the subject the name, as the form of its names does. An artist's new name is one of their spellings
    (made, if it is not: returned, so that a take-back forgets it), their music is then written in it."""
    from app.library import artists, edits

    if subject_type == "track":
        await edits.update_track(s, user, obj, title=name)
        after.scan = True
        return None
    if subject_type == "album":
        await edits.update_album(s, user, obj, title=name)
        after.scan = True
        return None
    row = (await s.execute(select(ArtistName).where(ArtistName.key == artists.key(name)))).scalar_one_or_none()
    added = None
    if row is None:
        row = await artists.add_name(s, user.id, obj, name)
        added = row.id
    elif row.artist_id != obj.id:
        raise Changed(f"«{name}» — уже другой исполнитель: их стоит объединить")
    await artists.set_main(s, user.id, obj, row.id)
    if dropping:
        await artists.drop_name(s, user.id, obj, dropping)
    after.rewrite.add(obj.id)
    return added


async def _name_apply(s: AsyncSession, user: User, p: TidyProposal, after: After) -> dict[str, Any]:
    obj = await _named(s, p.subject_type, p.subject_id)
    added = await _rename(s, user, p.subject_type, obj, p.to_json["name"], after)
    return {"added_name": added}


async def _name_revert(s: AsyncSession, user: User, p: TidyProposal, after: After) -> None:
    obj = await _named(s, p.subject_type, p.subject_id)
    if obj is None or _name_of(obj) != p.to_json["name"]:
        raise Changed("Название с тех пор изменили")
    await _rename(s, user, p.subject_type, obj, p.was_json["name"], after,
                  dropping=(p.undo_json or {}).get("added_name"))


# ---------------------------------------------------------------- a version of a song


async def _together(s: AsyncSession, a: int, b: int) -> bool:
    rows = dict((await s.execute(select(SongTrack.track_id, SongTrack.song_id).where(SongTrack.track_id.in_((a, b))))).all())
    return a in rows and rows.get(a) == rows.get(b)


async def _version_now(s: AsyncSession, p: TidyProposal) -> dict[str, Any] | None:
    of = p.to_json["of"]
    if await s.get(Track, p.subject_id) is None or await s.get(Track, of) is None:
        return None
    return {"together": await _together(s, of, p.subject_id)}


async def _version_apply(s: AsyncSession, user: User, p: TidyProposal, after: After) -> dict[str, Any]:
    from app.library import versions

    track, of = await s.get(Track, p.subject_id), await s.get(Track, p.to_json["of"])
    await versions.link(s, user.id, of, track)
    if p.to_json.get("label"):
        await versions.label(s, user.id, track, p.to_json["label"])
    return {}


async def _version_revert(s: AsyncSession, user: User, p: TidyProposal, after: After) -> None:
    from app.library import versions

    track = await s.get(Track, p.subject_id)
    if track is None or not await _together(s, p.to_json["of"], p.subject_id):
        raise Changed("Версии с тех пор разъединили")
    await versions.unlink(s, user.id, track)


# ---------------------------------------------------------------- one recording twice


async def _double_now(s: AsyncSession, p: TidyProposal) -> dict[str, Any] | None:
    from app.library.files import mains

    ids = [p.subject_id, p.to_json["keep"]]
    files = await mains(s, ids)
    return {"both": True} if all(i in files for i in ids) else None


async def _double_apply(s: AsyncSession, user: User, p: TidyProposal, after: After) -> dict[str, Any]:
    """The other copy goes to the trash, and all that was the listener's of it is the kept one's
    (`tracks.merge_into`): its places in playlists (once each, the earlier place), tags, listens, the transfers'
    rows, its catalog records."""
    from app.library import access, editor
    from app.library import tracks as registry

    gone, keep = await s.get(Track, p.subject_id), await s.get(Track, p.to_json["keep"])
    may, _others, _mine = await access.keeping(s, user, gone.id)
    if not may:
        raise PermissionError("Этот файл хранит кто-то ещё: удалить его может только администратор")
    merged = await registry.merge_into(s, gone, keep)
    await editor.trash_track(s, gone, user.id)
    await registry.bind_merged(s, keep, merged)
    trashed = (await s.execute(select(TrashedTrack.id).where(TrashedTrack.track_id == gone.id)
                               .order_by(TrashedTrack.id.desc()).limit(1))).scalar_one()
    after.scan, after.playlists = True, set(merged["playlists"])
    return {"trashed": trashed, "merged": merged}


async def _double_revert(s: AsyncSession, user: User, p: TidyProposal, after: After) -> None:
    from sqlalchemy import delete

    from app.library import trash

    undo = p.undo_json or {}
    row = await s.get(TrashedTrack, undo.get("trashed") or 0)
    if row is None:
        raise Changed("Файл уже удалён из корзины насовсем")
    try:
        back = await trash.restore(s, row)
    except ValueError as e:
        raise Changed(str(e)) from e
    keep = p.to_json["keep"]
    if "merged" in undo:
        from app.library import tracks as registry

        kept = await s.get(Track, keep)
        if kept is None:
            raise Changed("Оставленного трека уже нет")
        after.scan, after.playlists = True, await registry.unmerge(s, back, kept, undo["merged"])
        return
    # a copy deleted before the listener's all went with the places (its places and tags only)
    playlists = set()
    if undo.get("moved"):
        for item in (await s.execute(select(PlaylistItem).where(PlaylistItem.id.in_(undo["moved"]),
                                                                PlaylistItem.track_id == keep))).scalars():
            item.track_id = back.id
            playlists.add(item.playlist_id)
    if undo.get("tags"):
        await s.execute(delete(TrackTag).where(TrackTag.track_id == keep, TrackTag.tag_id.in_(undo["tags"]),
                                               TrackTag.auto.is_(False)))
    playlists |= set((await s.execute(select(PlaylistItem.playlist_id).where(PlaylistItem.track_id == back.id))).scalars())
    after.scan, after.playlists = True, playlists


# ---------------------------------------------------------------- two artists who are one


async def _artist_now(s: AsyncSession, p: TidyProposal) -> dict[str, Any] | None:
    """Two artists still apart — not joined, nor said to be two projects of one person or two people (the window
    of the artist answers the same question: «Это он», «Его проект», «Это другой»)."""
    from app.models import ArtistApart, ArtistLink

    other, into = await s.get(Artist, p.subject_id), await s.get(Artist, p.to_json["into"])
    if other is None or into is None or other.merged_into_id is not None or into.merged_into_id is not None:
        return None
    low, high = sorted((other.id, into.id))
    for model in (ArtistLink, ArtistApart):
        if await s.get(model, (low, high)) is not None:
            return None
    return {"apart": True}


async def _artist_apply(s: AsyncSession, user: User, p: TidyProposal, after: After) -> dict[str, Any]:
    from app.library import artists

    other, into = await s.get(Artist, p.subject_id), await s.get(Artist, p.to_json["into"])
    was = await artists.merge(s, user.id, into, other)
    after.rewrite.add(into.id)
    return was


async def _artist_revert(s: AsyncSession, user: User, p: TidyProposal, after: After) -> None:
    from app.library import artists

    other, into = await s.get(Artist, p.subject_id), await s.get(Artist, p.to_json["into"])
    if (p.undo_json or {}).get("project"):  # «Это его проект»: the two are not projects of one person any more
        if other is None or into is None:
            raise Changed("Одного из исполнителей уже нет")
        await artists.link(s, user.id, into, other, on=False)
        return
    if other is None or into is None or other.merged_into_id != into.id:
        raise Changed("Исполнителей с тех пор разъединили или объединили с другими")
    try:
        await artists.unmerge(s, user.id, into, other, p.undo_json or {})
    except ValueError as e:
        raise Changed(str(e)) from e
    after.rewrite |= {into.id, other.id}


# ---------------------------------------------------------------- the lines of a text that are not the song


async def _lyrics_now(s: AsyncSession, p: TidyProposal) -> dict[str, Any] | None:
    h = text_hash(await s.get(Lyrics, p.subject_id))
    return {"text": h} if h else None


def _set_text(row: Lyrics, lines: list[dict] | None, plain: str | None) -> None:
    from app.lyrics import parse

    row.lines_json, row.plain = lines, plain
    row.search_text = parse.plain_of(lines, plain).casefold()


def _unmasked(line: dict[str, Any], words: list[str]) -> dict[str, Any]:
    """A timed line with its hidden words put in: the line's text, and the timed words each mask is in."""
    from app.tidy import rules

    out = {**line, "text": rules.put_words(line.get("text", ""), words)}
    if line.get("words"):
        left = list(words)
        timed = []
        for w in line["words"]:
            n = len(rules.masks(w.get("text", "")))
            timed.append({**w, "text": rules.put_words(w.get("text", ""), left[:n])} if n else w)
            left = left[n:]
        out["words"] = timed
    return out


async def _lyrics_apply(s: AsyncSession, user: User, p: TidyProposal, after: After) -> dict[str, Any]:
    """Drop the lines that are not the song (`drop`), put the words a censored line hides back (`replace`: the
    line's place -> its words in order)."""
    from app.lyrics.service import _retag
    from app.tidy import rules

    row = await s.get(Lyrics, p.subject_id)
    drop = set(p.to_json.get("drop") or [])
    replace = {int(k): v for k, v in (p.to_json.get("replace") or {}).items()}
    undo = {"lines": row.lines_json, "plain": row.plain, "pinned": row.pinned}
    if row.lines_json:
        _set_text(row, [_unmasked(ln, replace[i]) if i in replace else ln
                        for i, ln in enumerate(row.lines_json) if i not in drop], None)
    else:
        kept = [rules.put_words(ln, replace[i]) if i in replace else ln
                for i, ln in enumerate((row.plain or "").split("\n")) if i not in drop]
        _set_text(row, None, "\n".join(kept).strip("\n"))
    row.pinned = True  # put right by hand: the lyrics' search leaves it as it is
    await s.flush()
    await _retag(s, row.track_id)
    return undo


async def _lyrics_revert(s: AsyncSession, user: User, p: TidyProposal, after: After) -> None:
    from app.lyrics.service import _retag

    row = await s.get(Lyrics, p.subject_id)
    undo = p.undo_json or {}
    if row is None or text_hash(row) != undo.get("after"):
        raise Changed("Текст с тех пор изменили")
    _set_text(row, undo.get("lines"), undo.get("plain"))
    row.pinned = bool(undo.get("pinned"))
    await s.flush()
    await _retag(s, row.track_id)


# ---------------------------------------------------------------- the kinds


# ---------------------------------------------------------------- a transfer's match to look over


async def _import_now(s: AsyncSession, p: TidyProposal) -> dict[str, Any] | None:
    """The song of a listener's transfer (a playlist, the likes of Spotify, Yandex, VK) was matched with this track
    probably, not surely: still so, and not looked over."""
    from app.imports import review

    item_id = int(p.was_json.get("item") or 0)
    row = (await s.execute(review.pending().where(ImportItem.id == item_id))).first()
    if row is None or row[0].track_id != p.subject_id:
        return None
    return {"item": item_id, "probable": True}


async def _import_apply(s: AsyncSession, user: User, p: TidyProposal, after: After) -> dict[str, Any]:
    """«Совпадает»: the match is the song — for every transfer of the listener that has it from the same service."""
    from app.imports import review

    marked = await review.acknowledge(s, p.user_id or user.id, int(p.was_json["item"]), track_id=p.subject_id)
    if not marked:
        raise Changed("Это совпадение уже проверили")
    return {"items": marked}


async def _import_revert(s: AsyncSession, user: User, p: TidyProposal, after: After) -> None:
    from app.imports import review

    await review.unacknowledge(s, [int(i) for i in (p.undo_json or {}).get("items") or []])


async def offer_imports(s: AsyncSession, user_id: int) -> int:
    """The matches of the listener's transfers to look over, as proposals of theirs (`import_match`): asked as their
    lists of «Наведение порядка» open, so a transfer's doubt is there at once. One question a track (a song in
    several playlists of a transfer is one); one asked and answered is not asked again."""
    from app.imports import review

    rows = (await s.execute(review.pending().where(Import.user_id == user_id).order_by(ImportItem.id))).all()
    if not rows:
        return 0
    have = set((await s.execute(select(TidyProposal.subject_id).where(
        TidyProposal.kind == "import_match", TidyProposal.user_id == user_id, TidyProposal.state == "open"))).scalars())
    n = 0
    for item, source, _ in rows:
        if item.track_id in have:
            continue
        have.add(item.track_id)
        raw = item.raw_json or {}
        n += await offer(s, kind="import_match", subject_type="track", subject_id=item.track_id,
                         was={"item": item.id, "probable": True}, to={"item": item.id},
                         show={"source": source, "title": raw.get("title") or "", "artist": raw.get("artist") or "",
                               "duration_s": raw.get("duration_s"), "cover": raw.get("cover_url"), "item_id": item.id},
                         by="rule", sure=False, why="Импорт нашёл похожий трек, но не уверен, что это та же песня",
                         user_id=user_id)
    return n


@dataclass(frozen=True)
class Kind:
    section: str
    now: Callable[[AsyncSession, TidyProposal], Awaitable[dict[str, Any] | None]]
    apply: Callable[[AsyncSession, User, TidyProposal, After], Awaitable[dict[str, Any]]]
    revert: Callable[[AsyncSession, User, TidyProposal, After], Awaitable[None]]


KINDS: dict[str, Kind] = {
    "title": Kind("names", _name_now, _name_apply, _name_revert),  # a note in the name, by rule
    "cyrillic": Kind("names", _name_now, _name_apply, _name_revert),  # a Russian name in Latin letters, by the AI
    "version": Kind("versions", _version_now, _version_apply, _version_revert),
    "double": Kind("versions", _double_now, _double_apply, _double_revert),
    "artist": Kind("artists", _artist_now, _artist_apply, _artist_revert),
    "lyrics": Kind("lyrics", _lyrics_now, _lyrics_apply, _lyrics_revert),  # lines that are not the song
    "uncensor": Kind("lyrics", _lyrics_now, _lyrics_apply, _lyrics_revert),  # words hidden by censorship
    "import_match": Kind("imports", _import_now, _import_apply, _import_revert),  # a transfer's probable match
}


async def holds(s: AsyncSession, p: TidyProposal) -> bool:
    """The subject is still what the proposal was made of."""
    return await KINDS[p.kind].now(s, p) == p.was_json


async def accept(s: AsyncSession, user: User, p: TidyProposal) -> After:
    """Do what the proposal says. `Changed`: the subject is not what it was made of (the proposal is gone then)."""
    if p.state != "open":
        raise Changed("Это предложение уже решено")
    if not await holds(s, p):
        await s.delete(p)
        await s.flush()
        raise Changed("С тех пор это уже изменили — предложение снято")
    after = After(rewrite=set())
    undo = await KINDS[p.kind].apply(s, user, p, after)
    if p.kind in ("lyrics", "uncensor"):
        undo["after"] = text_hash(await s.get(Lyrics, p.subject_id))
    p.undo_json, p.state, p.decided_at, p.decided_by = undo, "accepted", utcnow(), user.id
    await s.flush()
    return after


async def reject(s: AsyncSession, user: User, p: TidyProposal) -> None:
    """«Отклонить». Of two artists it is «Это другой»: they are not offered to be joined again — here, nor in the
    window of either of them (`artists.apart`)."""
    if p.state != "open":
        raise Changed("Это предложение уже решено")
    if p.kind == "artist":
        from app.library import artists

        other, into = await s.get(Artist, p.subject_id), await s.get(Artist, p.to_json["into"])
        if other is not None and into is not None:
            await artists.apart(s, into, other)
    p.state, p.decided_at, p.decided_by = "rejected", utcnow(), user.id
    await s.flush()


async def project(s: AsyncSession, user: User, p: TidyProposal) -> None:
    """«Это его проект» of two artists: not one artist — two projects of one person, each named on the other's page
    (`artists.link`). Taken back (`revert`), they are apart again."""
    from app.library import artists

    if p.kind != "artist":
        raise ValueError("Так можно ответить только про исполнителей")
    if p.state != "open":
        raise Changed("Это предложение уже решено")
    if not await holds(s, p):
        await s.delete(p)
        await s.flush()
        raise Changed("С тех пор это уже изменили — предложение снято")
    other, into = await s.get(Artist, p.subject_id), await s.get(Artist, p.to_json["into"])
    await artists.link(s, user.id, into, other)
    p.undo_json, p.state, p.decided_at, p.decided_by = {"project": True}, "accepted", utcnow(), user.id
    await s.flush()


async def _unreject(s: AsyncSession, p: TidyProposal) -> None:
    """A rejection of two artists taken back: they may be offered to be joined again (`reject`)."""
    if p.kind == "artist":
        from sqlalchemy import delete

        from app.models import ArtistApart

        low, high = sorted((p.subject_id, int(p.to_json["into"])))
        await s.execute(delete(ArtistApart).where(ArtistApart.low_id == low, ArtistApart.high_id == high))


async def reopen(s: AsyncSession, p: TidyProposal) -> None:
    """A rejection taken back: the proposal is open again."""
    if p.state != "rejected":
        raise Changed("Это предложение не отклонено")
    await _unreject(s, p)
    p.state, p.decided_at, p.decided_by = "open", None, None
    await s.flush()


async def answered(s: AsyncSession, a: int, b: int) -> TidyProposal | None:
    """The open question of joining these two artists, whichever way it was asked (the window of an artist answers
    it: `app.api.artists`)."""
    from sqlalchemy import or_

    return (await s.execute(select(TidyProposal).where(
        TidyProposal.kind == "artist", TidyProposal.state == "open",
        or_((TidyProposal.subject_id == a) & (TidyProposal.to_json["into"].as_integer() == b),
            (TidyProposal.subject_id == b) & (TidyProposal.to_json["into"].as_integer() == a)))
        .limit(1))).scalar_one_or_none()


async def revert(s: AsyncSession, user: User, p: TidyProposal) -> After:
    """Take back what an accepted proposal did. It is not offered again: it was answered."""
    if p.state != "accepted":
        raise Changed("Это предложение не принято")
    after = After(rewrite=set())
    await KINDS[p.kind].revert(s, user, p, after)
    p.state, p.decided_at, p.decided_by = "reverted", utcnow(), user.id
    await s.flush()
    return after


async def review(s: AsyncSession, user: User, p: TidyProposal) -> After | None:
    """A decision taken back whatever it was — the proposal is a question again, in its section's list: an accepted one
    is undone first, a rejected or a reverted one is just open. `Changed`: open already, or the subject is not what
    the proposal was made of any more (an accepted one is then undone all the same and stays «Откачено»)."""
    if p.state == "open":
        raise Changed("Это предложение уже на рассмотрении")
    if p.state == "rejected":
        await _unreject(s, p)
    after = await revert(s, user, p) if p.state == "accepted" else None
    if not await holds(s, p):
        if after is not None:
            return after  # undone, but what it was made of is gone: it stays «Откачено»
        raise Changed("С тех пор это уже изменили — вернуть на рассмотрение нельзя")
    p.state, p.decided_at, p.decided_by = "open", None, None
    await s.flush()
    return after


async def offer(s: AsyncSession, *, kind: str, subject_type: str, subject_id: int, was: dict[str, Any],
                to: dict[str, Any], show: dict[str, Any], by: str, sure: bool, why: str, user_id: int | None = None) -> bool:
    """Write a proposal down, unless the same question was asked before (whatever the answer). Returns whether it
    is new. `user_id`: a listener's own question (shown to them alone); none — the library's, everybody's."""
    import json

    key = hashlib.sha1(json.dumps([kind, subject_type, subject_id, was, to], sort_keys=True, ensure_ascii=False)
                       .encode()).hexdigest()
    res = await s.execute(insert(TidyProposal).values(
        key=key, kind=kind, section=KINDS[kind].section, subject_type=subject_type, subject_id=subject_id,
        was_json=was, to_json=to, show_json=show, by=by, sure=sure, why=why[:300], state="open", created_at=utcnow(),
        user_id=user_id,
    ).on_conflict_do_nothing(index_elements=[TidyProposal.key]).returning(TidyProposal.id))
    return res.scalar_one_or_none() is not None


async def drop_gone(s: AsyncSession) -> int:
    """The open proposals of something no longer there — the track, album or artist they are about, or the other
    one of them (a double's copy that stays, a version's original, the artist to join) — and of two artists answered
    in their own window since (joined, projects of one person, two people): not questions any more. Quick (by ids;
    the artists' few rows): asked as a list opens; `drop_stale` sees the rest at the next look-through."""
    rows = list((await s.execute(select(TidyProposal).where(TidyProposal.state == "open"))).scalars())
    if not rows:
        return 0
    models = {"track": Track, "album": Album, "artist": Artist}
    others = {"double": ("track", "keep"), "version": ("track", "of"), "artist": ("artist", "into")}
    wanted: dict[str, set[int]] = {k: set() for k in models}
    for p in rows:
        if p.subject_type in wanted:
            wanted[p.subject_type].add(p.subject_id)
        if (o := others.get(p.kind)) and isinstance((p.to_json or {}).get(o[1]), int):
            wanted[o[0]].add(p.to_json[o[1]])
    there = {k: set((await s.execute(select(models[k].id).where(models[k].id.in_(ids)))).scalars()) if ids else set()
             for k, ids in wanted.items()}
    n = 0
    for p in rows:
        o = others.get(p.kind)
        other = (p.to_json or {}).get(o[1]) if o else None
        gone = (p.subject_type in there and p.subject_id not in there[p.subject_type]) or \
            (o and isinstance(other, int) and other not in there[o[0]])
        # two artists answered already in their window (joined, projects, two people), a transfer's match looked
        # over or given another file: a few rows each, so asked
        if gone or (p.kind in ("artist", "import_match") and not await holds(s, p)):
            await s.delete(p)
            n += 1
    if n:
        await s.flush()
    return n


async def drop_stale(s: AsyncSession) -> int:
    """The open proposals whose subjects changed or are gone: not questions any more."""
    n = 0
    for p in (await s.execute(select(TidyProposal).where(TidyProposal.state == "open"))).scalars().all():
        if not await holds(s, p):
            await s.delete(p)
            n += 1
    await s.flush()
    return n

