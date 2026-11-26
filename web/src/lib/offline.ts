import { useSyncExternalStore } from 'react';
import { api, listenStreamUrl } from '../api/client';
import type { ListenOut, TrackOut } from '../api/types';
import { AS_IT_IS, cantPlayType } from './formats';
import { hubEvents } from './events';
import { needsTrackFile } from './findFile';
import { hubFetch, hubReachable, onHubBack } from './reach';

/** «Офлайн»: lists kept on the device — a playlist, «Любимое», a list of «Для вас» — so they play without the network.
 *
 * A list kept is written down here (IndexedDB: the list and each of its tracks, with what the Hub said of its file),
 * and the audio of its tracks is fetched one after another into the browser's cache (`hm-audio-v1`), under the very
 * address the player asks for. The service worker (`public/sw.js`) answers that address from the cache, so the
 * player plays a kept track without knowing it is kept — with the network and without. The lists and covers a page
 * reads are kept by the service worker as well, so the pages open offline.
 *
 * A kept list is kept up to date by itself (`syncKept`), as a subscription: when the app opens, when the library
 * changes (a ♥, a track added to a playlist or taken out of it), when the Hub is back and when the app is looked at
 * again — its new tracks are fetched, the ones taken out of it leave the device.
 *
 * A track is kept while some kept list has it; a list opened again is brought up to date (`syncList`) — and so is a
 * kept track whose file was put another in its place (another file picked, an upgrade: `FileOut.uid` is not the one
 * kept): it is fetched anew, and until then plays from the Hub where the Hub is reached (`keptPlays`). Needs a
 * service worker, so a secure address (https or localhost).
 *
 * A copy in a format this browser does not play (`cantPlayType`: Ogg Opus kept on an iPhone before the Hub served it
 * Opus in MP4 or AAC) is fetched anew as well, in the format the Hub serves it now; until then it is not played. */

export interface KeptTrack {
  ref: string;
  info: ListenOut;
  track: TrackOut;
  size: number;
  at: number;
  /** the copy's `Content-Type` (missing in what was kept before it was written down: read from the cache once) */
  type?: string;
}
export interface KeptList {
  key: string; // "playlist:12", "liked", "foryou:mix"
  title: string;
  to: string; // its page
  refs: string[];
  at: number;
}

const DB = 'homemusic-offline';
const AUDIO = 'hm-audio-v1';
const PARALLEL = 2;

export const offlineSupported = (): boolean => typeof caches !== 'undefined' && typeof indexedDB !== 'undefined' && 'serviceWorker' in navigator && window.isSecureContext;
// the key is the address without `cant` (as `public/sw.js` looks it up); the bytes are what this browser plays
const audioKey = (ref: string) => new URL(listenStreamUrl(ref, AS_IT_IS), window.location.origin).href;

// --- state --------------------------------------------------------------------------------------------------

const tracks = new Map<string, KeptTrack>();
const lists = new Map<string, KeptList>();
const failed = new Map<string, string>(); // ref -> why it was not fetched (tried again when its list is opened)
const stale = new Set<string>(); // kept with a file the track has no more: fetched anew
const waiting: string[] = [];
const busy = new Set<string>();
let wanted = new Map<string, TrackOut>(); // what a track is, for the ones still to fetch
let version = 0;
let ready: Promise<void> | null = null;
const subs = new Set<() => void>();
const emit = () => {
  version++;
  subs.forEach((f) => f());
};
const subscribe = (cb: () => void) => {
  subs.add(cb);
  return () => void subs.delete(cb);
};
const useVersion = () => useSyncExternalStore(subscribe, () => version, () => 0);

// --- the store ----------------------------------------------------------------------------------------------

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => {
      r.result.createObjectStore('tracks', { keyPath: 'ref' });
      r.result.createObjectStore('lists', { keyPath: 'key' });
    };
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

async function tx<T>(store: 'tracks' | 'lists', mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T> | void): Promise<T | undefined> {
  const db = await open();
  try {
    return await new Promise<T | undefined>((resolve, reject) => {
      const t = db.transaction(store, mode);
      const req = run(t.objectStore(store));
      t.oncomplete = () => resolve(req ? req.result : undefined);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    });
  } finally {
    db.close();
  }
}

/** Reads what is kept (once), and goes on with what was not fetched to the end. */
export function initOffline(): Promise<void> {
  if (!offlineSupported()) return Promise.resolve();
  ready ??= (async () => {
    try {
      for (const t of ((await tx<KeptTrack[]>('tracks', 'readonly', (s) => s.getAll())) ?? [])) tracks.set(t.ref, t);
      for (const l of ((await tx<KeptList[]>('lists', 'readonly', (s) => s.getAll())) ?? [])) lists.set(l.key, l);
    } catch (e) {
      console.warn('offline: what is kept could not be read', e);
    }
    emit();
    void refetchUnplayable();
  })();
  return ready;
}

/** Copies in a format this browser does not play are fetched anew. What was kept without its type said, its type
 * is read from the cache and written down (once). */
async function refetchUnplayable() {
  try {
    const cache = await caches.open(AUDIO);
    for (const kept of [...tracks.values()]) {
      if (kept.type === undefined) {
        const hit = await cache.match(audioKey(kept.ref));
        if (!hit) continue;
        kept.type = hit.headers.get('content-type') || 'audio/mpeg';
        await tx('tracks', 'readwrite', (s) => s.put(kept));
      }
      if (!cantPlayType(kept.type)) continue;
      stale.add(kept.ref);
      if (!wanted.has(kept.ref)) wanted.set(kept.ref, kept.track);
      if (!busy.has(kept.ref) && !waiting.includes(kept.ref)) waiting.push(kept.ref);
    }
  } catch (e) {
    console.warn('offline: the kept copies could not be checked', e);
  }
  emit();
  pump();
}

// --- fetching -----------------------------------------------------------------------------------------------

function needed(): Set<string> {
  const out = new Set<string>();
  for (const l of lists.values()) for (const r of l.refs) out.add(r);
  return out;
}

async function fetchOne(ref: string): Promise<void> {
  const track = wanted.get(ref);
  if (!track) return;
  const info = await api.listen(ref);
  // `X-HomeMusic-Prefetch`: this is no listen (the Hub counts a stream as one)
  const res = await fetch(listenStreamUrl(ref), { credentials: 'include', headers: { 'X-HomeMusic-Prefetch': '1' } });
  if (!res.ok) throw new Error(`сервер ответил ${res.status}`);
  const blob = await res.blob();
  if (!blob.size) throw new Error('пустой файл');
  if (!needed().has(ref)) return; // its list was let go meanwhile
  const type = res.headers.get('content-type') || 'audio/mpeg';
  await (await caches.open(AUDIO)).put(audioKey(ref), new Response(blob, { headers: { 'content-type': type, 'content-length': String(blob.size) } }));
  const kept: KeptTrack = { ref, info, track, size: blob.size, at: Date.now(), type };
  await tx('tracks', 'readwrite', (s) => s.put(kept));
  tracks.set(ref, kept);
  stale.delete(ref);
  // its covers go through the service worker, which keeps them
  for (const url of new Set([track.cover_small_url, track.cover_url])) if (url?.startsWith('/')) void hubFetch(url, { credentials: 'include' }).catch(() => undefined);
}

function pump() {
  while (busy.size < PARALLEL && waiting.length && hubReachable()) {
    const ref = waiting.shift() as string;
    if ((tracks.has(ref) && !stale.has(ref)) || busy.has(ref) || !needed().has(ref)) continue;
    busy.add(ref);
    failed.delete(ref);
    emit();
    fetchOne(ref)
      .catch((e: unknown) => {
        failed.set(ref, e instanceof Error ? e.message : 'не удалось скачать');
      })
      .finally(() => {
        busy.delete(ref);
        emit();
        pump();
      });
  }
}

/** The file a kept track was fetched with is not the track's file now. (A track without its file said — a list
 * that does not tell — is taken to be the same.) */
function outdated(kept: KeptTrack, file: { uid?: string | null } | null | undefined): boolean {
  return !!file?.uid && kept.info.file_uid !== file.uid;
}

function enqueue(items: readonly TrackOut[]) {
  for (const t of items) {
    wanted.set(t.ref, t);
    const kept = tracks.get(t.ref);
    if (kept && outdated(kept, t.file)) stale.add(t.ref);
    if ((!kept || stale.has(t.ref)) && !busy.has(t.ref) && !waiting.includes(t.ref)) waiting.push(t.ref);
  }
  pump();
}

/** The file of a track was put another in its place here (`FindFile`, «Качество звука»): a copy of it kept on the
 * device is fetched anew. The track by its ref or the library's id. */
export function fileReplaced(track: { ref?: string | null; library_track_id?: number | null }) {
  const hits = [...tracks.values()].filter((k) =>
    k.ref === track.ref || (track.library_track_id != null && k.track.library_track_id === track.library_track_id));
  for (const kept of hits) {
    stale.add(kept.ref);
    if (!wanted.has(kept.ref)) wanted.set(kept.ref, kept.track);
    if (!busy.has(kept.ref) && !waiting.includes(kept.ref)) waiting.push(kept.ref);
  }
  if (!hits.length) return;
  emit();
  pump();
}

/** The kept copy is what plays this track: it is the track's file — or the Hub cannot be reached (the old file is
 * better than none). Never a copy this browser does not play. */
export function keptPlays(t: { ref: string; file?: { uid?: string | null } | null }): boolean {
  const kept = tracks.get(t.ref);
  if (!kept || cantPlayType(kept.type)) return false;
  return !hubReachable() || !(stale.has(t.ref) || outdated(kept, t.file));
}

/** Tracks no kept list has any more leave the device. */
async function sweep() {
  const keep = needed();
  const gone = [...tracks.keys()].filter((r) => !keep.has(r));
  if (gone.length) {
    const cache = await caches.open(AUDIO);
    for (const ref of gone) {
      await cache.delete(audioKey(ref));
      await tx('tracks', 'readwrite', (s) => s.delete(ref));
      tracks.delete(ref);
    }
  }
  for (let i = waiting.length - 1; i >= 0; i--) if (!keep.has(waiting[i])) waiting.splice(i, 1);
  emit();
}

// --- what a page does -----------------------------------------------------------------------------------------

/** Keeps a list on the device: it is written down, and its tracks are fetched. */
export async function keepList(key: string, title: string, to: string, items: readonly TrackOut[]) {
  await initOffline();
  const list: KeptList = { key, title, to, refs: [...new Set(items.map((t) => t.ref))], at: Date.now() };
  lists.set(key, list);
  await tx('lists', 'readwrite', (s) => s.put(list));
  // the browser is asked not to clear this site's storage when the device runs short
  void navigator.storage?.persist?.().catch(() => undefined);
  enqueue(items);
  emit();
}

/** A kept list as the page has it now: new tracks are fetched, the ones taken out of it leave. */
export async function syncList(key: string, title: string, items: readonly TrackOut[]) {
  await initOffline();
  const had = lists.get(key);
  if (!had || !hubReachable()) return;
  const refs = [...new Set(items.map((t) => t.ref))];
  const same = had.title === title && had.refs.length === refs.length && had.refs.every((r, i) => r === refs[i]);
  if (!same) {
    const list = { ...had, title, refs };
    lists.set(key, list);
    await tx('lists', 'readwrite', (s) => s.put(list));
    await sweep();
  }
  enqueue(items); // also what failed last time
}

export async function dropList(key: string) {
  await initOffline();
  if (!lists.delete(key)) return;
  await tx('lists', 'readwrite', (s) => s.delete(key));
  await sweep();
}

/** Everything kept leaves the device (and what the pages read for this account: `forget` of the service worker). */
export async function clearOffline() {
  lists.clear();
  tracks.clear();
  failed.clear();
  waiting.length = 0;
  wanted = new Map();
  try {
    await tx('lists', 'readwrite', (s) => s.clear());
    await tx('tracks', 'readwrite', (s) => s.clear());
    await caches.delete(AUDIO);
  } catch (e) {
    console.warn('offline: could not clear', e);
  }
  emit();
}

/** The account leaves this device: nothing of it stays for the next one. */
export function forgetAccount() {
  if (!offlineSupported()) return;
  void clearOffline();
  navigator.serviceWorker.controller?.postMessage({ type: 'hm-forget' });
}

/** A track is kept on the device by some list (whether or not it is the track's file now). */
export const isKeptRef = (ref: string): boolean => tracks.has(ref);

/** What the Hub said of a kept track's file: the player shows it when the Hub cannot be asked. */
export const keptInfo = (ref: string): ListenOut | null => tracks.get(ref)?.info ?? null;
// --- for the screen -----------------------------------------------------------------------------------------

export interface ListState {
  kept: boolean;
  total: number;
  done: number;
  failed: number;
  busy: boolean;
}

export function useKeptList(key: string | null | undefined): ListState {
  useVersion();
  const l = key ? lists.get(key) : undefined;
  if (!l) return { kept: false, total: 0, done: 0, failed: 0, busy: false };
  const done = l.refs.filter((r) => tracks.has(r)).length;
  const bad = l.refs.filter((r) => failed.has(r)).length;
  return { kept: true, total: l.refs.length, done, failed: bad, busy: l.refs.some((r) => busy.has(r) || waiting.includes(r)) };
}

export function useKept(ref: string | null | undefined): boolean {
  useVersion();
  return !!ref && tracks.has(ref);
}

export function useKeptLists(): { lists: KeptList[]; bytes: number; tracks: number; sizeOf: (l: KeptList) => number; stateOf: (l: KeptList) => ListState } {
  useVersion();
  const stateOf = (l: KeptList): ListState => ({
    kept: true,
    total: l.refs.length,
    done: l.refs.filter((r) => tracks.has(r)).length,
    failed: l.refs.filter((r) => failed.has(r)).length,
    busy: l.refs.some((r) => busy.has(r) || waiting.includes(r)),
  });
  return {
    lists: [...lists.values()].sort((a, b) => b.at - a.at),
    bytes: [...tracks.values()].reduce((n, t) => n + t.size, 0),
    tracks: tracks.size,
    sizeOf: (l) => l.refs.reduce((n, r) => n + (tracks.get(r)?.size ?? 0), 0),
    stateOf,
  };
}

// the Hub is reachable again: what was waiting is fetched
onHubBack(() => {
  for (const ref of needed()) if ((!tracks.has(ref) || stale.has(ref)) && !waiting.includes(ref) && wanted.has(ref)) waiting.push(ref);
  pump();
});

// --- a kept list as a subscription ----------------------------------------------------------------------------

const SYNC_DELAY_MS = 3000;
const SYNC_GAP_MS = 30_000; // the library changes often while it downloads: the lists are read again at most this often
const SYNC_EVERY_MS = 60_000; // the app looked at again: not more often than this
let syncTimer: number | undefined;
let syncedAt = 0;
let syncing: Promise<void> | null = null;

/** A kept list as the Hub has it now (what has a file to play), or null when it is not there any more. */
async function listNow(key: string): Promise<{ title?: string; items: TrackOut[] } | null> {
  const playable = (items: readonly TrackOut[]) => items.filter((t) => !needsTrackFile(t));
  if (key === 'liked') return { items: playable((await api.ndLiked()).songs) };
  if (key.startsWith('playlist:')) {
    const p = await api.playlist(key.slice('playlist:'.length));
    return { title: p.name, items: playable(p.tracks) };
  }
  if (key.startsWith('foryou:')) return { items: playable((await api.forYouList(key.slice('foryou:'.length), {})).tracks) };
  return null;
}

/** «Скачивать „Любимое“»: the liked tracks kept on the device, new likes fetched as they come. */
export async function keepLiked(on: boolean): Promise<void> {
  if (!on) return dropList('liked');
  const now = await listNow('liked');
  if (now) await keepList('liked', 'Любимое', '/liked', now.items);
}

export function useLikedKept(): boolean {
  useVersion();
  return lists.has('liked');
}

/** Every kept list brought up to date with the Hub. */
export function syncKept(): Promise<void> {
  if (!offlineSupported()) return Promise.resolve();
  syncing ??= (async () => {
    await initOffline();
    syncedAt = Date.now();
    for (const list of [...lists.values()]) {
      if (!hubReachable()) break;
      try {
        const now = await listNow(list.key);
        if (now) await syncList(list.key, now.title ?? list.title, now.items);
      } catch (e) {
        console.info('offline: a kept list was not brought up to date', list.key, e);
      }
    }
  })().finally(() => { syncing = null; });
  return syncing;
}

function syncSoon() {
  if (!lists.size) return;
  window.clearTimeout(syncTimer);
  syncTimer = window.setTimeout(() => void syncKept(), Math.max(SYNC_DELAY_MS, syncedAt + SYNC_GAP_MS - Date.now()));
}

if (typeof window !== 'undefined') {
  // a ♥ given or taken back, a playlist changed, the library changed, a new issue of «Для вас»
  const LIST_CHANGES = new Set(['changed', 'imported', 'playlist', 'starred', 'unstarred', 'discoveries']);
  hubEvents.on('library', (e) => { if (LIST_CHANGES.has(e.kind)) syncSoon(); });
  hubEvents.on('reconnect', syncSoon);
  window.addEventListener('hm:hub-favorite', syncSoon);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - syncedAt > SYNC_EVERY_MS) syncSoon();
  });
  onHubBack(syncSoon);
}
