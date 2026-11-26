import { useSyncExternalStore } from 'react';
import { api, listenStreamUrl } from '../api/client';
import type { ListenOut } from '../api/types';
import { AS_IT_IS } from './formats';
import { fileReplaced as keptFileReplaced, isKeptRef, offlineSupported } from './offline';
import { hubReachable } from './reach';
import type { LikeTarget } from './stars';

/** «Кеш прослушанного»: what was listened to stays on the device, without asking — apart from what is kept for
 * offline (`lib/offline.ts`: the lists the listener keeps, for good). A track heard again starts at once and plays
 * without the network; so do the next ones of a queue, fetched ahead (`listen.ts`, `prefetchNext`).
 *
 * The audio is in the browser's cache (`hm-heard-v1`) under the address the player asks for, and the service worker
 * (`public/sw.js`) answers from it when the address says `heard=1`: the player says so only while the copy is the
 * track's file (`heardPlays`) — a file put another in its place is fetched anew, as a kept one is. What is kept here
 * has a limit (`heardLimit`, chosen in «Хранилище»): past it, the longest unheard go first, and of them first the
 * ones neither liked nor in a playlist. */

const DB = 'homemusic-heard';
const CACHE = 'hm-heard-v1';
const LIMIT_KEY = 'hm-heard-limit-gb';
export const HEARD_LIMITS_GB = [0, 1, 2, 5, 10, 20, 50] as const;
const DEFAULT_GB = 5;
const WAIT_MS = 5000; // a track starts first; its copy is fetched once it plays

export interface HeardTrack {
  ref: string; // the address the player asks for (`lib:<id>` for the library's tracks)
  alias: string; // the track's own ref, as a row knows it
  info: ListenOut;
  like: LikeTarget;
  songId: string | null; // the playlists' key of it (`lib/inPlaylists.ts`)
  size: number;
  at: number; // last heard
}

const index = new Map<string, HeardTrack>();
const byAlias = new Map<string, string>();
const queue: { ref: string; alias: string; like: LikeTarget; songId: string | null; uid: string | null }[] = [];
let running = false;
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
const keyOf = (ref: string) => new URL(listenStreamUrl(ref, AS_IT_IS), window.location.origin).href;

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore('tracks', { keyPath: 'ref' });
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T> | void): Promise<T | undefined> {
  const db = await open();
  try {
    return await new Promise<T | undefined>((resolve, reject) => {
      const t = db.transaction('tracks', mode);
      const req = run(t.objectStore('tracks'));
      t.oncomplete = () => resolve(req ? req.result : undefined);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    });
  } finally {
    db.close();
  }
}

function put(h: HeardTrack) {
  index.set(h.ref, h);
  byAlias.set(h.alias, h.ref);
}

/** Reads what was kept (once). */
export function initHeard(): Promise<void> {
  if (!offlineSupported()) return Promise.resolve();
  ready ??= (async () => {
    try {
      for (const h of ((await tx<HeardTrack[]>('readonly', (s) => s.getAll())) ?? [])) put(h);
    } catch (e) {
      console.warn('heard: what was kept could not be read', e);
    }
    emit();
  })();
  return ready;
}

export function heardLimit(): number {
  try {
    const v = Number(localStorage.getItem(LIMIT_KEY));
    if (localStorage.getItem(LIMIT_KEY) !== null && Number.isFinite(v) && v >= 0) return v * 2 ** 30;
  } catch { /* storage unavailable */ }
  return DEFAULT_GB * 2 ** 30;
}

export function setHeardLimit(gb: number) {
  try { localStorage.setItem(LIMIT_KEY, String(gb)); } catch { /* storage unavailable */ }
  emit();
  void evict();
}

const uidOf = (file: { uid?: string | null } | null | undefined) => file?.uid ?? null;

/** The copy is the track's file (a track that does not say its file is taken to be the same), or the Hub cannot be
 * reached (the old file is better than none). */
export function heardPlays(ref: string, file?: { uid?: string | null } | null): boolean {
  const h = index.get(ref);
  if (!h) return false;
  const uid = uidOf(file);
  return !hubReachable() || !uid || !h.info.file_uid || h.info.file_uid === uid;
}

/** What the Hub said of the file when it was kept: the player shows it when the Hub cannot be asked. */
export const heardInfo = (ref: string): ListenOut | null => index.get(ref)?.info ?? null;

/** A track is played (or is next): touched if it is kept, else fetched — once it has had its moment to start. */
export function noteHeard(ref: string, alias: string, like: LikeTarget, songId: string | null, file?: { uid?: string | null } | null, now = false) {
  if (!offlineSupported() || ref.startsWith('upload:') || heardLimit() === 0) return;
  void initHeard().then(() => {
    const h = index.get(ref);
    if (h && heardPlays(ref, file)) {
      const next = { ...h, at: Date.now(), like, songId: songId ?? h.songId };
      put(next);
      void tx('readwrite', (s) => s.put(next)).catch(() => undefined);
      return;
    }
    if (isKeptRef(ref) || isKeptRef(alias) || queue.some((q) => q.ref === ref)) return;
    const item = { ref, alias, like, songId, uid: uidOf(file) };
    window.setTimeout(() => {
      queue.push(item);
      void pump();
    }, now ? 0 : WAIT_MS);
  });
}

async function pump() {
  if (running) return;
  running = true;
  try {
    while (queue.length && hubReachable()) {
      const q = queue.shift()!;
      try {
        await fetchOne(q.ref, q.alias, q.like, q.songId);
      } catch (e) {
        console.info('heard: not kept', q.ref, e);
      }
    }
  } finally {
    running = false;
  }
}

async function fetchOne(ref: string, alias: string, like: LikeTarget, songId: string | null) {
  const info = await api.listen(ref);
  // `X-HomeMusic-Prefetch`: no listen of its own (the Hub counts a stream as one)
  const res = await fetch(listenStreamUrl(ref), { credentials: 'include', headers: { 'X-HomeMusic-Prefetch': '1' } });
  if (!res.ok) throw new Error(`сервер ответил ${res.status}`);
  const blob = await res.blob();
  if (!blob.size) throw new Error('пустой файл');
  const type = res.headers.get('content-type') || 'audio/mpeg';
  await (await caches.open(CACHE)).put(keyOf(ref), new Response(blob, { headers: { 'content-type': type, 'content-length': String(blob.size) } }));
  const h: HeardTrack = { ref, alias, info, like, songId, size: blob.size, at: Date.now() };
  await tx('readwrite', (s) => s.put(h));
  // the same track kept under another address (heard from the catalog, now the library's): one copy of it is enough
  const before = byAlias.get(alias);
  put(h);
  if (before && before !== ref) await drop(before);
  // the browser is asked not to clear this site's storage when the device runs short
  void navigator.storage?.persist?.().catch(() => undefined);
  emit();
  await evict();
}

/** Past the limit: the longest unheard go, the ones neither liked nor in a playlist first. (The likes and the
 * playlists are read when needed: the player imports this module, and they import the player.) */
async function evict() {
  const limit = heardLimit();
  let total = [...index.values()].reduce((n, h) => n + h.size, 0);
  if (total <= limit) return;
  const [{ isLiked }, { inPlaylists }] = await Promise.all([import('./stars'), import('./inPlaylists')]);
  const valued = (h: HeardTrack) => isLiked(h.like) || inPlaylists(h.songId) || isKeptRef(h.alias);
  const order = [...index.values()].sort((a, b) => Number(valued(a)) - Number(valued(b)) || a.at - b.at);
  for (const h of order) {
    if (total <= limit) break;
    await drop(h.ref);
    total -= h.size;
  }
  emit();
}

async function drop(ref: string) {
  const h = index.get(ref);
  if (!h) return;
  index.delete(ref);
  if (byAlias.get(h.alias) === ref) byAlias.delete(h.alias);
  try {
    await (await caches.open(CACHE)).delete(keyOf(ref));
    await tx('readwrite', (s) => s.delete(ref));
  } catch { /* gone already */ }
}

/** The file of a track was put another in its place: its copy here goes (the next listen fetches the new one). */
export function heardFileReplaced(track: { ref?: string | null; library_track_id?: number | null }) {
  const refs = [...index.values()].filter((h) => h.ref === track.ref || h.alias === track.ref
    || (track.library_track_id != null && h.ref === `lib:${track.library_track_id}`)).map((h) => h.ref);
  if (!refs.length) return;
  void Promise.all(refs.map(drop)).then(emit);
}

/** The file of a track was put another in its place here (`FindFile`, «Качество звука»): what the device has of it,
 * kept or heard, is fetched anew. */
export function fileReplaced(track: { ref?: string | null; library_track_id?: number | null }) {
  keptFileReplaced(track);
  heardFileReplaced(track);
}

export async function clearHeard() {
  index.clear();
  byAlias.clear();
  queue.length = 0;
  try {
    await tx('readwrite', (s) => s.clear());
    await caches.delete(CACHE);
  } catch (e) {
    console.warn('heard: could not clear', e);
  }
  emit();
}

/** The track (by the ref a row has, or the library's id) is on the device from listening. */
export function isHeard(ref: string, libraryId?: number | null): boolean {
  return index.has(ref) || byAlias.has(ref) || (libraryId != null && index.has(`lib:${libraryId}`));
}

export function useHeard(ref: string, libraryId?: number | null): boolean {
  useSyncExternalStore(subscribe, () => version, () => 0);
  return isHeard(ref, libraryId);
}

export function useHeardUsage(): { count: number; bytes: number; limit: number } {
  useSyncExternalStore(subscribe, () => version, () => 0);
  return { count: index.size, bytes: [...index.values()].reduce((n, h) => n + h.size, 0), limit: heardLimit() };
}
