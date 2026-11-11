import { useRef, useState, type PointerEvent, type ReactNode } from 'react';
import { Icon, type IconName } from './Icon';
import { cx } from './ui';

const THRESHOLD = 84; // past it the action fires on release
const MAX = 170;
const EDGE = 24; // a swipe from the very edge of the screen is the browser's «back»

/** A list row on a touch screen that is swiped aside: to the right — `right` (into the queue), to the left — `left`
 * (into a playlist). The plate under the row turns to the accent color once the action would fire; released
 * earlier, the row goes back and nothing happens. A vertical move scrolls the page as usual. */
export function SwipeRow({
  right,
  left,
  children,
}: {
  right?: { icon: IconName; onAction: () => void };
  left?: { icon: IconName; armedIcon?: IconName; onAction: () => void };
  children: ReactNode;
}) {
  const [dx, setDx] = useState(0);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ x: number; y: number; lock: 'x' | 'y' | null; dx: number } | null>(null);
  const swiped = useRef(false);

  const down = (e: PointerEvent<HTMLDivElement>) => {
    if ((e.pointerType === 'mouse' && e.button !== 0) || e.clientX < EDGE) return;
    drag.current = { x: e.clientX, y: e.clientY, lock: null, dx: 0 };
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const mx = e.clientX - d.x, my = e.clientY - d.y;
    if (!d.lock) {
      if (Math.abs(mx) < 10 && Math.abs(my) < 10) return;
      d.lock = Math.abs(mx) > Math.abs(my) * 1.2 ? 'x' : 'y';
      if (d.lock === 'x') {
        setDragging(true);
        try {
          e.currentTarget.setPointerCapture(e.pointerId);
        } catch {
          /* the pointer is gone already */
        }
      }
    }
    if (d.lock !== 'x') return;
    d.dx = Math.max(left ? -MAX : 0, Math.min(right ? MAX : 0, mx));
    setDx(d.dx);
  };
  const end = (fire: boolean) => {
    const d = drag.current;
    drag.current = null;
    if (!d || d.lock !== 'x') return;
    swiped.current = true; // the click that follows the release is not a tap on the row
    window.setTimeout(() => (swiped.current = false), 0);
    setDragging(false);
    setDx(0);
    if (!fire) return;
    if (d.dx >= THRESHOLD) right?.onAction();
    else if (d.dx <= -THRESHOLD) left?.onAction();
  };

  const armed = Math.abs(dx) >= THRESHOLD;
  const side = dx > 0 ? right : dx < 0 ? left : null;
  return (
    <div className="relative touch-pan-y select-none overflow-hidden">
      {side && (
        <div
          aria-hidden="true"
          className={cx('absolute inset-0 flex items-center px-6', dx > 0 ? 'justify-start' : 'justify-end', armed ? 'bg-accent text-on-accent' : 'bg-raised text-muted')}
        >
          <Icon name={armed && dx < 0 && left?.armedIcon ? left.armedIcon : side.icon} size={24} />
        </div>
      )}
      <div
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={() => end(true)}
        onPointerCancel={() => end(false)}
        onClickCapture={(e) => {
          if (!swiped.current) return;
          e.preventDefault();
          e.stopPropagation();
        }}
        className={cx('relative bg-bg', !dragging && 'transition-transform duration-200')}
        style={dx ? { transform: `translateX(${dx}px)` } : undefined}
      >
        {children}
      </div>
    </div>
  );
}
