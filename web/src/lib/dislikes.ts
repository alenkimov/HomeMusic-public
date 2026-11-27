import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api/client';
import type { DislikeOut } from '../api/types';
import { routeContext } from './plays';
import { toast, toastError } from './ui';
import { keep as keepChange, onDropped, onSettled, waiting, waits } from './outbox';

/** «Не нравится» (MIXES.md 3.2): tracks and artists the user doesn't want in mixes and «Моя волна» — set here, in the
 * app, or moved over with the dislikes of a Yandex import (`GET /api/dislikes`). A track is known by its catalog
 * ref and/or its Navidrome song; the Hub fills in the other id where it can. Marking shows a toast with «Отменить». */

export interface DislikeTarget {
  /** the Hub's id of the track, if the Hub knows it */
  track_id?: number | null;
  ref?: string | null;
  nd_song_id?: string | null;
  artist_ref?: string | null;
  artist?: string | null;
  title?: string | null;
}

let rows: readonly DislikeOut[] = [];
let version = 0;
let loadedAt = 0;
let loading: Promise<void> | null = null;
const subscribers = new Set<() => void>();
const REFRESH_MS = 60_000;

function emit() {
  version++;
  for (const fn of subscribers) fn();
}

function subscribe(fn: () => void) {
  subscribers.add(fn);
  return () => {
    subscribers.delete(fn);
  };
}

/** The account is left: its marks go with it. */
export function resetDislikes() {
  rows = [];
  loadedAt = 0;
  emit();
}

export function refreshDislikes(force = false): Promise<void> {
  if (loading) return loading;
  if (!force && Date.now() - loadedAt < REFRESH_MS) return Promise.resolve();
  loading = api
    .dislikes()
    .then(
      (r) => {
        rows = withWaiting(r);
        loadedAt = Date.now();
        emit();
      },
      () => {
        rows = withWaiting(rows); // offline: keep what we have, and what was marked here meanwhile
        emit();
      },
    )
    .finally(() => {
      loading = null;
    });
  return loading;
}

const matches = (d: DislikeOut, t: DislikeTarget) =>
  t.artist_ref ? d.artist_ref === t.artist_ref || !!d.artist_refs?.includes(t.artist_ref) : (!!t.track_id && d.track_id === t.track_id) || (!!t.ref && d.ref === t.ref) || (!!t.nd_song_id && d.nd_song_id === t.nd_song_id);

export function isDisliked(t: DislikeTarget): boolean {
  return rows.some((d) => matches(d, t));
}

/** Every mark, as last read; re-renders on changes. */
export function useDislikes(): readonly DislikeOut[] {
  useEffect(() => {
    void refreshDislikes();
  }, []);
  useSyncExternalStore(subscribe, () => version);
  return rows;
}

const nameKey = (s: string) => s.trim().toLocaleLowerCase('ru');

/** One of the track's artists — the first named or any credited with them — is one the user does not listen to: the
 * track does not play by itself (a tap on it still plays it). An artist is known by their catalog ref, and by their
 * name where a track carries none (a library file). */
export function ofHiddenArtist(t: { artist?: string | null; artist_ref?: string | null; artist_refs?: (string | null)[] | null; artists?: readonly (string | { name: string; ref?: string })[] | null }): boolean {
  const hidden = rows.filter((d) => d.artist_ref);
  if (!hidden.length) return false;
  const credited = t.artists?.length ? t.artists.map((a) => (typeof a === 'string' ? { name: a } : a)) : (t.artist ?? '').split(',').map((name) => ({ name }));
  // who the track is by: each artist with the catalog's record of them, where the track says it
  const by = credited.map((a, i): { name: string; ref: string | null } => ({ name: nameKey(a.name), ref: (('ref' in a ? a.ref : null) as string | null | undefined) ?? t.artist_refs?.[i] ?? (i === 0 ? t.artist_ref : null) ?? null }));
  // a mark is of the whole artist — every record a catalog keeps of them, every spelling. An artist is told by
  // the record; by the name only where the track gives none (a library file): a namesake is not hidden
  return hidden.some((d) => {
    const refs = new Set([d.artist_ref as string, ...(d.artist_refs ?? [])]);
    const names = new Set([d.artist, ...(d.artist_names ?? [])].filter((x): x is string => !!x).map(nameKey));
    return by.some((a) => (a.ref ? refs.has(a.ref) : !!a.name && names.has(a.name)));
  });
}

export function useOfHiddenArtist(t: Parameters<typeof ofHiddenArtist>[0]): boolean {
  useDislikes();
  return ofHiddenArtist(t);
}

/** Whether the track (or, with `artist_ref` alone, the artist) is marked «Не нравится»; re-renders on changes. */
export function useDisliked(t: DislikeTarget): boolean {
  useEffect(() => {
    void refreshDislikes();
  }, []);
  useSyncExternalStore(subscribe, () => version);
  return isDisliked(t);
}

/** Marks or unmarks «Не нравится»; the toast after marking undoes it. */
export async function setDisliked(t: DislikeTarget, on: boolean, quiet = false): Promise<boolean> {
  const before = rows;
  const what = t.artist_ref ? { artist_ref: t.artist_ref } : { track_id: t.track_id, ref: t.ref, nd_song_id: t.nd_song_id };
  rows = on
    ? [{ id: 0, track_id: t.track_id ?? null, ref: t.ref ?? null, nd_song_id: t.nd_song_id ?? null, artist_ref: t.artist_ref ?? null, artist: t.artist ?? null, title: t.title ?? null, source: 'pwa', created_at: new Date().toISOString() }, ...rows]
    : rows.filter((d) => !matches(d, t));
  emit();
  try {
    if (on) {
      const row = await api.dislike({ ...what, artist: t.artist, title: t.title, context: routeContext(), client: 'pwa' });
      rows = [row, ...rows.filter((d) => d.id !== 0 && d.id !== row.id)];
      emit();
      if (!quiet)
        toast(t.artist_ref ? 'Исполнитель скрыт: его треки не включаются сами' : 'Трек не будет попадать в миксы', 'success', {
          label: 'Отменить',
          run: () => void setDisliked(t, false, true),
        });
    } else {
      await api.undislike(what);
      if (!quiet) toast(t.artist_ref ? 'Исполнитель возвращён' : 'Отметка «Не нравится» снята', 'success');
    }
    return true;
  } catch (e) {
    // no Hub now: the mark stays and waits on the device (`lib/outbox.ts`)
    if (waits(e) && keepChange({ kind: 'dislike', key: keyOf(t), on, body: { ...what, artist: t.artist, title: t.title, context: routeContext(), client: 'pwa' } })) {
      if (!quiet) toast(`Нет связи с сервером — ${on ? 'отмечу' : 'сниму отметку'}, когда он будет доступен`);
      return true;
    }
    rows = before;
    emit();
    toastError(e);
    return false;
  }
}

const keyOf = (t: DislikeTarget) => (t.artist_ref ? `artist:${t.artist_ref}` : `track:${t.track_id ?? ''}:${t.ref ?? ''}:${t.nd_song_id ?? ''}`);

/** The marks as the Hub has them, with the ones set or taken off here without it, still waiting to be sent. */
function withWaiting(list: readonly DislikeOut[]): readonly DislikeOut[] {
  let out = list;
  for (const c of waiting()) {
    if (c.kind !== 'dislike') continue;
    const t: DislikeTarget = c.body;
    out = out.filter((d) => !matches(d, t));
    if (c.on) out = [{ id: 0, track_id: t.track_id ?? null, ref: t.ref ?? null, nd_song_id: t.nd_song_id ?? null, artist_ref: t.artist_ref ?? null, artist: t.artist ?? null, title: t.title ?? null, source: 'pwa', created_at: new Date().toISOString() }, ...out];
  }
  return out;
}

// sent, or refused for good: the marks as the Hub has them
onSettled((c) => { if (c.kind === 'dislike') void refreshDislikes(true); });
onDropped((c) => { if (c.kind === 'dislike') void refreshDislikes(true); });
