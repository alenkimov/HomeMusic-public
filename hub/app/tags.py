"""Tags of the library's tracks (`models.Tag`, `TrackTag`): what a list is filtered by — «Любимое without the Japanese
songs», «Japanese and chill».

Three groups. «Язык» and «Жанр» are everyone's and are set by the Hub itself (`sync`): the languages of a track are
told from its lyrics (a song may have several; one the listener marked as having no lyrics is «без слов»), its genre
is the one that came with it from the catalog. «Теги» are each user's own, set by hand.
A tag the Hub set may be taken off a track by hand, and then stays off (`TagRemoved`): the Hub does not argue.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.lyrics import translate
from app.models import Lyrics, Tag, TagRemoved, TagSelection, Track, TrackTag

GROUPS = {"lang": "Язык", "genre": "Жанр", "own": "Теги"}
LANG_NAMES = {"ja": "японский", "en": "английский", "ru": "русский", "uk": "украинский", "ko": "корейский",
              "zh": "китайский"}
NO_WORDS = ("instrumental", "без слов")
CHUNK = 400


async def get_or_create(s: AsyncSession, group: str, name: str, *, code: str | None = None,
                        user_id: int | None = None) -> Tag:
    name = " ".join(name.split())[:64]
    tag = await _named(s, group, name, user_id)
    if tag is None:
        tag = Tag(group=group, name=name, code=code, user_id=user_id)
        s.add(tag)
        await s.flush()
    return tag


async def _named(s: AsyncSession, group: str, name: str, user_id: int | None, but: int | None = None) -> Tag | None:
    """The tag of this name, whatever the case of its letters. Compared here, not in the database: SQLite's
    `lower()` knows only Latin letters, so «Пляжный» was never found there and a second one was made."""
    q = select(Tag).where(Tag.group == group, Tag.user_id.is_(None) if user_id is None else Tag.user_id == user_id)
    want = name.casefold()
    for tag in (await s.execute(q.order_by(Tag.id))).scalars():
        if tag.id != but and tag.name.casefold() == want:
            return tag
    return None


def visible(user_id: int):
    """The tags a user sees: everyone's, and their own."""
    return Tag.user_id.is_(None) | (Tag.user_id == user_id)


async def sync(s: AsyncSession, track_ids: list[int] | None = None) -> None:
    """Set the tags the Hub tells by itself — languages and genre — for these tracks (all of them by default), and
    take off the ones it set that no longer hold (another text was chosen). What a user set or took off stays."""
    await get_or_create(s, "lang", NO_WORDS[1], code=NO_WORDS[0])  # always there to be put on by hand
    q = (select(Track.id, Track.genre, Lyrics.kind, Lyrics.pinned, Lyrics.search_text, Lyrics.source)
         .outerjoin(Lyrics, Lyrics.track_id == Track.id))
    if track_ids is not None:
        q = q.where(Track.id.in_(track_ids))
    rows = (await s.execute(q)).all()
    if not rows:
        return
    cache: dict[tuple[str, str], int] = {}

    async def tag_id(group: str, name: str, code: str | None = None) -> int:
        key = (group, name.lower())
        if key not in cache:
            cache[key] = (await get_or_create(s, group, name, code=code)).id
        return cache[key]

    want: dict[int, set[int]] = {}
    for track_id, genre, kind, pinned, text, source in rows:
        ids: set[int] = set()
        if genre and genre.strip():
            ids.add(await tag_id("genre", genre.strip()))
        if kind == "none" and (pinned or source == NO_WORDS[0]):  # said by the listener, or by a lyrics source
            ids.add(await tag_id("lang", NO_WORDS[1], NO_WORDS[0]))
        elif kind and kind != "none" and text:
            for code in translate.languages(text):
                ids.add(await tag_id("lang", LANG_NAMES[code], code))
        want[track_id] = ids

    auto_groups = select(Tag.id).where(Tag.group.in_(("lang", "genre")), Tag.user_id.is_(None))
    ids = list(want)
    for i in range(0, len(ids), CHUNK):
        part = ids[i:i + CHUNK]
        have: dict[int, dict[int, bool]] = {}
        for track_id, tid, auto in (await s.execute(
                select(TrackTag.track_id, TrackTag.tag_id, TrackTag.auto)
                .where(TrackTag.track_id.in_(part), TrackTag.tag_id.in_(auto_groups)))).all():
            have.setdefault(track_id, {})[tid] = auto
        gone = {(t, g) for t, g in (await s.execute(
            select(TagRemoved.track_id, TagRemoved.tag_id).where(TagRemoved.track_id.in_(part)))).all()}
        add, drop = [], []
        for track_id in part:
            mine = have.get(track_id, {})
            add += [{"track_id": track_id, "tag_id": tid, "auto": True} for tid in want[track_id]
                    if tid not in mine and (track_id, tid) not in gone]
            drop += [(track_id, tid) for tid, auto in mine.items() if auto and tid not in want[track_id]]
        for j in range(0, len(add), 300):
            await s.execute(insert(TrackTag).values(add[j:j + 300]).on_conflict_do_nothing())
        for track_id, tid in drop:
            await s.execute(delete(TrackTag).where(TrackTag.track_id == track_id, TrackTag.tag_id == tid))


async def catalog(s: AsyncSession, user_id: int) -> dict[str, Any]:
    """The user's tags by group, with how many tracks each has."""
    counts = dict((await s.execute(
        select(TrackTag.tag_id, func.count(TrackTag.track_id)).group_by(TrackTag.tag_id))).all())
    tags = list((await s.execute(select(Tag).where(visible(user_id)))).scalars())
    groups = []
    for gid, title in GROUPS.items():
        mine = sorted((t for t in tags if t.group == gid), key=lambda t: (-counts.get(t.id, 0), t.name.lower()))
        groups.append({"id": gid, "title": title,
                       "tags": [{"id": t.id, "group": gid, "name": t.name, "code": t.code, "count": counts.get(t.id, 0)}
                                for t in mine if gid == "own" or counts.get(t.id, 0) or t.code == NO_WORDS[0]]})
    return {"groups": groups}


TRACK_KEY = "t:"  # how a page names a track that has no Navidrome song: "t:<the Hub's track id>"


def key_of(track_id: int, nd_song_id: str | None) -> str:
    """What a page calls a track when it asks about its tags: the Navidrome song of its file, else the Hub's own
    id of the track (a track with no file yet has tags like any other)."""
    return nd_song_id or f"{TRACK_KEY}{track_id}"


async def tag_map(s: AsyncSession, user_id: int) -> dict[str, list[int]]:
    """track (`key_of`) -> the ids of its tags (the ones this user sees)."""
    out: dict[str, list[int]] = {}
    q = (select(Track.id, Track.nd_song_id, TrackTag.tag_id).join(TrackTag, TrackTag.track_id == Track.id)
         .join(Tag, Tag.id == TrackTag.tag_id).where(visible(user_id)))
    for track_id, song, tid in (await s.execute(q)).all():
        out.setdefault(key_of(track_id, song), []).append(tid)
    return out


async def assign(s: AsyncSession, user_id: int, keys: list[str], add: list[int], remove: list[int]) -> int:
    """Put tags on tracks (named as `key_of` names them) and take others off, by hand. Returns how many tracks it
    was."""
    track_ids = [int(k[len(TRACK_KEY):]) for k in keys if k.startswith(TRACK_KEY) and k[len(TRACK_KEY):].isdigit()]
    songs = [k for k in keys if not k.startswith(TRACK_KEY)]
    for i in range(0, len(songs), CHUNK):
        track_ids += list((await s.execute(select(Track.id).where(Track.nd_song_id.in_(songs[i:i + CHUNK])))).scalars())
    return await assign_tracks(s, user_id, list(dict.fromkeys(track_ids)), add, remove)


async def assign_tracks(s: AsyncSession, user_id: int, track_ids: list[int], add: list[int],
                        remove: list[int] | None = None) -> int:
    """The same by the Hub's own track ids (files just imported have no Navidrome id yet)."""
    remove = remove or []
    allowed = {t.id: t for t in (await s.execute(select(Tag).where(Tag.id.in_({*add, *remove}), visible(user_id)))).scalars()}
    # «без слов» is not a mere mark: with it a track has no text and none is looked for (app/lyrics)
    no_words = next((t.id for t in allowed.values() if t.group == "lang" and t.code == NO_WORDS[0]), None)
    if no_words is not None and track_ids:
        from app.lyrics import service as lyrics

        if no_words in add:
            add = [t for t in add if t != no_words]
            await lyrics.set_no_words(s, track_ids, True)
            await forgive_no_words(s, track_ids)
            await sync(s, track_ids)  # the tag itself, and the languages of the text that is gone
        elif no_words in remove:
            await lyrics.set_no_words(s, track_ids, False)
    for tid in dict.fromkeys(add):
        if tid not in allowed:
            continue
        for i in range(0, len(track_ids), 300):
            part = track_ids[i:i + 300]
            await s.execute(insert(TrackTag).values([{"track_id": t, "tag_id": tid, "auto": False} for t in part])
                            .on_conflict_do_nothing())
            await s.execute(delete(TagRemoved).where(TagRemoved.tag_id == tid, TagRemoved.track_id.in_(part)))
    for tid in dict.fromkeys(remove):
        if tid not in allowed or tid in add:
            continue
        for i in range(0, len(track_ids), 300):
            part = track_ids[i:i + 300]
            await s.execute(delete(TrackTag).where(TrackTag.tag_id == tid, TrackTag.track_id.in_(part)))
            if allowed[tid].group != "own":  # the Hub would set it again: remember that it is not wanted here
                await s.execute(insert(TagRemoved).values([{"track_id": t, "tag_id": tid} for t in part])
                                .on_conflict_do_nothing())
    return len(track_ids)


async def forgive_no_words(s: AsyncSession, track_ids: list[int]) -> None:
    """«Без слов» taken off these tracks before may be set again."""
    tag = (await s.execute(select(Tag.id).where(Tag.group == "lang", Tag.code == NO_WORDS[0], Tag.user_id.is_(None))
                           .limit(1))).scalar_one_or_none()
    if tag is None:
        return
    for i in range(0, len(track_ids), CHUNK):
        await s.execute(delete(TagRemoved).where(TagRemoved.tag_id == tag, TagRemoved.track_id.in_(track_ids[i:i + CHUNK])))


# --- one's own tags: renaming, merging, deleting ------------------------------------------------------------


async def own_tag(s: AsyncSession, user_id: int, tag_id: int) -> Tag:
    tag = await s.get(Tag, tag_id)
    if tag is None or tag.group != "own" or tag.user_id != user_id:
        raise LookupError("Такого своего тега нет")
    return tag


async def rename(s: AsyncSession, user_id: int, tag_id: int, name: str) -> Tag:
    tag = await own_tag(s, user_id, tag_id)
    name = " ".join(name.split())[:64]
    if await _named(s, "own", name, user_id, but=tag.id) is not None:
        raise ValueError("Тег с таким названием уже есть — их можно слить")
    tag.name = name
    return tag


async def _refilter(s: AsyncSession, user_id: int, tag_id: int, into: int | None) -> None:
    """A tag is gone (or became another): the saved selections that named it follow."""
    for sel in (await s.execute(select(TagSelection).where(TagSelection.user_id == user_id))).scalars():
        f = dict(sel.filter_json or {})
        mode = f.pop(str(tag_id), None)
        if mode is None:
            continue
        if into is not None:
            f.setdefault(str(into), mode)
        sel.filter_json = f


async def merge(s: AsyncSession, user_id: int, tag_id: int, into_id: int) -> Tag:
    """Every track of the tag gets `into`, and the tag is gone."""
    tag, into = await own_tag(s, user_id, tag_id), await own_tag(s, user_id, into_id)
    if tag.id == into.id:
        raise ValueError("Нельзя слить тег с самим собой")
    track_ids = list((await s.execute(select(TrackTag.track_id).where(TrackTag.tag_id == tag.id))).scalars())
    await assign_tracks(s, user_id, track_ids, [into.id])
    await _refilter(s, user_id, tag.id, into.id)
    await s.execute(delete(TrackTag).where(TrackTag.tag_id == tag.id))
    await s.delete(tag)
    return into


async def remove(s: AsyncSession, user_id: int, tag_id: int) -> None:
    tag = await own_tag(s, user_id, tag_id)
    await _refilter(s, user_id, tag.id, None)
    await s.execute(delete(TrackTag).where(TrackTag.tag_id == tag.id))
    await s.delete(tag)
