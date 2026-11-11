import { useSyncExternalStore } from 'react';
import type { ShareTrack } from './telegram';

/** «Поделиться» (components/ShareSheet.tsx): the one window every share button opens — the chats of the user's
 * Telegram bot and, for a single track, its links on music services. */

let tracks: readonly ShareTrack[] = [];
const subs = new Set<() => void>();
const set = (next: readonly ShareTrack[]) => {
  tracks = next;
  subs.forEach((f) => f());
};
const subscribe = (cb: () => void) => {
  subs.add(cb);
  return () => void subs.delete(cb);
};

export const useShare = (): readonly ShareTrack[] => useSyncExternalStore(subscribe, () => tracks, () => tracks);
export const openShare = (list: ShareTrack | readonly ShareTrack[]) =>
  set((Array.isArray(list) ? list : [list as ShareTrack]).filter((t) => t.nd_song_id || t.ref));
export const closeShare = () => set([]);
