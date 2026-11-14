import { api } from '../api/client';
import { useHubEvent } from './events';
import { useAsync, useWindowEvent } from './hooks';

/** A list of «Для вас» was opened: what is drawn of them (the «новый» marks) is asked again. */
export const FOR_YOU_SEEN = 'hm:for-you-seen';

function useForYou() {
  const res = useAsync((s) => api.forYou(s), [], true, 'for-you');
  useHubEvent('reconnect', () => res.reload(true));
  useHubEvent('library', (e) => ['discoveries', 'mix'].includes((e as { kind?: string }).kind ?? '') && res.reload(true));
  // a list was opened in this window: its mark goes
  useWindowEvent(FOR_YOU_SEEN, () => res.reload(true));
  return res.data ?? [];
}

/** How many tracks each list of «Для вас» has now, by its kind — `mix`, `weekly` and the lists of a rule: what stands
 * under its name, as under a playlist's. Not known yet (nothing was made): no number. */
export function useForYouCounts(): Record<string, number | undefined> {
  return Object.fromEntries(useForYou().map((x) => [x.kind, x.count ?? undefined]));
}

/** The lists with a mix or an issue made since the user last opened them: «новый». */
export function useForYouFresh(): ReadonlySet<string> {
  return new Set(useForYou().filter((x) => x.fresh).map((x) => x.kind));
}

/** The latest mix or issue of the list is on screen: it is not new any more, here and on the Hub. */
export function forYouOpened(kind: string) {
  if (kind !== 'mix' && kind !== 'weekly') return;
  api.forYouSeen(kind).then(() => window.dispatchEvent(new Event(FOR_YOU_SEEN)), () => undefined);
}
