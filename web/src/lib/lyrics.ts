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
