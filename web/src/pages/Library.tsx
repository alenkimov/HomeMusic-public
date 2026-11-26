import { Chip } from '../components/Chip';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useFollowedCount, useUnseenReleases } from '../lib/releases';
import { api } from '../api/client';
import { ForYouTiles } from '../components/ForYouTiles';
import { Icon } from '../components/Icon';
import { ArtistBubble, TrackRow, TrackTableHeader } from '../components/catalog';
import { TrackTable } from '../components/TrackTable';
import { Empty, ErrorState, IconButton, PageBar, PageHeader, SectionTitle, SkeletonRows } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { plural } from '../lib/format';
import { useAsync, useDebounced } from '../lib/hooks';
import { LIKED_MARK, PlaylistRow, playlistLook } from '../components/Playlist';
import { useSession } from '../lib/session';
import { openPlaylistMenu } from '../lib/playlistMenu';
import { Link, navigate } from '../lib/router';

/** «Библиотека» of a phone: the lists themselves, at once — «Любимое» and the playlists one keeps (a group of rows),
 * (a finger held on one of them: its menu — the name, the picture, deleting it), the lists that make themselves («Для вас», tiles) and, last, the tags: the saved filters by them and «Все теги». A computer has the same in its
 * side panel (`components/Sidebar.tsx`). The files the Hub keeps are «Мои файлы» of «Меню» (`pages/Files.tsx`). */
export function LibraryPage() {
  const lists = useAsync((s) => api.playlists(s), [], true, 'playlists');
  const { me } = useSession();
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
      <PageBar className="pt-safe flex items-center justify-between pb-1 pt-4">
        <h1 className="text-3xl font-bold tracking-tight">Библиотека</h1>
        <span className="flex items-center">
          <IconButton icon="search" size={24} label="Поиск по библиотеке" onClick={() => navigate('/library/search')} />
          <IconButton icon="plus" size={28} label="Создать плейлист" onClick={() => navigate('/playlists')} />
        </span>
      </PageBar>

      <Title>Плейлисты</Title>
      <ul className="overflow-hidden rounded-2xl bg-surface">
        <li className="border-b border-fg/5">
          <PlaylistRow playlist={{ title: 'Любимое', to: '/liked', owners: [me.username], count: liked.data?.songs.length, mark: LIKED_MARK }} className="hover:bg-fg/5" />
        </li>
        <Row
          to="/subscriptions"
          title="Релизы"
          sub={followed ? [`${followed} ${plural(followed, ['исполнитель', 'исполнителя', 'исполнителей'])}`, fresh ? `новых релизов: ${fresh}` : null].filter(Boolean).join(' · ') : 'Исполнители и их новые релизы'}
          art={<span className="relative flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-accent/20 text-accent"><Icon name="user" size={22} />{fresh > 0 && <span className="absolute right-0 top-0 h-3 w-3 rounded-full bg-accent ring-2 ring-surface" />}</span>}
        />
        {(lists.data ?? []).map((p) => (
          <li key={p.id} className="border-b border-fg/5 last:border-0">
            <PlaylistRow playlist={playlistLook(p)} onMenu={(at) => openPlaylistMenu(p, at)} className="hover:bg-fg/5" />
          </li>
        ))}
      </ul>
      {lists.loading && !lists.data ? <SkeletonRows n={3} /> : lists.error && !lists.data ? <ErrorState text={lists.error} onRetry={() => lists.reload()} /> : null}

      <Title>Для вас</Title>
      <ForYouTiles />

      {/* the tags, last: the saved filters by them, and the way to the tags themselves */}
      <Title>Подборки по тегам</Title>
      <div className="flex flex-wrap gap-2">
        {(sels.data ?? []).map((x) => (
          <Chip key={x.id} size="lg" icon="filter" iconClass="text-accent" to={`/selection/${x.id}`} className="max-w-64">{x.name}</Chip>
        ))}
        <Chip size="lg" icon="tag" to="/tags">
          Все теги
        </Chip>
      </div>
    </div>
  );
}

function Title({ children }: { children: ReactNode }) {
  return <h2 className="px-1 pb-2 pt-5 text-[13px] font-semibold uppercase tracking-wide text-muted">{children}</h2>;
}

/** A row of the group of playlists: the rule between two rows starts after the picture. */
function Row({ to, title, sub, art }: { to: string; title: string; sub?: string; art: ReactNode }) {
  return (
    <li className="group">
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

/** The search of «Библиотека» (`/library/search`): through what the listener keeps and nothing else — the artists
 * they follow and the tracks of their playlists and «Любимое» (`/api/search/mine`). No releases: a release is not
 * kept as such. The field has the cursor at once: the page is opened to type. */
export function LibrarySearchPage() {
  const [q, setQ] = useState('');
  const dq = useDebounced(q.trim(), 250);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  const res = useAsync((s) => (dq ? api.searchMine(dq, s) : Promise.resolve(null)), [dq]);
  const d = dq ? res.data : null;
  const tracks = d?.tracks ?? [];
  return (
    <div className="pb-4">
      <PageHeader title="Поиск по библиотеке" back="/library" toolbar={
        <div className="px-4 pb-2 lg:px-6">
          <input ref={input} className="input" type="search" placeholder="Трек из плейлистов или исполнитель" aria-label="Поиск по библиотеке" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      } />
      {!dq ? (
        <p className="px-4 py-8 text-center text-sm text-muted lg:px-6">Ищет среди треков ваших плейлистов и «Любимого» и среди исполнителей, на которых вы подписаны.</p>
      ) : res.loading && !d ? (
        <SkeletonRows n={6} />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : d && !d.artists.length && !tracks.length ? (
        <Empty icon="search" title="В библиотеке такого нет" text="Попробуйте другое написание — или поищите во всём каталоге на вкладке «Поиск»." />
      ) : (
        d && (
          <>
            {d.artists.length > 0 && (
              <section aria-label="Исполнители">
                <SectionTitle>Исполнители</SectionTitle>
                <div className="no-scrollbar flex gap-3 overflow-x-auto px-4 pb-1">
                  {d.artists.map((a) => (
                    <ArtistBubble key={a.ref} artist={a} />
                  ))}
                </div>
              </section>
            )}
            {tracks.length > 0 && (
              <section aria-label="Треки">
                <SectionTitle>Треки</SectionTitle>
                <TrackTable tracks={tracks} numbered album>
                  <TrackTableHeader numbered album />
                  {tracks.map((t, i) => (
                    <TrackRow key={`${t.ref}:${i}`} track={t} showAlbum queue={tracks.filter((x) => !x.unknown)} rows={tracks} index={i + 1} />
                  ))}
                </TrackTable>
              </section>
            )}
          </>
        )
      )}
    </div>
  );
}
