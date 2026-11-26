import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api/client';
import type { TidyProposalOut } from '../api/types';
import { hubEvents } from './events';
import { EDITED_EVENT } from './trackEdit';

/** What «Наведение порядка» offers to put right, by what it is about: the pages mark a track's row, an album's or an
 * artist's name, the lines of a text, and the mark leads to the proposal (`tidyPath`). One index per signed-in
 * session (`/api/tidy/marks`), asked anew when somebody answers, a look-through ends or the library changes. */

export type MarkSubject = { track: number } | { album: number } | { artist: number };

/** The kinds a text's lines are marked with; the other kinds mark the name of their subject. */
export const TEXT_KINDS: ReadonlySet<TidyProposalOut['kind']> = new Set(['lyrics', 'uncensor']);

const NONE: TidyProposalOut[] = [];
let rows: TidyProposalOut[] = [];
let index = new Map<string, TidyProposalOut[]>();
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

const keyOf = (type: string, id: number) => `${type}:${id}`;

function setMarks(open: TidyProposalOut[]) {
  rows = open;
  const next = new Map<string, TidyProposalOut[]>();
  const put = (key: string, p: TidyProposalOut) => next.set(key, [...(next.get(key) ?? []), p]);
  for (const p of rows) {
    put(keyOf(p.subject_type, p.subject_id), p);
    // two artists to be one: both pages say so
    if (p.kind === 'artist' && p.show.into_id) put(keyOf('artist', p.show.into_id), p);
  }
  index = next;
  for (const listener of listeners) listener();
}

/** A decided proposal is gone from the marks at once (the index is asked anew after it too). */
export function dropMark(id: number) {
  setMarks(rows.filter((p) => p.id !== id));
}

function lookup(on: MarkSubject | null | undefined): TidyProposalOut[] {
  if (!on) return NONE;
  const [type, id] = 'track' in on ? ['track', on.track] : 'album' in on ? ['album', on.album] : ['artist', on.artist];
  if (!id || id <= 0) return NONE;
  return index.get(keyOf(type, id)) ?? NONE;
}

/** The open proposals about the subject; `text`: those of its text, else those of its name. */
export function useTidyMarks(on: MarkSubject | null | undefined, text = false): TidyProposalOut[] {
  const all = useSyncExternalStore(subscribe, () => lookup(on));
  const some = all.filter((p) => TEXT_KINDS.has(p.kind) === text);
  return some.length === all.length ? all : some.length ? some : NONE;
}

/** Where a proposal is answered: its section of «Наведение порядка», scrolled to it. */
export const tidyPath = (p: TidyProposalOut) => `/tidy?tab=${p.section}&p=${p.id}`;

export function useTidyMarksSync(account: number | null | undefined) {
  useEffect(() => {
    if (account == null) return;
    let request: AbortController | undefined;
    let timer: number | undefined;
    const load = () => {
      request?.abort();
      const controller = new AbortController();
      request = controller;
      void api.tidyMarks(controller.signal).then((open) => {
        if (!controller.signal.aborted) setMarks(open);
      }).catch(() => { /* the last marks stay while the Hub is unreachable */ });
    };
    const refresh = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(load, 300);
    };
    const offTidy = hubEvents.on('tidy', refresh);
    const offLibrary = hubEvents.on('library', ({ kind }) => {
      if (kind === 'changed' || kind === 'imported') refresh();
    });
    const offReconnect = hubEvents.on('reconnect', refresh);
    window.addEventListener(EDITED_EVENT, refresh);
    load();
    return () => {
      request?.abort();
      window.clearTimeout(timer);
      offTidy();
      offLibrary();
      offReconnect();
      window.removeEventListener(EDITED_EVENT, refresh);
      setMarks([]);
    };
  }, [account]);
}
