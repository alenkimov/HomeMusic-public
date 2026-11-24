import { useSyncExternalStore, type MouseEvent as RMouse } from 'react';
import type { MenuExtra, MenuTrack } from '../components/TrackMenu';
import type { MenuAt } from '../components/ContextMenu';

/** The one menu of a track (`components/TrackMenu.tsx`), drawn once for the whole page: the ⋯ of a row, a right
 * click on it and the players' ⋯ all open it, so a track has the same menu wherever it is. */
export interface TrackMenuState {
  track: MenuTrack;
  extra?: MenuExtra[];
  /** where a computer's menu opens; none — a sheet */
  at?: MenuAt;
  /** opened from a player: a wave started here is the player's */
  inPlayer?: boolean;
}

let state: TrackMenuState | null = null;
const subs = new Set<() => void>();
const set = (next: TrackMenuState | null) => {
  state = next;
  subs.forEach((f) => f());
};

export const useTrackMenu = (): TrackMenuState | null =>
  useSyncExternalStore(
    (cb) => {
      subs.add(cb);
      return () => void subs.delete(cb);
    },
    () => state,
    () => state,
  );
export const openTrackMenu = (next: TrackMenuState) => set(next);
export const closeTrackMenu = () => set(null);

/** A right click on whatever shows the track (a row, the player's title): its menu at the mouse. */
export const trackMenuAt = (track: MenuTrack, extra?: MenuExtra[], inPlayer?: boolean) => (e: RMouse) => {
  if ((e.target as HTMLElement).closest('input, textarea, [contenteditable=true]')) return; // a field keeps its own menu
  e.preventDefault();
  e.stopPropagation();
  openTrackMenu({ track, extra, inPlayer, at: { x: e.clientX, y: e.clientY } });
};
