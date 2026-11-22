import { useSyncExternalStore } from 'react';
import { api, apiSessionSignal, errorMessage, listenStreamUrl } from '../api/client';
import type { ListenOut, WaveTrackOut } from '../api/types';
import { offlineSupported } from './offline';
import { hubFetch, hubReachable, onHubBack } from './reach';

export const OFFLINE_WAVE = 'wave:offline';
export const WAVE_SIZES = [0, 10, 20, 30] as const;
export type WaveSize = (typeof WAVE_SIZES)[number];
export const waveBudget = (size: WaveSize) => size * 12 * 1024 * 1024;
const CACHE = 'hm-wave-audio-v1';
const KEY = 'hm-offline-wave:';
const RETRY_MS = 5 * 60 * 1000;
const RECENT_LIMIT = 120;

interface Entry { track: WaveTrackOut; info: ListenOut; bytes: number }
interface Saved { size: WaveSize; entries: Entry[]; recent: string[] }
interface Scope { account: number; session?: string; attemptedAt: number; job?: AbortController; primed?: ServiceWorker }

const EMPTY: Saved = { size: 0, entries: [], recent: [] };
let scope: Scope | null = null;
let saved = EMPTY;
let problem: string | null = null;
let version = 0;
const listeners = new Set<() => void>();

const emit = () => { version++; listeners.forEach((fn) => fn()); };
const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
};
const audioUrl = (account: number, ref: string) => `${listenStreamUrl(ref)}&wave=${account}`;
const isUnplayed = (entry: Entry) => !saved.recent.includes(entry.track.ref);
const space = () => saved.entries.reduce((sum, entry) => sum + entry.bytes, 0);

function read(account: number): Saved {
  try {
    const data = JSON.parse(localStorage.getItem(KEY + account) ?? 'null') as Saved | null;
    if (data && WAVE_SIZES.includes(data.size) && Array.isArray(data.entries) && Array.isArray(data.recent)) {
      return { size: data.size,
        entries: data.entries.filter((entry) => typeof entry?.track?.ref === 'string' && !!entry.info
          && Number.isFinite(entry.bytes) && entry.bytes > 0),
        recent: data.recent.filter((ref) => typeof ref === 'string').slice(0, RECENT_LIMIT) };
    }
  } catch { /* The browser may have cleared storage. */ }
  return EMPTY;
}

function write(active: Scope, data: Saved) {
  if (scope !== active) return;
  localStorage.setItem(KEY + active.account, JSON.stringify(data));
  saved = data;
  emit();
}

async function withLock(active: Scope, run: () => Promise<void>, ifAvailable = false) {
  if (navigator.locks) {
    await navigator.locks.request(KEY + active.account, { ifAvailable }, async (lock) => {
      if (lock) await run();
    });
  } else {
    await run();
  }
}

/** Remove spent entries first. A smaller user-selected limit can also remove old unplayed entries. */
async function fit(active: Scope, cache: Cache, count: number, bytes: number, resize: boolean) {
  let total = space();
  const kept = [...saved.entries];
  const removed: Entry[] = [];
  while (kept.length > count || total > bytes) {
    // Keep the most recent track available if it is still playing or is restored after a reload.
    let at = kept.findIndex((entry) => !isUnplayed(entry) && entry.track.ref !== saved.recent[0]);
    if (at === -1 && resize) at = 0;
    if (at === -1) return false;
    const [entry] = kept.splice(at, 1);
    removed.push(entry);
    total -= entry.bytes;
  }
  if (removed.length) {
    write(active, { ...saved, entries: kept });
    for (const entry of removed) await cache.delete(audioUrl(active.account, entry.track.ref));
  }
  return true;
}

async function checkCache(active: Scope) {
  await withLock(active, async () => {
    if (scope !== active) return;
    const cache = await caches.open(CACHE);
    const own = (await cache.keys()).filter((req) =>
      new URL(req.url).searchParams.get('wave') === String(active.account));
    if (scope !== active) return;
    saved = read(active.account);
    const address = (entry: Entry) => new URL(audioUrl(active.account, entry.track.ref), location.origin).href;
    const keys = new Set(own.map((req) => req.url));
    const entries = saved.entries.filter((entry) => keys.has(address(entry)));
    if (entries.length !== saved.entries.length) write(active, { ...saved, entries });
    // A page may close between writing the audio and committing its metadata.
    const known = new Set(entries.map(address));
    await Promise.all(own.filter((req) => !known.has(req.url)).map((req) => cache.delete(req)));
    if (scope !== active) return;
    await fit(active, cache, saved.size, waveBudget(saved.size), true);
  });
}

export function setOfflineWaveAccount(account: number | null) {
  if (scope?.account === account || (!scope && account == null)) return;
  const previous = scope;
  if (previous) {
    previous.job?.abort();
    void withLock(previous, async () => {
      if (scope?.account === previous.account) return;
      localStorage.removeItem(KEY + previous.account);
      const cache = await caches.open(CACHE);
      await Promise.all((await cache.keys()).filter((req) =>
        new URL(req.url).searchParams.get('wave') === String(previous.account)).map((req) => cache.delete(req)));
    }).catch(console.warn);
  }
  scope = account != null && offlineSupported() ? { account, attemptedAt: 0 } : null;
  saved = scope ? read(scope.account) : EMPTY;
  problem = null;
  emit();
  if (scope) void checkCache(scope).then(() => refreshWaveReserve()).catch(console.warn);
}

// On the first launch /me may finish before the new worker claims the page. Warm its offline copy as soon as it can.
function warmOfflineIdentity(active: Scope) {
  const controller = navigator.serviceWorker.controller;
  if (!controller || controller === active.primed) return;
  active.primed = controller;
  void api.me(apiSessionSignal()).catch(() => { if (active.primed === controller) active.primed = undefined; });
}

export function useWaveReserve() {
  useSyncExternalStore(subscribe, () => version, () => 0);
  return { size: saved.size, ready: saved.entries.filter(isUnplayed).length, bytes: space(),
    busy: !!scope?.job, problem };
}

export const offlineWaveTracks = (): WaveTrackOut[] => saved.size ? saved.entries.filter(isUnplayed).map((entry) => entry.track) : [];
export const waveReserveEnabled = () => saved.size > 0;

export function waveAudio(ref: string) {
  const entry = saved.entries.find((e) => e.track.ref === ref);
  return scope && saved.size && entry ? { url: audioUrl(scope.account, ref), info: entry.info } : null;
}

export async function setWaveReserveSize(size: WaveSize) {
  const active = scope;
  if (!active) return;
  active.job?.abort();
  active.job = undefined;
  active.attemptedAt = 0;
  problem = null;
  try {
    // Persist the new limit immediately so a downloader in another tab stops on its next check.
    write(active, { ...read(active.account), size });
    if (size) void navigator.storage?.persist?.().catch(() => undefined);
    await checkCache(active);
    if (scope === active && saved.size) await refreshWaveReserve(true);
  } catch (e) {
    if (scope === active) { problem = errorMessage(e); emit(); }
  }
}

export function heardWaveTrack(ref: string) {
  const active = scope;
  if (!active || !saved.size || saved.recent[0] === ref) return;
  try {
    const data = read(active.account);
    if (!data.size) return;
    write(active, { ...data, recent: [ref, ...data.recent.filter((old) => old !== ref)].slice(0, RECENT_LIMIT) });
    void refreshWaveReserve();
  } catch (e) { console.warn('offline wave: could not remember a played track', e); }
}

export function notePersonalWave(session: string) {
  if (scope && scope.session !== session) {
    scope.session = session;
    scope.attemptedAt = 0;
  }
  void refreshWaveReserve();
}

async function download(active: Scope, track: WaveTrackOut, signal: AbortSignal, limit: number) {
  const ref = track.library_track_id ? `lib:${track.library_track_id}` : track.ref;
  const info = await api.listen(ref, signal);
  const response = await hubFetch(listenStreamUrl(ref), { signal, cache: 'no-store', credentials: 'include', headers: {
    'X-HomeMusic-Account': String(active.account), 'X-HomeMusic-Prefetch': '1',
  } });
  if (response.status !== 200 || !response.body) throw new Error('Не удалось скачать аудио целиком');
  const declared = Number(response.headers.get('content-length'));
  if (declared > limit) {
    await response.body.cancel();
    return null;
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) { await reader.cancel(); return null; }
    chunks.push(value as Uint8Array<ArrayBuffer>);
  }
  if (!size || (declared && declared !== size && !response.headers.get('content-encoding'))) throw new Error('Аудиофайл скачался не полностью');
  return { info, blob: new Blob(chunks, { type: response.headers.get('content-type') ?? 'audio/mpeg' }) };
}

export async function refreshWaveReserve(force = false) {
  const active = scope;
  if (!active || !saved.size || !hubReachable() || document.hidden || active.job) return;
  warmOfflineIdentity(active);
  if (!force && (saved.entries.filter(isUnplayed).length >= saved.size / 2
    || Date.now() - active.attemptedAt < RETRY_MS)) return;
  const job = new AbortController();
  const signal = AbortSignal.any([job.signal, apiSessionSignal()]);
  active.job = job;
  problem = null;
  emit();
  try {
    await withLock(active, async () => {
      signal.throwIfAborted();
      saved = read(active.account);
      const size = saved.size;
      const remaining = size - saved.entries.filter(isUnplayed).length;
      if (remaining <= 0) return;
      active.attemptedAt = Date.now();
      const known = saved.entries.flatMap(({ track }) => [
        track.ref,
        track.nd_song_id ? `nd:${track.nd_song_id}` : track.library_track_id ? `lib:${track.library_track_id}` : track.ref,
      ]);
      const tracks = await api.waveReserve({ limit: Math.min(60, remaining * 2),
        exclude: [...new Set([...saved.recent, ...known])], session_id: active.session }, signal);
      if (!tracks.length) {
        if (!saved.entries.length) problem = 'Запустите «Мою волну» с интернетом, чтобы собрать запас';
        return;
      }
      const cache = await caches.open(CACHE);
      let added = 0;
      for (const track of tracks) {
        signal.throwIfAborted();
        saved = read(active.account);
        if (saved.size !== size || saved.entries.filter(isUnplayed).length >= size) break;
        const occupied = saved.entries.filter((entry) => isUnplayed(entry) || entry.track.ref === saved.recent[0])
          .reduce((sum, entry) => sum + entry.bytes, 0);
        const max = waveBudget(size) - occupied;
        if (max <= 0) break;
        if (saved.recent.includes(track.ref) || saved.entries.some((entry) => entry.track.ref === track.ref)) continue;
        let ready: Awaited<ReturnType<typeof download>>;
        try {
          ready = await download(active, track, AbortSignal.any([signal, AbortSignal.timeout(90000)]), max);
        } catch (e) {
          signal.throwIfAborted();
          if (!hubReachable()) return;
          console.info('offline wave: track skipped', errorMessage(e));
          continue;
        }
        signal.throwIfAborted();
        saved = read(active.account);
        if (saved.size !== size) break;
        if (!ready || saved.recent.includes(track.ref)) continue;
        if (!await fit(active, cache, size - 1, waveBudget(size) - ready.blob.size, false)) continue;
        const url = audioUrl(active.account, track.ref);
        signal.throwIfAborted();
        await cache.put(url, new Response(ready.blob, { headers: { 'content-type': ready.blob.type } }));
        try {
          signal.throwIfAborted();
          const latest = read(active.account);
          if (latest.size !== size || latest.recent.includes(track.ref)) {
            await cache.delete(url);
            continue;
          }
          write(active, { ...latest, entries: [...latest.entries, { track, info: ready.info, bytes: ready.blob.size }] });
          added++;
        } catch (e) {
          await cache.delete(url);
          throw e;
        }
      }
      if (!added && !saved.entries.length) problem = 'Пока не удалось скачать треки для запаса';
    }, !force);
  } catch (e) {
    if (!signal.aborted) problem = errorMessage(e);
  } finally {
    if (scope === active && active.job === job) { active.job = undefined; emit(); }
  }
}

window.addEventListener('storage', (event) => {
  const active = scope;
  if (!active || event.key !== KEY + active.account) return;
  const data = read(active.account);
  if (data.size !== saved.size) active.job?.abort();
  saved = data;
  emit();
});
onHubBack(() => void refreshWaveReserve());
document.addEventListener('visibilitychange', () => { if (!document.hidden) void refreshWaveReserve(); });
navigator.serviceWorker?.addEventListener('controllerchange', () => {
  if (scope && saved.size && hubReachable()) warmOfflineIdentity(scope);
});
window.setInterval(() => void refreshWaveReserve(), RETRY_MS);
