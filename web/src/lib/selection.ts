import { useSyncExternalStore } from 'react';
import type { ListenTrack } from './listen';

/** Tracks picked in a list to act on together (`components/SelectionBar.tsx`: send to Telegram, queue). On a
 * computer Ctrl+click picks a row and Shift+click the rows up to it; anywhere «Выбрать» of a row's ⋯ menu starts
 * it, and while something is picked a plain click on a row picks instead of playing. Leaving the page drops it. */
export interface SelTrack {
  ref: string;
  title: string;
  nd_song_id?: string | null;
  listen: ListenTrack;
}

let picked = new Map<string, SelTrack>();
let anchor: string | null = null;
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());
const subscribe = (cb: () => void) => {
  subs.add(cb);
  return () => void subs.delete(cb);
};

export function toggleSelected(t: SelTrack) {
  picked = new Map(picked);
  if (picked.has(t.ref)) picked.delete(t.ref);
  else picked.set(t.ref, t);
  anchor = t.ref;
  emit();
}

/** Shift+click: everything of `list` between the row picked last and `to`, both included. */
export function selectRange(list: readonly SelTrack[], to: SelTrack) {
  const a = anchor ? list.findIndex((t) => t.ref === anchor) : -1;
  const b = list.findIndex((t) => t.ref === to.ref);
  if (a < 0 || b < 0) return toggleSelected(to);
  picked = new Map(picked);
  for (const t of list.slice(Math.min(a, b), Math.max(a, b) + 1)) picked.set(t.ref, t);
  emit();
}

export function clearSelection() {
  if (!picked.size) return;
  picked = new Map();
  anchor = null;
  emit();
}

export const selectedTracks = (): SelTrack[] => [...picked.values()];
export const useSelected = (ref: string): boolean => useSyncExternalStore(subscribe, () => picked.has(ref), () => false);
export const useSelectionCount = (): number => useSyncExternalStore(subscribe, () => picked.size, () => 0);
