import { useContext, useEffect, useState } from 'react';
import { api } from '../api/client';
import { ArtistFollowButton, ArtistMap, ArtistMapPanel, useArtistMap } from '../components/ArtistMap';
import { Icon } from '../components/Icon';
import { ServiceText } from '../components/ServiceBadge';
import { Cover, Empty, ErrorState, IconButton, PageHeader, SkeletonRows } from '../components/ui';
import { useAsync, useDebounced } from '../lib/hooks';
import { RouteBackground, enc, navigate } from '../lib/router';
import { useSession } from '../lib/session';

/** «Карта похожих» (`/map/<artist>`): a way to look for artists one does not know — from one to those near them, and
 * on from there (`components/ArtistMap`). A press on an artist goes to their map; the strip under the map opens the
 * page of the one in the middle, plays their popular tracks, follows them. The search at the top starts from anybody;
 * without an artist (`/map`) the page offers whom to start from — those this server listens to. */
export function ArtistMapPage({ refId }: { refId?: string }) {
  const background = useContext(RouteBackground);
  const can = !!useSession().me.artist_map;
  const [searching, setSearching] = useState(false);
  const [q, setQ] = useState('');
  const dq = useDebounced(q.trim(), 400);
  const choosing = searching || !refId; // whom to start from is being chosen
  const found = useAsync((s) => api.tasteOnboarding(dq || undefined, s), [dq], can && choosing);
  const map = useArtistMap(refId, can && !!refId);
  // an artist has one address, whichever of the catalog's records of them the link named
  const own = map.data?.center.ref;
  useEffect(() => {
    if (!background && refId && own && own !== refId) navigate(`/map/${enc(own)}`, { replace: true });
  }, [background, own, refId]);
  const go = (ref: string) => {
    setSearching(false);
    setQ('');
    navigate(`/map/${enc(ref)}`);
  };

  if (!can)
    return (
      <div>
        <PageHeader title="Карта похожих" back="/home" />
        <Empty icon="nodes" title="Карты здесь нет" text={<ServiceText text="Кто на кого похож, карта узнаёт у Last.fm, а на этом сервере ключ Last.fm не задан. Похожих по каталогу можно посмотреть на странице исполнителя." />} />
      </div>
    );
  return (
    <div>
      <PageHeader
        title="Карта похожих"
        back="/home"
        right={refId ? <IconButton icon={searching ? 'x' : 'search'} label={searching ? 'Закрыть поиск' : 'Найти исполнителя'} onClick={() => setSearching(!searching)} /> : undefined}
        toolbar={choosing && (
          <div className="relative px-4 pt-1">
            <Icon name="search" size={20} className="pointer-events-none absolute left-7 top-1/2 mt-0.5 -translate-y-1/2 text-dim" />
            <input
              type="search"
              enterKeyHint="search"
              autoFocus={searching}
              className="input h-12 rounded-full pl-11"
              placeholder="С кого начать?"
              aria-label="Найти исполнителя"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
            />
          </div>
        )}
      />
      {choosing ? (
        <>
          {found.loading && !found.data ? (
            <SkeletonRows n={6} />
          ) : found.error ? (
            <ErrorState text={found.error} onRetry={() => found.reload()} />
          ) : !found.data?.suggestions.length ? (
            <p className="px-4 py-8 text-center text-muted">{dq ? 'Никого не нашли — попробуйте другое написание' : 'Найдите исполнителя по имени'}</p>
          ) : (
            <section className="pt-5">
              {!dq && <h2 className="px-4 text-sm font-semibold text-muted">{found.data.source === 'server' ? 'Слушают на этом сервере' : 'Популярное'}</h2>}
              <ul className="grid grid-cols-3 gap-x-2 gap-y-4 px-4 pb-6 pt-3 min-[560px]:grid-cols-4 min-[800px]:grid-cols-5 min-[1100px]:grid-cols-7">
                {found.data.suggestions.map((a) => (
                  <li key={a.ref}>
                    <button type="button" onClick={() => go(a.ref)} className="flex w-full flex-col items-center gap-2 text-center">
                      <Cover src={a.picture_small_url || a.picture_url} size={88} round icon="user" alt="" />
                      <span className="line-clamp-2 w-full text-sm leading-tight">{a.name}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      ) : (
        <>
          <ArtistMap data={map.data} loading={map.loading} filling={map.filling} error={map.error} onRetry={map.reload} onArtist={(a) => go(a.ref)} className="h-[min(calc(100dvh-17rem),46rem)] min-h-[20rem]" />
          {map.data && <ArtistMapPanel artist={map.data.center} action={<ArtistFollowButton artist={map.data.center} />} />}
        </>
      )}
    </div>
  );
}
