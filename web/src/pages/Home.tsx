import { api } from '../api/client';
import { Icon, type IconName } from '../components/Icon';
import { ReleaseTile } from './Releases';
import { WaveCard } from '../components/Wave';
import { WaveHero } from '../components/WaveHero';
import { ErrorState, SectionTitle, Spinner } from '../components/ui';
import { useDesktop } from '../lib/desktop';
import { useHubEvent } from '../lib/events';
import { useAsync, useRememberScrollX } from '../lib/hooks';
import { Link } from '../lib/router';
import { AlbumRow, ArtistRow } from './MediaLibrary';
import { TastePrompt } from './Mix';

/** «Главная» of the PWA in a plain browser (`STANDALONE`): «Моя волна», what the followed artists have released,
 * shortcuts to the library, «Радиостанции» (a wave by a library artist, liked ones first) and rows of albums
 * Navidrome knows the user plays. Inside the app this is its native home screen. */
export function HomePage() {
  const res = useAsync((s) => api.ndHome(s), []);
  useHubEvent('library', () => res.reload(true));
  const d = res.data;
  const desktop = useDesktop();

  return (
    <div className="pt-safe pb-4">
      {desktop ? <WaveHero /> : <WaveCard />}
      <TastePrompt />
      <NewReleases />
      <div className="grid grid-cols-2 gap-2 px-4 pt-3">
        <Shortcut to="/artists" icon="user" title="Исполнители" />
        <Shortcut to="/lib/albums" icon="disc" title="Все альбомы" />
      </div>

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

function Shortcut({ to, icon, title }: { to: string; icon: IconName; title: string }) {
  return (
    <Link to={to} className="flex min-h-14 items-center gap-2.5 rounded-lg bg-raised px-3 py-2 text-sm font-semibold transition hover:bg-hover">
      <Icon name={icon} size={20} className="shrink-0 text-accent" />
      <span className="truncate">{title}</span>
    </Link>
  );
}
