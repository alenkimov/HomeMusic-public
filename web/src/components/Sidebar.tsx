import { api } from '../api/client';
import { useHubEvent } from '../lib/events';
import { tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { useActiveDownloads } from '../lib/notify';
import { Link, enc, useRoute } from '../lib/router';
import { Icon, type IconName } from './Icon';
import type { Tab } from './TabBar';
import { cx } from './ui';

const NAV: Array<{ id: Tab; to: string; label: string; icon: IconName }> = [
  { id: 'home', to: '/home', label: 'Главная', icon: 'home' },
  { id: 'search', to: '/search', label: 'Поиск', icon: 'search' },
  { id: 'downloads', to: '/downloads', label: 'Загрузки', icon: 'download' },
  { id: 'more', to: '/more', label: 'Ещё', icon: 'more' },
];

/** The left panel of a computer (instead of the tab bar of a phone): the sections on top, then the user's own
 * music — «Понравившиеся» and the playlists, one click away from any screen. Its width is `--side-w`, it ends
 * above the player bar (`--tabbar-h`). */
export function Sidebar({ active, last }: { active: Tab | null; last: Partial<Record<Tab, string>> }) {
  const route = useRoute();
  const activeDownloads = useActiveDownloads();
  const lists = useAsync((s) => api.playlists(s), []);
  useHubEvent('library', () => lists.reload(true));
  useHubEvent('import', () => lists.reload(true));
  useHubEvent('reconnect', () => lists.reload(true));
  const shown = active === 'assistant' ? 'more' : active;
  const item = 'flex min-h-10 items-center gap-3 rounded-lg px-3 text-sm transition-colors';
  const at = (to: string) => route.path === to;

  return (
    <aside className="fixed bottom-(--tabbar-h) left-0 top-0 z-30 flex w-(--side-w) flex-col border-r border-line bg-surface/60" aria-label="Разделы и медиатека">
      <nav className="space-y-0.5 p-2" aria-label="Разделы">
        {NAV.map((t) => {
          const on = shown === t.id;
          return (
            <Link key={t.id} to={on ? t.to : (last[t.id] ?? t.to)} aria-current={on ? 'page' : undefined} className={cx(item, on ? 'bg-fg/10 font-semibold text-fg' : 'text-muted hover:bg-fg/5 hover:text-fg')}>
              <Icon name={t.icon} size={20} stroke={on ? 2.4 : 2} />
              <span className="flex-1">{t.label}</span>
              {t.id === 'downloads' && activeDownloads > 0 && (
                <span className="min-w-5 rounded-full bg-accent px-1.5 text-center text-[11px] font-bold leading-5 text-on-accent">{activeDownloads > 99 ? '99+' : activeDownloads}</span>
              )}
            </Link>
          );
        })}
      </nav>
      <div className="flex items-center gap-1 px-3 pb-1 pt-3 text-xs text-dim">
        <Link to="/library" className="flex-1 font-semibold uppercase tracking-wide hover:text-fg">Моя медиатека</Link>
        <Link to="/playlists" title="Создать плейлист" aria-label="Создать плейлист" className="flex h-7 w-7 items-center justify-center rounded-full text-muted transition hover:scale-115 hover:text-fg">
          <Icon name="plus" size={18} />
        </Link>
      </div>
      <div className="no-scrollbar min-h-0 flex-1 space-y-0.5 overflow-y-auto p-2 pt-0">
        <Link to="/liked" aria-current={at('/liked') ? 'page' : undefined} className={cx(item, 'py-1.5', at('/liked') ? 'bg-fg/10 text-fg' : 'text-muted hover:bg-fg/5 hover:text-fg')}>
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-accent/20 text-accent">
            <Icon name="heart" size={18} filled />
          </span>
          <span className="truncate text-fg">Понравившиеся</span>
        </Link>
        {(lists.data ?? []).map((p) => {
          const to = `/playlist/${enc(p.id)}`;
          const on = at(`/playlist/${p.id}`) || at(to);
          return (
            <Link key={p.id} to={to} aria-current={on ? 'page' : undefined} className={cx(item, 'py-1.5', on ? 'bg-fg/10' : 'hover:bg-fg/5')}>
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-raised text-muted">
                <Icon name="playlist" size={18} />
              </span>
              <span className="min-w-0">
                <span className={cx('block truncate', on ? 'font-semibold text-accent' : 'text-fg')}>{p.name}</span>
                <span className="block truncate text-xs text-dim">{tracksWord(p.song_count)}</span>
              </span>
            </Link>
          );
        })}
        {lists.data && !lists.data.length && <p className="px-3 py-2 text-xs text-dim">Плейлистов пока нет</p>}
      </div>
    </aside>
  );
}
