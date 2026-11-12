import { useSyncExternalStore } from 'react';

/** «В плейлисты» of several tracks opened from anywhere (a drag down the rows' ♥ / ✓ buttons): the window itself is
 * `BulkPlaylists` (components/PlaylistsButton.tsx), drawn once by `components/Overlays.tsx`. */

export interface SheetTrack {
  ref: string;
  nd_song_id?: string | null;
  data?: Record<string, unknown>;
}

let tracks: SheetTrack[] = [];
const subs = new Set<() => void>();
const set = (next: SheetTrack[]) => {
  tracks = next;
  subs.forEach((f) => f());
};
const subscribe = (cb: () => void) => {
  subs.add(cb);
  return () => void subs.delete(cb);
};

export const usePlaylistsSheet = (): SheetTrack[] => useSyncExternalStore(subscribe, () => tracks, () => tracks);
export const openPlaylistsSheet = (list: SheetTrack[]) => set(list);
export const closePlaylistsSheet = () => set([]);
