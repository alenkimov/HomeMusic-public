import { useSyncExternalStore } from 'react';
import type { HubEventMap, ImportDoubt, TrackOut } from '../api/types';

export function needsTrackFile(track: Pick<TrackOut, 'owned' | 'unknown' | 'unavailable'>): boolean {
  return !track.owned && !!(track.unknown || track.unavailable);
}

export function withRecordingAvailability<T extends TrackOut>(tracks: T[], { ref, unavailable }: HubEventMap['recording']): T[] {
  const changed = (track: T) => !track.owned && !!track.unavailable !== unavailable &&
    (track.ref === ref || (track.library_track_id != null && ref === `lib:${track.library_track_id}`));
  return tracks.some(changed) ? tracks.map((track) => changed(track) ? { ...track, unavailable } : track) : tracks;
}

/** A library track whose file can be selected or replaced in the file picker. */
type FilePickerIdentity = { library_track_id: number; ref?: string | null } | { library_track_id?: null; ref: string };

export type FileSought = FilePickerIdentity & {
  title: string;
  artist: string;
  has_file: boolean;
  cover_url?: string | null;
  /** a transfer's song this file is picked for: the question of «Наведение порядка» it answers */
  doubt?: ImportDoubt;
};

export function filePickerTarget(track: {
  library_track_id?: number | null;
  ref?: string | null;
  title: string;
  artist: string;
  cover_url?: string | null;
  cover_small_url?: string | null;
  doubt?: ImportDoubt | null;
}): FileSought | null {
  const file = {
    title: track.title, artist: track.artist, has_file: false, cover_url: track.cover_small_url || track.cover_url,
    doubt: track.doubt ?? undefined,
  };
  if (track.library_track_id != null) return { ...file, library_track_id: track.library_track_id, ref: track.ref };
  return track.ref && /^(dz|ym|sp):[^:]+$/.test(track.ref) ? { ...file, ref: track.ref } : null;
}

let state: FileSought | null = null;
const subs = new Set<() => void>();
const set = (next: FileSought | null) => {
  state = next;
  subs.forEach((f) => f());
};

export const useFindFile = (): FileSought | null =>
  useSyncExternalStore(
    (cb) => {
      subs.add(cb);
      return () => void subs.delete(cb);
    },
    () => state,
    () => state,
  );
export const openFindFile = (track: FileSought) => set(track);
export const closeFindFile = () => set(null);
