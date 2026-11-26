"""Whether two files are the same recording, told by their sound.

Names lie both ways: one song comes as «Марафоны» and as «Marafony (feat. Роки)», and two recordings of one name
and length — the album's and a re-recording — are not the same. The sound does not: Chromaprint (`fpcalc`, a
program on the Hub; nothing goes anywhere, no service is asked) draws a compact picture of a file's first two
minutes that stays almost the same whatever the file is encoded with — an MP3 at 128 and a FLAC of one recording
differ in a few bits of it, two recordings in half.

A file's picture is kept with it (`track_files.chroma`, an upload's `uploads.chroma`): made when the file arrives,
and for the files that were there before by a pass in the background (`fill`). `twin` finds the library's file
that is the same recording as a new one — among the tracks of about its length, so a library is not gone through
whole.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from array import array
from pathlib import Path

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.library.probe import yielding
from app.models import Track, TrackFile

log = logging.getLogger(__name__)

LENGTH_S = 120  # of the file's start that is listened to
# The share of bits two pictures agree in. Measured on a library of 700 files, 17 679 pairs of about one length: two
# recordings agree in 0.4–0.6 (all but eleven pairs); one recording encoded twice — 0.99; and between them stand the
# recordings made over one backing track — a song's version in English 0.86, a cover over the original's music 0.82.
# So «the same recording» is a high bar, and what is only alike is said to be alike, not the same.
SAME = 0.93
ALIKE = 0.80
SHIFT = 80  # frames (≈ 10 s) one file may start later than the other: silence before the music, a cut intro
MIN_OVERLAP = 160  # frames (≈ 20 s) compared at the least
NEAR_S = 8  # a twin is looked for among tracks whose length differs by no more than this
TIMEOUT_S = 60


def available() -> bool:
    return shutil.which("fpcalc") is not None


def pack(values: list[int]) -> bytes:
    return array("I", [v & 0xFFFFFFFF for v in values]).tobytes()


def unpack(data: bytes | None) -> list[int]:
    if not data:
        return []
    a = array("I")
    a.frombytes(data[: len(data) - len(data) % a.itemsize])
    return list(a)


async def of_file(path: Path) -> tuple[bytes, float] | None:
    """The picture of the file's sound and the file's length; None: there is no `fpcalc`, or it could not read the
    file."""
    if not available():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            *yielding("fpcalc", "-raw", "-json", "-length", str(LENGTH_S), str(path)),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
        body = json.loads(out or b"{}")
        values = body.get("fingerprint") or []
        if not isinstance(values, list) or len(values) < MIN_OVERLAP // 2:
            return None
        return pack([int(v) for v in values]), float(body.get("duration") or 0)
    except Exception as e:
        log.info("fpcalc %s: %r", path.name, e)
        return None


WHOLE_S = 1800  # of a file listened to when two files are laid side by side (`parts`): all of it
_whole: dict[tuple[str, float], tuple[list[int], float]] = {}


async def of_whole(path: Path) -> tuple[list[int], float] | None:
    """The picture of the whole of the file's sound, and the file's length (kept for the Hub's run: a pair is laid
    side by side more than once)."""
    if not available():
        return None
    try:
        key = (str(path), path.stat().st_mtime)
        if key in _whole:
            return _whole[key]
        proc = await asyncio.create_subprocess_exec(
            *yielding("fpcalc", "-raw", "-json", "-length", str(WHOLE_S), str(path)),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_S * 3)
        body = json.loads(out or b"{}")
        values = [int(v) for v in body.get("fingerprint") or []]
        if len(values) < MIN_OVERLAP:
            return None
        if len(_whole) > 60:
            _whole.clear()
        _whole[key] = (values, float(body.get("duration") or 0))
        return _whole[key]
    except Exception as e:
        log.info("fpcalc (whole) %s: %r", path.name, e)
        return None


# Where two files of one recording part. A copy with the rude words cut out is the same recording almost everywhere
# — its likeness to the uncut one is as high as that of any two encodings (0.93 and over) — and quite another sound
# for a second or two where the cut is: muted, bleeped, sung over. So the two pictures are laid side by side frame
# by frame, and the places where they agree far less than they do on the whole are the places to listen to.
# Measured: a second of silence put into a copy drops the agreement there to 0.6–0.7 (half a second: to about
# 0.75, at the edge of what is seen); two honest encodings of one master never fell under 0.8 anywhere but at the
# very ends, where fades and silence differ by themselves.
PARTS_WINDOW = 6  # frames (≈ 0.75 s) the agreement is averaged over
PARTS_DROP = 0.15  # how far under the pair's own average a place must fall (0.15: nothing false in fifteen clean pairs)
PARTS_EDGE = 20  # frames at each end that are not looked at
PARTS_MIN_ALIKE = 0.85  # two files less alike than this are not one recording: nothing to lay side by side


def parts(a: list[int], da: float, b: list[int], db: float) -> list[dict[str, float]]:
    """The places where two files of one recording differ: [{at, to — seconds in the first file; other_at, other_to
    — the same place in the second; depth — the agreement there, 0…1}]. Nothing where the two are not one recording
    at all, or do not differ anywhere in particular."""
    if len(a) < MIN_OVERLAP or len(b) < MIN_OVERLAP or not da or not db:
        return []
    best, shift = 0.0, 0
    for sh in range(-SHIFT, SHIFT + 1):  # where the second begins against the first
        x, y = (a[sh:], b) if sh >= 0 else (a, b[-sh:])
        n = min(len(x), len(y), 900)
        if n < MIN_OVERLAP:
            continue
        v = sum(32 - (x[i] ^ y[i]).bit_count() for i in range(0, n, 3)) / (32 * len(range(0, n, 3)))
        if v > best:
            best, shift = v, sh
    x, y = (a[shift:], b) if shift >= 0 else (a, b[-shift:])
    n = min(len(x), len(y))
    agree = [(32 - (x[i] ^ y[i]).bit_count()) / 32 for i in range(n)]
    mean = sum(agree) / n
    if mean < PARTS_MIN_ALIKE:
        return []
    half = PARTS_WINDOW // 2
    smooth = [sum(agree[max(0, i - half): i + half + 1]) / len(agree[max(0, i - half): i + half + 1]) for i in range(n)]
    spans: list[list[float]] = []
    start = None
    for i in range(PARTS_EDGE, n - PARTS_EDGE):
        low = smooth[i] < mean - PARTS_DROP
        if low and start is None:
            start = i
        if start is not None and (not low or i == n - PARTS_EDGE - 1):
            if spans and start - spans[-1][1] < 2 * PARTS_WINDOW:  # two dips of one place
                spans[-1][1], spans[-1][2] = i, min(spans[-1][2], min(smooth[start:i + 1]))
            else:
                spans.append([start, i, min(smooth[start:i + 1])])
            start = None
    dt_a, dt_b = da / len(a), db / len(b)
    off_a, off_b = (shift, 0) if shift >= 0 else (0, -shift)
    # a frame says what sounds a little after it: the place is given a margin on both sides
    return [{"at": max(0.0, round((i + off_a) * dt_a - 0.5, 1)), "to": round((j + off_a) * dt_a + 1.5, 1),
             "other_at": max(0.0, round((i + off_b) * dt_b - 0.5, 1)), "other_to": round((j + off_b) * dt_b + 1.5, 1),
             "depth": round(d, 2)} for i, j, d in spans]


async def loudness(path: Path, at: float, to: float) -> float | None:
    """How loud the file is between two moments (the mean of its samples' sizes), to tell which of two files falls
    silent at a place where they part."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *yielding("ffmpeg", "-v", "error", "-ss", f"{max(0.0, at):.2f}", "-t", f"{max(0.3, to - at):.2f}", "-i",
                      str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        raw, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
        samples = array("h")
        samples.frombytes(raw[: len(raw) // 2 * 2])
        return sum(abs(v) for v in samples) / len(samples) if samples else None
    except Exception as e:
        log.info("loudness %s: %r", path.name, e)
        return None


async def differences(mine: Path, other: Path) -> list[dict[str, Any]]:
    """Where a file parts with another of the same recording (`parts`), each place with which of the two is the
    quieter there (`quieter`: "mine" / "other" / None — neither clearly): a muted word is a hole in the sound."""
    a, b = await of_whole(mine), await of_whole(other)
    if a is None or b is None:
        return []
    found: list[dict[str, Any]] = await asyncio.to_thread(parts, a[0], a[1], b[0], b[1])
    for g in found:
        x, y = await loudness(mine, g["at"], g["to"]), await loudness(other, g["other_at"], g["other_to"])
        g["quieter"] = None if not x or not y else "mine" if x * 1.4 < y else "other" if y * 1.4 < x else None
    return found


def clock(s: float) -> str:
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def likeness(a: list[int] | bytes, b: list[int] | bytes) -> float:
    """How alike two pictures are, 0…1: the share of bits that agree where they overlap, at the shift at which they
    agree most. Each picture is taken as one long number, so a shift is a shift of bits and the count of the bits
    that differ is one operation — a pair is compared in a fraction of a millisecond, a library in a second."""
    ab = a if isinstance(a, bytes) else pack(a)
    bb = b if isinstance(b, bytes) else pack(b)
    na, nb = len(ab) // 4, len(bb) // 4
    if not na or not nb:
        return 0.0
    x, y = int.from_bytes(ab[: na * 4], "little"), int.from_bytes(bb[: nb * 4], "little")
    need = min(MIN_OVERLAP, min(na, nb) * 3 // 4)
    best = 0.0
    for shift in range(-SHIFT, SHIFT + 1):
        if shift >= 0:
            n, u, v = min(na - shift, nb), x >> (32 * shift), y
        else:
            n, u, v = min(na, nb + shift), x, y >> (32 * -shift)
        if n < need or n <= 0:
            continue
        wrong = ((u ^ v) & ((1 << (32 * n)) - 1)).bit_count()
        best = max(best, 1.0 - wrong / (32.0 * n))
        if best > 0.98:
            break
    return best


async def twin(s: AsyncSession, chroma: bytes | None, duration_s: float | None) -> tuple[Track, TrackFile, float] | None:
    """The library's file that sounds like this picture: (its track, the file, how alike) — the likest of them,
    when it is alike at least `ALIKE`. `SAME` and over is the same recording; between the two it is a recording
    over the same music (a version in another language, a cover over the original's backing)."""
    if not chroma:
        return None
    q = select(Track, TrackFile).join(TrackFile, TrackFile.track_id == Track.id).where(TrackFile.chroma.is_not(None))
    if duration_s:
        q = q.where(Track.duration_s.is_(None) | Track.duration_s.between(int(duration_s) - NEAR_S, int(duration_s) + NEAR_S))
    rows = (await s.execute(q.options(undefer(TrackFile.chroma)).order_by(TrackFile.id.desc()))).all()

    def best() -> tuple[int, float]:
        at, top = -1, 0.0
        for i, (_, f) in enumerate(rows):
            v = likeness(chroma, f.chroma or b"")
            if v > top:
                at, top = i, v
        return at, top

    at, top = await asyncio.to_thread(best)
    return (rows[at][0], rows[at][1], top) if at >= 0 and top >= ALIKE else None


_doubles: tuple[tuple[int, int], list[list[int]]] | None = None  # (how many pictures, the newest file) -> the groups


async def doubles(s: AsyncSession) -> list[list[int]]:
    """The tracks that are one recording twice or more: groups of track ids, each of files that sound the same
    (`SAME`). A library is gone through in seconds, not at once, so what was found is kept until a file with a
    picture comes or goes."""
    global _doubles
    count, newest = (await s.execute(select(func.count(TrackFile.id), func.max(TrackFile.id))
                                     .where(TrackFile.chroma.is_not(None)))).one()
    if _doubles is not None and _doubles[0] == (count, newest or 0):
        return _doubles[1]
    rows = [(tid, dur or 0, chroma) for tid, dur, chroma in (await s.execute(
        select(TrackFile.track_id, Track.duration_s, TrackFile.chroma).join(Track, Track.id == TrackFile.track_id)
        .where(TrackFile.chroma.is_not(None)).order_by(Track.duration_s))).all() if chroma]

    def find() -> list[list[int]]:
        group = {tid: tid for tid, _, _ in rows}

        def top(x: int) -> int:
            while group[x] != x:
                group[x] = group[group[x]]
                x = group[x]
            return x

        for i, (a, da, ca) in enumerate(rows):
            for b, db, cb in rows[i + 1:]:
                if da and db and db - da > NEAR_S:
                    break  # by length: the rest are longer still
                if a != b and top(a) != top(b) and likeness(ca, cb) >= SAME:
                    group[top(b)] = top(a)
        out: dict[int, list[int]] = {}
        for tid in group:
            out.setdefault(top(tid), []).append(tid)
        return sorted(sorted(g) for g in out.values() if len(g) > 1)

    found = await asyncio.to_thread(find)
    _doubles = ((count, newest or 0), found)
    return found


async def fill(s: AsyncSession, limit: int = 100) -> int:
    """Draw the pictures of the library's files that have none yet (the ones there before the Hub drew them), the
    newest first. Returns how many were drawn; a file that cannot be read is marked so it is not tried again."""
    if not available():
        return 0
    files = list((await s.execute(select(TrackFile).where(TrackFile.chroma.is_(None)).order_by(TrackFile.id.desc())
                                  .limit(limit))).scalars())
    n = 0
    for f in files:
        from app.library.importer import library_abs

        got = await of_file(library_abs(f.path))
        f.chroma = got[0] if got else b""  # empty: tried, nothing to draw
        n += 1 if got else 0
    await s.flush()
    return n
