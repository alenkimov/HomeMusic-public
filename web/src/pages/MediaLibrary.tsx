import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { NdAlbumOut, NdArtistOut, NdSongOut } from '../api/types';
import { playAll, TrackRow, TrackTableHeader } from '../components/catalog';
import { Icon, type IconName } from '../components/Icon';
import { LikeButton } from '../components/LikeButton';
import { useListSearch, type Found } from '../components/ListSearch';
import { Button, Cover, Empty, ErrorState, IconButton, PageHeader, SectionTitle, SkeletonRows, Spinner, cx } from '../components/ui';
import { fmtLength, tracksWord } from '../lib/format';
import { useDesktop } from '../lib/desktop';
import { useHubEvent } from '../lib/events';
import { useAsync, useRememberScrollX } from '../lib/hooks';
import { Link, enc, navigate } from '../lib/router';
import { attempt, confirmDialog } from '../lib/ui';
import { useSmartForPage, useSmartPage } from '../lib/smartShuffle';
import { canWave, seedKey, startWave, useWave } from '../lib/wave';

/** The Navidrome library as a player shows it, for the PWA in a plain browser (`STANDALONE`): inside the app these
 * are its native screens. Songs are `NdSongOut` (owned catalog-like tracks), so `TrackRow` plays them. */

// --- pieces ------------------------------------------------------------------------------------------------

export function NdAlbumCard({ album, showArtist = true }: { album: NdAlbumOut; showArtist?: boolean }) {
  return (
    <Link to={`/lib/album/${enc(album.id)}`} className="block min-w-0">
      <Cover src={album.cover_url} alt={album.title} icon="disc" className="aspect-square w-full" />
      <div className="mt-2 line-clamp-2 text-sm font-medium leading-tight">{album.title}</div>
      <div className="mt-0.5 truncate text-xs text-muted">{[showArtist ? album.artist : null, album.year].filter(Boolean).join(' · ')}</div>
    </Link>
  );
}

/** A horizontal row of album cards that remembers its scroll. */
export function AlbumRow({ title, albums, rowKey, more }: { title: string; albums: NdAlbumOut[]; rowKey: string; more?: string }) {
  const row = useRememberScrollX(rowKey);
  if (!albums.length) return null;
  return (
    <section aria-label={title}>
      <SectionTitle right={more ? <Link to={more} className="text-sm text-muted hover:text-fg">Все</Link> : undefined}>{title}</SectionTitle>
      <div ref={row} className="no-scrollbar flex gap-4 overflow-x-auto px-4 pb-1">
        {albums.map((a) => (
          <div key={a.id} className="w-36 shrink-0">
            <NdAlbumCard album={a} />
          </div>
        ))}
      </div>
    </section>
  );
}

/** An artist of the library: a tap opens it; `radio` — a tap starts the wave by the artist («Радиостанции»). */
export function NdArtistBubble({ artist, radio = false }: { artist: NdArtistOut; radio?: boolean }) {
  const { starting } = useWave();
  const body = (
    <>
      <span className="relative">
        <Cover src={artist.cover_url} size={104} round icon="user" alt={artist.name} />
        {radio && (
          <span className="absolute bottom-1 right-1 flex h-8 w-8 items-center justify-center rounded-full bg-accent text-on-accent shadow">
            {starting === seedKey({ nd_artist_id: artist.id }) ? <Spinner size={16} /> : <Icon name="wave" size={18} />}
          </span>
        )}
      </span>
      <span className="line-clamp-2 text-sm leading-tight">{artist.name}</span>
    </>
  );
  const cls = 'flex w-28 shrink-0 flex-col items-center gap-2 text-center';
  if (radio && canWave())
    return (
      <button type="button" className={cls} aria-label={`Радио: ${artist.name}`} onClick={() => void startWave({ nd_artist_id: artist.id }, 'radio')}>
        {body}
      </button>
    );
  return (
    <Link to={`/lib/artist/${enc(artist.id)}`} className={cls}>
      {body}
    </Link>
  );
}

/** «Слушать» and «Перемешать» for a whole list. */
export function ListActions({ tracks, context, children }: { tracks: readonly NdSongOut[]; context?: string; children?: ReactNode }) {
  const smart = useSmartForPage() && (context === 'playlist' || context === 'liked'); // «Перемешать» is the smart shuffle
  const desktop = useDesktop();
  if (!tracks.length && !children) return null;
  if (desktop)
    return (
      <div className="flex items-center gap-3 px-6 pb-4">
        {tracks.length > 0 && (
          <>
            <button
              type="button"
              aria-label="Слушать"
              title="Слушать"
              onClick={() => playAll(tracks, { shuffle: false, context })}
              className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition hover:scale-105 active:scale-95"
            >
              <Icon name="play" size={26} filled className="translate-x-0.5" />
            </button>
            <IconButton icon={smart ? 'shuffleSmart' : 'shuffle'} size={28} label="Перемешать" onClick={() => playAll(tracks, { shuffle: true, context })} className="text-muted hover:text-fg" />
          </>
        )}
        {children}
      </div>
    );
  return (
    <div className="flex gap-2 px-4 pb-3 pt-1">
      {tracks.length > 0 && (
        <>
          <Button variant="primary" icon="play" className="flex-1" onClick={() => playAll(tracks, { shuffle: false, context })}>
            Слушать
          </Button>
          <Button icon={smart ? 'shuffleSmart' : 'shuffle'} className="flex-1" onClick={() => playAll(tracks, { shuffle: true, context })}>
            Перемешать
          </Button>
        </>
      )}
      {children}
    </div>
  );
}

/** `found`: the rows a search of the list left (`useListSearch`) — they keep their places in the whole list: the
 * number shown, and what `extra` gets (a playlist removes a track by its place). */
function SongList({ tracks, extra, found }: { tracks: readonly NdSongOut[]; extra?: (t: NdSongOut, i: number) => Parameters<typeof TrackRow>[0]['menuExtra']; found?: Found[] | null }) {
  const rows = found ? found.map((f) => ({ t: tracks[f.index], i: f.index, line: f.line })) : tracks.map((t, i) => ({ t, i, line: undefined as string | undefined }));
  const queue = found ? rows.map((r) => r.t) : tracks;
  if (found && !rows.length) return <p className="px-4 py-10 text-center text-sm text-muted">Ничего не нашлось — ни в названиях, ни в текстах.</p>;
  return (
    <ul>
      {rows.length > 0 && <li><TrackTableHeader numbered album /></li>}
      {rows.map(({ t, i, line }) => (
        <li key={`${t.ref}:${i}`}>
          <TrackRow track={t} queue={queue} link={false} showAlbum menuExtra={extra?.(t, i)} index={i + 1} />
          {line !== undefined && (
            <div className="-mt-1 flex items-center gap-1.5 truncate pb-2 pl-16 pr-4 text-xs text-muted">
              <Icon name="lyrics" size={13} className="shrink-0 text-accent" />
              <span className="truncate">{line ? `«${line}»` : 'найдено в тексте'}</span>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

/** The ⋯ of a list's actions on a computer: what a phone keeps as icons in the page header. */
function MoreMenu({ items }: { items: Array<{ icon: IconName; label: string; danger?: boolean; onClick: () => void }> }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const key = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('click', close);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('click', close);
      document.removeEventListener('keydown', key);
    };
  }, [open]);
  return (
    <div className="relative">
      <IconButton icon="more" size={28} label="Ещё" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="text-muted hover:text-fg" />
      {open && (
        <div role="menu" className="absolute left-0 top-12 z-20 w-60 rounded-xl border border-line bg-raised p-1 shadow-lg">
          {items.map((x) => (
            <button key={x.label} type="button" role="menuitem" onClick={x.onClick} className={cx('flex min-h-10 w-full items-center gap-3 rounded-lg px-3 text-left text-sm hover:bg-fg/5', x.danger ? 'text-danger' : 'text-fg')}>
              <Icon name={x.icon} size={18} />
              {x.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Hero({ cover, round, title, sub, right, kicker }: { cover?: string | null; round?: boolean; title: string; sub?: ReactNode; right?: ReactNode; kicker?: string }) {
  const desktop = useDesktop();
  if (desktop)
    return (
      <div className="flex items-end gap-6 px-6 pb-5 pt-3">
        <Cover src={cover} round={round} icon={round ? 'user' : 'disc'} alt={title} className="aspect-square w-48 shrink-0 shadow-2xl" />
        <div className="min-w-0 flex-1">
          {kicker && <div className="text-sm text-muted">{kicker}</div>}
          <h2 className="line-clamp-2 break-words text-6xl font-black leading-[1.05] tracking-tight">{title}</h2>
          {sub && <div className="mt-3 text-sm text-muted">{sub}</div>}
        </div>
        {right}
      </div>
    );
  return (
    <div className="flex flex-col items-center gap-3 px-4 pb-4 pt-2 text-center">
      <Cover src={cover} round={round} icon={round ? 'user' : 'disc'} alt={title} className="aspect-square w-52 shadow-2xl" />
      <div className="min-w-0">
        <h2 className="line-clamp-2 text-2xl font-bold leading-tight">{title}</h2>
        {sub && <div className="mt-1 text-sm text-muted">{sub}</div>}
      </div>
      {right}
    </div>
  );
}

// --- pages -------------------------------------------------------------------------------------------------

export function LikedPage() {
  const res = useAsync((s) => api.ndLiked(s), []);
  useSmartPage('liked', null);
  useHubEvent('library', () => res.reload(true));
  useHubEvent('import', () => res.reload(true));
  useHubEvent('download', (d) => {
    if (['done', 'failed', 'cancelled', 'needs_review'].includes(d.status)) res.reload(true);
  });
  useHubEvent('reconnect', () => res.reload(true));
  useEffect(() => {
    const wake = () => { if (document.visibilityState === 'visible') res.reload(true); };
    const changed = () => res.reload(true);
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('hm:hub-favorite', changed);
    return () => {
      document.removeEventListener('visibilitychange', wake);
      window.removeEventListener('hm:hub-favorite', changed);
    };
  }, [res.reload]);
  const songs = useMemo(() => res.data?.songs ?? [], [res.data]);
  const search = useListSearch(songs);
  const q = search.q;

  return (
    <div>
      <PageHeader title="Понравившиеся" back="/library" sub={res.data ? tracksWord(songs.length) : undefined} right={<IconButton icon="refresh" label="Обновить" onClick={() => res.reload(true)} />} />
      {res.loading ? (
        <SkeletonRows />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !songs.length && !res.data?.albums.length && !res.data?.artists.length ? (
        <Empty icon="heart" title="Пока ничего" text="Ставьте ♥ трекам, альбомам и исполнителям — они соберутся здесь." />
      ) : (
        <>
          <ListActions tracks={search.found ? search.found.map((f) => songs[f.index]) : songs} context="liked" />
          <SongList tracks={songs} found={search.found} />
          {songs.length > 0 && search.control}
          {!q && <AlbumRow title="Альбомы" albums={res.data?.albums ?? []} rowKey="liked:albums" />}
          {!q && (res.data?.artists.length ?? 0) > 0 && (
            <section aria-label="Исполнители">
              <SectionTitle>Исполнители</SectionTitle>
              <ArtistRow artists={res.data?.artists ?? []} rowKey="liked:artists" />
            </section>
          )}
        </>
      )}
    </div>
  );
}

export function ArtistRow({ artists, rowKey, radio }: { artists: NdArtistOut[]; rowKey: string; radio?: boolean }) {
  const row = useRememberScrollX(rowKey);
  return (
    <div ref={row} className="no-scrollbar flex gap-3 overflow-x-auto px-4 pb-1">
      {artists.map((a) => (
        <NdArtistBubble key={a.id} artist={a} radio={radio} />
      ))}
    </div>
  );
}

export function PlaylistPage({ id }: { id: string }) {
  const desktop = useDesktop();
  const res = useAsync((s) => api.playlist(id, s), [id]);
  const p = res.data;
  useSmartPage(`playlist:${id}`, p?.name ?? null);
  const all = useMemo(() => p?.tracks ?? [], [p]);
  const search = useListSearch(all);

  const remove = async (i: number, t: NdSongOut) => {
    if (await attempt(() => api.removeFromPlaylist(id, [i]), `«${t.title}» убран из плейлиста`))
      res.setData((prev) => (prev ? { ...prev, song_count: prev.song_count - 1, duration_s: Math.max(0, prev.duration_s - (t.duration_s ?? 0)), tracks: prev.tracks.filter((_, j) => j !== i) } : prev));
  };
  const drop = async () => {
    if (!p || !(await confirmDialog({ title: `Удалить плейлист «${p.name}»?`, body: 'Песни останутся в библиотеке.', okLabel: 'Удалить', danger: true }))) return;
    if (await attempt(() => api.deletePlaylist(id), 'Плейлист удалён')) navigate('/playlists', { replace: true });
  };

  if (res.loading) return (<><PageHeader title="Плейлист" back="/playlists" /><SkeletonRows /></>);
  if (!p) return (<><PageHeader title="Плейлист" back="/playlists" /><ErrorState text={res.error ?? 'Плейлист не найден'} onRetry={() => res.reload()} /></>);
  return (
    <div>
      <PageHeader title={desktop ? '' : p.name} back="/playlists" right={desktop ? undefined : <IconButton icon="trash" label="Удалить плейлист" onClick={drop} />} />
      <Hero
        kicker="Плейлист"
        cover={p.cover_art && p.tracks.length ? `/api/nd/cover/${enc(p.cover_art)}?size=600&v=${p.tracks.length}` : p.tracks[0]?.cover_url}
        title={p.name}
        sub={[tracksWord(p.song_count), p.duration_s ? fmtLength(p.duration_s) : null].filter(Boolean).join(' · ')}
      />
      <ListActions tracks={p.tracks} context="playlist">
        {desktop && <MoreMenu items={[{ icon: 'trash', label: 'Удалить плейлист', danger: true, onClick: () => void drop() }]} />}
      </ListActions>
      {p.tracks.length ? (
        <>
          <SongList tracks={p.tracks} found={search.found} extra={(t, i) => [{ icon: 'x', label: 'Убрать из плейлиста', onClick: () => void remove(i, t) }]} />
          {search.control}
        </>
      ) : (
        <Empty icon="playlist" title="Плейлист пуст" text="Добавляйте треки через ⋯ → «Добавить в плейлист»." />
      )}
    </div>
  );
}

export function ArtistsPage() {
  const res = useAsync((s) => api.ndArtists(s), []);
  const [q, setQ] = useState('');
  const shown = useMemo(() => {
    const needle = q.trim().toLocaleLowerCase('ru');
    return (res.data ?? []).filter((a) => !needle || a.name.toLocaleLowerCase('ru').includes(needle));
  }, [res.data, q]);
  return (
    <div>
      <PageHeader title="Исполнители" back="/library" sub={res.data ? `${res.data.length}` : undefined} />
      <div className="px-4 pb-2">
        <input className="input" type="search" placeholder="Найти исполнителя" aria-label="Найти исполнителя" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {res.loading ? (
        <SkeletonRows />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !shown.length ? (
        <Empty icon="user" title={q ? 'Никого не нашлось' : 'В библиотеке пока нет исполнителей'} />
      ) : (
        <ul>
          {shown.map((a) => (
            <li key={a.id}>
              <Link to={`/lib/artist/${enc(a.id)}`} className="flex min-h-16 items-center gap-3 px-4 py-2 hover:bg-fg/5">
                <Cover src={a.cover_url} size={48} round icon="user" alt="" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{a.name}</span>
                  <span className="block text-xs text-muted">{albumsWord(a.album_count)}</span>
                </span>
                {a.starred && <Icon name="heart" filled size={16} className="text-accent" />}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const albumsWord = (n: number) => {
  const m10 = n % 10, m100 = n % 100;
  const w = m10 === 1 && m100 !== 11 ? 'альбом' : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? 'альбома' : 'альбомов';
  return `${n} ${w}`;
};

export function NdArtistPage({ id }: { id: string }) {
  const res = useAsync((s) => api.ndArtist(id, s), [id]);
  const { starting } = useWave();
  if (res.loading) return (<><PageHeader title="Исполнитель" back="/artists" /><SkeletonRows /></>);
  if (!res.data) return (<><PageHeader title="Исполнитель" back="/artists" /><ErrorState text={res.error ?? 'Исполнитель не найден'} onRetry={() => res.reload()} /></>);
  const { artist, albums } = res.data;
  return (
    <div>
      <PageHeader
        title={artist.name}
        back="/artists"
        right={<LikeButton targetType="artist" ndId={artist.id} title={artist.name} data={{ name: artist.name }} />}
      />
      <Hero
        cover={artist.cover_url ?? albums[0]?.cover_url}
        round
        title={artist.name}
        sub={albumsWord(albums.length)}
        right={
          canWave() && (
            <Button
              variant="primary"
              icon="wave"
              busy={starting === seedKey({ nd_artist_id: artist.id })}
              onClick={() => void startWave({ nd_artist_id: artist.id }, 'radio')}
            >
              Радио по исполнителю
            </Button>
          )
        }
      />
      <div className="grid grid-cols-2 gap-4 px-4 pb-4 sm:grid-cols-3">
        {albums.map((a) => (
          <NdAlbumCard key={a.id} album={a} showArtist={false} />
        ))}
      </div>
    </div>
  );
}

export function NdAlbumPage({ id }: { id: string }) {
  const res = useAsync((s) => api.ndAlbum(id, s), [id]);
  if (res.loading) return (<><PageHeader title="Альбом" back="/library" /><SkeletonRows /></>);
  if (!res.data) return (<><PageHeader title="Альбом" back="/library" /><ErrorState text={res.error ?? 'Альбом не найден'} onRetry={() => res.reload()} /></>);
  const a = res.data;
  const tracks = a.tracks ?? [];
  return (
    <div>
      <PageHeader
        title={a.title}
        sub={a.artist}
        back="/library"
        right={<LikeButton targetType="album" ndId={a.id} title={a.title} data={{ name: a.title, artist: a.artist }} />}
      />
      <Hero
        cover={a.cover_url?.replace(/size=\d+/, 'size=600')}
        title={a.title}
        sub={
          <>
            {a.artist_id ? (
              <Link to={`/lib/artist/${enc(a.artist_id)}`} className="font-medium text-fg hover:underline">
                {a.artist}
              </Link>
            ) : (
              a.artist
            )}
            {[a.year, tracksWord(a.song_count), a.duration_s ? fmtLength(a.duration_s) : null].filter(Boolean).map((x) => ` · ${x}`)}
          </>
        }
      />
      <ListActions tracks={tracks} context="album" />
      <ul>
        {tracks.length > 0 && <li><TrackTableHeader numbered /></li>}
        {tracks.map((t, i) => (
          <li key={`${t.nd_song_id}:${i}`}>
            <TrackRow track={t} number={t.track_no ?? i + 1} queue={tracks} link={false} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** «Все альбомы» of the library, by name, a page at a time. */
export function NdAlbumsPage() {
  const [pages, setPages] = useState<NdAlbumOut[][]>([]);
  const first = useAsync((s) => api.ndAlbums('alphabeticalByName', 0, 60, s), []);
  const [busy, setBusy] = useState(false);
  const all = [...(first.data ?? []), ...pages.flat()];
  const done = (first.data?.length ?? 0) < 60 || (pages.length > 0 && pages[pages.length - 1].length < 60);
  const more = async () => {
    setBusy(true);
    const next = await attempt(() => api.ndAlbums('alphabeticalByName', all.length, 60));
    setBusy(false);
    if (next) setPages((p) => [...p, next]);
  };
  return (
    <div>
      <PageHeader title="Все альбомы" back="/library" />
      {first.loading ? (
        <SkeletonRows />
      ) : first.error ? (
        <ErrorState text={first.error} onRetry={() => first.reload()} />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 px-4 pb-4 sm:grid-cols-3">
            {all.map((a) => (
              <NdAlbumCard key={a.id} album={a} />
            ))}
          </div>
          {!done && (
            <div className={cx('px-4 pb-6')}>
              <Button block onClick={more} busy={busy}>
                Показать ещё
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
