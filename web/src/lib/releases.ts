import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api/client';
import { hubEvents } from './events';

/** «Подписки» in numbers: how many artists the user follows and how many of their releases the user has not seen —
 * the mark on the bell of «Главная» and beside «Подписки». Asked once, then again when the Hub says it has found
 * new ones or a follow changes; the page zeroes the unseen when opened. */

let state = { unseen: 0, artists: 0 };
let asked = false;
const subs = new Set<() => void>();
const subscribe = (cb: () => void) => {
  subs.add(cb);
  return () => void subs.delete(cb);
};
const set = (next: typeof state) => {
  if (next.unseen === state.unseen && next.artists === state.artists) return;
  state = next;
  subs.forEach((f) => f());
};

export function refreshUnseenReleases() {
  asked = true;
  api.releasesCount().then((r) => set({ unseen: r.unseen, artists: r.artists }), () => undefined);
}

export const setUnseenReleases = (n: number) => set({ ...state, unseen: n });

function useAsked() {
  useEffect(() => {
    if (!asked) refreshUnseenReleases();
  }, []);
}

export function useUnseenReleases(): number {
  useAsked();
  return useSyncExternalStore(subscribe, () => state.unseen, () => 0);
}

/** How many artists the user follows. */
export function useFollowedCount(): number {
  useAsked();
  return useSyncExternalStore(subscribe, () => state.artists, () => 0);
}

hubEvents.on('library', (e) => {
  if ((e as { kind?: string }).kind === 'releases') refreshUnseenReleases();
});
hubEvents.on('reconnect', () => asked && refreshUnseenReleases());
// an artist followed or not any more (the ♥ of the Hub): a moment later, when the Hub has written it
if (typeof window !== 'undefined') window.addEventListener('hm:hub-favorite', () => asked && window.setTimeout(refreshUnseenReleases, 1500));

export function resetReleases() {
  asked = false;
  set({ unseen: 0, artists: 0 });
}
