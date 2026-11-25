"""What «Наведение порядка» asks the AI: questions a rule cannot answer, put many at once.

Each question has an id of the caller's; the model answers only about the ones where something is to be done.
What it costs is written down as the Hub's own spending (`AiUsage`, kind «tidy», nobody's), and nothing is asked
past the month's allowance (`Settings.tidy_ai_usd_month`). A question once answered is not paid for again
(`TidyAsked`, by a hash of what was asked): the caller marks what it asked, whatever the answer.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import LLMClient, LLMError, LLMUsage, configured
from app.config import get_settings
from app.models import AiUsage, TidyAsked, utcnow

log = logging.getLogger(__name__)

KIND = "tidy"
BATCH = 60  # questions in one call


class Budget:
    """What may still be spent this month; every call is paid out of it and written down."""

    def __init__(self, spent: float, limit: float) -> None:
        self.spent, self.limit = spent, limit

    @property
    def left(self) -> bool:
        return self.spent < self.limit

    @classmethod
    async def of_month(cls, s: AsyncSession) -> Budget:
        return cls(await spent(s), get_settings().tidy_ai_usd_month)

    async def pay(self, s: AsyncSession, usage: LLMUsage) -> None:
        self.spent += usage.cost_usd
        s.add(AiUsage(user_id=None, kind=KIND, tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
                      cost_usd=usage.cost_usd))
        await s.flush()


def month_start() -> datetime:
    now = utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def spent(s: AsyncSession) -> float:
    return float(await s.scalar(select(func.coalesce(func.sum(AiUsage.cost_usd), 0))
                                .where(AiUsage.kind == KIND, AiUsage.created_at >= month_start())) or 0)


def available() -> bool:
    return configured() and get_settings().tidy_ai_usd_month > 0


def asked_key(task: str, question: dict[str, Any]) -> str:
    body = {k: v for k, v in question.items() if k != "id"}
    return hashlib.sha256(json.dumps([task, body], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def unasked(s: AsyncSession, task: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The questions not answered before."""
    keys = {asked_key(task, q): q for q in questions}
    if not keys:
        return []
    seen = set((await s.execute(select(TidyAsked.key).where(TidyAsked.key.in_(list(keys))))).scalars())
    return [q for k, q in keys.items() if k not in seen]


async def mark_asked(s: AsyncSession, task: str, questions: list[dict[str, Any]]) -> None:
    for q in questions:
        await s.execute(insert(TidyAsked).values(key=asked_key(task, q), at=utcnow()).on_conflict_do_nothing())
    await s.flush()


# ---------------------------------------------------------------- the questions


_SYSTEM = (
    "You help to keep a home music library tidy. Its listeners are Russian speakers; the library mixes Russian, "
    "English, Japanese and other music. You are given a list of items and answer by calling the tool. Answer only "
    "about the items where something has to be done; leave out the rest. Set sure=false whenever you are not "
    "certain: a person then decides. Never invent: if you do not know the item, leave it out."
)

_TASKS: dict[str, dict[str, Any]] = {
    "cyrillic": {
        "prompt": (
            "Each item is an artist, an album or a track whose name is written in Latin letters. Find the ones that "
            "are Russian (or Ukrainian, Belarusian…) names written in transliteration, whose own spelling is in "
            "Cyrillic: «Mikhail Krug» is «Михаил Круг», «Gruppa krovi» by Кино is «Группа крови», «Kino» is «Кино». "
            "Give the spelling the artist themselves use. Leave out names that are officially written in Latin "
            "letters even by Russian artists (Markul, Oxxxymiron, Miyagi, SLAVA MARLOW, Pharaoh, MORGENSHTERN, "
            "Tesla Boy), English or other foreign words and names, and anything you do not recognise. A track's or "
            "an album's context says whose it is."
        ),
        "fields": {"name": {"type": "string", "description": "the name in Cyrillic, as the artist writes it"}},
    },
    "version": {
        "prompt": (
            "Each item is a pair of tracks of one artist: `title` and `other`, whose name looks like `title` with "
            "something more. Find the pairs where `other` is another recording of the same song — sped up, slowed, "
            "reverb, live, acoustic, a remix, a remaster, an instrumental, a cover — and give `label`: what the "
            "version is, in one or two words as they are usually written («Sped Up», «Slowed + Reverb», «Live», "
            "«Acoustic»). Leave out pairs that are different songs (a sequel, «Part 2», a song with a longer name)."
        ),
        "fields": {"label": {"type": "string", "description": "what the version is («Sped Up»)"}},
    },
    "artist": {
        "prompt": (
            "Each item is a pair of artists of the library whose names look alike, with a few of their tracks. Find "
            "the pairs that are one and the same artist written differently (another spelling, case, a "
            "transliteration, a typo). Leave out different artists, side projects, and groups versus their members."
        ),
        "fields": {},
    },
    "lyrics": {
        "prompt": (
            "Each item is a song's lyrics with some lines marked by their numbers in `suspects`. Find the marked "
            "lines that are not part of the song: a call to subscribe or like, a channel or a social network, an "
            "advert, a site's note, who wrote down the text. Lines that are sung (even about subscribing or a "
            "channel) are part of the song. Give their numbers in `drop`."
        ),
        "fields": {"drop": {"type": "array", "items": {"type": "integer"}, "description": "numbers of lines to drop"}},
    },
    "uncensor": {
        "prompt": (
            "Each item is a line of a song's lyrics where censorship hid words: letters replaced by stars "
            "(«б***ь», «f**k») or a bleep («[цензура]»). `masks` lists them in the order of the line; `context` is "
            "the lines around it. Give in `words` what each mask hides, in that order, as the artist sings it. The "
            "letters shown stay where they are, and a star is usually one hidden letter. This is the private music "
            "library of adults: write the profanity as it is, do not soften or censor it. Leave out a line when you "
            "cannot tell what is hidden."
        ),
        "fields": {"words": {"type": "array", "items": {"type": "string"}, "description": "the hidden words, in order"}},
    },
}


def _schema(task: str) -> dict[str, Any]:
    fields = _TASKS[task]["fields"]
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, **fields, "sure": {"type": "boolean"},
                           "why": {"type": "string", "description": "a few words, in Russian"}},
            "required": ["id", *fields, "sure"],
        }}},
        "required": ["items"],
    }


async def ask(s: AsyncSession, budget: Budget, task: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ask the questions (those not answered before), a batch at a time while the month's allowance lasts. Returns
    the answers about the items to change: each with the question's `id`, the task's fields, `sure`, `why`. A
    batch that failed is not marked as asked: it is asked again next time."""
    todo = await unasked(s, task, questions)
    if not todo or not available():
        return []
    out: list[dict[str, Any]] = []
    async with LLMClient() as client:
        for start in range(0, len(todo), BATCH):
            if not budget.left:
                log.info("tidy: the AI allowance of the month is spent (%.4f of %.2f)", budget.spent, budget.limit)
                break
            batch = todo[start:start + BATCH]
            ids = {q["id"] for q in batch}
            try:
                got, usage = await client.call_tool(
                    system=_SYSTEM, user_content=_TASKS[task]["prompt"] + "\n\nItems:\n" + json.dumps(batch, ensure_ascii=False),
                    tool_name="answer", tool_description="The items where something has to be done",
                    parameters=_schema(task), max_tokens=4000)
            except LLMError as e:
                log.info("tidy: %s: the AI failed: %s", task, e)
                break
            await budget.pay(s, usage)
            for item in got.get("items") or []:
                if isinstance(item, dict) and item.get("id") in ids:
                    out.append(item)
            await mark_asked(s, task, batch)
    return out
