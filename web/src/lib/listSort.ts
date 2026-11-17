import { useCallback, useState } from 'react';
import type { ListSort } from '../components/catalog';

/** The order a list was put in by its heading («Дата добавления», «Прослушивания») stays the list's own on this
 * device: left and come back to — or opened tomorrow — it is as it was sorted, until its own order is asked for
 * again. Kept by the list's name (`playlist:<id>`, `liked`…). */
const KEY = 'hm-list-sort';
const ORDERS: readonly ListSort[] = ['new', 'old', 'most', 'least'];

let kept: Record<string, ListSort> | null = null;
function all(): Record<string, ListSort> {
  if (kept) return kept;
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Record<string, unknown>;
    kept = Object.fromEntries(Object.entries(raw).filter(([, v]) => ORDERS.includes(v as ListSort))) as Record<string, ListSort>;
  } catch {
    kept = {};
  }
  return kept;
}

export function useListSort(list: string): [ListSort, (next: ListSort) => void] {
  const [, redraw] = useState(0);
  const set = useCallback(
    (next: ListSort) => {
      const now = { ...all() };
      if (next) now[list] = next;
      else delete now[list];
      kept = now;
      try {
        localStorage.setItem(KEY, JSON.stringify(now));
      } catch {
        /* it lasts while the app is open then */
      }
      redraw((n) => n + 1);
    },
    [list],
  );
  return [all()[list] ?? null, set];
}
