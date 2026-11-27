import { useEffect, useRef, useState, useSyncExternalStore, type PointerEvent as ReactPointerEvent } from 'react';
import { cx } from './ui';

/** A mouse is there: the window's own bar is hidden (index.css) and this one is drawn instead. */
const MOUSE = '(hover: hover) and (pointer: fine)';
const mql = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(MOUSE) : null;
const subscribe = (cb: () => void) => {
  mql?.addEventListener('change', cb);
  return () => mql?.removeEventListener('change', cb);
};
const useMouse = () => useSyncExternalStore(subscribe, () => !!mql?.matches, () => false);

const MIN_THUMB = 32;

/** The scrollbar of the page, as Spotify's: a translucent pill at the right edge of the page — of the panel on a
 * computer (`panel`), of the window otherwise — not the browser's bar at the edge of the window. The window still
 * scrolls (sticky bars and long lists go by it); this only shows where it is and can be dragged. It is there while
 * the page is longer than the window and the mouse is over the page (or it has just scrolled), and fades a moment
 * after, as the bars of the other boxes do (index.css); brighter under the mouse. */
export function PageScrollbar({ panel }: { panel: boolean }) {
  const mouse = useMouse();
  const track = useRef<HTMLDivElement>(null);
  const [m, setM] = useState({ top: 0, size: 0, long: false });
  const [dragging, setDragging] = useState(false);
  const thumb = useRef<HTMLDivElement>(null);
  const [over, setOver] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  // the mouse is over the page: over the panel on a computer (not the sidebar or the player), the window otherwise
  useEffect(() => {
    if (!mouse) return;
    const at = (e: PointerEvent) => {
      const t = e.target instanceof Element ? e.target : null;
      setOver(!panel || !!t?.closest('.hm-page') || t === thumb.current);
    };
    const gone = () => setOver(false);
    document.addEventListener('pointerover', at, { passive: true });
    document.documentElement.addEventListener('pointerleave', gone);
    return () => {
      document.removeEventListener('pointerover', at);
      document.documentElement.removeEventListener('pointerleave', gone);
    };
  }, [mouse, panel]);

  // and for a moment after the page scrolls, whatever scrolled it
  useEffect(() => {
    if (!mouse) return;
    let timer = 0;
    const onScroll = () => {
      setScrolled(true);
      clearTimeout(timer);
      timer = window.setTimeout(() => setScrolled(false), 800);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      clearTimeout(timer);
      window.removeEventListener('scroll', onScroll);
    };
  }, [mouse]);

  useEffect(() => {
    if (!mouse) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const el = track.current;
      if (!el) return;
      const doc = document.documentElement;
      const view = window.innerHeight;
      const full = doc.scrollHeight;
      const room = el.clientHeight;
      const long = full > view + 1;
      const size = long ? Math.max(MIN_THUMB, Math.round((room * view) / full)) : 0;
      const top = long ? Math.round(((room - size) * window.scrollY) / (full - view)) : 0;
      setM((p) => (p.top === top && p.size === size && p.long === long ? p : { top, size, long }));
    };
    const soon = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    const grows = new ResizeObserver(soon);
    grows.observe(document.body);
    window.addEventListener('scroll', soon, { passive: true });
    window.addEventListener('resize', soon);
    measure();
    return () => {
      cancelAnimationFrame(frame);
      grows.disconnect();
      window.removeEventListener('scroll', soon);
      window.removeEventListener('resize', soon);
    };
  }, [mouse]);

  if (!mouse) return null;

  const drag = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const pill = e.currentTarget;
    pill.setPointerCapture(e.pointerId);
    setDragging(true);
    const from = e.clientY;
    const start = window.scrollY;
    const room = (track.current?.clientHeight ?? 1) - m.size;
    const span = document.documentElement.scrollHeight - window.innerHeight;
    const move = (ev: PointerEvent) => window.scrollTo(0, start + ((ev.clientY - from) * span) / Math.max(1, room));
    const end = () => {
      setDragging(false);
      pill.removeEventListener('pointermove', move);
      pill.removeEventListener('pointerup', end);
      pill.removeEventListener('pointercancel', end);
    };
    pill.addEventListener('pointermove', move);
    pill.addEventListener('pointerup', end);
    pill.addEventListener('pointercancel', end);
  };

  return (
    <div
      ref={track}
      aria-hidden="true"
      className={cx(
        'pointer-events-none fixed z-30 w-3',
        // the panel's edge on a computer (it stands 8px in from the window), the window's otherwise
        panel ? 'right-[11px] top-[calc(var(--page-top)+6px)] bottom-[calc(var(--tabbar-h)+6px)]' : 'right-0.5 top-1 bottom-[calc(var(--tabbar-h)+4px)]',
      )}
    >
      <div
        ref={thumb}
        onPointerDown={drag}
        className={cx(
          'pointer-events-auto absolute inset-x-[2px] rounded-full transition-[opacity,background-color]',
          dragging ? 'bg-fg/50' : 'bg-fg/30 hover:bg-fg/50',
          // in quickly, out slowly after a moment, as the other boxes' bars
          over || scrolled || dragging ? 'opacity-100 duration-150' : 'opacity-0 delay-500 duration-300',
          !m.long && 'hidden',
        )}
        style={{ top: m.top, height: m.size }}
      />
    </div>
  );
}
