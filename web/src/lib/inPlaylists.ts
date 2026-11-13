import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api/client';
import { hubEvents } from './events';

/** The tracks (the Hub's ids, `lib/tags.ts` `tagKey`) that are in some playlist of the user's («Микс дня» apart): what the «+ / ✓» of a track row
 * goes by (`components/PlaylistsButton.tsx`). One request for the whole library, asked again when a playlist changes. */

let ids = new Set<string>();
let version = 0;
let loadedAt = 0;
let loading: Promise<void> | null = null;
let again = false;
const subs = new Set<() => void>();
const REFRESH_MS = 60_000;

const subscribe = (fn: () => void) => {
  subs.add(fn);
  return () => void subs.delete(fn);
};
function emit() {
  version++;
  subs.forEach((f) => f());
}

export function refreshInPlaylists(force = false): Promise<void> {
  if (loading) {
    if (force) again = true;
    return loading;
  }
  if (!force && Date.now() - loadedAt < REFRESH_MS) return Promise.resolve();
  loading = api
    .playlistMembers(AbortSignal.timeout(15_000))
    .then(
      (r) => {
        ids = new Set(r.track_ids.map(String));
        loadedAt = Date.now();
        emit();
      },
      () => undefined, // offline, or an older Hub: what is known stays
    )
    .finally(() => {
      loading = null;
      if (again) {
        again = false;
        void refreshInPlaylists(true);
      }
    });
  return loading;
}

/** Known at once after a change made here, before the Hub is asked again. */
export function noteInPlaylists(songIds: readonly string[], inSome: boolean) {
  if (!inSome) return void refreshInPlaylists(true); // taken out of one, it may still be in another
  const next = new Set(ids);
  songIds.forEach((x) => next.add(x));
  ids = next;
  emit();
  void refreshInPlaylists(true);
}

export const inPlaylists = (songId?: string | null): boolean => !!songId && ids.has(songId);

export function useInPlaylists(songId?: string | null): boolean {
  useEffect(() => void refreshInPlaylists(), []);
  useSyncExternalStore(subscribe, () => version);
  return inPlaylists(songId);
}

hubEvents.on('library', () => void refreshInPlaylists(true));
hubEvents.on('reconnect', () => void refreshInPlaylists(true));

export function resetInPlaylists() {
  ids = new Set();
  loadedAt = 0;
  emit();
}
