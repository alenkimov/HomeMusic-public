import { Children, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { NdAlbumOut, NdArtistOut, NdSongOut, TrackOut } from '../api/types';
import { playAll, TrackRow, TrackTableHeader, type ListSort } from '../components/catalog';
import { Icon } from '../components/Icon';
import { LikeButton } from '../components/LikeButton';
import { ListBar } from '../components/ListBar';
import { useListSearch, type Found } from '../components/ListSearch';
import { CoverView, ownCover } from '../components/PlaylistMenu';
import { SaveSelection, TagFilterChips } from '../components/Tags';
import { Button, Cover, Empty, ErrorState, IconButton, PageHeader, SectionTitle, SkeletonRows, Spinner, cx } from '../components/ui';
import { fmtLength, plural, tracksWord } from '../lib/format';
import { AUTO_PLAYLISTS, playCount, usePlayCounts } from '../lib/stats';
import { useDesktop } from '../lib/desktop';
import { useHubEvent } from '../lib/events';
import { cyclePageShuffle, useListen } from '../lib/listen';
import { useAsync, useRememberScrollX } from '../lib/hooks';
import { Link, enc, navigate, useRoute } from '../lib/router';
import { filterActive, filterOfQuery, filterWords, passes, selectionsChanged, tagKey, useTags, type TagFilter } from '../lib/tags';
import { attempt, confirmDialog } from '../lib/ui';
import { useSmartForPage, useSmartPage, useSmartPossible } from '../lib/smartShuffle';
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

/** ▶ and the shuffle of a whole list, at the right of its name. The shuffle goes round as the player's — off, on,
 * and for a playlist or «Любимое» the smart one; ▶ starts the list as the button says. */
function PlayPair({ tracks, context }: { tracks: readonly TrackOut[]; context?: string }) {
  const canSmart = useSmartPossible() && (context === 'playlist' || context === 'liked');
  const smart = useSmartForPage() && canSmart; // the shuffle is the smart one
  const shuffleOn = useListen((s) => s.shuffle);
  return (
    <div className="flex shrink-0 items-center gap-1">
      <IconButton
        icon={smart ? 'shuffleSmart' : 'shuffle'}
        size={26}
        aria-pressed={shuffleOn}
        label={!shuffleOn ? 'Включить перемешивание' : smart ? 'Умное перемешивание включено — выключить' : canSmart ? 'Перемешивание включено — включить умное' : 'Перемешивание включено — выключить'}
        onClick={() => cyclePageShuffle(canSmart)}
        className={shuffleOn ? '!text-accent' : 'text-muted hover:text-fg'}
      />
      <button
        type="button"
        aria-label="Слушать"
        title="Слушать"
        onClick={() => playAll(tracks, { context })}
        className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition hover:scale-105 active:scale-95"
      >
        <Icon name="play" size={26} filled className="translate-x-0.5" />
      </button>
    </div>
  );
}

/** What else can be done with a list, in a row under its head (nothing: no row). */
export function ListActions({ children }: { children?: ReactNode }) {
  if (!Children.toArray(children).length) return null;
  return <div className="flex flex-wrap items-center gap-2 px-4 pb-3 lg:px-6">{children}</div>;
}

/** `found`: the rows a search of the list left (`useListSearch`) — they keep their places in the whole list: the
 * number shown, and what `extra` gets (a playlist removes a track by its place). */
/** The rows of a list by «Дата добавления» (`sort`: null — the list's own order); each keeps its place in the list.
 * Tracks of one moment (an import) keep the list's order, undated ones go last. */
function byAdded<T extends { t: NdSongOut; i: number }>(list: T[], sort: ListSort): T[] {
  if (!sort) return list;
  if (sort === 'most' || sort === 'least') return [...list].sort((a, b) => (playCount(b.t.nd_song_id) - playCount(a.t.nd_song_id)) * (sort === 'most' ? 1 : -1) || a.i - b.i);
  const at = (t: NdSongOut) => (t.added_at ? Date.parse(/T[\d:.]+$/.test(t.added_at) ? t.added_at + 'Z' : t.added_at) : NaN);
  return [...list].sort((a, b) => {
    const x = at(a.t);
    const y = at(b.t);
    if (Number.isNaN(x) || Number.isNaN(y)) return Number.isNaN(x) === Number.isNaN(y) ? a.i - b.i : Number.isNaN(x) ? 1 : -1;
    return (sort === 'new' ? y - x : x - y) || a.i - b.i;
  });
}

/** The rows of a list that the search and the tag filter leave (null: neither is on — the whole list). */
export function useShown(tracks: readonly TrackOut[], found: Found[] | null, filter: TagFilter): Found[] | null {
  const tags = useTags();
  return useMemo(() => {
    if (!filterActive(filter)) return found;
    const base: Found[] = found ?? tracks.map((_, index) => ({ index }));
    return base.filter((f) => {
      const id = tracks[f.index] ? tagKey(tracks[f.index]) : null;
      return passes(id ? tags.map[id] : undefined, filter, tags.byId);
    });
  }, [tracks, found, filter, tags.map, tags.byId]);
}

/** The tracks as the page shows them: what ▶ of the page plays. */
const inOrder = (tracks: readonly NdSongOut[], sort: ListSort): readonly NdSongOut[] => (sort ? byAdded(tracks.map((t, i) => ({ t, i })), sort).map((r) => r.t) : tracks);

function SongList({ tracks, extra, found, sort, onSort, period }: { period?: { label: string; of: Record<string, number> }; tracks: readonly NdSongOut[]; extra?: (t: NdSongOut, i: number) => Parameters<typeof TrackRow>[0]['menuExtra']; found?: Found[] | null; sort: ListSort; onSort: (next: ListSort) => void }) {
  const setSort = onSort;
  const dated = useMemo(() => tracks.some((t) => t.added_at), [tracks]);
  const counts = usePlayCounts();
  const rows = useMemo(() => {
    const list = found ? found.map((f) => ({ t: tracks[f.index], i: f.index, line: f.line })) : tracks.map((t, i) => ({ t, i, line: undefined as string | undefined }));
    return byAdded(list, sort);
  }, [tracks, found, sort, counts]);
  const queue = found || sort ? rows.map((r) => r.t) : tracks;
  if (found && !rows.length) return <p className="px-4 py-10 text-center text-sm text-muted">Ничего не подходит — ни под поиск, ни под выбранные теги.</p>;
  return (
    <ul>
      {rows.length > 0 && <li><TrackTableHeader numbered album tags plays playsLabel={period?.label} added={dated} sort={sort} onSort={setSort} /></li>}
      {rows.map(({ t, i, line }, at) => (
        <li key={`${t.ref}:${i}`}>
          <TrackRow track={t} queue={queue} link={false} showAlbum showTags showPlays playsOf={period ? (period.of[t.nd_song_id ?? ''] ?? 0) : undefined} showAdded={dated} menuExtra={extra?.(t, i)} index={sort ? at + 1 : i + 1} />
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

/** The head of a list: its picture in its own light (the cover, blurred, fading into the page; the accent's for a
 * list with no cover), then its name with ▶ and the shuffle of `tracks` at the right. `art`: a picture of the page's
 * own in place of the cover («Любимое»: the heart). `onCover`: a tap on the picture (it opens large). */
export function Hero({ cover, round, title, sub, right, kicker, art, tracks, context, onCover }: { cover?: string | null; round?: boolean; title: string; sub?: ReactNode; right?: ReactNode; kicker?: string; art?: ReactNode; tracks?: readonly TrackOut[]; context?: string; onCover?: () => void }) {
  const desktop = useDesktop();
  const play = tracks?.length ? <PlayPair tracks={tracks} context={context} /> : null;
  const picture = (size: string) =>
    art ? (
      <div className={cx('aspect-square shrink-0 overflow-hidden rounded-md shadow-2xl', size)}>{art}</div>
    ) : onCover ? (
      <button type="button" onClick={onCover} aria-label="Открыть обложку" title="Открыть обложку" className={cx('shrink-0 transition active:scale-[0.98]', size)}>
        <Cover src={cover} round={round} icon={round ? 'user' : 'disc'} alt={title} className="aspect-square w-full shadow-2xl" />
      </button>
    ) : (
      <Cover src={cover} round={round} icon={round ? 'user' : 'disc'} alt={title} className={cx('aspect-square shrink-0 shadow-2xl', size)} />
    );
  // it reaches up under the bar with «back»
  const light = (
    <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 -top-20 bottom-0 -z-10 overflow-hidden [mask-image:linear-gradient(black_35%,transparent)]">
      {cover ? <img src={cover} alt="" referrerPolicy="no-referrer" className="h-full w-full scale-150 object-cover opacity-50 blur-3xl" /> : <div className="h-full w-full bg-accent/25" />}
    </div>
  );
  if (desktop)
    return (
      <div className="relative isolate flex items-end gap-6 px-6 pb-5 pt-3">
        {light}
        {picture('w-48')}
        <div className="min-w-0 flex-1">
          {kicker && <div className="text-sm text-muted">{kicker}</div>}
          <h2 data-list-title className="line-clamp-2 break-words text-6xl font-black leading-[1.05] tracking-tight">{title}</h2>
          {sub && <div className="mt-3 text-sm text-muted">{sub}</div>}
        </div>
        {right}
        {play}
      </div>
    );
  return (
    <div className="relative isolate px-4 pb-3">
      {light}
      <div className="flex justify-center pb-4">{picture('w-52')}</div>
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <h2 data-list-title className="line-clamp-2 text-2xl font-bold leading-tight">{title}</h2>
          {sub && <div className="mt-1 text-sm text-muted">{sub}</div>}
        </div>
        {play}
      </div>
      {right && <div className="pt-3">{right}</div>}
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
  const length = useMemo(() => songs.reduce((sum, t) => sum + (t.duration_s ?? 0), 0), [songs]);
  const search = useListSearch(songs);
  const [sort, setSort] = useState<ListSort>(null);
  const [filter, setFilter] = useState<TagFilter>({});
  const [saving, setSaving] = useState(false);
  const shown = useShown(songs, search.found, filter);
  const songIds = useMemo(() => songs.map(tagKey), [songs]);
  const playing = inOrder(shown ? shown.map((f) => songs[f.index]) : songs, sort);

  return (
    <div>
      {saving && <SaveSelection scope="liked" filter={filter} onClose={() => setSaving(false)} onSaved={(r) => navigate(`/selection/${r.id}`)} />}
      <ListBar title="Любимое" back="/library" tracks={playing} context="liked" search={songs.length ? search : undefined} tags={songs.length ? { songIds, filter, onChange: setFilter, onSave: () => setSaving(true), count: shown ? shown.length : songs.length } : undefined} />
      {res.data && !search.alone && (
        <Hero
          kicker="Плейлист"
          title="Любимое"
          art={
            <div className="flex h-full w-full items-center justify-center bg-accent/20 text-accent">
              <Icon name="heart" size={84} filled />
            </div>
          }
          sub={[tracksWord(songs.length), length ? fmtLength(length) : null].filter(Boolean).join(' · ')}
          tracks={playing}
          context="liked"
        />
      )}
      {res.loading ? (
        <SkeletonRows />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !songs.length ? (
        <Empty icon="heart" title="Пока ничего" text="Нажмите «+» у трека — он окажется здесь." />
      ) : (
        <>
          <TagFilterChips filter={filter} onChange={setFilter} count={shown ? shown.length : songs.length} />
          <SongList tracks={songs} found={shown} sort={sort} onSort={setSort} />
        </>
      )}
    </div>
  );
}

/** «Подборка по тегам» (`/selection/<id>`): a saved filter over «Любимое», a playlist or the whole library — its
 * tracks are found anew each time. `/selection/library?yes=…&no=…` is the same over the whole library, not saved
 * yet (the page of tags opens it). The filter can be changed here and saved again. */
/** An «автоплейлист» («Топ месяца», «Давно не слушал»…): the Hub makes the list by its rule every time the page is
 * opened; nothing can be put into it or taken out. */
export function AutoPlaylistPage({ kind }: { kind: string }) {
  const def = AUTO_PLAYLISTS.find((a) => a.kind === kind);
  const res = useAsync(
    async (s) => {
      const [auto, songs] = await Promise.all([api.statsAuto(kind, s), api.ndSongs(s)]);
      const byId = new Map(songs.map((t) => [t.nd_song_id, t] as const));
      const tracks = auto.items.map((x) => byId.get(x.nd_song_id)).filter((t): t is NdSongOut => !!t).map((t) => ({ ...t, added_at: null }));
      return { tracks, plays: Object.fromEntries(auto.items.map((x) => [x.nd_song_id, x.plays])) };
    },
    [kind],
  );
  const tracks = useMemo(() => res.data?.tracks ?? [], [res.data]);
  const length = useMemo(() => tracks.reduce((sum, t) => sum + (t.duration_s ?? 0), 0), [tracks]);
  const title = def?.title ?? 'Автоплейлист';
  return (
    <div>
      <ListBar title={title} back="/library" tracks={tracks} context="auto" />
      <Hero
        kicker="Автоплейлист"
        title={title}
        art={
          <div className="flex h-full w-full items-center justify-center bg-accent/20 text-accent">
            <Icon name={def?.icon ?? 'chart'} size={84} />
          </div>
        }
        sub={res.data ? [tracksWord(tracks.length), length ? fmtLength(length) : null, 'обновляется сам'].filter(Boolean).join(' · ') : ''}
        tracks={tracks}
        context="auto"
      />
      {res.loading ? (
        <SkeletonRows />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !tracks.length ? (
        <Empty icon="history" title="Пока пусто" text={kind === 'forgotten' ? 'Здесь появятся треки, которые вы слушали хотя бы три раза и не включали три месяца.' : 'Здесь появятся треки, которые вы слушали за этот период.'} />
      ) : (
        <>
          <SongList tracks={tracks} sort={null} onSort={() => undefined} period={{ label: def?.column ?? 'Прослушивания', of: res.data!.plays }} />
        </>
      )}
    </div>
  );
}

export function SelectionPage({ id }: { id: string }) {
  const route = useRoute();
  const tags = useTags();
  const unsaved = id === 'library';
  const sel = useAsync(async (s) => (unsaved ? null : ((await api.tagSelections(s)).find((x) => String(x.id) === id) ?? null)), [id]);
  const scope = unsaved ? 'library' : sel.data?.scope;
  const src = useAsync(
    async (s) => {
      if (!scope) return null;
      if (scope === 'liked') return { title: 'Любимое', tracks: (await api.ndLiked(s)).songs };
      if (scope.startsWith('playlist:')) {
        const p = await api.playlist(scope.slice(9), s);
        return { title: p.name, tracks: p.tracks };
      }
      return { title: 'Вся библиотека', tracks: await api.ndSongs(s) };
    },
    [scope],
  );
  const [filter, setFilter] = useState<TagFilter>({});
  const [sort, setSort] = useState<ListSort>(null);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const saved = sel.data?.filter;
  useEffect(() => {
    setFilter(unsaved ? filterOfQuery(route.query) : (saved ?? {}));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, saved]);
  const tracks = useMemo(() => src.data?.tracks ?? [], [src.data]);
  const search = useListSearch(tracks);
  const shown = useShown(tracks, search.found, filter);
  const songIds = useMemo(() => tracks.map(tagKey), [tracks]);
  const changed = !unsaved && !!saved && JSON.stringify(Object.entries(saved).sort()) !== JSON.stringify(Object.entries(filter).sort());
  const name = unsaved ? 'Вся библиотека' : (sel.data?.name ?? 'Подборка');

  const save = async () => {
    if (!sel.data) return;
    setBusy(true);
    const r = await attempt(() => api.tagSelectionSave(sel.data!.id, { name: sel.data!.name, scope: sel.data!.scope, filter }), 'Подборка обновлена');
    setBusy(false);
    if (r) {
      sel.setData(r);
      selectionsChanged();
    }
  };
  const drop = async () => {
    if (!sel.data || !(await confirmDialog({ title: `Удалить подборку «${sel.data.name}»?`, body: 'Треки и их теги останутся.', okLabel: 'Удалить', danger: true }))) return;
    if (await attempt(() => api.tagSelectionDelete(sel.data!.id), 'Подборка удалена')) {
      selectionsChanged();
      navigate('/tags', { replace: true });
    }
  };

  if (sel.loading || src.loading) return (<><PageHeader hero title="Подборка" back="/tags" /><SkeletonRows /></>);
  if (!unsaved && !sel.data) return (<><PageHeader hero title="Подборка" back="/tags" /><ErrorState text={sel.error ?? 'Подборка не найдена'} onRetry={() => sel.reload()} /></>);
  if (!src.data) return (<><PageHeader hero title={name} back="/tags" /><ErrorState text={src.error ?? 'Не удалось получить треки'} onRetry={() => src.reload()} /></>);
  const list = shown ? shown.map((f) => tracks[f.index]) : tracks;
  return (
    <div>
      {saving && <SaveSelection scope="library" filter={filter} onClose={() => setSaving(false)} onSaved={(r) => navigate(`/selection/${r.id}`, { replace: true })} />}
      <ListBar title={name} back="/tags" tracks={inOrder(list, sort)} context="selection" search={search} tags={{ songIds, filter, onChange: setFilter, onSave: unsaved ? () => setSaving(true) : undefined, count: list.length }} />
      {!search.alone && <Hero
        kicker={unsaved ? 'Фильтр по тегам' : 'Подборка по тегам'}
        title={name}
        art={
          <div className="flex h-full w-full items-center justify-center bg-accent/20 text-accent">
            <Icon name="filter" size={84} />
          </div>
        }
        sub={[unsaved ? null : src.data.title, filterActive(filter) ? filterWords(filter, tags.byId) : 'теги не выбраны', `${tracksWord(list.length)}${list.length ? ` · ${fmtLength(list.reduce((n, t) => n + (t.duration_s ?? 0), 0))}` : ''}`].filter(Boolean).join(' · ')}
        tracks={inOrder(list, sort)}
        context="selection"
      />}
      <ListActions>
        {!unsaved && changed && filterActive(filter) && (
          <Button small variant="primary" busy={busy} onClick={() => void save()}>
            Сохранить изменения
          </Button>
        )}
        {!unsaved && <IconButton icon="trash" size={24} label="Удалить подборку" onClick={() => void drop()} className="text-muted hover:text-danger" />}
      </ListActions>
      <TagFilterChips filter={filter} onChange={setFilter} count={list.length} />
      <SongList tracks={tracks} found={shown ?? (filterActive(filter) ? [] : null)} sort={sort} onSort={setSort} />
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
  const res = useAsync((s) => api.playlist(id, s), [id]);
  const p = res.data;
  useSmartPage(`playlist:${id}`, p?.name ?? null);
  // the playlist is the Hub's: every track stands in its place, whether the library has its file yet or not
  const all = useMemo(() => p?.tracks ?? [], [p]);
  const waits = all.filter((t) => !t.owned).length;
  const search = useListSearch(all);
  const [sort, setSort] = useState<ListSort>(null);
  const [viewing, setViewing] = useState(false);
  const [filter, setFilter] = useState<TagFilter>({});
  const [saving, setSaving] = useState(false);
  const shown = useShown(all, search.found, filter);
  const songIds = useMemo(() => all.map(tagKey), [all]);
  const closeCover = useCallback(() => setViewing(false), []);
  // its name or its picture changed (the playlist's menu, the picture's window), or it is gone: the Hub says so
  useHubEvent('library', () => res.reload(true));

  const remove = async (i: number, t: NdSongOut) => {
    if (await attempt(() => api.removeFromPlaylist(id, [i]), `«${t.title}» убран из плейлиста`))
      res.setData((prev) => (prev ? { ...prev, song_count: prev.song_count - (t.owned ? 1 : 0), duration_s: Math.max(0, prev.duration_s - (t.duration_s ?? 0)), tracks: prev.tracks.filter((_, j) => j !== i) } : prev));
  };

  if (res.loading && !p) return (<><PageHeader hero title="Плейлист" back="/library" /><SkeletonRows /></>);
  if (!p) return (<><PageHeader hero title="Плейлист" back="/library" /><ErrorState text={res.error ?? 'Плейлист не найден'} onRetry={() => res.reload()} /></>);
  const cover = p.cover_url ? `${p.cover_url}&size=600` : p.tracks[0]?.cover_url;
  const playing = inOrder(shown ? shown.map((f) => all[f.index]) : all, sort);
  return (
    <div>
      <ListBar title={p.name} back="/library" tracks={playing} context="playlist" search={all.length ? search : undefined} tags={all.length ? { songIds, filter, onChange: setFilter, onSave: () => setSaving(true), count: shown ? shown.length : all.length } : undefined} />
      {viewing && <CoverView src={cover} title={p.name} playlistId={id} own={ownCover(p.cover_url)} onClose={closeCover} />}
      {!search.alone && <Hero
        kicker="Плейлист"
        onCover={() => setViewing(true)}
        cover={cover}
        title={p.name}
        sub={[tracksWord(all.length), p.duration_s ? fmtLength(p.duration_s) : null, waits ? `${waits} ещё не ${plural(waits, ['скачан', 'скачаны', 'скачаны'])} — скачаются, когда послушаете` : null].filter(Boolean).join(' · ')}
        tracks={playing}
        context="playlist"
      />}
      {all.length ? (
        <>
          {saving && <SaveSelection scope={`playlist:${id}`} filter={filter} onClose={() => setSaving(false)} onSaved={(r) => navigate(`/selection/${r.id}`)} />}
          <TagFilterChips filter={filter} onChange={setFilter} count={shown ? shown.length : all.length} />
          <SongList tracks={all} found={shown} sort={sort} onSort={setSort} extra={(t, i) => [{ icon: 'x', label: 'Убрать из плейлиста', onClick: () => void remove(i, t) }]} />
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
  if (res.loading) return (<><PageHeader hero title="Исполнитель" back="/artists" /><SkeletonRows /></>);
  if (!res.data) return (<><PageHeader hero title="Исполнитель" back="/artists" /><ErrorState text={res.error ?? 'Исполнитель не найден'} onRetry={() => res.reload()} /></>);
  const { artist, albums } = res.data;
  return (
    <div>
      <PageHeader
        hero
        title={artist.name}
        back="/artists"
        right={<LikeButton targetType="artist" ndId={artist.id} title={artist.name} data={{ name: artist.name }} />}
      />
      <Hero
        kicker="Исполнитель"
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
  if (res.loading) return (<><PageHeader hero title="Альбом" back="/library" /><SkeletonRows /></>);
  if (!res.data) return (<><PageHeader hero title="Альбом" back="/library" /><ErrorState text={res.error ?? 'Альбом не найден'} onRetry={() => res.reload()} /></>);
  const a = res.data;
  const tracks = a.tracks ?? [];
  return (
    <div>
      <PageHeader
        hero
        title={a.title}
        sub={a.artist}
        back="/library"
      />
      <Hero
        kicker="Альбом"
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
        tracks={tracks}
        context="album"
      />
      <ul>
        {tracks.length > 0 && <li><TrackTableHeader numbered /></li>}
        {tracks.map((t, i) => (
          <li key={`${t.nd_song_id}:${i}`}>
            <TrackRow track={t} index={t.track_no ?? i + 1} queue={tracks} link={false} />
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
