import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api/client';

/** Tags of the library's tracks (hub `app/tags.py`): «Язык» and «Жанр» the Hub sets by itself, «Теги» are the
 * user's own. The whole of it — the tags and which song has which — is loaded once and kept here, so a list filters
 * itself without asking the Hub (`passes`), and a change shows everywhere at once. */

export interface Tag {
  id: number;
  group: 'lang' | 'genre' | 'own';
  name: string;
  code?: string | null;
  /** library tracks that have it */
  count: number;
}

export interface TagGroup {
  id: 'lang' | 'genre' | 'own';
  title: string;
  tags: Tag[];
}

export interface TagsData {
  groups: TagGroup[];
  /** Navidrome song id -> the ids of its tags */
  map: Record<string, number[]>;
}

interface State extends TagsData {
  loaded: boolean;
  byId: Map<number, Tag>;
}

let state: State = { groups: [], map: {}, loaded: false, byId: new Map() };
let loading: Promise<void> | null = null;
const subs = new Set<() => void>();
const subscribe = (cb: () => void) => {
  subs.add(cb);
  return () => void subs.delete(cb);
};

export function setTagsData(data: TagsData) {
  state = { ...data, loaded: true, byId: new Map(data.groups.flatMap((g) => g.tags.map((t) => [t.id, t] as const))) };
  subs.forEach((f) => f());
}

export function refreshTags(): Promise<void> {
  loading ??= api
    .tags()
    .then(setTagsData, () => undefined)
    .finally(() => {
      loading = null;
    });
  return loading;
}

export function useTags(): State {
  useEffect(() => {
    if (!state.loaded) void refreshTags();
  }, []);
  return useSyncExternalStore(subscribe, () => state, () => state);
}

// --- the filter of a list --------------------------------------------------------------------------------

/** What a list is narrowed to: a tag's id (or `untagged` — no tags of one's own) -> wanted or left out. */
export type TagFilter = Record<string, 'yes' | 'no'>;
export const UNTAGGED = 'untagged';

export const filterActive = (f: TagFilter): boolean => Object.keys(f).length > 0;

/** A track with these tags passes: it has none of the tags left out; and of every group that has wanted tags, at
 * least one of them (so «японский» + «chill» is both, «Rock» + «Jazz» is either). */
export function passes(tagIds: readonly number[] | undefined, filter: TagFilter, byId: ReadonlyMap<number, Tag>): boolean {
  const have = new Set(tagIds ?? []);
  const noOwn = ![...have].some((id) => byId.get(id)?.group === 'own');
  const has = (key: string) => (key === UNTAGGED ? noOwn : have.has(Number(key)));
  const wanted: Record<string, boolean> = {};
  for (const [key, mode] of Object.entries(filter)) {
    if (mode === 'no') {
      if (has(key)) return false;
      continue;
    }
    const group = key === UNTAGGED ? 'own' : (byId.get(Number(key))?.group ?? 'own');
    wanted[group] = wanted[group] || has(key);
  }
  return Object.values(wanted).every(Boolean);
}

// --- the window that puts tags on tracks -------------------------------------------------------------------

interface SheetState {
  songIds: string[];
  title: string;
}

let sheet: SheetState | null = null;
const sheetSubs = new Set<() => void>();

export const useTagsSheet = (): SheetState | null =>
  useSyncExternalStore(
    (cb) => {
      sheetSubs.add(cb);
      return () => void sheetSubs.delete(cb);
    },
    () => sheet,
    () => null,
  );

const setSheet = (next: SheetState | null) => {
  sheet = next;
  sheetSubs.forEach((f) => f());
};

/** «Теги» of one track or of several picked ones. */
export const openTagsSheet = (songIds: string[], title: string) => songIds.length && setSheet({ songIds, title });
export const closeTagsSheet = () => setSheet(null);
