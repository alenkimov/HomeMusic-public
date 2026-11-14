import { api } from '../api/client';
import { WaveHero } from '../components/WaveHero';
import { Icon } from '../components/Icon';
import { ErrorState, SectionTitle, Spinner } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { useAsync } from '../lib/hooks';
import { useUnseenReleases } from '../lib/releases';
import { Link } from '../lib/router';
import { AlbumRow, ArtistRow } from './MediaLibrary';
import { TastePrompt } from './Mix';
import { NewFromFollowed } from './Subscriptions';

/** «Главная» of the PWA in a plain browser (`STANDALONE`): «Моя волна», what the followed artists have released
 * (and the bell to «Подписки» in the corner of a phone — a computer has them in its side panel),
 * «Радиостанции» (a wave by a library artist, liked ones first) and rows of albums
 * Navidrome knows the user plays. Inside the app this is its native home screen. */
export function HomePage() {
  const res = useAsync((s) => api.ndHome(s), []);
  useHubEvent('library', () => res.reload(true));
  const d = res.data;
  const fresh = useUnseenReleases();

  return (
    <div className="pt-safe relative pb-4">
      <Link to="/subscriptions" aria-label={fresh ? `Подписки: новых релизов ${fresh}` : 'Подписки'} title="Подписки" className="pt-safe absolute right-2 top-2 z-10 lg:hidden">
        <span className="relative flex h-11 w-11 items-center justify-center rounded-full text-fg">
          <Icon name="bell" size={24} />
          {fresh > 0 && <span className="absolute right-2 top-2 h-2.5 w-2.5 rounded-full bg-accent ring-2 ring-bg" />}
        </span>
      </Link>
      <WaveHero />
      <TastePrompt />
      <NewFromFollowed />

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
