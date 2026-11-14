import { useCallback, useEffect, useRef, useState, type DependencyList } from 'react';
import { errorMessage, isAbort } from '../api/client';

export interface AsyncState<T> {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
  /** Refetch; `silent` keeps the current data on screen (no spinner). */
  reload: (silent?: boolean) => void;
  setData: (fn: T | ((prev: T | undefined) => T | undefined)) => void;
}

// What a page showed the last time, by the name it keeps it under: shown at once the next time the page opens, while
// the fresh answer is on its way. In memory, and in the browser's storage within a budget: the answers asked for
// longest ago leave it first, and one too large for it stays in memory alone.
const LAST = 'hm-last:';
const LAST_INDEX = 'hm-last-index'; // [name, characters][], the latest kept last
const LAST_MAX = 1_500_000; // characters of JSON the storage is given for one answer
const LAST_BUDGET = 3_000_000; // …and for all of them
const last = new Map<string, unknown>();

function lastOf<T>(key: string): T | undefined {
  if (last.has(key)) return last.get(key) as T;
  try {
    const raw = localStorage.getItem(LAST + key);
    if (raw) last.set(key, JSON.parse(raw));
  } catch {
    /* no storage, or not JSON: nothing is remembered */
  }
  return last.get(key) as T | undefined;
}

function keepLast(key: string, value: unknown) {
  last.set(key, value);
  try {
    const raw = JSON.stringify(value);
    let index = (JSON.parse(localStorage.getItem(LAST_INDEX) ?? '[]') as [string, number][]).filter(([k]) => k !== key);
    localStorage.removeItem(LAST + key);
    if (raw.length <= LAST_MAX) {
      index.push([key, raw.length]);
      let total = index.reduce((n, [, size]) => n + size, 0);
      while (total > LAST_BUDGET && index.length > 1) {
        const [gone, size] = index[0];
        index = index.slice(1);
        total -= size;
        localStorage.removeItem(LAST + gone);
      }
      localStorage.setItem(LAST + key, raw);
    }
    localStorage.setItem(LAST_INDEX, JSON.stringify(index));
  } catch {
    /* a full or closed storage: it stays in memory */
  }
}

/** The account is left: what its pages showed goes with it. */
export function forgetLast() {
  last.clear();
  try {
    for (const k of Object.keys(localStorage)) if (k.startsWith(LAST)) localStorage.removeItem(k); // the index too
  } catch {
    /* nothing to clear */
  }
}

/** Listen to an event of the window while the component is there. */
export function useWindowEvent(name: string, fn: () => void) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const run = () => ref.current();
    window.addEventListener(name, run);
    return () => window.removeEventListener(name, run);
  }, [name]);
}

/** Runs `fn` whenever deps change; aborts the previous run. `keep`: a name to remember the answer under — the next
 * time the page opens it shows that answer at once (no spinner, nothing appearing half a second later) and replaces
 * it with the fresh one when it comes. */
export function useAsync<T>(fn: (signal: AbortSignal) => Promise<T>, deps: DependencyList, enabled = true, keep?: string): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(() => (keep ? lastOf<T>(keep) : undefined));
  const [error, setError] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState<boolean>(enabled && data === undefined);
  const [nonce, setNonce] = useState(0);
  const silentRef = useRef(data !== undefined); // with a remembered answer on screen the first asking is a quiet one
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const keptAs = useRef(keep);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    if (keep !== keptAs.current) {
      // the same page, another thing (a playlist after a playlist): what that one showed last, or nothing yet
      keptAs.current = keep;
      const was = keep ? lastOf<T>(keep) : undefined;
      setData(was);
      silentRef.current = was !== undefined;
    }
    const ctl = new AbortController();
    if (!silentRef.current) setLoading(true);
    silentRef.current = false;
    setError(undefined);
    fnRef
      .current(ctl.signal)
      .then((d) => {
        if (!ctl.signal.aborted) {
          setData(d);
          setLoading(false);
          if (keep) keepLast(keep, d);
        }
      })
      .catch((e) => {
        if (ctl.signal.aborted || isAbort(e)) return;
        setError(errorMessage(e));
        setLoading(false);
      });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, enabled]);

  const reload = useCallback((silent = false) => {
    silentRef.current = silent;
    setNonce((n) => n + 1);
  }, []);

  // a track or an album was put right (`lib/trackEdit.ts`): what a page keeps of its lists is stale — asked again,
  // quietly, by every page that keeps its answer
  useEffect(() => {
    if (!keep) return;
    const again = () => reload(true);
    window.addEventListener('hm:track-edited', again);
    return () => window.removeEventListener('hm:track-edited', again);
  }, [keep, reload]);

  const set = useCallback((v: T | ((prev: T | undefined) => T | undefined)) => {
    setData(v as T | ((prev: T | undefined) => T));
  }, []);

  return { data, error, loading, reload, setData: set };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function useInterval(fn: () => void, ms: number | null) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (ms == null) return;
    const t = window.setInterval(() => ref.current(), ms);
    return () => window.clearInterval(t);
  }, [ms]);
}

const scrollX = new Map<string, number>();

/** Ref for a horizontal row: going to an album and back remounts the row, and the browser only restores
 * the page's vertical scroll. This puts the row back where it was left, per `key`, until a reload. */
export function useRememberScrollX(key: string) {
  return useCallback(
    (el: HTMLElement | null) => {
      if (!el) return;
      const saved = scrollX.get(key);
      if (saved) el.scrollLeft = saved;
      const onScroll = () => scrollX.set(key, el.scrollLeft);
      el.addEventListener('scroll', onScroll, { passive: true });
      return () => el.removeEventListener('scroll', onScroll);
    },
    [key],
  );
}
