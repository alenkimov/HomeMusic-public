import { useCallback, useEffect, useRef, useState, type DependencyList } from 'react';
import { currentApiAccount, errorMessage, isAbort } from '../api/client';
import type { SourceStatus } from '../api/types';
import { clearLast, dropLast, readLast, writeLast } from './lastStore';

export function useSourceRequest<T>(key: string,
  load: (signal: AbortSignal, onSources: (sources: SourceStatus[]) => void) => Promise<T>, revision = 0) {
  const [progress, setProgress] = useState<{ key: string; sources: SourceStatus[] }>({ key, sources: [] });
  const res = useAsync(async (signal) => {
    setProgress({ key, sources: [] });
    try {
      return await load(signal, (sources) => {
        if (!signal.aborted) setProgress({ key, sources });
      });
    } catch (error) {
      if (!signal.aborted) setProgress((current) => ({ key, sources: current.sources.map((source) =>
        source.state === 'searching' ? { ...source, state: 'error' } : source) }));
      throw error;
    }
  }, [key, revision], true, key);
  return { res, sources: progress.key === key ? progress.sources : [] };
}

export interface AsyncState<T> {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
  /** Refetch; `silent` keeps the current data on screen (no spinner). */
  reload: (silent?: boolean) => void;
  setData: (fn: T | ((prev: T | undefined) => T | undefined)) => void;
}

// What a page showed the last time, by the name it keeps it under: shown at once the next time the page opens, while
// the fresh answer is on its way. In memory, and on the device (`lib/lastStore.ts`) for the next start of the app —
// under the account's name, so that another account's pages are never shown from it.
const last = new Map<string, unknown>();
const stored = (key: string) => `${currentApiAccount() ?? '-'}:${key}`;

function lastOf<T>(key: string): T | undefined {
  return last.get(key) as T | undefined;
}

function keepLast(key: string, value: unknown) {
  last.set(key, value);
  void writeLast(stored(key), value);
}

// what was kept in localStorage before (`hm-last:*`, its index): its few megabytes are the player's room now
try {
  for (const k of Object.keys(localStorage)) if (k.startsWith('hm-last')) localStorage.removeItem(k);
} catch {
  /* no storage */
}

/** The account is left: what its pages showed goes with it. */
export function forgetLast() {
  last.clear();
  void clearLast();
}

export function forgetSavedPages(keys: string[]) {
  const clear = new Set(keys);
  for (const key of clear) {
    last.delete(key);
    void dropLast(stored(key));
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
 * it with the fresh one when it comes.
 *
 * A quiet `reload(true)` (what the Hub's events ask for) does not abort a run on its way: the Hub would go on with
 * the abandoned one all the same, and a burst of events (a transfer finishing a track a second) piled up dozens of
 * heavy reads of the same list. It is asked once more when that run ends instead — however many came meanwhile —
 * and not sooner than `QUIET_GAP_MS` after it: a transfer's events never stop, and a page of thousands of tracks
 * asked again the moment its last answer came kept a core of the Hub busy for as long as it was open. */
/** The least time between the end of a page's answer and its next quiet asking (`useAsync`). */
const QUIET_GAP_MS = 3000;

export function useAsync<T>(fn: (signal: AbortSignal) => Promise<T>, deps: DependencyList, enabled = true, keep?: string): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(() => (keep ? lastOf<T>(keep) : undefined));
  const [error, setError] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState<boolean>(enabled && data === undefined);
  const [nonce, setNonce] = useState(0);
  const silentRef = useRef(data !== undefined); // with a remembered answer on screen the first asking is a quiet one
  const running = useRef(false);
  const again = useRef(false);
  const ended = useRef(0);
  const later = useRef<number | undefined>(undefined);
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
    again.current = false;
    running.current = true;
    setError(undefined);
    // nothing in memory yet (the app was just opened): what the device kept is shown until the Hub answers
    let fresh = false;
    if (keep && lastOf(keep) === undefined) {
      void readLast(stored(keep)).then((kept) => {
        if (fresh || ctl.signal.aborted || kept === undefined || lastOf(keep) !== undefined) return;
        last.set(keep, kept);
        setData(kept as T);
        setLoading(false);
      });
    }
    const settled = () => {
      running.current = false;
      ended.current = Date.now();
      if (!again.current) return;
      again.current = false;
      askQuietly.current();
    };
    fnRef
      .current(ctl.signal)
      .then((d) => {
        if (ctl.signal.aborted) return;
        fresh = true;
        setData(d);
        setLoading(false);
        if (keep) keepLast(keep, d);
        settled();
      })
      .catch((e) => {
        if (ctl.signal.aborted || isAbort(e)) return;
        setError(errorMessage(e));
        setLoading(false);
        settled();
      });
    return () => {
      running.current = false;
      ctl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, enabled]);

  const askQuietly = useRef(() => {});
  askQuietly.current = () => {
    if (running.current) {
      again.current = true;
      return;
    }
    const wait = ended.current + QUIET_GAP_MS - Date.now();
    if (wait > 0) {
      later.current ??= window.setTimeout(() => {
        later.current = undefined;
        askQuietly.current();
      }, wait);
      return;
    }
    silentRef.current = true;
    setNonce((n) => n + 1);
  };
  useEffect(() => () => window.clearTimeout(later.current), []);

  const reload = useCallback((silent = false) => {
    if (silent) return askQuietly.current();
    window.clearTimeout(later.current);
    later.current = undefined;
    silentRef.current = false;
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

/** A reload asked for by the Hub's events, which come in bursts (a transfer: one per downloaded track, its progress):
 * the first asks for it in `ms`, the ones until then join it; a page not on screen reloads when it comes back. One
 * reload of a list of thousands of tracks instead of one per event. */
export function useCoalesced(fn: () => void, ms = 3000): () => void {
  const ref = useRef(fn);
  ref.current = fn;
  const timer = useRef<number | undefined>(undefined);
  const owed = useRef(false);
  useEffect(() => {
    const shown = () => {
      if (document.visibilityState === 'visible' && owed.current && timer.current === undefined) {
        owed.current = false;
        ref.current();
      }
    };
    document.addEventListener('visibilitychange', shown);
    return () => {
      document.removeEventListener('visibilitychange', shown);
      window.clearTimeout(timer.current);
    };
  }, []);
  return useCallback(() => {
    if (timer.current !== undefined) return;
    timer.current = window.setTimeout(() => {
      timer.current = undefined;
      if (document.visibilityState === 'hidden') owed.current = true;
      else ref.current();
    }, ms);
  }, [ms]);
}
