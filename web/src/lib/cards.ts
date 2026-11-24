import { useEffect, useRef } from 'react';
import { api } from '../api/client';
import type { LibraryEvent, NdSongOut } from '../api/types';
import { useHubEvent } from './events';

/** Point updates of a list of tracks (docs/track-cards-plan.md, point 3). A track's card — its names, its file, its
 * cover, how its download goes — changes on the Hub's own (`app/library/cards.py` `build`), and the Hub says which
 * (event `cards`); a page showing some of them takes just those cards (`POST /api/tracks/cards`) and puts them in
 * place of the rows, instead of reading its whole list again — «Любимое» of thousands of tracks is megabytes. */

/** What the Hub says of the library that only changes cards: a file came or was replaced, a name or an album
 * changed. The event `cards` tells it of each track; a list does not need to be read again for it. */
const CARDS_ONLY = new Set<LibraryEvent['kind']>(['imported', 'changed']);

/** Does this event of the library change what the list is (its tracks, their order, the user's marks on them)? */
export const listChanged = (e: LibraryEvent): boolean => !CARDS_ONLY.has(e.kind);

const WAIT_MS = 250; // the Hub draws cards in batches: their events come one after another
const MAX = 2000; // what the Hub takes at once

/** `tracks` — the rows the page shows now; `apply` — puts the new cards (by the Hub's track id) into them. What the
 * list itself adds to a card (`added_at`) is kept by `patch`. */
export function useCardUpdates(tracks: readonly NdSongOut[] | undefined, apply: (cards: Map<number, NdSongOut>) => void) {
  const shown = useRef<Set<number>>(new Set());
  const pending = useRef<Set<number>>(new Set());
  const timer = useRef(0);
  const latest = useRef(apply);
  latest.current = apply;

  useEffect(() => {
    shown.current = new Set((tracks ?? []).map((t) => t.library_track_id).filter((id): id is number => id != null));
  }, [tracks]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const fetchNow = async () => {
    const ids = [...pending.current];
    pending.current.clear();
    for (let k = 0; k < ids.length; k += MAX) {
      try {
        const got = await api.trackCards(ids.slice(k, k + MAX));
        latest.current(new Map(got.filter((c) => c.library_track_id != null).map((c) => [c.library_track_id as number, c])));
      } catch {
        // the Hub is away: the list is read again when it is back (`reconnect`)
      }
    }
  };

  useHubEvent('cards', (e) => {
    const mine = e.ids.filter((id) => shown.current.has(id));
    if (!mine.length) return;
    mine.forEach((id) => pending.current.add(id));
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void fetchNow(), WAIT_MS);
  });
}

/** The rows with the new cards in place; the same array when none of them is here. */
export function patch<T extends NdSongOut>(rows: readonly T[], cards: Map<number, NdSongOut>): readonly T[] {
  let changed = false;
  const out = rows.map((t) => {
    const card = t.library_track_id != null ? cards.get(t.library_track_id) : undefined;
    if (!card) return t;
    changed = true;
    // `unavailable` is sent only when it is so: a row that had it and lost it must not keep it
    return { ...t, ...card, unavailable: card.unavailable ?? false, added_at: t.added_at } as T;
  });
  return changed ? out : rows;
}
