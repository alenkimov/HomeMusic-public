import { useSyncExternalStore } from 'react';
import { api } from '../api/client';
import type { HubVersionOut } from '../api/types';
import { useAsync } from './hooks';

/** The version of this bundle (`VERSION` of the repository), and «Что нового»: the Hub's history of releases
 * (`hub/CHANGELOG.md` → `/api/version`). A release the listener has not opened «Что нового» since is new: a dot in
 * «Меню» until they do (kept on the device). */

export const WEB_VERSION: string = __HM_VERSION__;
const SEEN_KEY = 'hm-seen-version';

let seen: string | null = (() => {
  try {
    return localStorage.getItem(SEEN_KEY);
  } catch {
    return null;
  }
})();
const subs = new Set<() => void>();

export function markSeen(version: string) {
  if (seen === version) return;
  seen = version;
  try {
    localStorage.setItem(SEEN_KEY, version);
  } catch {
    /* private mode: shown again next time */
  }
  subs.forEach((f) => f());
}

const useSeen = () => useSyncExternalStore((cb) => { subs.add(cb); return () => void subs.delete(cb); }, () => seen, () => seen);

/** The Hub's version and its releases; `fresh`: a release not opened yet. */
export function useVersion(): { data: HubVersionOut | undefined; fresh: boolean } {
  const res = useAsync((s) => api.version(s), [], true, 'version');
  const last = useSeen();
  const version = res.data?.version;
  return { data: res.data, fresh: !!version && version !== 'dev' && version !== last };
}
