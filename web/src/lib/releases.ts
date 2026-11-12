import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api/client';
import { hubEvents } from './events';

/** How many releases of followed artists the user has not seen yet — the number beside «Релизы» in the sidebar.
 * Asked once, then again when the Hub says it has found new ones; the feed's page zeroes it when opened. */

let unseen = 0;
let asked = false;
const subs = new Set<() => void>();
const subscribe = (cb: () => void) => {
  subs.add(cb);
  return () => void subs.delete(cb);
};
const set = (n: number) => {
  if (n === unseen) return;
  unseen = n;
  subs.forEach((f) => f());
};

export function refreshUnseenReleases() {
  asked = true;
  api.releasesCount().then((r) => set(r.unseen), () => undefined);
}

export const setUnseenReleases = (n: number) => set(n);

export function useUnseenReleases(): number {
  useEffect(() => {
    if (!asked) refreshUnseenReleases();
  }, []);
  return useSyncExternalStore(subscribe, () => unseen, () => 0);
}

hubEvents.on('library', (e) => {
  if ((e as { kind?: string }).kind === 'releases') refreshUnseenReleases();
});
hubEvents.on('reconnect', () => asked && refreshUnseenReleases());

export function resetReleases() {
  asked = false;
  set(0);
}
