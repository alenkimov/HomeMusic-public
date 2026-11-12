"""Lyrics formats → one shape: lines `{"t": ms, "text": str, "end"?: ms, "words"?: [{"t": ms, "text": str}]}`.

`t` is the start of the line; `words` are there only when the source times the parts of a line (a word-synced
text). A text without timings is kept as a plain string, not as lines.
"""

from __future__ import annotations

import re
from typing import Any

Line = dict[str, Any]

_TAG_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_WORD_RE = re.compile(r"<(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?>")
_META_RE = re.compile(r"^\[(ar|ti|al|by|offset|length|re|ve|la|au|id|tool|#)[^\]]*\]\s*$", re.I)
# NetEase heads its texts with the credits as timed lines: «作词 : …», «作曲 : …»
_CREDIT_RE = re.compile(r"^\s*(作词|作詞|作曲|编曲|編曲|制作人|混音|母带|词|曲|lyrics? by|composed by|arranged by|produced by)\s*[:：]", re.I)
_YRC_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
_YRC_WORD_RE = re.compile(r"\((\d+),(\d+),-?\d+\)([^(]*)")


def _ms(m: str, s: str, frac: str | None) -> int:
    f = int((frac or "0").ljust(3, "0")[:3])
    return int(m) * 60_000 + int(s) * 1000 + f


def has_timestamps(text: str) -> bool:
    return bool(_TAG_RE.search(text or ""))


def parse_lrc(text: str) -> list[Line]:
    """LRC, with several stamps on a line and the enhanced `<mm:ss.xx>word` stamps."""
    out: list[Line] = []
    for raw in (text or "").splitlines():
        raw = raw.strip()
        if not raw or _META_RE.match(raw) or raw.startswith("{"):  # NetEase puts JSON credit lines in its LRC
            continue
        stamps = []
        pos = 0
        while (m := _TAG_RE.match(raw, pos)) is not None:
            stamps.append(_ms(*m.groups()))
            pos = m.end()
        if not stamps:
            continue
        body = raw[pos:].strip()
        words: list[dict[str, Any]] = []
        if _WORD_RE.search(body):
            parts = _WORD_RE.split(body)  # [before, m, s, f, text, m, s, f, text, ...]
            for i in range(1, len(parts) - 3, 4):
                w = parts[i + 3]
                if w.strip():
                    words.append({"t": _ms(parts[i], parts[i + 1], parts[i + 2]), "text": w})
            body = "".join(w["text"] for w in words).strip() or _WORD_RE.sub("", body).strip()
        for t in stamps:
            line: Line = {"t": t, "text": body}
            if len(words) > 1:
                line["words"] = words
            out.append(line)
    out.sort(key=lambda x: x["t"])
    return drop_credits(out)


def drop_credits(lines: list[Line]) -> list[Line]:
    """The credit lines a text starts with are not sung."""
    i = 0
    while i < len(lines) and i < 12 and _CREDIT_RE.match(lines[i]["text"]):
        i += 1
    return lines[i:]


def parse_yrc(text: str) -> list[Line]:
    """NetEase's word-timed format: `[start,dur](start,dur,0)word(start,dur,0)word…`."""
    out: list[Line] = []
    for raw in (text or "").splitlines():
        m = _YRC_LINE_RE.match(raw.strip())
        if not m:
            continue
        start, dur, body = int(m[1]), int(m[2]), m[3]
        words = [{"t": int(w[0]), "text": w[2]} for w in _YRC_WORD_RE.findall(body) if w[2]]
        text_ = "".join(w["text"] for w in words).strip()
        if not text_:
            continue
        line: Line = {"t": start, "end": start + dur, "text": text_}
        if len(words) > 1:
            line["words"] = words
        out.append(line)
    return drop_credits(out)


def parse_lyricsfile(doc: Any) -> list[Line]:
    """LRCLIB's Lyricsfile (YAML, already loaded): lines with `start_ms`, `end_ms` and `words`."""
    out: list[Line] = []
    for ln in (doc or {}).get("lines") or []:
        if not isinstance(ln, dict) or ln.get("start_ms") is None or not str(ln.get("text") or "").strip():
            continue
        line: Line = {"t": int(ln["start_ms"]), "text": str(ln["text"]).strip()}
        if ln.get("end_ms") is not None:
            line["end"] = int(ln["end_ms"])
        words = [{"t": int(w["start_ms"]), "text": str(w.get("text") or "")}
                 for w in ln.get("words") or [] if isinstance(w, dict) and w.get("start_ms") is not None and w.get("text")]
        if len(words) > 1:
            line["words"] = words
        out.append(line)
    out.sort(key=lambda x: x["t"])
    return out


def parse_lyricsplus(rows: Any) -> list[Line]:
    """LyricsPlus: lines `{time, duration, text, syllabus: [{time, duration, text}]}`, in ms."""
    out: list[Line] = []
    for ln in rows or []:
        if not isinstance(ln, dict) or not isinstance(ln.get("time"), (int, float)):
            continue
        text = str(ln.get("text") or "").strip()
        if not text:
            continue
        line: Line = {"t": int(ln["time"]), "text": text}
        if isinstance(ln.get("duration"), (int, float)):
            line["end"] = int(ln["time"] + ln["duration"])
        words = [{"t": int(w["time"]), "text": str(w.get("text") or "")}
                 for w in ln.get("syllabus") or []
                 if isinstance(w, dict) and isinstance(w.get("time"), (int, float)) and w.get("text")]
        if len(words) > 1:
            line["words"] = words
        out.append(line)
    out.sort(key=lambda x: x["t"])
    return out


def kind_of(lines: list[Line] | None, plain: str | None) -> str:
    if lines:
        return "word" if any(ln.get("words") for ln in lines) else "synced"
    return "plain" if (plain or "").strip() else "none"


def plain_of(lines: list[Line] | None, plain: str | None) -> str:
    """The text as a reader sees it (for a message, a search)."""
    if lines:
        return "\n".join(ln["text"] for ln in lines)
    return (plain or "").strip()
