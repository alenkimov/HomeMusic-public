import { useEffect, type RefObject } from 'react';

type TopPull = {
  ready: () => boolean;
  move: (distance: number) => void;
  end: (distance: number) => void;
};

/** Let the browser handle ordinary scrolling. Take over a downward gesture that starts at the top. */
export function listenTopPull(element: HTMLElement, { ready, move, end }: TopPull) {
  let touch: { x: number; y: number; distance: number; pulling: boolean } | null = null;
  const release = (cancelled: boolean) => {
    const drag = touch;
    touch = null;
    if (drag?.pulling) end(cancelled ? 0 : drag.distance);
  };
  const start = (e: TouchEvent) => {
    if (e.touches.length !== 1) {
      release(true);
      return;
    }
    if (!ready()) return;
    const ownGesture = e.target instanceof Element
      ? e.target.closest('input, textarea, select, [contenteditable="true"], [data-pull-ignore], [role="dialog"]')
      : null;
    if (ownGesture && element.contains(ownGesture)) return;
    const { clientX: x, clientY: y } = e.touches[0];
    touch = { x, y, distance: 0, pulling: false };
  };
  const drag = (e: TouchEvent) => {
    if (!touch) return;
    if (e.touches.length !== 1) {
      release(true);
      return;
    }
    const dx = Math.abs(e.touches[0].clientX - touch.x);
    const dy = e.touches[0].clientY - touch.y;
    if (!touch.pulling) {
      if (dx < 5 && Math.abs(dy) < 5) return;
      if (dy <= dx || !ready()) {
        touch = null;
        return;
      }
    }
    if (!e.cancelable) {
      release(true);
      return;
    }
    e.preventDefault();
    touch.pulling = true;
    touch.distance = Math.max(0, dy);
    move(touch.distance);
  };
  const finish = (e: TouchEvent) => {
    if (touch?.pulling && e.cancelable) e.preventDefault();
    release(false);
  };
  const cancel = () => release(true);

  element.addEventListener('touchstart', start, { passive: true });
  element.addEventListener('touchmove', drag, { passive: false });
  element.addEventListener('touchend', finish);
  element.addEventListener('touchcancel', cancel);
  return () => {
    element.removeEventListener('touchstart', start);
    element.removeEventListener('touchmove', drag);
    element.removeEventListener('touchend', finish);
    element.removeEventListener('touchcancel', cancel);
  };
}

/** Stretch the space below the page bar so the controls and any artwork behind them stay in place. */
export function usePagePull(ref: RefObject<HTMLElement | null>, enabled: boolean, route: string) {
  useEffect(() => {
    const page = ref.current;
    if (!page || !enabled || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let offset = 0;
    let frame = 0;
    const place = (distance: number) => {
      offset = distance;
      page.style.setProperty('--hm-page-pull', `${distance}px`);
    };
    const reset = () => {
      page.removeAttribute('data-page-pulling');
      page.style.removeProperty('--hm-page-pull');
      offset = 0;
    };
    const stop = listenTopPull(page, {
      ready: () => window.scrollY <= 1 && !frame,
      move: (distance) => {
        if (offset === 0) page.setAttribute('data-page-pulling', '');
        const limit = Math.min(window.innerHeight * 0.38, 240);
        place((limit * distance) / (limit + distance));
      },
      end: () => {
        const from = offset;
        const started = performance.now();
        const bounce = (time: number) => {
          const progress = Math.min(1, (time - started) / 190);
          if (progress === 1) {
            frame = 0;
            reset();
            return;
          }
          place(from * (1 - progress) ** 3);
          frame = requestAnimationFrame(bounce);
        };
        frame = requestAnimationFrame(bounce);
      },
    });
    return () => {
      stop();
      cancelAnimationFrame(frame);
      reset();
    };
  }, [ref, enabled, route]);
}
