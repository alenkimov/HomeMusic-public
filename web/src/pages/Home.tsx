import { useMemo } from 'react';
import { api } from '../api/client';
import type { RecentPlaceOut, ReleaseOut, TrackOut } from '../api/types';
import { TrackRow } from '../components/catalog';
import { ForYouTiles } from '../components/ForYouTiles';
import { Icon, type IconName } from '../components/Icon';
import { WaveHero } from '../components/WaveHero';
import { Cover, SectionTitle } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { useAsync } from '../lib/hooks';
import { useUnseenReleases } from '../lib/releases';
import { Link } from '../lib/router';
import { TastePrompt } from './Mix';

/** «Главная» of the PWA in a plain browser (`STANDALONE`), four things and no more: «Моя волна» — play something;
 * «Продолжить» — the lists last played from; «Новые релизы» — what the followed artists released, as tracks (the
 * bell in the corner of a phone leads to «Подписки» — a computer has them in its side panel); «Для вас» — the lists
 * that make themselves. A block with nothing in it is not shown. Inside the app this is its native home screen. */
export function HomePage() {
  const fresh = useUnseenReleases();
  return (
    <div className="pt-safe relative pb-6">
      <Link to="/subscriptions" aria-label={fresh ? `Релизы: новых ${fresh}` : 'Релизы'} title="Релизы" className="pt-safe absolute right-2 top-2 z-10 lg:hidden">
        <span className="relative flex h-11 w-11 items-center justify-center rounded-full text-fg">
          <Icon name="bell" size={24} />
          {fresh > 0 && <span className="absolute right-2 top-2 h-2.5 w-2.5 rounded-full bg-accent ring-2 ring-bg" />}
        </span>
      </Link>
      <WaveHero />
      <TastePrompt />
      <Continue />
      <NewReleases />
      <section aria-label="Для вас">
        <SectionTitle>Для вас</SectionTitle>
        <ForYouTiles className="px-4 min-[700px]:grid-cols-3 min-[1200px]:grid-cols-6" />
      </section>
    </div>
  );
}

/** «Продолжить»: the lists the user last played from — a playlist, «Любимое», an album, an artist, a list of «Для
 * вас» — as tiles, the latest first. */
function Continue() {
  const res = useAsync((s) => api.recentPlaces(s), [], true, 'home-recent');
  const items = res.data ?? [];
  if (!items.length) return null;
  return (
    <section aria-label="Продолжить">
      <SectionTitle>Продолжить</SectionTitle>
      <div className="grid grid-cols-2 gap-2 px-4 min-[900px]:grid-cols-4">
        {items.map((p) => (
          <Place key={p.to} p={p} />
        ))}
      </div>
    </section>
  );
}

function Place({ p }: { p: RecentPlaceOut }) {
  return (
    <Link to={p.to} className="flex h-14 min-w-0 items-center gap-3 overflow-hidden rounded-lg bg-surface pr-3 hover:bg-raised">
      {p.cover_url ? (
        <Cover src={p.cover_url} size={56} round={p.round} icon={p.icon as IconName} alt="" className={p.round ? 'ml-1 !h-12 !w-12' : '!rounded-none'} />
      ) : (
        <span className="flex h-14 w-14 shrink-0 items-center justify-center bg-accent/20 text-accent">
          <Icon name={p.icon as IconName} size={24} filled={p.icon === 'heart'} />
        </span>
      )}
      <span className="min-w-0 truncate text-[15px] font-semibold">{p.title}</span>
    </Link>
  );
}

const TRACKS = 10; // tracks of «Новые релизы»
const OF_ONE = 3; // …of one release at most: an album of twelve would leave room for nobody else

/** «Новые релизы»: what the followed artists released lately, as tracks — the newest release first, its first tracks
 * in their order. A track of a release not seen yet is marked until «Подписки» is opened. */
function NewReleases() {
  const res = useAsync((s) => api.releases(s), [], true, 'releases');
  useHubEvent('library', (e) => (e as { kind?: string }).kind === 'releases' && res.reload(true));
  const rows = useMemo(() => {
    const out: { t: TrackOut; r: ReleaseOut }[] = [];
    for (const r of res.data?.items ?? []) {
      for (const t of (r.tracks ?? []).slice(0, OF_ONE)) {
        if (out.length >= TRACKS) return out;
        out.push({ t, r });
      }
    }
    return out;
  }, [res.data]);
  if (!rows.length) return null;
  const queue = rows.map((x) => x.t);
  return (
    <section aria-label="Новые релизы">
      <SectionTitle right={<Link to="/subscriptions" className="text-sm text-muted hover:text-fg">Все релизы</Link>}>Новые релизы</SectionTitle>
      {/* a wide window: two columns, 1–5 and 6–10 */}
      <div className={rows.length > 5 ? 'min-[1700px]:grid min-[1700px]:grid-flow-col min-[1700px]:grid-cols-2 min-[1700px]:gap-x-4' : undefined} style={{ gridTemplateRows: `repeat(${Math.ceil(rows.length / 2)}, auto)` }}>
        {rows.map(({ t, r }, i) => (
          <div key={`${r.album_ref}:${t.ref}`} className="relative">
            {r.new && <span aria-label="новое" title="Вы этого ещё не видели" className="absolute left-0.5 top-1/2 z-10 h-1.5 w-1.5 -translate-y-1/2 rounded-full bg-accent lg:left-1" />}
            <TrackRow track={{ ...t, added_at: r.release_date }} number={i + 1} showAdded queue={queue} />
          </div>
        ))}
      </div>
    </section>
  );
}
