"""What «Наведение порядка» finds by rule, without the AI: pure functions of names and texts."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.library.credits import split_artists
from app.matching.normalize import _clean, _prep, has_cyrillic, normalize_title, split_version, to_latin, version_kind

# ---------------------------------------------------------------- names


# the words of a video's or a site's note on a song's name: «(Official Music Video)», «[Lyrics]», «(Премьера клипа
# 2023)». A bracket is dropped only when it is nothing but these — «(Live Video)» says «Live», and is the name's
_JUNK_WORDS = {
    "official", "officiel", "oficial", "offical", "music", "musik", "lyric", "lyrics", "lyrical", "video", "videoclip",
    "clip", "audio", "visualizer", "visualiser", "visualization", "hd", "hq", "4k", "1080p", "720p", "mv", "m/v",
    "full", "song", "new", "with", "w/", "and", "&", "color", "coded",
    "клип", "клипа", "видео", "видеоклип", "аудио", "премьера", "премьеры", "текст", "текстом", "песни", "песня",
    "трека", "трек", "официальный", "официальное", "официальная", "новинка", "с", "и", "lyricvideo",
}
# a bracket that says one of these is a note even alone; the others need one of these beside them («(Full Song)»,
# «(New)» alone may be the name's)
_JUNK_CORE = {
    "video", "videoclip", "clip", "audio", "visualizer", "visualiser", "visualization", "lyric", "lyrics", "lyrical",
    "lyricvideo", "mv", "m/v", "клип", "клипа", "видео", "видеоклип", "аудио", "премьера", "премьеры", "текст",
    "текстом", "hd", "hq", "4k", "1080p", "720p", "official", "officiel", "oficial", "offical",
}
_YEAR = re.compile(r"^(?:19|20)\d\d$")
_GROUP = re.compile(r"\s*[\(\[\{【]([^\(\)\[\]\{\}【】]*)[\)\]\}】]")
_TAIL = re.compile(r"\s+(?:[-–—|/]|::)\s+([^-–—|/]+)$")
_NUMBER = re.compile(r"^(\d{1,2})\s*[.\-–]\s+(?=\S)")


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\s,.:;!_\-–—]+", text.casefold()) if w]


def is_note(text: str) -> bool:
    """A bracket (or what follows a dash) that is a video's or a site's note, not a part of the name."""
    words = _words(text)
    if not words or not any(w in _JUNK_CORE for w in words):
        return False
    return all(w in _JUNK_WORDS or _YEAR.match(w) for w in words)


def _key(text: str) -> str:
    return to_latin(_clean(_prep(text)))


@dataclass(frozen=True)
class Cleaned:
    title: str
    why: str


def clean_title(title: str | None, artist: str | None = None, track_no: int | None = None) -> Cleaned | None:
    """The name without what is not the name: the notes of a video («(Official Video)», «| Lyrics»), the artist in
    front of it («Jin - Kagerou Days» of Jin), the number of the track («03. Name» of the third). None: it is clean.
    What tells a version («(Live)», «(Remix)») and the guests are the name's own and stay."""
    raw = " ".join((title or "").split())
    name, why = raw, []
    cut = _GROUP.sub(lambda m: "" if is_note(m.group(1)) else m.group(0), name)
    if cut != name:
        name, why = " ".join(cut.split()), [*why, "приписка видео"]
    while (m := _TAIL.search(name)) and is_note(m.group(1)):
        name, why = name[: m.start()].rstrip(), [*why, "приписка видео"]
    if artist:
        for who in dict.fromkeys([artist, *split_artists(artist)]):
            m = re.match(rf"^\s*{re.escape(who)}\s+[-–—]\s+(?=\S)", name, re.I)
            if m is None:  # spelt otherwise: «LSP - Деньги» of «ЛСП» reads the same in Latin letters
                head, sep, _rest = name.partition(" - ")
                if sep and _key(head) and _key(head) == _key(who):
                    m = re.match(rf"^{re.escape(head)}\s+-\s+", name)
            if m is not None:
                name, why = name[m.end():], [*why, "имя исполнителя в названии"]
                break
    if track_no and (m := _NUMBER.match(name)) and int(m.group(1)) == track_no:
        name, why = name[m.end():], [*why, "номер трека в названии"]
    # what a cut leaves dangling: a separator between spaces at an edge («Song -», «| Song»); a dash that is a
    # part of the name stays («-THE BEST OF 八王子P-»)
    name = re.sub(r"^\s*[-–—|]+\s+|(?:\s+[-–—|]+)+\s*$", "", name).strip()
    if not why or not name or name == raw:
        return None
    return Cleaned(name, ", ".join(dict.fromkeys(why)).capitalize())


def needs_cyrillic(name: str | None) -> bool:
    """A name the AI may be asked about: in Latin letters only, and with a word in it."""
    text = (name or "").strip()
    return bool(re.search(r"[A-Za-z]{2}", text)) and not has_cyrillic(text)


# ---------------------------------------------------------------- versions


def _pretty(version: str) -> str:
    """«sped up» → «Sped Up»; a Russian word keeps its case but the first letter."""
    if has_cyrillic(version):
        return version[:1].upper() + version[1:]
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in version.split())


@dataclass(frozen=True)
class Version:
    base: str  # the song's name without what tells the version
    label: str  # what the version is, in a word («Sped Up»)


def version_of(title: str | None) -> Version | None:
    """What a name says of the recording: «Trampoline (Sped Up)» is the version «Sped Up» of «Trampoline». Only what
    changes the recording counts (a remix, live, slowed, a remaster…), not «Deluxe» or «Album Version». None: the
    name says nothing of a version."""
    try:
        base, version = split_version(title or "")
    except Exception:
        return None
    if not version or not base or not _tells(version):
        return None
    return Version(base, _as_written(title or "", version)[:64])


def _tells(version: str) -> bool:
    return bool(version_kind(version) or re.search(r"remaster|ремастер", version, re.I))


def _as_written(title: str, version: str) -> str:
    """The words of the version as the name writes them («Slowed + Reverb»), not as they are compared."""
    parts = [m.group(1).strip() for m in _GROUP.finditer(title)]
    if (m := re.search(r"\s+[-–—]\s+([^-–—]+)$", title)) is not None:
        parts.append(m.group(1).strip())
    written = " ".join(p for p in parts if p and _tells(p)) or version.strip()
    return _pretty(written) if written == written.lower() else written


def song_key(title: str | None) -> str:
    """The name a song is told by, without the guests, what tells its version or edition, and the letters' case
    and alphabet: «Trampoline (Sped Up)», «Trampoline (Deluxe)» and «trampoline» have one."""
    return normalize_title(title).latin


# ---------------------------------------------------------------- lyrics


# a line that is surely not sung: an address, a site's own lines around a text
_STRONG = re.compile(
    r"https?://|www\.|\bt\.me/|\b(?:vk\.com|vk\.cc|youtu\.be|youtube\.com|instagram\.com|tiktok\.com|boosty\.to|patreon\.com|genius\.com)\b"
    r"|\b[\w-]{2,}\.(?:com|ru|net|org|me|io|su|рф|fm|to|cc)(?:/\S*)?(?=\s|$)"
    r"|^\s*\d+\s*contributors?\b|\bembed\s*$|^\s*you might also like\s*$|\bget tickets\b|^\s*see .{2,40} live\s*$"
    r"|lyrics (?:provided|powered|licensed) by|^\s*(?:paroles|текст песни|перевод песни|lyrics)\b.{0,80}$"
    r"|musixmatch|lyricfind|^\s*translations?\s*$",
    re.I,
)
# a line that may not be the song: a call to subscribe, a channel, an advert — but «подпиши мне» may be sung, so
# the AI says which
_WEAK = re.compile(
    r"подпис|subscribe|\bлайк|\blike and\b|репост|донат|donat|\bpatreon\b|\bboosty\b|instagram|инстаграм|\bvk\b|вконтакт"
    r"|telegram|телеграм|\byoutube\b|ютуб|tiktok|тикток|\bканал|\bchannel\b|спонсор|реклам|промокод|promo ?code|sponsor"
    r"|скача|download|бесплатн|free download|@\w{3,}",
    re.I,
)


@dataclass(frozen=True)
class Junk:
    index: int
    text: str
    sure: bool


def lyric_junk(lines: list[str]) -> list[Junk]:
    """The lines of a text that are an address or a site's own line (`sure`), or may be a call to subscribe or an
    advert (not `sure`: the AI is asked)."""
    out: list[Junk] = []
    for i, line in enumerate(lines):
        text = line.strip()
        if not text:
            continue
        if _STRONG.search(text):
            out.append(Junk(i, text, True))
        elif _WEAK.search(text):
            out.append(Junk(i, text, False))
    return out
