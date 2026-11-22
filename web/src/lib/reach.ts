import { useSyncExternalStore } from 'react';

/** Whether the Hub answers — the one thing the app means by «online». `navigator.onLine` only says the device is on
 * some network: on Wi-Fi without the internet, on a weak signal, or with the computer or the tunnel down it says
 * «yes» while every request hangs or gets a 502 from the gateway. What the app knows of the Hub comes instead from
 * the Hub itself:
 * - every request made with `hubFetch`: an answer says it is reachable; not so a 502/503/504 without the Hub's
 *   mark (`x-homemusic-hub`: the gateway on the VPS answering for a Hub it cannot reach — the Hub's own 502 of a
 *   catalog that did not answer is an answer) or a copy the service worker gave instead (`x-homemusic-kept`);
 * - a request that fails or hangs past `SLOW_MS` and the event stream dropping (`lib/events.ts`) make it ask
 *   `/api/ping`, which decides (while the Hub is known not to answer, they say nothing new: the retries ask);
 * - while the Hub is not reachable it is asked again, less and less often, and at once when the page comes back on
 *   screen or the device says it has a network again. The device saying it has none is taken at its word.
 * Nothing is asked while the Hub answers: the requests the app makes anyway tell. */

const PING = '/api/ping';
const PING_TIMEOUT_MS = 5000;
/** A request without an answer this long: the Hub is asked whether it is there at all. */
const SLOW_MS = 4000;
const RETRY_FIRST_MS = 2000;
const RETRY_MAX_MS = 30_000;
const GATEWAY_FAILED = new Set([502, 503, 504]);
/** Set by the service worker on what it answers from its copy because the Hub did not (`public/sw.js`). */
const KEPT_HEADER = 'x-homemusic-kept';
/** On every answer of the Hub itself (`hub/app/main.py`, `HubMark`). */
const HUB_HEADER = 'x-homemusic-hub';

let reachable = navigator.onLine;
const subs = new Set<(reachable: boolean) => void>();
let pinging: Promise<void> | null = null;
let retryTimer: number | undefined;
let retryDelay = RETRY_FIRST_MS;

function set(next: boolean) {
  if (!next) scheduleRetry();
  if (next === reachable) return;
  reachable = next;
  if (next) {
    window.clearTimeout(retryTimer);
    retryTimer = undefined;
    retryDelay = RETRY_FIRST_MS;
  }
  for (const fn of subs) fn(next);
}

function scheduleRetry() {
  if (retryTimer !== undefined || document.hidden) return; // a hidden page is asked again when it is shown
  retryTimer = window.setTimeout(() => {
    retryTimer = undefined;
    void ping();
  }, retryDelay);
  retryDelay = Math.min(RETRY_MAX_MS, retryDelay * 2);
}

/** Asks the Hub whether it is there; one question at a time. */
function ping(): Promise<void> {
  if (!navigator.onLine) {
    set(false);
    return Promise.resolve();
  }
  pinging ??= fetch(PING, { cache: 'no-store', credentials: 'include', signal: AbortSignal.timeout(PING_TIMEOUT_MS) })
    .then((res) => set(res.ok), () => set(false))
    .finally(() => (pinging = null));
  return pinging;
}

/** Something answered a request to the Hub (`header` reads the answer's headers): reachable, unless it was the
 * gateway or the service worker's copy. */
export function noteAnswer(status: number, header: (name: string) => string | null) {
  const gateway = GATEWAY_FAILED.has(status) && header(HUB_HEADER) === null;
  set(status !== 0 && !gateway && header(KEPT_HEADER) === null);
}

/** A request to the Hub failed or is late: asked whether the Hub is there — unless it is known not to be. */
export function suspect() {
  if (reachable) void ping();
}

/** The Hub answered (the event stream opened). */
export const noteReached = () => set(true);

/** The service worker's copies of what pages read (`public/sw.js`, `API`), by the request's full URL. */
const API_CACHE = 'hm-api-v1';

/** The copy of a read kept by the service worker, if it kept one. */
async function keptCopy(input: string): Promise<Response | undefined> {
  if (typeof caches === 'undefined') return undefined;
  try {
    return await (await caches.open(API_CACHE)).match(new URL(input, location.href).href);
  } catch {
    return undefined;
  }
}

/** `fetch` of the Hub, which tells what came of it (see above). A read (GET) without the Hub is given the copy the
 * service worker kept of it: at once while the Hub is known not to answer, and in place of the answer when the Hub
 * is found gone while the read is on its way (a request that hangs on a network without the internet). However
 * slow, an answer from a Hub that is there is waited for: a slow answer is not a lost connection. */
export async function hubFetch(input: string, init?: RequestInit): Promise<Response> {
  const read = (init?.method ?? 'GET').toUpperCase() === 'GET';
  if (read && !reachable) {
    const kept = await keptCopy(input);
    if (kept) return kept;
  }
  const lost = new AbortController();
  const signal = init?.signal ? AbortSignal.any([init.signal, lost.signal]) : lost.signal;
  const off = read ? onHubReach((r) => { if (!r) lost.abort(); }) : () => {};
  const slow = window.setTimeout(suspect, SLOW_MS);
  try {
    const res = await fetch(input, { ...init, signal });
    noteAnswer(res.status, (name) => res.headers.get(name));
    return res;
  } catch (e) {
    if (init?.signal?.aborted) throw e;
    if (lost.signal.aborted) {
      const kept = await keptCopy(input);
      if (kept) return kept;
      throw new TypeError('Хаб недоступен');
    }
    suspect();
    throw e;
  } finally {
    window.clearTimeout(slow);
    off();
  }
}

export const hubReachable = (): boolean => reachable;

/** Called with the new state each time it changes. Returns the unsubscribe. */
export function onHubReach(fn: (reachable: boolean) => void): () => void {
  subs.add(fn);
  return () => subs.delete(fn);
}

/** Called each time the Hub can be reached again after it could not. */
export const onHubBack = (fn: () => void): (() => void) => onHubReach((r) => r && fn());

export const useHubReachable = (): boolean =>
  useSyncExternalStore((cb) => onHubReach(cb), hubReachable, () => true);

window.addEventListener('offline', () => set(false));
window.addEventListener('online', () => void ping());
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    window.clearTimeout(retryTimer);
    retryTimer = undefined;
  } else if (!reachable) void ping();
});
void ping();
