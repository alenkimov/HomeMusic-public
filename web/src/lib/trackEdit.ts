import { useSyncExternalStore } from 'react';

/** The window that puts a track's name and cover right (`components/TrackEdit.tsx`): asked for from a track's menu,
 * drawn once over everything. The track is named the way a row knows it — the Hub's id, the catalog's ref or
 * Navidrome's song. */
export interface EditedTrack {
  title: string;
  artist: string;
  library_track_id?: number | null;
  ref?: string | null;
  nd_song_id?: string | null;
}

let state: EditedTrack | null = null;
const subs = new Set<() => void>();
const set = (next: EditedTrack | null) => {
  state = next;
  subs.forEach((f) => f());
};

export const useTrackEdit = (): EditedTrack | null =>
  useSyncExternalStore(
    (cb) => {
      subs.add(cb);
      return () => void subs.delete(cb);
    },
    () => state,
    () => state,
  );
export const openTrackEdit = (track: EditedTrack) => set(track);
export const closeTrackEdit = () => set(null);

/** A track or an album was put right: the lists drawn from memory are stale (`lib/hooks.ts` asks again). */
export const EDITED_EVENT = 'hm:track-edited';
