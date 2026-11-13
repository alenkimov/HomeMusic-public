import { api } from '../api/client';
import { useHubEvent } from './events';
import { useAsync } from './hooks';
import { AUTO_PLAYLISTS } from './stats';

/** How many tracks each list of «Для вас» has now — «Микс дня» (`mix`), «Открытия недели» (`discoveries`) and the
 * lists made by a rule (by their kind): what stands under its name, as under a playlist's. Not known yet: no number. */
export function useForYouCounts(): Record<string, number | undefined> {
  const res = useAsync(async (s) => {
    const none = () => null;
    const [mix, found, ...auto] = await Promise.all([
      api.mixToday(s).then((r) => r.mix?.total ?? 0, none),
      api.discoveries(null, s).then((r) => r.issue?.tracks.length ?? 0, none),
      ...AUTO_PLAYLISTS.map((a) => api.statsAuto(a.kind, s).then((r) => r.items.length, none)),
    ]);
    const out: Record<string, number | undefined> = { mix: mix ?? undefined, discoveries: found ?? undefined };
    AUTO_PLAYLISTS.forEach((a, i) => (out[a.kind] = auto[i] ?? undefined));
    return out;
  }, []);
  useHubEvent('reconnect', () => res.reload(true));
  return res.data ?? {};
}
