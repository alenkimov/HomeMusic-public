import { useSyncExternalStore } from 'react';
import { api, listenStreamUrl } from '../api/client';
import type { ListenOut, TrackOut } from '../api/types';

/** «Офлайн»: lists kept on the device — a playlist, «Любимое», a list of «Для вас» — so they play without the network.
 *
 * A list kept is written down here (IndexedDB: the list and each of its tracks, with what the Hub said of its file),
 * and the audio of its tracks is fetched one after another into the browser's cache (`hm-audio-v1`), under the very
 * address the player asks for. The service worker (`public/sw.js`) answers that address from the cache, so the
 * player plays a kept track without knowing it is kept — with the network and without. The lists and covers a page
 * reads are kept by the service worker as well, so the pages open offline.
 *
 * A track is kept while some kept list has it; a list opened again is brought up to date (`syncList`). Needs a
 * service worker, so a secure address (https or localhost). */

export interface KeptTrack {
  ref: string;
  info: ListenOut;
  track: TrackOut;
  size: number;
  at: number;
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
const audioKey = (ref: string) => new URL(listenStreamUrl(ref), window.location.origin).href;

// --- state --------------------------------------------------------------------------------------------------

const tracks = new Map<string, KeptTrack>();
const lists = new Map<string, KeptList>();
const failed = new Map<string, string>(); // ref -> why it was not fetched (tried again when its list is opened)
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
  })();
  return ready;
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
  if (info.kind === 'preview') throw new Error('есть только отрывок');
  // `X-HomeMusic-Prefetch`: this is no listen (the Hub counts a stream as one)
  const res = await fetch(listenStreamUrl(ref), { credentials: 'include', headers: { 'X-HomeMusic-Prefetch': '1' } });
  if (!res.ok) throw new Error(`сервер ответил ${res.status}`);
  const blob = await res.blob();
  if (!blob.size) throw new Error('пустой файл');
  if (!needed().has(ref)) return; // its list was let go meanwhile
  const type = res.headers.get('content-type') || 'audio/mpeg';
  await (await caches.open(AUDIO)).put(audioKey(ref), new Response(blob, { headers: { 'content-type': type, 'content-length': String(blob.size) } }));
  const kept: KeptTrack = { ref, info, track, size: blob.size, at: Date.now() };
  await tx('tracks', 'readwrite', (s) => s.put(kept));
  tracks.set(ref, kept);
  // its covers go through the service worker, which keeps them
  for (const url of new Set([track.cover_small_url, track.cover_url])) if (url?.startsWith('/')) void fetch(url, { credentials: 'include' }).catch(() => undefined);
}

function pump() {
  while (busy.size < PARALLEL && waiting.length && navigator.onLine) {
    const ref = waiting.shift() as string;
    if (tracks.has(ref) || busy.has(ref) || !needed().has(ref)) continue;
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

function enqueue(items: readonly TrackOut[]) {
  for (const t of items) {
    wanted.set(t.ref, t);
    if (!tracks.has(t.ref) && !busy.has(t.ref) && !waiting.includes(t.ref)) waiting.push(t.ref);
  }
  pump();
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
  if (!had || !navigator.onLine) return;
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

// --- the network ----------------------------------------------------------------------------------------------

const netSubs = (cb: () => void) => {
  window.addEventListener('online', cb);
  window.addEventListener('offline', cb);
  return () => {
    window.removeEventListener('online', cb);
    window.removeEventListener('offline', cb);
  };
};
/** The device has a network (as the browser tells it). */
export const useOnline = (): boolean => useSyncExternalStore(netSubs, () => navigator.onLine, () => true);

if (typeof window !== 'undefined') {
  // the network is back: what was waiting is fetched
  window.addEventListener('online', () => {
    for (const ref of needed()) if (!tracks.has(ref) && !waiting.includes(ref) && wanted.has(ref)) waiting.push(ref);
    pump();
  });
}
