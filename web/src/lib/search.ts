import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage, isAbort } from '../api/client';
import type { SearchScope, SearchSnapshot } from '../api/types';

type SearchRun = SearchSnapshot & { key: string; loading: boolean; error?: string };

export function useLiveSearch(q: string, scope: SearchScope, enabled: boolean) {
  const key = JSON.stringify([scope, q]);
  const [run, setRun] = useState<SearchRun>({ key, loading: enabled, sources: [] });
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setRun({ key, loading: true, sources: [] });
    void api.searchLive(q, scope, (snapshot) => {
      if (!controller.signal.aborted) setRun({ key, loading: true, ...snapshot });
    }, controller.signal).then(() => {
      if (!controller.signal.aborted) setRun((prev) => ({ ...prev, loading: false }));
    }).catch((error) => {
      if (controller.signal.aborted || isAbort(error)) return;
      setRun((prev) => ({
        ...prev, loading: false, error: errorMessage(error),
        sources: prev.sources.map((source) => source.state === 'searching' ? { ...source, state: 'error' } : source),
      }));
    });
    return () => controller.abort();
  }, [q, scope, enabled, key, attempt]);

  const current = enabled && run.key === key ? run : { key, loading: enabled, sources: [] };
  return { ...current, reload };
}
