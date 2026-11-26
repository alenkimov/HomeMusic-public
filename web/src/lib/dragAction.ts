import { useSyncExternalStore } from 'react';
import { tracksWord } from './format';
import { addToQueue, manualQueueKey, queuedRefs, unqueue } from './listen';
import { openPlaylistsSheet } from './playlistsSheet';
import { playable, selData, type SelTrack } from './selection';
import { openShare } from './shareTrack';
import { inPlaylists } from './inPlaylists';
import { isLiked, setLikes } from './stars';
import { openTagsSheet } from './tags';
import { toast } from './ui';

/** A row's «+», «в очередь» or «поделиться» held and dragged over other rows of the list: the same button of every
 * row passed is marked, and the action is done to all of them when the mouse comes up (`components/catalog.tsx`
 * starts it, `components/Overlays.tsx` says what will happen). Nothing is done before that: Esc, or the mouse coming
 * up outside the rows, calls it off. A press without a drag is the button's own click.
 * Started on «+», the drag puts into «Любимое» the tracks passed that are kept nowhere (one with ♥ or ✓ is left as
 * it is). Started on ♥ or ✓ — a track that is kept already — it opens the playlists for all the tracks passed, as
 * a click on that button does for one. The queue: started on a queued track, the drag takes the tracks out of the
 * queue. */

export type DragKind = 'like' | 'lists' | 'queue' | 'unqueue' | 'share' | 'tags';

interface Drag {
  kind: DragKind;
  list: readonly SelTrack[];
  from: string;
  to: string;
}

let drag: Drag | null = null;
let marks = new Map<string, DragKind>();
let version = 0;
const subs = new Set<() => void>();
const subscribe = (cb: () => void) => {
  subs.add(cb);
  return () => void subs.delete(cb);
};

function range(d: Drag): SelTrack[] {
  const a = d.list.findIndex((x) => x.ref === d.from);
  const b = d.list.findIndex((x) => x.ref === d.to);
  return a < 0 || b < 0 ? [] : d.list.slice(Math.min(a, b), Math.max(a, b) + 1);
}

function emit() {
  const picked = drag ? range(drag) : [];
  // one row is a click, not a drag: nothing is marked
  marks = new Map(drag && picked.length > 1 ? picked.map((t) => [t.ref, drag!.kind]) : []);
  version++;
  subs.forEach((f) => f());
}

const likeTarget = (t: SelTrack) => ({ trackId: t.tag_key ? Number(t.tag_key) : null, songId: t.nd_song_id, ref: t.ref, data: selData(t) });
/** In «Любимое» or in some playlist: the row's button shows ♥ or ✓. */
const kept = (t: SelTrack) => isLiked(likeTarget(t)) || inPlaylists(t.nd_song_id);

async function apply(kind: DragKind, tracks: SelTrack[]) {
  if (kind === 'share') return openShare(tracks);
  if (kind === 'tags') {
    const keys = tracks.map((t) => t.tag_key).filter((x): x is string => !!x);
    return keys.length ? openTagsSheet(keys, '') : toast('Хаб ещё не знает эти треки — добавьте их в плейлист или «Любимое»', 'info');
  }
  if (kind === 'queue') {
    const have = queuedRefs();
    const add = playable(tracks).filter((t) => !have.has(manualQueueKey(t)));
    if (!add.length) return toast('Эти треки уже в очереди', 'info');
    const n = await addToQueue(add);
    if (n) toast(`В очереди: ${tracksWord(n)}`, 'success');
    return;
  }
  if (kind === 'unqueue') {
    const n = await unqueue(playable(tracks));
    return toast(n ? `Убрано из очереди: ${tracksWord(n)}` : 'Этих треков в очереди нет', n ? 'success' : 'info');
  }
  if (kind === 'lists') return openPlaylistsSheet(tracks.map((t) => ({ ref: t.ref, nd_song_id: t.nd_song_id, key: t.tag_key, data: selData(t) })));
  const n = await setLikes(tracks.filter((t) => !kept(t)).map(likeTarget), true); // one request: the buttons fill together
  toast(n ? `Добавлено в «Любимое»: ${tracksWord(n)}` : 'Все эти треки уже где-то лежат', n ? 'success' : 'info');
}

export function cancelDragAction() {
  if (!drag) return;
  drag = null;
  document.body.style.userSelect = '';
  window.removeEventListener('mouseup', up, true);
  window.removeEventListener('keydown', key, true);
  emit();
}

function up(e: MouseEvent) {
  const d = drag;
  if (!d) return;
  const picked = range(d);
  cancelDragAction();
  if (picked.length < 2) return; // a click
  if (!(e.target instanceof Element) || !e.target.closest('[data-track-row]')) return; // let go outside the list
  void apply(d.kind, picked);
}

function key(e: KeyboardEvent) {
  if (e.key !== 'Escape' || !drag) return;
  e.stopPropagation();
  cancelDragAction();
}

/** The mouse went down on a row's button. */
export function beginDragAction(kind: 'like' | 'queue' | 'share' | 'tags', list: readonly SelTrack[], t: SelTrack) {
  cancelDragAction();
  const real: DragKind = kind === 'like' && kept(t) ? 'lists' : kind === 'queue' && !!t.listen && queuedRefs().has(manualQueueKey(t.listen)) ? 'unqueue' : kind;
  drag = { kind: real, list, from: t.ref, to: t.ref };
  document.body.style.userSelect = 'none';
  window.addEventListener('mouseup', up, true);
  window.addEventListener('keydown', key, true);
}

/** The mouse is over a row. */
export function dragActionOver(t: SelTrack) {
  if (!drag || drag.to === t.ref || !drag.list.some((x) => x.ref === t.ref)) return;
  drag.to = t.ref;
  emit();
}

/** What is about to be done to this row, if it is among the rows dragged over. */
export const useDragMark = (ref: string): DragKind | null =>
  useSyncExternalStore(subscribe, () => marks.get(ref) ?? null, () => null);

/** The drag as a whole, for the line that says what letting go will do. */
export function useDragAction(): { kind: DragKind; count: number } | null {
  useSyncExternalStore(subscribe, () => version, () => 0);
  return drag && marks.size ? { kind: drag.kind, count: marks.size } : null;
}
