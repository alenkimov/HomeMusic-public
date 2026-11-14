import { api } from '../api/client';
import { useHubEvent } from './events';
import { useAsync } from './hooks';

/** How many tracks each list of «Для вас» has now, by its kind — `mix`, `weekly` and the lists of a rule: what stands
 * under its name, as under a playlist's. Not known yet (nothing was made): no number. */
export function useForYouCounts(): Record<string, number | undefined> {
  const res = useAsync((s) => api.forYou(s), [], true, 'for-you');
  useHubEvent('reconnect', () => res.reload(true));
  return Object.fromEntries((res.data ?? []).map((x) => [x.kind, x.count ?? undefined]));
}
