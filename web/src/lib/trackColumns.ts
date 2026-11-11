import { useSyncExternalStore } from 'react';

/** The optional columns of the track table on a computer (`TrackTableHeader`, `TrackRow`): the user switches them
 * in the header's menu, the choice is kept in this browser. */
export interface TrackColumns {
  album: boolean;
  added: boolean;
  duration: boolean;
}

export const TRACK_COLUMN_LABELS: Record<keyof TrackColumns, string> = { album: 'Альбом', added: 'Дата добавления', duration: 'Длительность' };

const KEY = 'hm-track-columns';
const DEFAULTS: TrackColumns = { album: true, added: true, duration: true };

function load(): TrackColumns {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<TrackColumns>;
    return { album: saved.album !== false, added: saved.added !== false, duration: saved.duration !== false };
  } catch {
    return DEFAULTS;
  }
}

let state = load();
const subs = new Set<() => void>();

export function setTrackColumn(name: keyof TrackColumns, on: boolean) {
  state = { ...state, [name]: on };
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    /* storage unavailable: the choice lives until the page is closed */
  }
  subs.forEach((f) => f());
}

const subscribe = (cb: () => void) => {
  subs.add(cb);
  return () => void subs.delete(cb);
};

export const useTrackColumns = (): TrackColumns => useSyncExternalStore(subscribe, () => state, () => DEFAULTS);

/** One grid for the header and every row, so the columns line up. */
export function trackGrid(numbered: boolean, album: boolean, duration: boolean, added = false): string {
  return [numbered ? '2rem' : null, 'minmax(0,1fr)', album ? 'minmax(0,12rem)' : null, added ? '8.5rem' : null, '7rem', duration ? '3rem' : null, '2rem']
    .filter(Boolean)
    .join(' ');
}
