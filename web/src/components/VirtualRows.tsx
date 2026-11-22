import { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState, type ReactNode } from 'react';

/** A long list (thousands of tracks) drawn only where it is seen: the rows on screen and `overscan` screens' worth
 * around them; the rest is the room it takes (padding above and below), so the page scrolls, and is put back where
 * it was, as if every row were there. A row of 4000 on the page cost seconds to mount on a phone, and every change
 * of a store the rows read (a like, the track playing) went through all of them; the page's own layout (the mini
 * player measuring itself) was a layout of all of them.
 *
 * The page scrolls as a whole (the window), so does this list. Rows may differ in height: each drawn row is
 * measured, one not drawn yet is taken to be as tall as those measured are on average. */

/** What a row measured last, on average — the first guess of the next list (back to a page: its height is right at
 * once and the browser puts the scroll back where it was). */
let lastAverage = 0;

export function VirtualRows<T>({ items, rowKey, render, estimate = 56, overscan = 1.5, className }: {
  items: readonly T[];
  rowKey: (item: T, index: number) => string;
  render: (item: T, index: number) => ReactNode;
  /** px: a row's height before any was measured */
  estimate?: number;
  /** screens drawn above and below the one seen */
  overscan?: number;
  className?: string;
}) {
  const list = useRef<HTMLUListElement>(null);
  const heights = useRef(new Map<string, number>());
  const [measured, remeasure] = useReducer((n: number) => n + 1, 0);
  const keys = useMemo(() => items.map(rowKey), [items, rowKey]);

  // where each row begins (px from the list's top); the last entry is the list's height
  const offsets = useMemo(() => {
    const known = heights.current;
    let sum = 0;
    let count = 0;
    for (const k of keys) {
      const h = known.get(k);
      if (h !== undefined) { sum += h; count++; }
    }
    const guess = count ? sum / count : lastAverage || estimate;
    if (count) lastAverage = guess;
    const out = new Float64Array(keys.length + 1);
    for (let i = 0; i < keys.length; i++) out[i + 1] = out[i] + (known.get(keys[i]) ?? guess);
    return out;
  }, [keys, measured, estimate]);

  const [range, setRange] = useState<[number, number]>(() => [0, Math.min(items.length, 30)]);

  const place = useCallback(() => {
    const el = list.current;
    if (!el) return;
    const screen = window.innerHeight;
    const top = -el.getBoundingClientRect().top - screen * overscan;
    const bottom = top + screen * (1 + 2 * overscan);
    const n = offsets.length - 1;
    const start = Math.max(0, Math.min(n, firstAfter(offsets, top) - 1));
    const end = Math.max(start, Math.min(n, firstAfter(offsets, bottom)));
    setRange((r) => (r[0] === start && r[1] === end ? r : [start, end]));
  }, [offsets, overscan]);

  useLayoutEffect(place, [place]);
  useEffect(() => {
    let frame = 0;
    const later = () => {
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; place(); });
    };
    window.addEventListener('scroll', later, { passive: true });
    window.addEventListener('resize', later);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', later);
      window.removeEventListener('resize', later);
    };
  }, [place]);

  // each drawn row tells its height; a change of any is counted once a frame
  const observer = useMemo(() => {
    if (typeof ResizeObserver === 'undefined') return null;
    let frame = 0;
    return new ResizeObserver((entries) => {
      let changed = false;
      for (const e of entries) {
        const key = (e.target as HTMLElement).dataset.rowKey;
        const h = e.borderBoxSize?.[0]?.blockSize ?? (e.target as HTMLElement).offsetHeight;
        if (key === undefined || !h || heights.current.get(key) === h) continue;
        heights.current.set(key, h);
        changed = true;
      }
      if (changed && !frame) frame = requestAnimationFrame(() => { frame = 0; remeasure(); });
    });
  }, []);
  useEffect(() => () => observer?.disconnect(), [observer]);
  const watch = useCallback((el: HTMLLIElement | null) => {
    if (!el || !observer) return;
    observer.observe(el);
    return () => observer.unobserve(el);
  }, [observer]);

  const n = items.length;
  const start = Math.min(range[0], n);
  const end = Math.min(range[1], n);
  const rows: ReactNode[] = [];
  for (let i = start; i < end; i++) {
    rows.push(<li key={keys[i]} data-row-key={keys[i]} ref={watch}>{render(items[i], i)}</li>);
  }
  return (
    <ul ref={list} className={className} style={{ paddingTop: offsets[start], paddingBottom: offsets[n] - offsets[end] }}>
      {rows}
    </ul>
  );
}

/** The first index whose offset is beyond `y` (offsets ascending). */
function firstAfter(offsets: Float64Array, y: number): number {
  let lo = 0;
  let hi = offsets.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (offsets[mid] <= y) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}
