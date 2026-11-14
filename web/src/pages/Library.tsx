import type { ReactNode } from 'react';
import { useFollowedCount, useUnseenReleases } from '../lib/releases';
import { api } from '../api/client';
import { ForYouTiles } from '../components/ForYouTiles';
import { Icon } from '../components/Icon';
import { Cover, ErrorState, IconButton, SkeletonRows } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { plural, tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { playlistMenuProps } from '../lib/playlistMenu';
import { Link, enc, navigate } from '../lib/router';

/** «Библиотека» of a phone: the lists themselves, at once — «Любимое» and the playlists one keeps (a group of rows),
 * (a finger held on one of them: its menu — the name, the picture, deleting it), the lists that make themselves («Для вас», tiles) and, last, the tags: the saved filters by them and «Все теги». A computer has the same in its
 * side panel (`components/Sidebar.tsx`). The files the Hub keeps are «Мои файлы» of «Меню» (`pages/Files.tsx`). */
export function LibraryPage() {
  const lists = useAsync((s) => api.playlists(s), [], true, 'playlists');
  const liked = useAsync((s) => api.ndLiked(s), [], true, 'liked');
  const sels = useAsync((s) => api.tagSelections(s), [], true, 'selections');
  const again = () => {
    lists.reload(true);
    liked.reload(true);
    sels.reload(true);
  };
  // the Hub says when a playlist or a ♥ changes
  useHubEvent('library', again);
  useHubEvent('reconnect', again);

  const followed = useFollowedCount();
  const fresh = useUnseenReleases();

  return (
    <div className="px-4 pb-4">
      <header className="pt-safe flex items-center justify-between pb-1 pt-4">
        <h1 className="text-3xl font-bold tracking-tight">Библиотека</h1>
        <IconButton icon="plus" size={28} label="Создать плейлист" onClick={() => navigate('/playlists')} />
      </header>

      <Title>Плейлисты</Title>
      <ul className="overflow-hidden rounded-2xl bg-surface">
        <Row to="/liked" title="Любимое" sub={liked.data ? tracksWord(liked.data.songs.length) : undefined} art={<span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-accent text-on-accent"><Icon name="heart" size={22} filled /></span>} />
        <Row
          to="/subscriptions"
          title="Релизы"
          sub={followed ? [`${followed} ${plural(followed, ['исполнитель', 'исполнителя', 'исполнителей'])}`, fresh ? `новых релизов: ${fresh}` : null].filter(Boolean).join(' · ') : 'Исполнители и их новые релизы'}
          art={<span className="relative flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-accent/20 text-accent"><Icon name="user" size={22} />{fresh > 0 && <span className="absolute right-0 top-0 h-3 w-3 rounded-full bg-accent ring-2 ring-surface" />}</span>}
        />
        {(lists.data ?? []).map((p) => (
          <Row
            key={p.id}
            menu={playlistMenuProps(p)}
            to={`/playlist/${enc(p.id)}`}
            title={p.name}
            sub={tracksWord(p.song_count + (p.pending_count ?? 0))}
            art={<Cover src={p.cover_url ? `${p.cover_url}&size=160` : null} size={48} icon="playlist" className="!rounded-xl" />}
          />
        ))}
      </ul>
      {lists.loading && !lists.data ? <SkeletonRows n={3} /> : lists.error && !lists.data ? <ErrorState text={lists.error} onRetry={() => lists.reload()} /> : null}

      <Title>Для вас</Title>
      <ForYouTiles />

      {/* the tags, last: the saved filters by them, and the way to the tags themselves */}
      <Title>Подборки по тегам</Title>
      <div className="flex flex-wrap gap-2">
        {(sels.data ?? []).map((x) => (
          <Link key={x.id} to={`/selection/${x.id}`} className="flex h-11 items-center gap-2 rounded-full bg-surface pl-3 pr-4 text-[15px] font-medium hover:bg-raised">
            <Icon name="filter" size={18} className="text-accent" />
            <span className="max-w-56 truncate">{x.name}</span>
          </Link>
        ))}
        <Link to="/tags" className="flex h-11 items-center gap-2 rounded-full bg-surface pl-3 pr-4 text-[15px] font-medium text-muted hover:bg-raised hover:text-fg">
          <Icon name="tag" size={18} />
          Все теги
        </Link>
      </div>
    </div>
  );
}

function Title({ children }: { children: ReactNode }) {
  return <h2 className="px-1 pb-2 pt-5 text-[13px] font-semibold uppercase tracking-wide text-muted">{children}</h2>;
}

/** A row of the group of playlists: the rule between two rows starts after the picture. */
function Row({ to, title, sub, art, menu }: { to: string; title: string; sub?: string; art: ReactNode; menu?: ReturnType<typeof playlistMenuProps> }) {
  return (
    <li className="group select-none [-webkit-touch-callout:none]" {...menu}>
      <Link to={to} className="flex h-[68px] items-center gap-3.5 pl-3.5 hover:bg-fg/5">
        {art}
        <span className="flex min-w-0 flex-1 flex-col justify-center self-stretch border-b border-fg/5 pr-3.5 group-last:border-0">
          <span className="truncate font-semibold">{title}</span>
          {sub && <span className="truncate text-[13px] text-muted">{sub}</span>}
        </span>
      </Link>
    </li>
  );
}
