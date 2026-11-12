"""How a text in another script is read: Latin letters under the lines of a Japanese, Chinese or Korean song, and the
kana over the kanji of a Japanese one (furigana).

No model is asked: a dictionary knows how a word is read. Japanese — pykakasi; Chinese — pypinyin (with tone marks);
Korean — spelt out here, syllable by syllable (Revised Romanization, with a consonant carried over to a following
vowel; the finer sound changes between syllables are not followed). It is worked out when the text is opened — a
song takes milliseconds — so nothing of it is kept.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

LANGS = ("ja", "zh", "ko")

_KANJI_RE = re.compile(r"[㐀-䶿一-鿿々]")
_HAN_RE = re.compile(r"[㐀-䶿一-鿿]+")
_SPACES_RE = re.compile(r"\s+")
_BEFORE_PUNCT_RE = re.compile(r"\s+([,.!?;:、。！？）」』])")


def _tidy(text: str) -> str:
    return _BEFORE_PUNCT_RE.sub(r"\1", _SPACES_RE.sub(" ", text)).strip()


# --- Japanese -----------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _kakasi() -> Any:
    import pykakasi

    return pykakasi.kakasi()


def _japanese(line: str) -> tuple[str, list[list[str | None]]]:
    """(romaji, the line cut into pieces [text, its kana or None]) — a piece has kana when it holds kanji."""
    latin: list[str] = []
    ruby: list[list[str | None]] = []
    for part in _kakasi().convert(line):
        orig, hira = part.get("orig") or "", part.get("hira") or ""
        if not orig:
            continue
        latin.append(part.get("hepburn") or orig)
        ruby.append([orig, hira if _KANJI_RE.search(orig) and hira and hira != orig else None])
    return _tidy(" ".join(latin)), ruby


# --- Chinese ------------------------------------------------------------------------------------------


def _chinese(line: str) -> str:
    from pypinyin import Style, lazy_pinyin

    out: list[str] = []
    pos = 0
    for m in _HAN_RE.finditer(line):
        out.append(line[pos:m.start()])
        out.append(" " + " ".join(lazy_pinyin(m.group(), style=Style.TONE)) + " ")
        pos = m.end()
    out.append(line[pos:])
    return _tidy("".join(out))


# --- Korean -------------------------------------------------------------------------------------------

_INITIAL = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
_MEDIAL = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu",
           "eu", "ui", "i"]
# a final consonant: (as the syllable ends, as it sounds carried over to a following vowel)
_FINAL = [("", ""), ("k", "g"), ("k", "kk"), ("k", "ks"), ("n", "n"), ("n", "nj"), ("n", "n"), ("t", "d"), ("l", "r"),
          ("k", "lg"), ("m", "lm"), ("l", "lb"), ("l", "ls"), ("l", "lt"), ("p", "lp"), ("l", "r"), ("m", "m"),
          ("p", "b"), ("p", "bs"), ("t", "s"), ("t", "ss"), ("ng", "ng"), ("t", "j"), ("t", "ch"), ("k", "k"),
          ("t", "t"), ("p", "p"), ("t", "h")]
_NO_INITIAL = 11  # ㅇ at the start of a syllable is silent: the consonant before it is carried over


def _korean(line: str) -> str:
    out: list[str] = []
    chars = list(line)
    for i, ch in enumerate(chars):
        code = ord(ch) - 0xAC00
        if not 0 <= code < 11172:
            out.append(ch)
            continue
        initial, medial, final = code // 588, (code % 588) // 28, code % 28
        nxt = ord(chars[i + 1]) - 0xAC00 if i + 1 < len(chars) else -1
        next_initial = nxt // 588 if 0 <= nxt < 11172 else None
        end, carried = _FINAL[final]
        if final and next_initial == _NO_INITIAL:
            tail = carried
        elif final == 8 and next_initial == 5:  # ㄹ + ㄹ
            tail = "l"
        else:
            tail = end
        head = "l" if initial == 5 and out and out[-1].endswith("l") else _INITIAL[initial]
        out.append(head + _MEDIAL[medial] + tail)
    return _tidy("".join(out))


# --- all together -------------------------------------------------------------------------------------


def of(lines: list[str], lang: str | None) -> dict[str, Any] | None:
    """How the lines are read: `{"lang", "lines": [Latin letters, or "" where there is nothing to read], "ruby"?:
    […]}` — line for line with `lines`. None: the text is in a script that needs none."""
    if lang not in LANGS or not any(x.strip() for x in lines):
        return None
    try:
        if lang == "ja":
            done = [_japanese(x) if x.strip() else ("", []) for x in lines]
            latin, ruby = [a for a, _ in done], [b for _, b in done]
            return {"lang": lang, "lines": [a if a != x.strip() else "" for a, x in zip(latin, lines, strict=True)],
                    "ruby": ruby}
        fn = _chinese if lang == "zh" else _korean
        latin = [fn(x) if x.strip() else "" for x in lines]
        return {"lang": lang, "lines": [a if a != x.strip() else "" for a, x in zip(latin, lines, strict=True)]}
    except Exception:  # a text is shown without its reading rather than not at all
        import logging

        logging.getLogger(__name__).exception("lyrics: reading of a %s text failed", lang)
        return None
