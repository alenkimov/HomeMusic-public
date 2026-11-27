import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api/client';
import { hubEvents } from './events';
import { toastError } from './ui';
import { keep as keepChange, onDropped, onSettled, waiting, waits } from './outbox';

/** The tracks (the Hub's ids, `lib/tags.ts` `tagKey`) that are in some playlist of the user's («Микс дня» apart): what the «+ / ✓» of a track row
 * goes by (`components/PlaylistsButton.tsx`). One request for the whole library, asked again when a playlist changes. */

let ids = new Set<string>();
let version = 0;
let loadedAt = 0;
let loading: Promise<void> | null = null;
let again = false;
const subs = new Set<() => void>();
const REFRESH_MS = 60_000;

const subscribe = (fn: () => void) => {
  subs.add(fn);
  return () => void subs.delete(fn);
};
function emit() {
  version++;
  subs.forEach((f) => f());
}

export function refreshInPlaylists(force = false): Promise<void> {
  if (loading) {
    if (force) again = true;
    return loading;
  }
  if (!force && Date.now() - loadedAt < REFRESH_MS) return Promise.resolve();
  loading = api
    .playlistMembers(AbortSignal.timeout(15_000))
    .then(
      (r) => {
        ids = new Set(r.track_ids.map(String));
        loadedAt = Date.now();
        emit();
      },
      () => undefined, // offline, or an older Hub: what is known stays
    )
    .finally(() => {
      loading = null;
      if (again) {
        again = false;
        void refreshInPlaylists(true);
      }
    });
  return loading;
}

/** Known at once after a change made here, before the Hub is asked again. */
export function noteInPlaylists(songIds: readonly string[], inSome: boolean) {
  if (!inSome) return void refreshInPlaylists(true); // taken out of one, it may still be in another
  const next = new Set(ids);
  songIds.forEach((x) => next.add(x));
  ids = next;
  emit();
  void refreshInPlaylists(true);
}

export const inPlaylists = (songId?: string | null): boolean => !!songId && ids.has(songId);

export function useInPlaylists(songId?: string | null): boolean {
  useEffect(() => void refreshInPlaylists(), []);
  useSyncExternalStore(subscribe, () => version);
  return inPlaylists(songId);
}

hubEvents.on('library', () => void refreshInPlaylists(true));
hubEvents.on('reconnect', () => void refreshInPlaylists(true));

export function resetInPlaylists() {
  ids = new Set();
  loadedAt = 0;
  emit();
}

/** Tracks (`tagKey`s) put into playlists and taken out of them: at once with the Hub; without it the change waits on
 * the device (`lib/outbox.ts`) and is shown as done. `inSome`: the tracks are in some playlist after it (unknown —
 * null — when taken out without the Hub, which alone knows the rest). False: not done (the caller was told why). */
export async function changeMembership(tracks: readonly string[], add: readonly string[], remove: readonly string[]):
  Promise<{ waits: boolean; inSome: boolean | null } | false> {
  try {
    const r = tracks.length === 1
      ? await api.playlistMembership({ track: tracks[0], add: [...add], remove: [...remove] })
      : await api.playlistMembershipMany({ tracks: [...tracks], add: [...add], remove: [...remove] });
    const inSome = r.some((p) => ('has' in p ? p.has : p.count > 0));
    noteInPlaylists(tracks, inSome);
    return { waits: false, inSome };
  } catch (e) {
    if (waits(e) && keepChange({ kind: 'membership', key: [...tracks].sort().join(','), tracks: [...tracks], add: [...add], remove: [...remove] })) {
      if (add.length) noteInPlaylists(tracks, true);
      return { waits: true, inSome: add.length ? true : null };
    }
    toastError(e);
    return false;
  }
}

/** A playlist row of a track as the Hub said it, with what was changed here without it and waits to be sent. */
export function withWaiting<T extends { id: string }>(tracks: readonly string[], rows: T[], set: (row: T, into: boolean) => T): T[] {
  const changes = waiting().filter((c) => c.kind === 'membership' && tracks.every((x) => c.tracks.includes(x)));
  return rows.map((row) => {
    let out = row;
    for (const c of changes) if (c.kind === 'membership') {
      if (c.add.includes(row.id)) out = set(out, true);
      else if (c.remove.includes(row.id)) out = set(out, false);
    }
    return out;
  });
}

onSettled((c) => { if (c.kind === 'membership') void refreshInPlaylists(true); });
onDropped((c) => { if (c.kind === 'membership') void refreshInPlaylists(true); });
