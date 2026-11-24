import { useSyncExternalStore } from 'react';
import { menuPressProps, type MenuAt } from '../components/ContextMenu';

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
  at?: MenuAt;
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
export const openPlaylistMenu = (playlist: MenuPlaylist, at?: MenuAt) => set({ playlist, at });
export const closePlaylistMenu = () => set(null);

/** What the row of a playlist gets, to ask for its menu: the right button, or a finger held on it. */
export const playlistMenuProps = (playlist: MenuPlaylist) => menuPressProps((at) => openPlaylistMenu(playlist, at));
