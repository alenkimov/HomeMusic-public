import { useSyncExternalStore } from 'react';
import type { ListenTrack } from './listen';

/** Tracks picked in a list to act on together (`components/SelectionBar.tsx`: ♥, playlists, queue, share). On a
 * computer a click on a row's number picks it and a drag from the number down or up picks the rows passed; Ctrl+click
 * picks a row and Shift+click the rows up to it; anywhere «Выбрать» of a row's ⋯ menu starts it, and while something
 * is picked a plain click on a row picks instead of playing. Leaving the page drops it. */
export interface SelTrack {
  ref: string;
  title: string;
  nd_song_id?: string | null;
  /** what the track is called for tags (`lib/tags.ts` `tagKey`) */
  tag_key?: string | null;
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

// --- a drag from a row's ⋯ over other rows -----------------------------------------------------------

let drag: { list: readonly SelTrack[]; from: string; base: Map<string, SelTrack>; moved: boolean; unpick: boolean } | null = null;

/** The mouse went down on a row's ⋯: rows it passes until it comes up are picked, added to what was picked.
 * Begun on a row that is picked, the drag takes the rows it passes out instead. */
export function beginDragSelect(list: readonly SelTrack[], t: SelTrack) {
  drag = { list, from: t.ref, base: new Map(picked), moved: false, unpick: picked.has(t.ref) };
  // the click that follows the mouse coming up still has to know whether it was a drag
  window.addEventListener('mouseup', () => window.setTimeout(() => (drag = null), 0), { once: true });
}

export function dragSelectOver(t: SelTrack) {
  if (!drag || (t.ref === drag.from && !drag.moved)) return;
  const a = drag.list.findIndex((x) => x.ref === drag!.from);
  const b = drag.list.findIndex((x) => x.ref === t.ref);
  if (a < 0 || b < 0) return;
  drag.moved = true;
  picked = new Map(drag.base);
  for (const x of drag.list.slice(Math.min(a, b), Math.max(a, b) + 1)) {
    if (drag.unpick) picked.delete(x.ref);
    else picked.set(x.ref, x);
  }
  anchor = drag.from;
  emit();
}

export const dragSelectMoved = (): boolean => !!drag?.moved;

export const selectedTracks = (): SelTrack[] => [...picked.values()];
export const useSelected = (ref: string): boolean => useSyncExternalStore(subscribe, () => picked.has(ref), () => false);
export const useSelectionCount = (): number => useSyncExternalStore(subscribe, () => picked.size, () => 0);
