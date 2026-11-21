import { useSyncExternalStore } from 'react';
import type { EditedTrack } from './trackEdit';

export type AlbumArtwork = {
  albumId?: number | null;
  catalogRef?: string;
  title: string;
  artist: string;
  src?: string | null;
  tracks: readonly { title: string; artist: string }[];
};

type Target = ({ kind: 'track'; src?: string | null } & EditedTrack) | ({ kind: 'album' } & AlbumArtwork);
type Window = { target: Target; view: 'large' | 'edit' };
let windowState: Window | null = null;
const listeners = new Set<() => void>();

function set(next: Window | null) {
  windowState = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export const useCoverPicker = () => useSyncExternalStore(subscribe, () => windowState, () => null);
export const closeCoverPicker = () => set(null);
export const editTrackArtwork = (track: EditedTrack & { src?: string | null }) => set({ target: { ...track, kind: 'track' }, view: 'edit' });
export const openAlbumArtwork = (album: AlbumArtwork, view: Window['view'] = 'large') => set({ target: { ...album, kind: 'album' }, view });
export const canEditAlbumArtwork = (album: AlbumArtwork) => album.albumId != null || !!album.catalogRef;
