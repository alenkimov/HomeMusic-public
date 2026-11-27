import { ApiError, api } from '../api/client';
import type { DislikeIn, StarIn } from '../api/types';
import { listeningAccount } from './plays';
import { onHubBack } from './reach';
import { toast } from './ui';

/** What was done without the Hub — a ♥, «Не нравится», a track put into a playlist or taken out of it — kept on the
 * device and sent once the Hub is reached again: the outbox of the offline-first players (Symfonium; TanStack DB's
 * offline transactions). The listening events have their own (`lib/plays.ts`).
 *
 * - Shown at once: the page shows the change as done (the ♥ is on) and the change waits here, in local storage, so it
 *   outlives the page (`hm-outbox:<id>`).
 * - Sent in the order made, one after another, by one tab at a time (Web Locks), when the Hub is back, when the app
 *   opens and every while after a failure (30 s, doubling, up to half an hour).
 * - Sent twice, it is done once: each change is the same request every time — a ♥ with its own `event_id` and the
 *   time it was given (the Hub keeps one decision of an id, and of two decisions of a track the later one: a ♥ given
 *   here offline and taken back on the computer meanwhile stays taken back); «Не нравится» on and off, a track into
 *   playlists and out of them are states, not steps.
 * - The later change of the same thing replaces the earlier one still waiting (♥ on, then off: only «off» goes).
 * - No network, the Hub not answering (502–504 of the gateway, 5xx, 408, 429), the login expired (401): it waits. Any
 *   other refusal (4xx: a track the Hub does not know, a playlist deleted) is final: the change is dropped, the page
 *   takes the Hub's state back and says so.
 *
 * Creating, renaming or deleting a playlist and taking a track out of a playlist's page by its place need the Hub —
 * as Symfonium's «online first» playlists: a place in a list edited on two devices has no answer that is not a
 * guess. */

export type Change =
  /** `aliases`: of each of `body.targets`, the names the page knows it by (`lib/stars.ts`) */
  | { kind: 'star'; key: string; aliases: string[][]; liked: boolean; body: StarIn }
  | { kind: 'dislike'; key: string; on: boolean; body: DislikeIn }
  | { kind: 'membership'; key: string; tracks: string[]; add: string[]; remove: string[] };

interface Entry {
  id: string;
  at: number;
  account: number | null;
  change: Change;
}

const PREFIX = 'hm-outbox:';
const FIRST_RETRY_MS = 30_000;
const LAST_RETRY_MS = 1_800_000;

let entries: Entry[] = load();
let sending = false;
let failures = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
const subs = new Set<() => void>();
const settled = new Set<(change: Change, result: unknown) => void>();
const dropped = new Set<(change: Change) => void>();

function load(): Entry[] {
  const out: Entry[] = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k?.startsWith(PREFIX)) continue;
      const e = JSON.parse(localStorage.getItem(k) ?? 'null') as Entry | null;
      if (e?.id && e.change) out.push(e);
    }
  } catch (e) {
    console.error('outbox: what waits could not be read', e);
  }
  return out.sort((a, b) => a.at - b.at);
}

const emit = () => subs.forEach((f) => f());

function newId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  }
}

function forget(id: string) {
  entries = entries.filter((e) => e.id !== id);
  try {
    localStorage.removeItem(PREFIX + id);
  } catch { /* storage gone: it goes with the page */ }
}

/** The Hub is not there, or not now: try again later. Anything else it said is its answer. */
export function waits(e: unknown): boolean {
  if (e instanceof ApiError) return e.status === 0 || e.status === 401 || e.status === 408 || e.status === 425 || e.status === 429 || e.status >= 500;
  const name = (e as Error | undefined)?.name;
  return name === 'TimeoutError' || name === 'AbortError' || e instanceof TypeError;
}

/** Keep a change to send later (it replaces a waiting change of the same thing). False: the device keeps nothing
 * (storage full or off) — the change is lost with the page, the caller says so. */
export function keep(change: Change): boolean {
  const same = entries.filter((x) => x.change.kind === change.kind && x.change.key === change.key);
  // the tracks put into some playlists, then into others: one change of them all, the later choice over the earlier
  if (change.kind === 'membership') {
    const now = change;
    const add = new Set<string>(now.add);
    const remove = new Set<string>(now.remove);
    for (const e of same) if (e.change.kind === 'membership') {
      e.change.add.forEach((x) => { if (!remove.has(x)) add.add(x); });
      e.change.remove.forEach((x) => { if (!add.has(x)) remove.add(x); });
    }
    change = { ...now, add: [...add], remove: [...remove] };
  }
  for (const e of same) forget(e.id);
  const entry: Entry = { id: newId(), at: Date.now(), account: listeningAccount(), change };
  try {
    localStorage.setItem(PREFIX + entry.id, JSON.stringify(entry));
  } catch (e) {
    console.error('outbox: a change could not be kept', e);
    return false;
  }
  entries.push(entry);
  emit();
  soon(FIRST_RETRY_MS);
  return true;
}

/** What waits (of this account), oldest first. */
export const waiting = (): Change[] => entries.filter((e) => e.account === listeningAccount()).map((e) => e.change);

export function onChange(fn: () => void): () => void {
  subs.add(fn);
  return () => void subs.delete(fn);
}
/** A change the Hub took: its answer (the ♥'s journal id). */
export function onSettled(fn: (change: Change, result: unknown) => void): () => void {
  settled.add(fn);
  return () => void settled.delete(fn);
}
/** A change the Hub refused for good: the page takes its state back. */
export function onDropped(fn: (change: Change) => void): () => void {
  dropped.add(fn);
  return () => void dropped.delete(fn);
}

function send(c: Change): Promise<unknown> {
  const signal = AbortSignal.timeout(15_000);
  if (c.kind === 'star') return api.star(c.body, signal);
  if (c.kind === 'dislike') {
    const b = c.body;
    return c.on ? api.dislike(b) : api.undislike({ track_id: b.track_id, ref: b.ref, nd_song_id: b.nd_song_id, artist_ref: b.artist_ref });
  }
  return c.tracks.length === 1
    ? api.playlistMembership({ track: c.tracks[0], add: c.add, remove: c.remove })
    : api.playlistMembershipMany({ tracks: c.tracks, add: c.add, remove: c.remove });
}

const WHAT: Record<Change['kind'], string> = { star: 'отметку «Любимое»', dislike: 'отметку «Не нравится»', membership: 'изменение плейлистов' };

async function drain(): Promise<void> {
  for (;;) {
    const account = listeningAccount();
    if (account === null) {
      if (entries.length) throw new Error('outbox: the account is not known yet'); // tried again a little later
      return;
    }
    const entry = entries.find((e) => e.account === account);
    if (!entry) return;
    try {
      const result = await send(entry.change);
      forget(entry.id);
      failures = 0;
      settled.forEach((f) => f(entry.change, result));
      emit();
    } catch (e) {
      if (waits(e)) throw e;
      forget(entry.id);
      dropped.forEach((f) => f(entry.change));
      emit();
      toast(`Сервер не принял ${WHAT[entry.change.kind]}, сделанное без связи: ${e instanceof Error ? e.message : 'ошибка'}`, 'error');
    }
  }
}

/** Send what waits, now. */
export async function flushOutbox(): Promise<void> {
  if (sending || !entries.length) return;
  sending = true;
  clearTimeout(timer);
  try {
    // one tab sends; the others find the lock held and leave it to it
    if (navigator.locks) await navigator.locks.request('hm-outbox', { ifAvailable: true }, async (lock) => { if (lock) await drain(); });
    else await drain();
  } catch {
    failures++;
    soon(Math.min(FIRST_RETRY_MS * 2 ** Math.min(failures - 1, 6), LAST_RETRY_MS));
  } finally {
    sending = false;
  }
}

function soon(ms: number) {
  clearTimeout(timer);
  timer = setTimeout(() => void flushOutbox(), ms);
}

if (typeof window !== 'undefined') {
  onHubBack(() => soon(1000));
  window.addEventListener('online', () => soon(1000));
  if (entries.length) soon(3000);
}
