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
  spotify: boolean;
  lastfm: boolean;
  /** the file: its source's mark, what it is, its bitrate */
  file: boolean;
  duration: boolean;
}

export const TRACK_COLUMN_LABELS: Record<keyof TrackColumns, string> = {
  album: 'Альбом', tagLang: 'Язык', tagGenre: 'Жанр', tagOwn: 'Теги', added: 'Дата добавления',
  plays: 'Мои прослушивания', spotify: 'Прослушивания в Spotify', lastfm: 'Слушатели Last.fm',
  file: 'Файл', duration: 'Длительность',
};

const KEY = 'hm-track-columns';
/** Shown until the user says otherwise: the tags and the listens are for those who want them. */
const DEFAULTS: TrackColumns = {
  album: true, tagLang: false, tagGenre: false, tagOwn: false, added: true, plays: false,
  spotify: true, lastfm: true, file: true, duration: true,
};

function load(): TrackColumns {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<TrackColumns>;
    return {
      album: saved.album !== false, tagLang: saved.tagLang === true, tagGenre: saved.tagGenre === true, tagOwn: saved.tagOwn === true,
      added: saved.added !== false, plays: saved.plays === true, file: saved.file !== false, duration: saved.duration !== false,
      spotify: saved.spotify !== false, lastfm: saved.lastfm !== false,
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

type Column = keyof TrackColumns;
export type TrackTableKind = 'library' | 'album' | 'popular';

const widths = {
  album: 8, tagLang: 6, tagGenre: 6.5, tagOwn: 9, added: 8.5, plays: 7.5,
  spotify: 5.5, lastfm: 5.5, file: 6.5, duration: 3,
} satisfies Record<Column, number>;
const leading: readonly Column[] = ['album', 'tagLang', 'tagGenre', 'tagOwn', 'added', 'plays', 'spotify', 'lastfm', 'file'];
const albumOrder: readonly (readonly Column[])[] = [['lastfm'], ['spotify'], ['file'], ['duration']];
const libraryOrder: readonly (readonly Column[])[] = [
  ['tagLang', 'tagGenre', 'tagOwn'], ['lastfm'], ['spotify'], ['plays'], ['file'], ['added'], ['album'], ['duration'],
];

export function trackGridMinWidth(numbered: boolean, columns?: TrackColumns): number {
  const optional = columns ? (Object.keys(widths) as Column[]).filter((key) => columns[key]) : [];
  // The row and header both have mx-2, px-2 and gap-3. Include the title, actions and menu.
  return 2 + 11 + 7 + 2 + (numbered ? 2 : 0) + optional.reduce((sum, key) => sum + widths[key], 0)
    + (2 + Number(numbered) + optional.length) * 0.75;
}

export function fitTrackColumns(width: number, numbered: boolean, enabled: TrackColumns, kind: TrackTableKind): TrackColumns {
  const visible = { ...enabled };
  for (const group of kind === 'library' ? libraryOrder : albumOrder) {
    if (trackGridMinWidth(numbered, visible) <= width) break;
    for (const column of group) visible[column] = false;
  }
  return visible;
}

/** One grid for the header and every row, so the columns line up. */
export function trackGrid(numbered: boolean, columns: TrackColumns): string {
  const middle = leading.filter((key) => columns[key]).map((key) => key === 'album' ? 'minmax(8rem, 11rem)' : `${widths[key]}rem`);
  return [numbered && '2rem', 'minmax(11rem, 1.6fr)', ...middle, '7rem', columns.duration && '3rem', '2rem'].filter(Boolean).join(' ');
}
