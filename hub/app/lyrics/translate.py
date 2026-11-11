"""Translating lyrics, line for line, into the language a listener asks for.

Three translators: the Hub's own LLM (`LLM_*` of .env — a local Ollama model or a cloud one: it knows what a song is
and translates by meaning), Google Translate's keyless endpoint (fast, literal; Google refuses it from some networks)
and MyMemory (free, a line at a time, a few songs a day). A song's lines repeat, so only the different ones are sent; what comes back is checked to be line for line, else the other translator is tried.
What language a text is in is told by its letters (`detect`) — a song already in the listener's language is not sent
anywhere.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import Any

from app.net import make_client

log = logging.getLogger(__name__)

# code -> (the name shown, the name a model is told)
LANGS: dict[str, tuple[str, str]] = {
    "ru": ("Русский", "Russian"), "en": ("English", "English"), "uk": ("Українська", "Ukrainian"),
    "be": ("Беларуская", "Belarusian"), "kk": ("Қазақша", "Kazakh"), "de": ("Deutsch", "German"),
    "fr": ("Français", "French"), "es": ("Español", "Spanish"), "it": ("Italiano", "Italian"),
    "pt": ("Português", "Portuguese"), "pl": ("Polski", "Polish"), "tr": ("Türkçe", "Turkish"),
    "ja": ("日本語", "Japanese"), "ko": ("한국어", "Korean"), "zh": ("中文", "Chinese (Simplified)"),
}
PROVIDERS = ("llm", "google", "mymemory")
TITLES = {"llm": "ИИ Хаба", "google": "Google Translate", "mymemory": "MyMemory"}

LLM_BATCH = 20  # lines per request: a small model loses count on more
GOOGLE_CHARS = 1800  # per request (it is a GET)
GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

_EN = frozenset("the and you that for with are this have not but what all your from they will one when there can "
                "love know just like don't it's i'm me my we be to of in is on so no oh".split())
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")


class TranslateError(Exception):
    """A translator failed (the message is user-facing, Russian)."""


def detect(text: str) -> str | None:
    """The language of a text by its letters: ja / ko / zh / ru / uk / en, or None when it cannot be told (a Latin
    text that is not English: French, Spanish, romaji...). A few foreign lines in a song do not change it."""
    counts = {"kana": 0, "hangul": 0, "han": 0, "cyr": 0, "latin": 0}
    for ch in text:
        if not ch.isalpha():
            continue
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF or 0xFF66 <= o <= 0xFF9D:
            counts["kana"] += 1
        elif 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF:
            counts["hangul"] += 1
        elif 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            counts["han"] += 1
        elif 0x0400 <= o <= 0x04FF:
            counts["cyr"] += 1
        elif "LATIN" in unicodedata.name(ch, ""):
            counts["latin"] += 1
    total = sum(counts.values())
    if not total:
        return None
    # Japanese is written with Chinese characters too: some kana among them is enough
    if counts["kana"] >= 0.08 * total:
        return "ja"
    top = max(counts, key=lambda k: counts[k])
    if counts[top] < 0.6 * total:
        return None
    if top == "hangul":
        return "ko"
    if top == "han":
        return "zh"
    if top == "cyr":
        return "uk" if sum(text.count(c) for c in "іїєґІЇЄҐ") >= 3 else "ru"
    if top == "latin":
        words = [w.lower() for w in _WORD.findall(text)]
        known = sum(1 for w in words if w in _EN)
        return "en" if words and known >= 0.18 * len(words) else None
    return None


LANG_SHARE = 0.15  # of a song's lines: less is a borrowed phrase, not a language of the song
LANG_LINES = 3


def languages(text: str) -> list[str]:
    """Every language a song is sung in, the main one first: a Japanese song with English verses is both. A line
    is told by its letters; the Latin lines together are English or (other languages are not told) nothing."""
    lines = [x for x in text.splitlines() if any(ch.isalpha() for ch in x)]
    if not lines:
        return []
    kinds: dict[str, list[str]] = {}
    for line in lines:
        c = {"kana": 0, "hangul": 0, "han": 0, "cyr": 0, "latin": 0}
        for ch in line:
            if not ch.isalpha():
                continue
            o = ord(ch)
            if 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF or 0xFF66 <= o <= 0xFF9D:
                c["kana"] += 1
            elif 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF:
                c["hangul"] += 1
            elif 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
                c["han"] += 1
            elif 0x0400 <= o <= 0x04FF:
                c["cyr"] += 1
            elif "a" <= ch.lower() <= "z" or "LATIN" in unicodedata.name(ch, ""):
                c["latin"] += 1
        top = "kana" if c["kana"] else max(c, key=lambda k: c[k])
        if c[top]:
            kinds.setdefault(top, []).append(line)
    if "kana" in kinds and "han" in kinds:  # lines of kanji alone, in a Japanese song, are Japanese
        kinds["kana"] += kinds.pop("han")
    found: list[tuple[int, str]] = []
    for kind, got in kinds.items():
        if len(got) < LANG_LINES or len(got) < LANG_SHARE * len(lines):
            continue
        joined = "\n".join(got)
        if kind == "kana":
            code: str | None = "ja"
        elif kind == "hangul":
            code = "ko"
        elif kind == "han":
            code = "zh"
        elif kind == "cyr":
            code = "uk" if sum(joined.count(ch) for ch in "іїєґІЇЄҐ") >= 3 else "ru"
        else:
            words = [w.lower() for w in _WORD.findall(joined)]
            code = "en" if words and sum(1 for w in words if w in _EN) >= 0.18 * len(words) else None
        if code:
            found.append((len(got), code))
    return [code for _, code in sorted(found, reverse=True)]


def _same_shape(got: list[str] | None, want: int) -> bool:
    return isinstance(got, list) and len(got) == want and all(isinstance(x, str) for x in got)


async def google(lines: list[str], target: str) -> list[str]:
    """Google Translate without a key. Newlines survive a request, so a chunk of lines comes back line for line;
    a chunk that did not is sent again a line at a time."""
    async def ask(c: Any, text: str) -> str:
        r = await c.get(GOOGLE_URL, params={"client": "gtx", "sl": "auto", "tl": "zh-CN" if target == "zh" else target,
                                           "dt": "t", "q": text})
        r.raise_for_status()
        return "".join(part[0] for part in (r.json()[0] or []) if part and part[0])

    out: list[str] = []
    try:
        async with make_client(headers={"User-Agent": UA}, timeout=20) as c:
            chunk: list[str] = []

            async def flush() -> None:
                if not chunk:
                    return
                got = [x.strip() for x in (await ask(c, "\n".join(chunk))).split("\n")]
                if len(got) != len(chunk):
                    got = [(await ask(c, x)).strip() for x in chunk]
                out.extend(got)
                chunk.clear()

            for line in lines:
                if chunk and sum(len(x) + 1 for x in chunk) + len(line) > GOOGLE_CHARS:
                    await flush()
                chunk.append(line)
            await flush()
    except Exception as e:
        refused = getattr(getattr(e, "response", None), "status_code", None) in (302, 429)
        raise TranslateError("Google Translate отказывает запросам с этого адреса" if refused
                             else f"Google Translate не ответил ({type(e).__name__})") from e
    return out


MYMEMORY_URL = "https://api.mymemory.translated.net/get"
MYMEMORY_AT_ONCE = 4


async def mymemory(lines: list[str], target: str, source: str | None) -> list[str]:
    """MyMemory: free without a key, one line a request, about 5000 characters a day from one address."""
    pair = f"{'zh-CN' if source == 'zh' else source or 'Autodetect'}|{'zh-CN' if target == 'zh' else target}"
    gate = asyncio.Semaphore(MYMEMORY_AT_ONCE)

    async def ask(c: Any, text: str) -> str:
        async with gate:
            r = await c.get(MYMEMORY_URL, params={"q": text[:480], "langpair": pair})
        r.raise_for_status()
        body = r.json()
        if body.get("quotaFinished") or str(body.get("responseStatus")) != "200":
            raise TranslateError("MyMemory: дневной лимит бесплатных переводов исчерпан")
        return str((body.get("responseData") or {}).get("translatedText") or "").strip()

    try:
        async with make_client(headers={"User-Agent": UA}, timeout=20) as c:
            return list(await asyncio.gather(*(ask(c, x) for x in lines)))
    except TranslateError:
        raise
    except Exception as e:
        raise TranslateError(f"MyMemory не ответил ({type(e).__name__})") from e


SYSTEM = """You translate song lyrics into {language}.
You are given the lines of a song as a numbered list. Translate every line into {language}:
- by meaning, in natural {language}, the way a good subtitle would: keep the mood, the imagery and the slang;
- one line in, one line out, in the same order: never merge, split, drop or add lines;
- a line that is already {language}, a name, or a sound («la la la», «oh») stays as it is;
- no notes, no transliteration, no quotes around the lines.
Answer with JSON only: {{"lines": ["...", "..."]}} — exactly as many strings as there are numbered lines."""


async def llm(lines: list[str], target: str, *, artist: str = "", title: str = "") -> list[str]:
    """The Hub's LLM, a batch of lines at a time; each batch knows the song and the lines just before it.
    A local reasoning model is told not to reason: thinking a translation over takes it minutes and the whole
    answer, where the plain translation takes seconds and reads the same."""
    import openai

    from app.ai.llm import LLMClient, configured, extract_json_object, is_local

    if not configured():
        raise TranslateError("ИИ не настроен в Хабе (LLM_BASE_URL / LLM_API_KEY в .env)")
    language = LANGS[target][1]
    out: list[str] = []
    try:
        async with LLMClient() as client:
            extra = {"reasoning_effort": "none"} if is_local(client.base_url) else None
            for start in range(0, len(lines), LLM_BATCH):
                batch = lines[start:start + LLM_BATCH]
                before = "\n".join(lines[max(0, start - 4):start])
                user = (f"Song: {artist} — {title}\n"
                        + (f"The lines just before these (for context only, do not translate them):\n{before}\n\n" if before else "")
                        + f"Translate these {len(batch)} lines:\n"
                        + "\n".join(f"{i + 1}. {x}" for i, x in enumerate(batch)))
                got: Any = None
                for _ in range(2):  # a miscount is usually a one-off
                    resp = await client._client.chat.completions.create(
                        model=client.model, temperature=0, max_tokens=4000, extra_body=extra,
                        response_format={"type": "json_object"},
                        messages=[{"role": "system", "content": SYSTEM.format(language=language)},
                                  {"role": "user", "content": user}])
                    got = (extract_json_object(resp.choices[0].message.content or "") or {}).get("lines")
                    if _same_shape(got, len(batch)):
                        break
                if not _same_shape(got, len(batch)):
                    raise TranslateError("ИИ вернул не столько строк, сколько в тексте")
                out.extend(re.sub(r"^\d+\.\s*", "", x).strip() for x in got)
    except openai.APITimeoutError as e:
        raise TranslateError("ИИ не ответил вовремя") from e
    except openai.APIConnectionError as e:
        raise TranslateError("Нет связи с ИИ Хаба — запущен ли он?") from e
    except openai.APIError as e:
        raise TranslateError(f"ИИ ответил ошибкой ({type(e).__name__})") from e
    return out


async def translate(lines: list[str], target: str, providers: list[str], *, artist: str = "", title: str = "",
                    source: str | None = None) -> tuple[list[str], str]:
    """The lines in `target`, by the first of `providers` that manages: (translations, the provider). Empty lines
    stay empty; a line that repeats is translated once."""
    unique = list(dict.fromkeys(x.strip() for x in lines if x.strip()))
    if not unique:
        return ["" for _ in lines], providers[0] if providers else "llm"
    error: TranslateError | None = None
    for provider in providers:
        try:
            if provider == "llm":
                got = await llm(unique, target, artist=artist, title=title)
            elif provider == "google":
                got = await google(unique, target)
            elif provider == "mymemory":
                got = await mymemory(unique, target, source)
            else:
                continue
        except TranslateError as e:
            log.info("lyrics: translating with %s failed: %s", provider, e)
            error = e
            continue
        except asyncio.CancelledError:
            raise
        if _same_shape(got, len(unique)):
            done = dict(zip(unique, got, strict=True))
            return [done.get(x.strip(), "") for x in lines], provider
        error = TranslateError("Переводчик вернул не столько строк, сколько в тексте")
    raise error or TranslateError("Нет ни одного включённого переводчика")
