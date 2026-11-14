import { useSyncExternalStore } from 'react';

/** The optional columns of the track table on a computer (`TrackTableHeader`, `TrackRow`): the user switches them
 * in the header's menu, the choice is kept in this browser. */
export interface TrackColumns {
  album: boolean;
  tagLang: boolean;
  tagGenre: boolean;
  tagOwn: boolean;
  added: boolean;
  plays: boolean;
  /** the file: its source's mark, what it is, its bitrate */
  file: boolean;
  duration: boolean;
}

export const TRACK_COLUMN_LABELS: Record<keyof TrackColumns, string> = { album: 'Альбом', tagLang: 'Язык', tagGenre: 'Жанр', tagOwn: 'Теги', added: 'Дата добавления', plays: 'Прослушивания', file: 'Файл', duration: 'Длительность' };

const KEY = 'hm-track-columns';
/** Shown until the user says otherwise: the tags and the listens are for those who want them. */
const DEFAULTS: TrackColumns = { album: true, tagLang: false, tagGenre: false, tagOwn: false, added: true, plays: false, file: true, duration: true };

function load(): TrackColumns {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<TrackColumns>;
    return {
      album: saved.album !== false, tagLang: saved.tagLang === true, tagGenre: saved.tagGenre === true, tagOwn: saved.tagOwn === true,
      added: saved.added !== false, plays: saved.plays === true, file: saved.file !== false, duration: saved.duration !== false,
    };
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
export function trackGrid(numbered: boolean, album: boolean, duration: boolean, added = false, tags: { lang: boolean; genre: boolean; own: boolean } | null = null, plays = false, file = false): string {
  return [numbered ? '2rem' : null, 'minmax(11rem,1.6fr)', album ? (tags ? 'minmax(0,10rem)' : 'minmax(0,12rem)') : null,
    tags?.lang ? 'minmax(0,6rem)' : null, tags?.genre ? 'minmax(0,6.5rem)' : null, tags?.own ? 'minmax(0,9rem)' : null,
    added ? '8.5rem' : null, plays ? '6.5rem' : null, file ? '6.5rem' : null, '7rem', duration ? '3rem' : null, '2rem']
    .filter(Boolean)
    .join(' ');
}
