import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api/client';

/** «Статистика» (hub `app/stats.py`): what the user listened to. The page (`pages/Stats.tsx`) asks for a period;
 * the numbers of the «Прослушивания» column — every library song's listens in all the time — are kept here. */

export type StatsPeriod = 'week' | 'month' | 'halfyear' | 'year' | 'all';
export type StatsBy = 'plays' | 'time';

export interface StatsItem {
  name: string;
  sub?: string | null;
  plays: number;
  ms: number;
  /** a library song of it (the track itself; of an artist, an album, a genre — one of theirs); none: not in the library */
  nd_song_id?: string | null;
  /** of a track: its listens in each of the last 8 weeks (the last one is this week) */
  weeks?: number[];
  /** of a track: its place in the period before this one (none: it was not there, or there is nothing to compare with) */
  was?: number | null;
  /** of a track: it was not listened to in the period before this one */
  fresh?: boolean;
}

/** One song's listens (the window of the «Прослушивания» column). */
export interface StatsTrackOut {
  plays: number;
  ms: number;
  weeks: number[];
  first_at?: string | null;
  last_at?: string | null;
}

/** The lists of «Для вас» the Hub makes by a rule every time they are opened. */
export const AUTO_PLAYLISTS = [
  { kind: 'top-month', title: 'Топ месяца', column: 'За месяц', icon: 'chart' },
  { kind: 'top-year', title: 'Топ года', column: 'За год', icon: 'chart' },
  { kind: 'top-all', title: 'Топ за всё время', column: 'Прослушивания', icon: 'chart' },
  { kind: 'forgotten', title: 'Давно не слушал', column: 'Прослушивания', icon: 'history' },
] as const;

export interface StatsAutoOut {
  kind: string;
  title: string;
  items: { nd_song_id: string; plays: number }[];
}

export interface StatsTop {
  total: number;
  items: StatsItem[];
}

export type StatsKind = 'tracks' | 'artists' | 'genres' | 'albums';

export interface StatsOut extends Record<StatsKind, StatsTop> {
  period: StatsPeriod;
  by: StatsBy;
  summary: { ms: number; plays: number; tracks: number; streak: number; skips: number; attempts: number };
  /** by the hour of the day (0–23) and the day of the week (Monday first), in the user's time zone */
  hours: number[];
  weekdays: number[];
  /** the last 26 weeks day by day: «2026-11-12» -> listens (or ms) */
  heatmap: { from: string; to: string; days: Record<string, number> };
  first_at?: string | null;
  /** listens brought in from Spotify's history */
  imported: number;
}

export interface StatsImportOut {
  found: number;
  added: number;
  duplicates: number;
  linked: number;
  outside: number;
  listens: number;
}

export const timeZone = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
};

// --- the «Прослушивания» column ----------------------------------------------------------------------------

let counts: Record<string, number> = {};
let loaded = false;
let loading: Promise<void> | null = null;
let at = 0;
const subs = new Set<() => void>();
const FRESH_MS = 60_000;

export function refreshPlayCounts(): Promise<void> {
  loading ??= api
    .statsCounts()
    .then(
      (data) => {
        counts = data;
        loaded = true;
        at = Date.now();
        subs.forEach((f) => f());
      },
      () => undefined,
    )
    .finally(() => {
      loading = null;
    });
  return loading;
}

/** Navidrome song id -> listens; asked again when a list is opened a minute or more after the last time. */
export function usePlayCounts(enabled = true): Record<string, number> {
  useEffect(() => {
    if (enabled && (!loaded || Date.now() - at > FRESH_MS)) void refreshPlayCounts();
  }, [enabled]);
  return useSyncExternalStore(
    (cb) => {
      subs.add(cb);
      return () => void subs.delete(cb);
    },
    () => counts,
    () => counts,
  );
}

export const playCount = (ndSongId?: string | null): number => (ndSongId ? (counts[ndSongId] ?? 0) : 0);
