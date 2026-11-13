import { useEffect, useState, useSyncExternalStore, type CSSProperties, type InputHTMLAttributes, type PointerEvent } from 'react';
import { seek } from './listen';

/** The seek bar of a player — the bar along the bottom of a computer, the mini player, the full player: one behaviour
 * for the three. The track is sought when the handle is let go; while it is dragged, and while a mouse is over the
 * bar, the place aimed at is known (`aim`) — `components/SeekLine.tsx` shows the line of the text sung there. */

let scrubAt: number | null = null;
const subs = new Set<() => void>();
const subscribe = (fn: () => void) => {
  subs.add(fn);
  return () => void subs.delete(fn);
};
function setScrubAt(v: number | null) {
  if (scrubAt === v) return;
  scrubAt = v;
  subs.forEach((f) => f());
}

/** Where the handle is being dragged to, in seconds (null: it is not dragged): the open text goes there with it. */
export const useScrubAt = (): number | null => useSyncExternalStore(subscribe, () => scrubAt);

export interface Seek {
  /** the position to show: where the handle is while it is dragged, else where the track is */
  shownPos: number;
  /** seconds the listener aims at — dragging, or a mouse over the bar; null: neither */
  aim: number | null;
  /** `aim` along the bar, 0–100 */
  aimPct: number;
  dragging: boolean;
  /** for the `<input className="hm-range">` */
  range: InputHTMLAttributes<HTMLInputElement>;
}

const THUMB = 12; // px, as in index.css: the handle's centre does not reach the bar's ends

export function useSeek(position: number, duration: number): Seek {
  const [scrub, setScrub] = useState<number | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, Math.round(duration));
  const shownPos = scrub ?? Math.min(position, duration || position);
  const pct = duration ? Math.min(100, (shownPos / duration) * 100) : 0;
  const aim = duration ? (scrub ?? hover) : null;
  useEffect(() => {
    setScrubAt(scrub);
  }, [scrub]);
  useEffect(() => () => setScrubAt(null), []);
  const commit = () => {
    if (scrub !== null) seek(scrub);
    setScrub(null);
  };
  const over = (e: PointerEvent<HTMLInputElement>) => {
    if (e.pointerType !== 'mouse') return; // a finger does not hover: it drags
    const r = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - r.left - THUMB / 2) / Math.max(1, r.width - THUMB);
    setHover(Math.round(Math.min(1, Math.max(0, x)) * max));
  };
  return {
    shownPos,
    aim,
    aimPct: aim === null ? 0 : Math.min(100, (aim / max) * 100),
    dragging: scrub !== null,
    range: {
      type: 'range',
      'aria-label': 'Перемотка',
      min: 0,
      max,
      step: 1,
      value: Math.round(shownPos),
      style: { '--p': `${pct}%` } as CSSProperties,
      onChange: (e) => setScrub(Number(e.target.value)),
      onPointerUp: commit,
      onKeyUp: commit,
      onBlur: commit,
      onPointerMove: over,
      onPointerLeave: () => setHover(null),
    },
  };
}
