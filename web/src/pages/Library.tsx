import type { ReactNode } from 'react';
import { api } from '../api/client';
import { Icon, type IconName } from '../components/Icon';
import { Cover, ErrorState, IconButton, SectionTitle, SkeletonRows } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link, enc, navigate } from '../lib/router';
import { AUTO_PLAYLISTS } from '../lib/stats';

/** «Библиотека» of a phone: the lists themselves, at once — «Любимое» and the playlists one keeps, the saved filters
 * by tags, the lists that make themselves. A computer has the same in its side panel (`components/Sidebar.tsx`).
 * The files the Hub keeps are «Мои файлы» of «Меню» (`pages/Files.tsx`). */
export function LibraryPage() {
  const lists = useAsync((s) => api.playlists(s), []);
  const liked = useAsync((s) => api.ndLiked(s), []);
  const sels = useAsync((s) => api.tagSelections(s), []);
  const again = () => {
    lists.reload(true);
    liked.reload(true);
    sels.reload(true);
  };
  // the Hub says when a playlist or a ♥ changes
  useHubEvent('library', again);
  useHubEvent('reconnect', again);

  return (
    <div className="pb-4">
      <header className="pt-safe flex items-center justify-between px-4 pb-2 pt-4">
        <h1 className="text-2xl font-bold">Библиотека</h1>
        <IconButton icon="plus" label="Создать плейлист" onClick={() => navigate('/playlists')} className="-mr-2" />
      </header>
      <ul>
        <Row to="/liked" title="Любимое" sub={liked.data ? tracksWord(liked.data.songs.length) : undefined} art={<Mark icon="heart" filled />} />
        {(lists.data ?? []).map((p) => (
          <Row
            key={p.id}
            to={`/playlist/${enc(p.id)}`}
            title={p.name}
            sub={tracksWord(p.song_count + (p.pending_count ?? 0))}
            art={<Cover src={p.cover_url ? `${p.cover_url}&size=160` : null} size={48} icon="playlist" />}
          />
        ))}
      </ul>
      {lists.loading && !lists.data ? <SkeletonRows n={3} /> : lists.error && !lists.data ? <ErrorState text={lists.error} onRetry={() => lists.reload()} /> : null}

      {!!sels.data?.length && (
        <section aria-label="Подборки по тегам">
          <SectionTitle right={<Link to="/tags" className="text-sm text-muted hover:text-fg">Теги</Link>}>Подборки по тегам</SectionTitle>
          <ul>
            {sels.data.map((x) => (
              <Row
                key={x.id}
                to={`/selection/${x.id}`}
                title={x.name}
                sub={x.scope === 'liked' ? 'Любимое' : x.scope === 'library' ? 'Вся библиотека' : (lists.data?.find((p) => `playlist:${p.id}` === x.scope)?.name ?? 'Плейлист')}
                art={<Mark icon="filter" quiet />}
              />
            ))}
          </ul>
        </section>
      )}

      <section aria-label="Автоплейлисты">
        <SectionTitle>Автоплейлисты</SectionTitle>
        <ul>
          <Row to="/mix" title="Микс дня" sub="Знакомое и новое, каждый день" art={<Mark icon="sparkle" />} />
          <Row to="/discoveries" title="Открытия недели" sub="Новые имена, по понедельникам" art={<Mark icon="wave" />} />
          {AUTO_PLAYLISTS.map((a) => (
            <Row key={a.kind} to={`/auto/${a.kind}`} title={a.title} sub={a.sub} art={<Mark icon={a.icon} quiet />} />
          ))}
        </ul>
      </section>
    </div>
  );
}

function Row({ to, title, sub, art }: { to: string; title: string; sub?: string; art: ReactNode }) {
  return (
    <li>
      <Link to={to} className="flex min-h-16 items-center gap-3 px-4 py-2 hover:bg-fg/5">
        {art}
        <span className="min-w-0 flex-1">
          <span className="block truncate">{title}</span>
          {sub && <span className="block truncate text-xs text-muted">{sub}</span>}
        </span>
      </Link>
    </li>
  );
}

/** The picture of a list that has none of its own. */
function Mark({ icon, filled, quiet }: { icon: IconName; filled?: boolean; quiet?: boolean }) {
  return (
    <span className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-md text-accent ${quiet ? 'bg-raised' : 'bg-accent/20'}`}>
      <Icon name={icon} size={22} filled={filled} />
    </span>
  );
}
