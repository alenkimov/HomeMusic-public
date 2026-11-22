import { useSyncExternalStore, type MouseEvent as RMouse, type PointerEvent as RPointer, type TouchEvent as RTouch } from 'react';

/** The menu of a playlist one keeps — «Переименовать», «Сменить обложку», «Удалить» (`components/PlaylistMenu.tsx`).
 * It is asked for where the playlists are listed — a long press on a phone, the right button of the mouse on a
 * computer — and by the ⋯ at the top of the playlist's own page. */
export interface MenuPlaylist {
  id: string;
  name: string;
  cover_url?: string | null;
}

export interface PlaylistMenuState {
  playlist: MenuPlaylist;
  /** where the mouse was (a computer's menu opens there); none: a sheet */
  at?: { x: number; y: number };
}

let state: PlaylistMenuState | null = null;
const subs = new Set<() => void>();
const set = (next: PlaylistMenuState | null) => {
  state = next;
  subs.forEach((f) => f());
};

export const usePlaylistMenu = (): PlaylistMenuState | null =>
  useSyncExternalStore(
    (cb) => {
      subs.add(cb);
      return () => void subs.delete(cb);
    },
    () => state,
    () => state,
  );
export const openPlaylistMenu = (playlist: MenuPlaylist, at?: { x: number; y: number }) => set({ playlist, at });
export const closePlaylistMenu = () => set(null);

const HOLD_MS = 500;
let timer = 0;
let from: { x: number; y: number } | null = null;
let touch = false; // the press under way is a finger's
let held = false; // the press became the menu: the click that ends it opens nothing

/** What the row of a playlist gets, to ask for its menu: the right button, or a finger held on it. */
export function playlistMenuProps(playlist: MenuPlaylist) {
  const drop = () => {
    window.clearTimeout(timer);
    from = null;
  };
  const moved = (x: number, y: number) => {
    if (from && Math.hypot(x - from.x, y - from.y) > 10) drop();
  };
  return {
    onContextMenu: (e: RMouse) => {
      e.preventDefault();
      if (touch) return; // a finger: the hold itself opens the menu
      openPlaylistMenu(playlist, { x: e.clientX, y: e.clientY });
    },
    onPointerDown: (e: RPointer) => {
      held = false;
      touch = e.pointerType !== 'mouse';
      if (!touch) return;
      from = { x: e.clientX, y: e.clientY };
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        held = true;
        from = null;
        navigator.vibrate?.(10);
        openPlaylistMenu(playlist);
      }, HOLD_MS);
    },
    onPointerMove: (e: RPointer) => moved(e.clientX, e.clientY),
    onTouchMove: (e: RTouch) => {
      if (e.touches.length !== 1) drop();
      else moved(e.touches[0].clientX, e.touches[0].clientY);
    },
    onTouchEnd: drop,
    onTouchCancel: drop,
    onPointerUp: drop,
    onPointerCancel: drop,
    onClickCapture: (e: RMouse) => {
      if (!held) return;
      held = false;
      e.preventDefault();
      e.stopPropagation();
    },
  };
}
