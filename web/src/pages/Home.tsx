import { api } from '../api/client';
import { ReleaseTile } from './Releases';
import { WaveHero } from '../components/WaveHero';
import { ErrorState, SectionTitle, Spinner } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { useAsync, useRememberScrollX } from '../lib/hooks';
import { Link } from '../lib/router';
import { AlbumRow, ArtistRow } from './MediaLibrary';
import { TastePrompt } from './Mix';

/** «Главная» of the PWA in a plain browser (`STANDALONE`): «Моя волна», what the followed artists have released,
 * «Радиостанции» (a wave by a library artist, liked ones first) and rows of albums
 * Navidrome knows the user plays. Inside the app this is its native home screen. */
export function HomePage() {
  const res = useAsync((s) => api.ndHome(s), []);
  useHubEvent('library', () => res.reload(true));
  const d = res.data;

  return (
    <div className="pt-safe pb-4">
      <WaveHero />
      <TastePrompt />
      <NewReleases />

      {res.loading ? (
        <div className="flex justify-center py-10">
          <Spinner className="text-accent" />
        </div>
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : d ? (
        <>
          {d.radio.length > 0 && (
            <section aria-label="Радиостанции">
              <SectionTitle right={<Link to="/artists" className="text-sm text-muted hover:text-fg">Все</Link>}>Радиостанции</SectionTitle>
              <ArtistRow artists={d.radio} rowKey="home:radio" radio />
            </section>
          )}
          <AlbumRow title="Недавно слушали" albums={d.recent} rowKey="home:recent" />
          <AlbumRow title="Часто слушаете" albums={d.frequent} rowKey="home:frequent" />
          <AlbumRow title="Новое в библиотеке" albums={d.newest} rowKey="home:newest" more="/lib/albums" />
        </>
      ) : null}
    </div>
  );
}

/** «Новые релизы»: what the artists the user follows have released lately, the newest first — seen here without
 * going to «Релизы» for it. Nothing is shown when there is nothing; what is new stays marked until «Релизы» is opened. */
function NewReleases() {
  const res = useAsync((s) => api.releases(s), []);
  const row = useRememberScrollX('home:releases');
  const items = res.data?.items.slice(0, 12) ?? [];
  if (!items.length) return null;
  return (
    <section aria-label="Новые релизы">
      <SectionTitle right={<Link to="/releases" className="text-sm text-muted hover:text-fg">Все</Link>}>Новые релизы</SectionTitle>
      <div ref={row} className="no-scrollbar flex gap-4 overflow-x-auto px-4 pb-1">
        {items.map((r) => (
          <div key={r.album_ref} className="w-36 shrink-0">
            <ReleaseTile r={r} />
          </div>
        ))}
      </div>
    </section>
  );
}
