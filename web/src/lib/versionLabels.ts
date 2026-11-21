import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api/client';
import type { VersionLabelOut } from '../api/types';
import { hubEvents } from './events';
import { EDITED_EVENT } from './trackEdit';

export interface VersionIdentity {
  library_track_id?: number | null;
  track_id?: number | null;
  nd_song_id?: string | null;
  ref?: string | null;
  track_ref?: string | null;
  page_url?: string | null;
}

let labels = new Map<string, string>();
const listeners = new Set<() => void>();

export function subscribeVersionLabels(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function setLabels(rows: VersionLabelOut[]) {
  const next = new Map<string, string>();
  for (const row of rows) {
    next.set(`lib:${row.track_id}`, row.label);
    if (row.ref) next.set(row.ref, row.label);
    for (const id of row.nd_song_ids) next.set(`nd:${id}`, row.label);
    for (const url of row.source_urls) next.set(url, row.label);
  }
  labels = next;
  for (const listener of listeners) listener();
}

/** An exact id takes precedence over other names of the same recording. Titles and artists can be shared by versions. */
export function versionLabel(track?: VersionIdentity | null): string | undefined {
  if (!track) return undefined;
  const id = track.library_track_id ?? track.track_id;
  if (id != null && id > 0) return labels.get(`lib:${id}`);
  if (track.nd_song_id) return labels.get(`nd:${track.nd_song_id}`);
  return labels.get(track.ref ?? track.track_ref ?? '') ?? labels.get(track.page_url ?? '');
}

export function useVersionLabel(track?: VersionIdentity | null) {
  return useSyncExternalStore(subscribeVersionLabels, () => versionLabel(track));
}

export function titleWithVersion(title: string, track?: VersionIdentity | null, label = versionLabel(track)) {
  return label ? `${title} (${label})` : title;
}

/** One index per signed-in session, so a list of tracks does not have to query the Hub row by row. */
export function useVersionLabelsSync(account: number | null | undefined) {
  useEffect(() => {
    if (account == null) return;
    let request: AbortController | undefined;
    let timer: number | undefined;
    const load = () => {
      request?.abort();
      const controller = new AbortController();
      request = controller;
      void api.versionLabels(controller.signal).then((rows) => {
        if (!controller.signal.aborted) setLabels(rows);
      }).catch(() => { /* Keep the last labels while the Hub is unreachable. */ });
    };
    const refresh = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(load, 150);
    };
    const shown = () => { if (document.visibilityState === 'visible') refresh(); };
    const offLibrary = hubEvents.on('library', ({ kind }) => {
      if (kind === 'changed' || kind === 'imported') refresh();
    });
    const offReconnect = hubEvents.on('reconnect', refresh);
    window.addEventListener(EDITED_EVENT, refresh);
    document.addEventListener('visibilitychange', shown);
    load();
    return () => {
      request?.abort();
      window.clearTimeout(timer);
      offLibrary();
      offReconnect();
      window.removeEventListener(EDITED_EVENT, refresh);
      document.removeEventListener('visibilitychange', shown);
      setLabels([]);
    };
  }, [account]);
}
