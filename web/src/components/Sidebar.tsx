import { Fragment, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { NdSongOut } from '../api/types';
import { useHubEvent } from '../lib/events';
import { tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { useActiveDownloads } from '../lib/notify';
import { AUTO_PLAYLISTS } from '../lib/stats';
import { SELECTIONS_EVENT } from '../lib/tags';
import { attempt } from '../lib/ui';
import { playAll } from './catalog';
import { Link, enc, useRoute } from '../lib/router';
import { Icon, type IconName } from './Icon';
import type { Tab } from './TabBar';
import { Cover, Spinner, cx } from './ui';

const NAV: Array<{ id: Tab; to: string; label: string; icon: IconName }> = [
  { id: 'home', to: '/home', label: 'Главная', icon: 'home' },
  { id: 'search', to: '/search', label: 'Поиск', icon: 'search' },
  { id: 'downloads', to: '/downloads', label: 'Загрузки', icon: 'download' },
  { id: 'more', to: '/more', label: 'Ещё', icon: 'more' },
];

/** The left panel of a computer (instead of the tab bar of a phone): the sections on top, then the user's own
 * music — «Любимое» and the playlists, one click away from any screen. Its width is `--side-w`, it ends
 * above the player bar (`--tabbar-h`). */
export function Sidebar({ active, last }: { active: Tab | null; last: Partial<Record<Tab, string>> }) {
  const route = useRoute();
  const activeDownloads = useActiveDownloads();
  const lists = useAsync((s) => api.playlists(s), []);
  useHubEvent('library', () => lists.reload(true));
  useHubEvent('import', () => lists.reload(true));
  useHubEvent('reconnect', () => lists.reload(true));
  useEffect(() => {
    const changed = () => lists.reload(true); // a playlist was renamed or given a picture
    window.addEventListener('hm:playlists-changed', changed);
    return () => window.removeEventListener('hm:playlists-changed', changed);
  }, [lists.reload]);
  // the saved filters by tags (lib/tags.ts)
  const sels = useAsync((s) => api.tagSelections(s), []);
  useEffect(() => {
    const changed = () => sels.reload(true);
    window.addEventListener(SELECTIONS_EVENT, changed);
    return () => window.removeEventListener(SELECTIONS_EVENT, changed);
  }, [sels.reload]);
  // «Любимое» is a playlist too: its number of tracks, kept fresh as ♥ are set
  const liked = useAsync((s) => api.ndLiked(s), []);
  useHubEvent('library', () => liked.reload(true));
  useEffect(() => {
    let timer: number | undefined;
    const changed = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => liked.reload(true), 1500); // after the Hub has written the ♥
    };
    window.addEventListener('hm:hub-favorite', changed);
    return () => {
      window.removeEventListener('hm:hub-favorite', changed);
      window.clearTimeout(timer);
    };
  }, [liked.reload]);
  const shown = active === 'assistant' ? 'more' : active;
  const item = 'flex min-h-10 items-center gap-3 rounded-lg px-3 text-sm transition-colors';
  const at = (to: string) => route.path === to;

  return (
    <aside className="fixed bottom-(--tabbar-h) left-0 top-0 z-30 flex w-(--side-w) flex-col border-r border-line bg-surface/60" aria-label="Разделы и медиатека">
      <nav className="space-y-0.5 p-2" aria-label="Разделы">
        {NAV.map((t) => {
          const on = shown === t.id && !(t.id === 'more' && at('/stats'));
          return (
            <Fragment key={t.id}>
            <Link to={on ? t.to : (last[t.id] ?? t.to)} aria-current={on ? 'page' : undefined} className={cx(item, on ? 'bg-fg/10 font-semibold text-fg' : 'text-muted hover:bg-fg/5 hover:text-fg')}>
              <Icon name={t.icon} size={20} stroke={on ? 2.4 : 2} />
              <span className="flex-1">{t.label}</span>
              {t.id === 'downloads' && activeDownloads > 0 && (
                <span className="min-w-5 rounded-full bg-accent px-1.5 text-center text-[11px] font-bold leading-5 text-on-accent">{activeDownloads > 99 ? '99+' : activeDownloads}</span>
              )}
            </Link>
            {t.id === 'downloads' && (
              <Link to="/stats" aria-current={at('/stats') ? 'page' : undefined} className={cx(item, at('/stats') ? 'bg-fg/10 font-semibold text-fg' : 'text-muted hover:bg-fg/5 hover:text-fg')}>
                <Icon name="chart" size={20} stroke={at('/stats') ? 2.4 : 2} />
                <span className="flex-1">Статистика</span>
              </Link>
            )}
            </Fragment>
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
        <div className="group relative">
          <Link to="/liked" aria-current={at('/liked') ? 'page' : undefined} className={cx(item, 'py-1.5', at('/liked') ? 'bg-fg/10 text-fg' : 'text-muted hover:bg-fg/5 hover:text-fg')}>
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-accent/20 text-accent">
              <Icon name="heart" size={18} filled />
            </span>
            <span className="min-w-0">
              <span className={cx('block truncate', at('/liked') ? 'font-semibold text-accent' : 'text-fg')}>Любимое</span>
              {liked.data && <span className="block truncate text-xs text-dim">{tracksWord(liked.data.songs.length)}</span>}
            </span>
          </Link>
          <PlayOver name="Любимое" context="liked" page="/liked" load={async () => (await api.ndLiked()).songs} />
        </div>
        {(lists.data ?? []).map((p) => {
          const to = `/playlist/${enc(p.id)}`;
          const on = at(`/playlist/${p.id}`) || at(to);
          return (
            <div key={p.id} className="group relative">
              <Link to={to} aria-current={on ? 'page' : undefined} className={cx(item, 'py-1.5', on ? 'bg-fg/10' : 'hover:bg-fg/5')}>
                {/* the playlist's own picture (Navidrome makes one of its albums' covers); it changes with the tracks */}
                <Cover src={p.cover_url ? `${p.cover_url}&size=96` : null} size={36} icon="playlist" />
                <span className="min-w-0">
                  <span className={cx('block truncate', on ? 'font-semibold text-accent' : 'text-fg')}>{p.name}</span>
                  <span className="block truncate text-xs text-dim">{tracksWord(p.song_count)}</span>
                </span>
              </Link>
              {p.song_count > 0 && <PlayOver name={p.name} context="playlist" page={to} load={async () => (await api.playlist(p.id)).tracks} />}
            </div>
          );
        })}
        {lists.data && !lists.data.length && <p className="px-3 py-2 text-xs text-dim">Плейлистов пока нет</p>}
        <div className="flex items-center gap-1 px-1 pb-1 pt-4 text-xs text-dim">
          <Link to="/tags" className="flex-1 font-semibold uppercase tracking-wide hover:text-fg">Подборки по тегам</Link>
          <Link to="/tags" title="Теги" aria-label="Теги" className="flex h-7 w-7 items-center justify-center rounded-full text-muted transition hover:scale-115 hover:text-fg">
            <Icon name="tag" size={16} />
          </Link>
        </div>
        {(sels.data ?? []).map((x) => {
          const to = `/selection/${x.id}`;
          const on = at(to);
          const from = x.scope === 'liked' ? 'Любимое' : x.scope === 'library' ? 'Вся библиотека' : (lists.data?.find((p) => `playlist:${p.id}` === x.scope)?.name ?? 'Плейлист');
          return (
            <Link key={x.id} to={to} aria-current={on ? 'page' : undefined} className={cx(item, 'py-1.5', on ? 'bg-fg/10' : 'hover:bg-fg/5')}>
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-raised text-accent">
                <Icon name="filter" size={17} />
              </span>
              <span className="min-w-0">
                <span className={cx('block truncate', on ? 'font-semibold text-accent' : 'text-fg')}>{x.name}</span>
                <span className="block truncate text-xs text-dim">{from}</span>
              </span>
            </Link>
          );
        })}
        {sels.data && !sels.data.length && <p className="px-3 py-1 text-xs text-dim">Сохранённые фильтры по тегам появятся здесь</p>}
        <div className="px-1 pb-1 pt-4 text-xs font-semibold uppercase tracking-wide text-dim">Автоплейлисты</div>
        {AUTO_PLAYLISTS.map((a) => {
          const to = `/auto/${a.kind}`;
          const on = at(to);
          return (
            <Link key={a.kind} to={to} aria-current={on ? 'page' : undefined} className={cx(item, 'py-1.5', on ? 'bg-fg/10' : 'hover:bg-fg/5')}>
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-raised text-accent">
                <Icon name={a.icon} size={17} />
              </span>
              <span className="min-w-0">
                <span className={cx('block truncate', on ? 'font-semibold text-accent' : 'text-fg')}>{a.title}</span>
                <span className="block truncate text-xs text-dim">{a.sub}</span>
              </span>
            </Link>
          );
        })}
      </div>
    </aside>
  );
}

/** ▶ over a list's picture while the pointer is on its row: plays the list from the top without opening it. */
function PlayOver({ name, context, page, load }: { name: string; context: string; page: string; load: () => Promise<readonly NdSongOut[]> }) {
  const [busy, setBusy] = useState(false);
  const play = async () => {
    if (busy) return;
    setBusy(true);
    const tracks = await attempt(load);
    setBusy(false);
    if (tracks) playAll(tracks, { shuffle: false, context, page });
  };
  return (
    <button
      type="button"
      onClick={() => void play()}
      aria-label={`Слушать: ${name}`}
      title={`Слушать: ${name}`}
      className={cx('absolute left-3 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-md bg-black/60 text-white transition focus-visible:opacity-100', busy ? 'opacity-100' : 'opacity-0 group-hover:opacity-100')}
    >
      {busy ? <Spinner size={16} /> : <Icon name="play" size={20} filled className="translate-x-px" />}
    </button>
  );
}
