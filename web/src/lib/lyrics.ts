import { useSyncExternalStore } from 'react';

/** Lyrics of a library track as the Hub keeps them (`/api/lyrics`, hub `app/lyrics`). */

export interface LyricsWord {
  /** ms from the start of the track */
  t: number;
  text: string;
}

export interface LyricsLine {
  t: number;
  text: string;
  end?: number;
  /** only where the source times the parts of the line */
  words?: LyricsWord[];
}

export type LyricsKind = 'word' | 'synced' | 'plain' | 'none';

export interface LyricsOut {
  /** null: the track is not in the Hub's library */
  track_id: number | null;
  kind: LyricsKind;
  source?: string | null;
  source_title?: string | null;
  lines?: LyricsLine[] | null;
  plain?: string | null;
  offset_ms: number;
  pinned: boolean;
  /** how the text is read, for a Japanese, Chinese or Korean one */
  reading?: LyricsReading | null;
}

export interface LyricsReading {
  lang: string;
  /** Latin letters, line for line with the lyrics ("" where there is nothing to read) */
  lines: string[];
  /** Japanese: each line cut into pieces [text, its kana or null] — the kana goes over the kanji */
  ruby?: [string, string | null][][];
}

export interface LyricsCandidate {
  source: string;
  source_title: string;
  kind: LyricsKind;
  preview: string;
}

export interface LyricsSettings {
  sources: { id: string; title: string; on: boolean }[];
  retry_days: number;
  on_add: boolean;
  stats: { total: number; word: number; synced: number; plain: number; none: number; unchecked: number };
}

export const KIND_LABEL: Record<LyricsKind, string> = { word: 'по словам', synced: 'по строкам', plain: 'без таймингов', none: 'нет текста' };

/** The line that is sung at `ms` (-1: before the first one). */
export function activeLine(lines: readonly LyricsLine[], ms: number): number {
  let lo = 0, hi = lines.length - 1, at = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].t <= ms) {
      at = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return at;
}

/** Where the singing stops for a while (an intro, a solo): shown as a countdown instead of a line that hangs lit.
 * A line with no end of its own is taken to last as long as its words would take to sing. */
export const BREAK_MS = 7000;

export function breakAt(lines: readonly LyricsLine[], at: number, ms: number): { left: number } | null {
  const next = lines[at + 1];
  if (!next) return null;
  if (at < 0) return next.t >= BREAK_MS && ms < next.t ? { left: next.t - ms } : null;
  const line = lines[at];
  const sung = line.end ?? line.t + Math.min(6000, 1500 + line.text.length * 120);
  return next.t - sung >= BREAK_MS && ms >= sung ? { left: next.t - ms } : null;
}

// --- the look of the text -------------------------------------------------------------------------------

/** How this listener wants the text to look (kept in the browser). Things to try on and take off: each is a
 * switch, off until switched on. */
export interface LyricsLook {
  /** Latin letters under a line of a Japanese, Chinese or Korean text */
  reading: boolean;
  /** kana over the kanji of a Japanese text */
  furigana: boolean;
  /** the lines away from the one that is sung are blurred */
  blur: boolean;
  /** the page's light: the cover blurred and still ("cover"), or its colours drifting ("drift") */
  background: 'cover' | 'drift';
  /** the text takes the whole screen, which stays awake */
  fullscreen: boolean;
}

const LOOK_KEY = 'hm-lyrics-look';
const LOOK_DEFAULT: LyricsLook = { reading: false, furigana: false, blur: false, background: 'cover', fullscreen: false };

function loadLook(): LyricsLook {
  try {
    const v = JSON.parse(localStorage.getItem(LOOK_KEY) ?? '{}') as Partial<LyricsLook>;
    return { reading: v.reading === true, furigana: v.furigana === true, blur: v.blur === true,
      background: v.background === 'drift' ? 'drift' : 'cover', fullscreen: false };
  } catch {
    return LOOK_DEFAULT;
  }
}

let look = loadLook();
const lookSubs = new Set<() => void>();

export function setLyricsLook(next: Partial<LyricsLook>) {
  look = { ...look, ...next };
  try {
    // the whole screen is asked for by a tap, each time: a page cannot take it by itself when it opens
    localStorage.setItem(LOOK_KEY, JSON.stringify({ ...look, fullscreen: false }));
  } catch {
    /* storage unavailable: the choice lives until the page is closed */
  }
  lookSubs.forEach((f) => f());
}

export const useLyricsLook = (): LyricsLook =>
  useSyncExternalStore(
    (cb) => {
      lookSubs.add(cb);
      return () => void lookSubs.delete(cb);
    },
    () => look,
    () => LOOK_DEFAULT,
  );

// --- translation ----------------------------------------------------------------------------------------

export interface LyricsTranslation {
  /** ready; same — the song is in that language; skipped — a language the listener reads; none — no lyrics */
  status: 'ready' | 'same' | 'skipped' | 'none';
  lang: string;
  /** the language of the lyrics, where the Hub can tell */
  detected?: string | null;
  provider?: string | null;
  provider_title?: string | null;
  /** line for line with the lyrics (`lines`, or the lines of `plain`) */
  lines: string[];
  /** the lines corrected by hand */
  edited: number[];
}

export interface LyricsTranslators {
  providers: { id: string; title: string; on: boolean }[];
  langs: { code: string; title: string }[];
  llm: boolean;
}

/** How this listener wants translations (kept in the browser): on or off, into what language, under each line or
 * in its place, and the languages they read — songs in those are left alone. */
export interface TranslatePrefs {
  on: boolean;
  lang: string;
  mode: 'below' | 'replace';
  skip: string[];
}

const PREFS_KEY = 'hm-lyrics-translate';
const PREFS_DEFAULT: TranslatePrefs = { on: false, lang: 'ru', mode: 'below', skip: [] };

function loadPrefs(): TranslatePrefs {
  try {
    const v = JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}') as Partial<TranslatePrefs>;
    return {
      on: v.on === true,
      lang: typeof v.lang === 'string' && v.lang ? v.lang : 'ru',
      mode: v.mode === 'replace' ? 'replace' : 'below',
      skip: Array.isArray(v.skip) ? v.skip.filter((x) => typeof x === 'string') : [],
    };
  } catch {
    return PREFS_DEFAULT;
  }
}

let prefs = loadPrefs();
const prefSubs = new Set<() => void>();

export function setTranslatePrefs(next: Partial<TranslatePrefs>) {
  prefs = { ...prefs, ...next };
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* storage unavailable: the choice lives until the page is closed */
  }
  prefSubs.forEach((f) => f());
}

export const useTranslatePrefs = (): TranslatePrefs =>
  useSyncExternalStore(
    (cb) => {
      prefSubs.add(cb);
      return () => void prefSubs.delete(cb);
    },
    () => prefs,
    () => PREFS_DEFAULT,
  );
