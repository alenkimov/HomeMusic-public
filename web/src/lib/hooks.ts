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
// the fresh answer is on its way. In memory, and in the browser's storage when it is not too large for it.
const LAST = 'hm-last:';
const LAST_MAX = 400_000; // characters of JSON the storage is given for one answer
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
    if (raw.length <= LAST_MAX) localStorage.setItem(LAST + key, raw);
    else localStorage.removeItem(LAST + key);
  } catch {
    /* a full or closed storage: it stays in memory */
  }
}

/** The account is left: what its pages showed goes with it. */
export function forgetLast() {
  last.clear();
  try {
    for (const k of Object.keys(localStorage)) if (k.startsWith(LAST)) localStorage.removeItem(k);
  } catch {
    /* nothing to clear */
  }
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

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
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
