import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { useDesktop } from '../lib/desktop';
import { Icon, type IconName } from './Icon';
import { cx } from './ui';

/** One line of a menu: its icon (none — an empty place, so the names stay in one column), its name, the key that
 * does it while the menu is open, and its state — on (the icon is green), done (a ✓ at the end), not possible now
 * (grey, does nothing). No explanations under the names. */
export interface MenuItemSpec {
  icon?: IconName;
  /** a picture of its own in place of the icon (a service's logo) */
  glyph?: ReactNode;
  label: string;
  /** what the menu shows and the keyboard presses while it is open: a letter ('Q'), 'Alt+A', 'Del', 'F2' */
  shortcut?: string;
  active?: boolean;
  done?: boolean;
  disabled?: boolean;
  danger?: boolean;
  /** a blue dot after the name: something here wants a look (the artist's ⋯ wears it too) */
  dot?: boolean;
  /** A computer's menu opens this beside the line (under the mouse, a click, →) instead of `onSelect`; a phone's
   * sheet does `onSelect` (a sheet of its own for the same choice). `close` closes the whole menu. */
  submenu?: (close: () => void) => ReactNode;
  onSelect: () => void;
}

/** 'sep' — a line between groups; empty values are skipped, so a menu is written with conditions inline. */
export type MenuEntry = MenuItemSpec | 'sep' | null | false | undefined | '' | 0;

/** What the menu is of: the head of the sheet on a phone (a computer's menu has none — it opens at the thing). */
export interface MenuHead {
  title: string;
  sub?: string;
  cover?: ReactNode;
}

/** Where a computer's menu opens: at the mouse (a right click), or under a button (`right`: its right edge). */
export interface MenuAt {
  x: number;
  y: number;
  right?: boolean;
}

export const menuAtButton = (el: Element): MenuAt => {
  const r = el.getBoundingClientRect();
  return { x: r.right, y: r.bottom + 4, right: true };
};

/** A menu of a page or a card: `open` is drawn while `shown`. `fromButton` — the ⋯ (under it on a computer),
 * `fromMouse` — a right click (at the mouse; a field keeps its own menu). */
export function useContextMenu() {
  // undefined — closed; null — open as a sheet
  const [at, setAt] = useState<MenuAt | null | undefined>(undefined);
  return {
    shown: at !== undefined,
    at: at ?? null,
    fromButton: (e: { currentTarget: Element }) => setAt(menuAtButton(e.currentTarget)),
    fromMouse: (e: ReactMouseEvent) => {
      if (typing(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
      setAt({ x: e.clientX, y: e.clientY });
    },
    /** from `menuPressProps`: at the mouse, or a sheet (a finger held) */
    openAt: (where?: MenuAt) => setAt(where ?? null),
    close: () => setAt(undefined),
  };
}

/** The groups as they are drawn: no line at the ends or twice in a row. */
function lines(entries: readonly MenuEntry[]): (MenuItemSpec | 'sep')[] {
  const out: (MenuItemSpec | 'sep')[] = [];
  for (const e of entries) {
    if (!e) continue;
    if (e === 'sep' && (!out.length || out[out.length - 1] === 'sep')) continue;
    out.push(e);
  }
  while (out[out.length - 1] === 'sep') out.pop();
  return out;
}

/** Does this key press a shortcut? By the key's place, not its letter: the same with a Russian layout. */
function pressed(shortcut: string, e: KeyboardEvent): boolean {
  const parts = shortcut.split('+');
  const key = parts[parts.length - 1];
  const alt = parts.includes('Alt');
  if (alt !== e.altKey || e.ctrlKey || e.metaKey) return false;
  if (key === 'Del') return e.key === 'Delete' || e.key === 'Backspace';
  if (/^F\d+$/.test(key)) return e.key === key;
  return key.length === 1 && e.code === `Key${key.toUpperCase()}`;
}

const typing = (el: EventTarget | null) => el instanceof HTMLElement && !!el.closest('input, textarea, [contenteditable=true]');

/** The lines of a panel that the arrows walk. */
const walkable = (el: HTMLElement | null) => [...(el?.querySelectorAll<HTMLElement>('[role=menuitem]:not([aria-disabled=true])') ?? [])];

function walk(panel: HTMLElement | null, down: boolean) {
  const buttons = walkable(panel);
  if (!buttons.length) return;
  const now = buttons.indexOf(document.activeElement as HTMLElement);
  const next = now < 0 ? (down ? 0 : buttons.length - 1) : (now + (down ? 1 : -1) + buttons.length) % buttons.length;
  buttons[next].focus();
}

/** A menu of a thing (a track, an album, a playlist): at the mouse on a computer, a sheet from below elsewhere. The
 * same lines both ways. Escape, a click outside or the choice itself close it; the arrows and Enter walk it, → and ←
 * go into a submenu and out of it, and the shortcuts shown work while it is open. */
export function ContextMenu({ at, head, entries, onClose }: { at?: MenuAt | null; head: MenuHead; entries: readonly MenuEntry[]; onClose: () => void }) {
  const desktop = useDesktop();
  const popover = desktop && !!at;
  const items = lines(entries);
  const box = useRef<HTMLDivElement>(null);
  const subBox = useRef<HTMLDivElement>(null);
  // the line whose submenu is open, and where that line is
  const [sub, setSub] = useState<{ label: string; row: DOMRect } | null>(null);
  const subItem = sub ? items.find((it): it is MenuItemSpec => it !== 'sep' && it.label === sub.label) : undefined;

  const openSub = (it: MenuItemSpec, row: Element | null, focus = false) => {
    if (!row) return;
    setSub({ label: it.label, row: row.getBoundingClientRect() });
    if (focus) requestAnimationFrame(() => walkable(subBox.current)[0]?.focus());
  };
  const run = (it: MenuItemSpec, row?: Element | null) => {
    if (it.disabled) return;
    if (popover && it.submenu) return openSub(it, row ?? box.current?.querySelector(`[data-label="${CSS.escape(it.label)}"]`) ?? null, !row);
    onClose();
    it.onSelect();
  };

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        if (sub) {
          setSub(null);
          (box.current?.querySelector<HTMLElement>(`[data-label="${CSS.escape(sub.label)}"]`))?.focus();
        } else onClose();
        return;
      }
      if (typing(e.target)) return; // a field of a submenu (the search of playlists) takes the keys
      const inSub = !!sub && !!subBox.current?.contains(document.activeElement);
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        walk(inSub ? subBox.current : box.current, e.key === 'ArrowDown');
        return;
      }
      if (e.key === 'ArrowLeft' && inSub) {
        e.preventDefault();
        (box.current?.querySelector<HTMLElement>(`[data-label="${CSS.escape(sub!.label)}"]`))?.focus();
        setSub(null);
        return;
      }
      if (e.key === 'ArrowRight' && !inSub) {
        const focused = document.activeElement as HTMLElement | null;
        const it = items.find((x): x is MenuItemSpec => x !== 'sep' && x.label === focused?.dataset.label);
        if (it?.submenu && popover) {
          e.preventDefault();
          openSub(it, focused, true);
        }
        return;
      }
      if (inSub) return;
      const hit = items.find((it): it is MenuItemSpec => it !== 'sep' && !!it.shortcut && !it.disabled && pressed(it.shortcut, e));
      if (hit) {
        e.preventDefault();
        run(hit);
      }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  });

  const list = items.map((it, i) =>
    it === 'sep' ? (
      <div key={`sep${i}`} role="separator" className={cx('h-px bg-line', popover ? 'mx-2 my-1' : 'mx-4 my-1')} />
    ) : (
      <MenuLine
        key={it.label}
        item={it}
        big={!popover}
        open={sub?.label === it.label}
        onRun={(row) => run(it, row)}
        // a computer's menu: the mouse on a line with a submenu opens it, on any other line closes the open one
        onHover={popover ? (row) => (it.submenu && !it.disabled ? openSub(it, row) : sub && setSub(null)) : undefined}
      />
    ),
  );

  if (popover) {
    return (
      <Popover at={at!} label={head.title} box={box} onClose={onClose}>
        {list}
        {sub && subItem?.submenu && (
          <SubPanel row={sub.row} parent={box} box={subBox} label={subItem.label}>
            {subItem.submenu(onClose)}
          </SubPanel>
        )}
      </Popover>
    );
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center" role="dialog" aria-modal="true" aria-label={head.title}>
      <button type="button" aria-label="Закрыть" className="absolute inset-0 bg-black/70" onClick={onClose} />
      <div ref={box} role="menu" className="anim-up pb-safe relative flex max-h-[85dvh] w-full max-w-lg flex-col rounded-t-2xl bg-surface">
        <div className="flex justify-center pb-1 pt-2" aria-hidden="true">
          <span className="h-1 w-9 rounded-full bg-line" />
        </div>
        <div className="flex items-center gap-3 border-b border-line px-4 pb-3 pt-1">
          {head.cover}
          <span className="min-w-0">
            <span className="block truncate font-semibold">{head.title}</span>
            {head.sub && <span className="block truncate text-[13px] text-muted">{head.sub}</span>}
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto py-1">{list}</div>
      </div>
    </div>,
    document.body,
  );
}

/** Keeps a box inside the window: at the point, or as near to it as the window lets. */
function useFit(box: RefObject<HTMLDivElement | null>, want: (w: number, h: number) => { left: number; top: number }, deps: unknown[]) {
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const gap = 8;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const p = want(w, h);
    setPlace({ left: Math.max(gap, Math.min(p.left, window.innerWidth - w - gap)), top: Math.max(gap, Math.min(p.top, window.innerHeight - h - gap)) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return place ? { left: place.left, top: place.top } : { left: -9999, top: 0 };
}

/** A computer's menu: at the point, kept inside the window, scrolled when it is taller than the window. */
function Popover({ at, label, box, onClose, children }: { at: MenuAt; label: string; box: RefObject<HTMLDivElement | null>; onClose: () => void; children: ReactNode }) {
  // below the point when it fits, else above it
  const style = useFit(box, (w, h) => ({ left: at.right ? at.x - w : at.x, top: at.y + h + 8 <= window.innerHeight ? at.y : at.y - h }), [at]);
  return createPortal(
    <>
      <div
        className="fixed inset-0 z-50"
        onMouseDown={onClose}
        onContextMenu={(e) => {
          e.preventDefault();
          onClose();
        }}
      />
      <div
        ref={box}
        role="menu"
        aria-label={label}
        className="fixed z-50 max-h-[calc(100dvh-16px)] w-72 overflow-y-auto rounded-md bg-raised p-1 text-sm shadow-2xl"
        style={style}
        onContextMenu={(e) => e.preventDefault()}
      >
        {children}
      </div>
    </>,
    document.body,
  );
}

/** The submenu beside its line: to the right of the menu, to the left when the window ends there. */
function SubPanel({ row, parent, box, label, children }: { row: DOMRect; parent: RefObject<HTMLDivElement | null>; box: RefObject<HTMLDivElement | null>; label: string; children: ReactNode }) {
  const style = useFit(box, (w) => {
    const menu = parent.current?.getBoundingClientRect();
    const right = (menu?.right ?? row.right) + 4;
    return { left: right + w <= window.innerWidth - 8 ? right : (menu?.left ?? row.left) - w - 4, top: row.top - 4 };
  }, [row]);
  return createPortal(
    <div
      ref={box}
      role="menu"
      aria-label={label}
      className="fixed z-50 flex max-h-[min(28rem,calc(100dvh-16px))] w-64 flex-col overflow-hidden rounded-md bg-raised p-1 text-sm shadow-2xl"
      style={style}
      onMouseDown={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      {children}
    </div>,
    document.body,
  );
}

/** A line of a menu as a submenu draws its own: the same look as the menu's. */
export function MenuLine({ item, big = false, open = false, onRun, onHover }: { item: Omit<MenuItemSpec, 'onSelect'>; big?: boolean; open?: boolean; onRun: (row: HTMLElement) => void; onHover?: (row: HTMLElement) => void }) {
  const tone = item.disabled ? 'text-dim' : item.danger ? 'text-danger' : '';
  const iconTone = item.active ? 'text-accent' : item.disabled ? 'text-dim' : item.danger ? 'text-danger' : 'text-muted';
  const more = !!item.submenu && !big;
  return (
    <button
      type="button"
      role="menuitem"
      data-label={item.label}
      aria-disabled={item.disabled || undefined}
      aria-checked={item.done || undefined}
      aria-haspopup={more ? 'menu' : undefined}
      aria-expanded={more ? open : undefined}
      onClick={(e) => onRun(e.currentTarget)}
      onMouseEnter={onHover ? (e) => onHover(e.currentTarget) : undefined}
      className={cx(
        'flex w-full shrink-0 items-center text-left outline-none',
        big ? 'min-h-12 gap-4 px-4 text-[15px]' : 'h-10 gap-3 rounded-sm px-3',
        item.disabled ? 'cursor-default' : 'hover:bg-fg/10 focus-visible:bg-fg/10 active:bg-fg/15',
        open && 'bg-fg/10',
        tone,
      )}
    >
      {item.glyph ? <span className={cx('flex shrink-0 justify-center', big ? 'w-[22px]' : 'w-[18px]')}>{item.glyph}</span>
        : item.icon ? <Icon name={item.icon} size={big ? 22 : 18} className={cx('shrink-0', iconTone)} /> : <span className={cx('shrink-0', big ? 'w-[22px]' : 'w-[18px]')} />}
      <span className="flex min-w-0 flex-1 items-center gap-2">
        <span className="truncate">{item.label}</span>
        {item.dot && <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full bg-ai" />}
      </span>
      {item.shortcut && !big && <span className="shrink-0 text-xs text-muted">{item.shortcut.replace('+', ' ')}</span>}
      {item.done && <Icon name="check" size={big ? 20 : 18} stroke={3} className="shrink-0 text-accent" />}
      {more && <Icon name="chevron" size={16} className="shrink-0 text-muted" />}
    </button>
  );
}

const HOLD_MS = 500;
let holdTimer = 0;
let holdFrom: { x: number; y: number } | null = null;
let holdTouch = false; // the press under way is a finger's
let held = false; // the press became the menu: the click that ends it does nothing

/** What a thing with a menu gets to open it the other ways: the right button (a menu at the mouse), or a finger held
 * on it for half a second (a sheet; a phone's browser that sends no `contextmenu` for it too). A finger that moves
 * — a scroll, a swipe — opens nothing. */
export function menuPressProps(open: (at?: MenuAt) => void) {
  const drop = () => {
    window.clearTimeout(holdTimer);
    holdFrom = null;
  };
  const moved = (x: number, y: number) => {
    if (holdFrom && Math.hypot(x - holdFrom.x, y - holdFrom.y) > 10) drop();
  };
  return {
    onContextMenu: (e: ReactMouseEvent) => {
      if (typing(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
      if (holdTouch) return; // a finger: the hold itself opens the menu
      open({ x: e.clientX, y: e.clientY });
    },
    onPointerDown: (e: ReactPointerEvent) => {
      held = false;
      holdTouch = e.pointerType !== 'mouse';
      if (!holdTouch) return;
      holdFrom = { x: e.clientX, y: e.clientY };
      window.clearTimeout(holdTimer);
      holdTimer = window.setTimeout(() => {
        held = true;
        holdFrom = null;
        navigator.vibrate?.(10);
        open();
      }, HOLD_MS);
    },
    onPointerMove: (e: ReactPointerEvent) => moved(e.clientX, e.clientY),
    onPointerUp: drop,
    onPointerCancel: drop,
    onClickCapture: (e: ReactMouseEvent) => {
      if (!held) return;
      held = false;
      e.preventDefault();
      e.stopPropagation();
    },
  };
}
