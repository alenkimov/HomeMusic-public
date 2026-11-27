import { titleWithVersion } from '../lib/versionLabels';
import { Children, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { imageUrl } from '../lib/img';
import { useSoftCover } from '../lib/softCover';
import { ArtistBanner, ArtistPage, BannerPicture } from './Artist';
import { api } from '../api/client';
import type { NdAlbumOut, NdArtistOut, NdSongOut, TrackOut } from '../api/types';
import { LibraryAlbumMenu } from '../components/AlbumEdit';
import { playAll, TrackRow, TrackTableHeader, type ListSort } from '../components/catalog';
import { TrackTable } from '../components/TrackTable';
import { Icon } from '../components/Icon';
import { ListBar } from '../components/ListBar';
import { useListSearch, type Found } from '../components/ListSearch';
import { CoverView, ownCover } from '../components/PlaylistMenu';
import { ContextMenu, menuAtButton, useContextMenu, type MenuAt } from '../components/ContextMenu';
import { NdAlbumMenu, NdArtistMenu } from '../components/EntityMenus';
import { AlbumCard, AlbumHero } from '../components/Album';
import { ArtistRow, artistLine } from '../components/Artist';
import { LIKED_MARK, PlaylistHero, playlistLook } from '../components/Playlist';
import { useSession } from '../lib/session';
import { listChanged, patch, useCardUpdates } from '../lib/cards';
import { SaveSelection, TagFilterChips } from '../components/Tags';
import { ArtistLooksWindow } from '../components/ArtistLooks';
import { ArtistProjects, ArtistWindow, artistHint, useArtistCard } from '../components/ArtistNames';
import { Button, Cover, Empty, ErrorState, HeroSkeleton, IconButton, PageHeader, SkeletonRows, Spinner, cx , SectionTitle, buttonClass } from '../components/ui';
import { fmtLength, tracksWord } from '../lib/format';
import { playCount, usePlayCounts } from '../lib/stats';
import { useDesktop } from '../lib/desktop';
import { openAlbumArtwork } from '../lib/coverPicker';
import { useHubEvent } from '../lib/events';
import { needsTrackFile, withRecordingAvailability } from '../lib/findFile';
import { useListSort } from '../lib/listSort';
import { cyclePageShuffle, useListen } from '../lib/listen';
import { useAsync, useRememberScrollX, useCoalesced } from '../lib/hooks';
import { enc, navigate, useCanonicalPath, useRoute } from '../lib/router';
import { filterActive, filterOfQuery, filterWords, passes, selectionsChanged, tagKey, useTags, type TagFilter } from '../lib/tags';
import { attempt, confirmDialog } from '../lib/ui';
import { openPlaylistMenu } from '../lib/playlistMenu';
import { useSmartForPage, useSmartPage, useSmartPossible } from '../lib/smartShuffle';
import { toggleLike, useLiked } from '../lib/stars';
import { seedKey, startWave, useWave } from '../lib/wave';
import { VirtualRows } from '../components/VirtualRows';

/** The Navidrome library as a player shows it. Songs are `NdSongOut` (owned catalog-like tracks), so `TrackRow` plays them. */

// --- pieces ------------------------------------------------------------------------------------------------

export function NdAlbumCard({ album, showArtist = true }: { album: NdAlbumOut; showArtist?: boolean }) {
  return (
    <AlbumCard
      album={{ title: album.title, to: `/lib/album/${enc(album.id)}`, cover: album.cover_url, artist: album.artist, year: album.year }}
      showArtist={showArtist}
      menu={(at, onClose) => <NdAlbumMenu album={album} at={at} onClose={onClose} />}
    />
  );
}


/** ▶ and the shuffle of a whole list, at the right of its name. The shuffle goes round as the player's — off, on,
 * and for a playlist or «Любимое» the smart one; ▶ starts the list as the button says. */
export function PlayPair({ tracks, context }: { tracks: readonly TrackOut[]; context?: string }) {
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
function byAdded<T extends { t: TrackOut; i: number }>(list: T[], sort: ListSort): T[] {
  if (!sort) return list;
  if (sort === 'most' || sort === 'least') return [...list].sort((a, b) => (playCount(b.t.nd_song_id) - playCount(a.t.nd_song_id)) * (sort === 'most' ? 1 : -1) || a.i - b.i);
  const at = (t: TrackOut) => (t.added_at ? Date.parse(/T[\d:.]+$/.test(t.added_at) ? t.added_at + 'Z' : t.added_at) : NaN);
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
export const inOrder = <T extends TrackOut>(tracks: readonly T[], sort: ListSort): readonly T[] => (sort ? byAdded(tracks.map((t, i) => ({ t, i })), sort).map((r) => r.t) : tracks);

const songRowKey = (row: { t: TrackOut; i: number }) => `${row.t.ref}:${row.i}`;

export function SongList<T extends TrackOut>({ tracks, extra, found, sort, onSort, period }: { period?: { label: string; of: Record<string, number> }; tracks: readonly T[]; extra?: (t: T, i: number) => Parameters<typeof TrackRow>[0]['menuExtra']; found?: Found[] | null; sort: ListSort; onSort: (next: ListSort) => void }) {
  const setSort = onSort;
  const dated = useMemo(() => tracks.some((t) => t.added_at), [tracks]);
  const counts = usePlayCounts();
  const rows = useMemo(() => {
    const list = found ? found.map((f) => ({ t: tracks[f.index], i: f.index, line: f.line })) : tracks.map((t, i) => ({ t, i, line: undefined as string | undefined }));
    return byAdded(list, sort);
  }, [tracks, found, sort, counts]);
  const displayed = useMemo(() => rows.map(({ t }) => t), [rows]);
  const queue = useMemo(() => plays(found || sort ? rows.map((r) => r.t) : tracks), [rows, tracks, found, sort]);
  if (found && !rows.length) return <p className="px-4 py-10 text-center text-sm text-muted">Ничего не подходит — ни под поиск, ни под выбранные теги.</p>;
  return (
    <TrackTable tracks={displayed} numbered album tags plays added={dated} periodPlays={!!period}>
      {rows.length > 0 && <TrackTableHeader numbered album tags plays playsLabel={period?.label} added={dated} sort={sort} onSort={setSort} />}
      <VirtualRows
        items={rows}
        rowKey={songRowKey}
        render={({ t, i, line }, at) => (
          <>
            <TrackRow track={t} queue={queue} rows={displayed} link={false} showAlbum showTags showPlays playsOf={period ? (period.of[t.nd_song_id ?? ''] ?? 0) : undefined} showAdded={dated} menuExtra={extra?.(t, i)} index={sort ? at + 1 : i + 1} />
            {line !== undefined && (
              <div className="-mt-1 flex items-center gap-1.5 truncate pb-2 pl-16 pr-4 text-xs text-muted">
                <Icon name="lyrics" size={13} className="shrink-0 text-accent" />
                <span className="truncate">{line ? `«${line}»` : 'найдено в тексте'}</span>
              </div>
            )}
          </>
        )}
      />
    </TrackTable>
  );
}

/** The head of a list: its picture in its own light (the cover, blurred, fading into the page; the accent's for a
 * list with no cover), then its name with ▶ and the shuffle of `tracks` at the right. `art`: a picture of the page's
 * own in place of the cover («Любимое»: the heart). `onCover`: a tap on the picture (it opens large). */
export function Hero({ cover, round, title, mark, sub, right, kicker, art, tracks, context, onCover }: { cover?: string | null; round?: boolean; title: string; /** after the name: the mark of «Наведение порядка» */ mark?: ReactNode; sub?: ReactNode; right?: ReactNode; kicker?: string; art?: ReactNode; tracks?: readonly TrackOut[]; context?: string; onCover?: () => void }) {
  const desktop = useDesktop();
  // the cover as a soft light, blurred once into a tiny picture (`lib/softCover.ts`): no filter while drawn
  const soft = useSoftCover(cover ? imageUrl(cover) : null);
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
    <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-[calc(-5rem_-_var(--hm-page-pull,0px))] bottom-0 -z-10 overflow-hidden">
      {soft ? <div className="absolute inset-0 bg-cover bg-center opacity-50" style={{ backgroundImage: `url(${soft})` }} /> : !cover && <div className="absolute inset-0 bg-accent/25" />}
      {/* it fades into the page below (a gradient over it: a mask is work for Safari as the filter was) */}
      <div className="absolute inset-0 bg-gradient-to-b from-transparent from-35% to-bg" />
    </div>
  );
  if (desktop)
    return (
      <div className="relative isolate flex items-end gap-6 px-6 pb-5 pt-3">
        {light}
        {picture('w-48')}
        <div className="min-w-0 flex-1">
          {kicker && <div className="text-sm text-muted">{kicker}</div>}
          <h2 data-list-title className="line-clamp-2 break-words text-6xl font-black leading-[1.05] tracking-tight">{title}{mark && <span className="ml-3 inline-flex align-middle">{mark}</span>}</h2>
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
          <h2 data-list-title className="line-clamp-2 text-2xl font-bold leading-tight">{title}{mark && <span className="ml-2 inline-flex align-middle">{mark}</span>}</h2>
          {sub && <div className="mt-1 text-sm text-muted">{sub}</div>}
        </div>
        {play}
      </div>
      {right && <div className="pt-3">{right}</div>}
    </div>
  );
}

const plays = <T extends TrackOut>(tracks: readonly T[]): T[] => tracks.filter((t) => !needsTrackFile(t));

// --- pages -------------------------------------------------------------------------------------------------

export function LikedPage() {
  const { me } = useSession();
  const res = useAsync((s) => api.ndLiked(s), [], true, 'liked');
  useSmartPage('liked', null);
  // what only changes cards comes as their own event (`lib/cards.ts`): the list is not read again for it
  // a transfer sends an event per track: the list of thousands is read again once for a burst of them
  const later = useCoalesced(() => res.reload(true));
  useHubEvent('library', (e) => listChanged(e) && later());
  useHubEvent('import', later);
  useCardUpdates(res.data?.songs, (cards) => res.setData((prev) => {
    if (!prev) return prev;
    const songs = patch(prev.songs, cards);
    return songs === prev.songs ? prev : { ...prev, songs: [...songs] };
  }));
  useHubEvent('recording', (event) => res.setData((prev) => {
    if (!prev) return prev;
    const songs = withRecordingAvailability(prev.songs, event);
    return songs === prev.songs ? prev : { ...prev, songs };
  }));
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
  // «Вам нравится» of an artist's page (`/liked?artist=<name>&ref=<ref>`): the liked tracks that artist is on
  const route = useRoute();
  const by = route.query.get('artist');
  const byRef = route.query.get('ref');
  const songs = useMemo(() => {
    const all = res.data?.songs ?? [];
    if (!by) return all;
    const want = by.trim().toLocaleLowerCase('ru');
    return all.filter((t) => (!!byRef && (t.artist_ref === byRef || !!t.artist_refs?.includes(byRef))) || (t.artists?.length ? t.artists : [t.artist]).some((a) => a.trim().toLocaleLowerCase('ru') === want));
  }, [res.data, by, byRef]);
  const length = useMemo(() => songs.reduce((sum, t) => sum + (t.duration_s ?? 0), 0), [songs]);
  const title = by ? `Вам нравится · ${by}` : 'Любимое';
  const back = by ? (byRef ? `/artist/${enc(byRef)}` : '/library') : '/library';
  const search = useListSearch(songs);
  const [sort, setSort] = useListSort('liked');
  const [filter, setFilter] = useState<TagFilter>({});
  const [saving, setSaving] = useState(false);
  const shown = useShown(songs, search.found, filter);
  const songIds = useMemo(() => songs.map(tagKey), [songs]);
  const playing = plays(inOrder(shown ? shown.map((f) => songs[f.index]) : songs, sort));

  return (
    <div>
      {saving && <SaveSelection scope="liked" filter={filter} onClose={() => setSaving(false)} onSaved={(r) => navigate(`/selection/${r.id}`)} />}
      <ListBar title={title} back={back} tracks={playing} context="liked" offline={by ? undefined : { key: 'liked', to: '/liked', tracks: plays(songs) }} search={songs.length ? search : undefined} tags={songs.length ? { songIds, filter, onChange: setFilter, onSave: () => setSaving(true), count: shown ? shown.length : songs.length } : undefined} />
      {res.data && !search.alone && (
        <PlaylistHero
          playlist={{ title, kind: by ? 'Из «Любимого»' : 'Плейлист', owners: [me.username], mark: LIKED_MARK }}
          facts={[tracksWord(songs.length), length ? fmtLength(length) : null]}
          tracks={playing}
          context="liked"
        />
      )}
      {res.loading ? (
        res.data ? <SkeletonRows /> : <HeroSkeleton />
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
export function SelectionPage({ id }: { id: string }) {
  const { me } = useSession();
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
  useHubEvent('recording', (event) => src.setData((prev) => {
    if (!prev) return prev;
    const tracks = withRecordingAvailability(prev.tracks, event);
    return tracks === prev.tracks ? prev : { ...prev, tracks };
  }));
  const laterSrc = useCoalesced(() => src.reload(true));
  useHubEvent('library', laterSrc);
  useHubEvent('reconnect', () => src.reload(true));
  const [filter, setFilter] = useState<TagFilter>({});
  const [sort, setSort] = useListSort(`selection:${id}`);
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

  if (sel.loading || src.loading) return (<><PageHeader hero title="Подборка" back="/tags" /><HeroSkeleton /></>);
  if (!unsaved && !sel.data) return (<><PageHeader hero title="Подборка" back="/tags" /><ErrorState text={sel.error ?? 'Подборка не найдена'} onRetry={() => sel.reload()} /></>);
  if (!src.data) return (<><PageHeader hero title={name} back="/tags" /><ErrorState text={src.error ?? 'Не удалось получить треки'} onRetry={() => src.reload()} /></>);
  const list = shown ? shown.map((f) => tracks[f.index]) : tracks;
  return (
    <div>
      {saving && <SaveSelection scope="library" filter={filter} onClose={() => setSaving(false)} onSaved={(r) => navigate(`/selection/${r.id}`, { replace: true })} />}
      <ListBar title={name} back="/tags" tracks={inOrder(list, sort)} context="selection" search={search} tags={{ songIds, filter, onChange: setFilter, onSave: unsaved ? () => setSaving(true) : undefined, count: list.length }} />
      {!search.alone && <PlaylistHero
        playlist={{ title: name, kind: unsaved ? 'Фильтр по тегам' : 'Подборка по тегам', owners: [me.username], mark: { icon: 'filter' } }}
        facts={[unsaved ? null : src.data.title, filterActive(filter) ? filterWords(filter, tags.byId) : 'теги не выбраны', `${tracksWord(list.length)}${list.length ? ` · ${fmtLength(list.reduce((n, t) => n + (t.duration_s ?? 0), 0))}` : ''}`]}
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

export function PlaylistPage({ id }: { id: string }) {
  const res = useAsync((s) => api.playlist(id, s), [id], true, `playlist:${id}`);
  const p = res.data;
  useSmartPage(`playlist:${id}`, p?.name ?? null);
  // the playlist is the Hub's: every track stands in its place, whether the library has its file yet or not
  const all = useMemo(() => p?.tracks ?? [], [p]);
  const search = useListSearch(all);
  const [sort, setSort] = useListSort(`playlist:${id}`);
  const [viewing, setViewing] = useState(false);
  const [filter, setFilter] = useState<TagFilter>({});
  const [saving, setSaving] = useState(false);
  const shown = useShown(all, search.found, filter);
  const songIds = useMemo(() => all.map(tagKey), [all]);
  const closeCover = useCallback(() => setViewing(false), []);
  // its name or its picture changed (the playlist's menu, the picture's window), or it is gone: the Hub says so;
  // what only changes the cards of its tracks comes as their own event (`lib/cards.ts`)
  const later = useCoalesced(() => res.reload(true));
  useHubEvent('library', (e) => listChanged(e) && later());
  useCardUpdates(res.data?.tracks, (cards) => res.setData((prev) => {
    if (!prev) return prev;
    const tracks = patch(prev.tracks, cards);
    return tracks === prev.tracks ? prev : { ...prev, tracks: [...tracks] };
  }));
  useHubEvent('import', later); // a transfer settled its playlists
  useHubEvent('recording', (event) => res.setData((prev) => {
    if (!prev) return prev;
    const tracks = withRecordingAvailability(prev.tracks, event);
    return tracks === prev.tracks ? prev : { ...prev, tracks };
  }));
  useHubEvent('reconnect', () => res.reload(true));

  const remove = async (t: NdSongOut) => {
    const trackId = t.library_track_id;
    if (trackId == null) return;
    if (await attempt(() => api.removeFromPlaylist(id, [trackId]), `«${titleWithVersion(t.title, t)}» убран из плейлиста`))
      res.setData((prev) => (prev ? { ...prev, song_count: prev.song_count - (t.owned ? 1 : 0), duration_s: Math.max(0, prev.duration_s - (t.duration_s ?? 0)), tracks: prev.tracks.filter((x) => x.library_track_id !== trackId) } : prev));
  };

  if (res.loading && !p) return (<><PageHeader hero title="Плейлист" back="/library" /><HeroSkeleton /></>);
  if (!p) return (<><PageHeader hero title="Плейлист" back="/library" /><ErrorState text={res.error ?? 'Плейлист не найден'} onRetry={() => res.reload()} /></>);
  const cover = p.cover_url ? `${p.cover_url}&size=600` : p.tracks[0]?.cover_url;
  const playing = plays(inOrder(shown ? shown.map((f) => all[f.index]) : all, sort));
  return (
    <div>
      <ListBar title={p.name} back="/library" tracks={playing} context="playlist"
        // the playlist's own menu — its name, its picture, deleting it — here as where the playlists are listed; a
        // list the Hub makes («Микс дня») has nothing to edit, and no menu
        extra={p.editable !== false && <IconButton icon="more" label="Ещё" onClick={(e) => openPlaylistMenu({ id, name: p.name, cover_url: p.cover_url }, menuAtButton(e.currentTarget))} />}
        offline={{ key: `playlist:${id}`, to: `/playlist/${id}`, tracks: plays(all) }} search={all.length ? search : undefined} tags={all.length ? { songIds, filter, onChange: setFilter, onSave: () => setSaving(true), count: shown ? shown.length : all.length } : undefined} />
      {viewing && <CoverView src={cover} title={p.name} playlistId={id} own={ownCover(p.cover_url)} onClose={closeCover} />}
      {!search.alone && <PlaylistHero
        playlist={{ ...playlistLook(p), cover }}
        onCover={() => setViewing(true)}
        facts={[tracksWord(all.length), p.duration_s ? fmtLength(p.duration_s) : null]}
        tracks={playing}
      />}
      {all.length ? (
        <>
          {saving && <SaveSelection scope={`playlist:${id}`} filter={filter} onClose={() => setSaving(false)} onSaved={(r) => navigate(`/selection/${r.id}`)} />}
          <TagFilterChips filter={filter} onChange={setFilter} count={shown ? shown.length : all.length} />
          <SongList tracks={all} found={shown} sort={sort} onSort={setSort} extra={(t) => [{ icon: 'x', label: 'Убрать из плейлиста', shortcut: 'Del', onClick: () => void remove(t) }]} />
        </>
      ) : (
        <Empty icon="playlist" title="Плейлист пуст" text="Добавляйте треки через ⋯ → «Добавить в плейлист»." />
      )}
    </div>
  );
}

export function ArtistsPage() {
  const res = useAsync((s) => api.ndArtists(s), [], true, 'nd-artists');
  const [q, setQ] = useState('');
  const shown = useMemo(() => {
    const needle = q.trim().toLocaleLowerCase('ru');
    return (res.data ?? []).filter((a) => !needle || a.name.toLocaleLowerCase('ru').includes(needle));
  }, [res.data, q]);
  return (
    <div>
      <PageHeader title="Исполнители" back="/library" sub={res.data ? `${res.data.length}` : undefined} toolbar={
        <div className="px-4 pb-2">
          <input className="input" type="search" placeholder="Найти исполнителя" aria-label="Найти исполнителя" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      } />
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
              <LibArtistRow artist={a} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** An artist of the library in its list: their albums, the ♥ of one followed, their menu. */
function LibArtistRow({ artist }: { artist: NdArtistOut }) {
  const menu = useContextMenu();
  return (
    <>
      <ArtistRow
        artist={{ name: artist.name, to: `/lib/artist/${enc(artist.id)}`, photo: artist.cover_url }}
        sub={artistLine(albumsWord(artist.album_count))}
        actions={artist.starred && <Icon name="heart" filled size={16} className="shrink-0 text-accent" />}
        onMenu={menu.openAt}
        className="hover:bg-fg/5"
      />
      {menu.shown && <NdArtistMenu artist={artist} at={menu.at} onClose={menu.close} />}
    </>
  );
}

const albumsWord = (n: number) => {
  const m10 = n % 10, m100 = n % 100;
  const w = m10 === 1 && m100 !== 11 ? 'альбом' : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? 'альбома' : 'альбомов';
  return `${n} ${w}`;
};

/** A library artist (`/lib/artist/<id>`). The Hub says who they are in the catalog — by its own records of their
 * tracks, so a name spelt otherwise there («Mikhail Krug» for «Михаил Круг») is still the same person — and the page
 * is then the catalog's (`pages/Artist.tsx`). An artist the catalog does not know (a friend's band, a file with a
 * name of its own) has the same page drawn of the library: the round picture, «Подписаться», the wave, their tracks
 * the library has and their albums in a row. */
export function NdArtistPage({ id }: { id: string }) {
  const res = useAsync((s) => api.ndArtist(id, s), [id], true, `nd-artist:${id}`);
  const { starting } = useWave();
  const d = res.data;
  const target = { targetType: 'artist' as const, ndId: id, data: { name: d?.artist.name ?? '' } };
  const followed = useLiked(target);
  const [busy, setBusy] = useState(false);
  const row = useRememberScrollX(`nd-artist:${id}`);
  const names = useArtistCard(d && !d.catalog_ref ? d.artist.name : null);
  const [naming, setNaming] = useState<'names' | 'looks' | 'menu' | null>(null);
  const [namingAt, setNamingAt] = useState<MenuAt | null>(null);
  if (res.loading && !d) return (<><PageHeader hero title="Исполнитель" back="/artists" /><HeroSkeleton round buttons /></>);
  if (!d) return (<><PageHeader hero title="Исполнитель" back="/artists" /><ErrorState text={res.error ?? 'Исполнитель не найден'} onRetry={() => res.reload()} /></>);
  if (d.hub_artist_id || d.catalog_ref) {
    return <ArtistPage refId={String(d.hub_artist_id ?? d.catalog_ref)} initialPath={`/lib/artist/${enc(id)}`} />;
  }
  const { artist, albums } = d;
  const tracks = d.tracks ?? [];
  const photo = artist.cover_url ?? albums[0]?.cover_url ?? tracks[0]?.cover_small_url;
  const follow = async () => {
    setBusy(true);
    try {
      await toggleLike(target);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      {photo && <BannerPicture src={photo} blurred />}
      <ListBar title={artist.name} back="/artists" tracks={tracks} context="artist" />
      <ArtistBanner name={artist.name} photo={photo ?? null} sub={[tracks.length ? tracksWord(tracks.length) : null, albums.length ? albumsWord(albums.length) : null, 'только в медиатеке'].filter(Boolean).join(' · ')} />
      {/* what goes up over the picture has the page's own colour under it */}
      <div className="relative z-[1] min-h-[60dvh] bg-bg">
      <div className="flex items-center px-4 pb-3 lg:px-6">
        {/* the buttons go sideways where they do not fit; the ⋯ stays at the right edge, whatever the width */}
        <div className="no-scrollbar -my-1 flex min-w-0 flex-1 items-center gap-2 overflow-x-auto py-1 pr-5 [mask-image:linear-gradient(to_right,#000_calc(100%-1.25rem),transparent)] [&>*]:shrink-0">
        <button
          type="button"
          onClick={() => void follow()}
          disabled={busy}
          aria-pressed={followed}
          className={buttonClass('outline', true, cx('gap-1.5', followed && '!border-accent !text-accent'))}
        >
          {busy ? <Spinner size={14} /> : followed && <Icon name="check" size={15} stroke={3} />}
          {followed ? 'Вы подписаны' : 'Подписаться'}
        </button>
        <button
          type="button"
          disabled={starting === seedKey({ nd_artist_id: artist.id })}
          onClick={() => void startWave({ nd_artist_id: artist.id }, 'radio')}
          className={buttonClass('outline', true, 'gap-1.5 disabled:opacity-60')}
        >
          <Icon name="wave" size={16} className="text-accent" />
          Волна по исполнителю
        </button>
        <ArtistProjects card={names.card} />
        </div>
        {/* ▶ and the shuffle, as on a catalog artist's page: of the row under the head */}
        {tracks.length > 0 && <PlayPair tracks={tracks} context="artist" />}
        {names.card && (
          <button type="button" onClick={(e) => {
            setNamingAt(menuAtButton(e.currentTarget));
            setNaming('menu');
          }} aria-label="Ещё" title="Ещё" className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-muted hover:text-fg">
            <Icon name="more" size={24} />
            {artistHint(names.card) && <span aria-hidden="true" className="absolute right-2 top-2 h-2 w-2 rounded-full bg-ai" />}
          </button>
        )}
      </div>
      {naming === 'menu' && (
        <ContextMenu
          at={namingAt}
          head={{ title: artist.name, sub: 'Исполнитель' }}
          onClose={() => setNaming(null)}
          entries={[
            { icon: 'user', label: 'Соцсети, проекты и псевдонимы', shortcut: 'F2', dot: !!artistHint(names.card), onSelect: () => setNaming('names') },
            { icon: 'image', label: 'Фото и обложка', onSelect: () => setNaming('looks') },
          ]}
        />
      )}
      <ArtistWindow open={naming === 'names'} card={names.card} onChange={(next) => { names.set(next); res.reload(true); }} onClose={() => setNaming(null)} />
      {naming === 'looks' && names.card && <ArtistLooksWindow artistId={names.card.id} onChanged={() => res.reload()} onClose={() => setNaming(null)} />}

      {tracks.length > 0 && (
        <section aria-label="Треки">
          <SectionTitle page note="в медиатеке">Треки</SectionTitle>
          <TrackTable tracks={tracks} numbered>
            <TrackTableHeader numbered />
            {tracks.map((t, i) => (
              <TrackRow key={t.ref} track={t} number={i + 1} queue={tracks} />
            ))}
          </TrackTable>
        </section>
      )}
      {albums.length > 0 && (
        <section aria-label="Альбомы" className="pb-4">
          <SectionTitle page>Альбомы</SectionTitle>
          <div ref={row} className="no-scrollbar flex gap-4 overflow-x-auto px-4 pb-1 lg:px-6">
            {albums.map((a) => (
              <div key={a.id} className="w-36 shrink-0 lg:w-44">
                <NdAlbumCard album={a} showArtist={false} />
              </div>
            ))}
          </div>
        </section>
      )}
      {!tracks.length && !albums.length && <Empty icon="user" title="Здесь пока пусто" text="В медиатеке нет треков этого исполнителя." />}
      </div>
    </div>
  );
}

export function NdAlbumPage({ id }: { id: string }) {
  const res = useAsync((s) => api.ndAlbum(id, s), [id], true, `nd-album:${id}`);
  useCanonicalPath(`/lib/album/${enc(id)}`, res.data?.release_id ? `/album/${res.data.release_id}` : undefined);
  if (res.loading) return (<><PageHeader hero title="Альбом" back="/library" /><HeroSkeleton /></>);
  if (!res.data) return (<><PageHeader hero title="Альбом" back="/library" /><ErrorState text={res.error ?? 'Альбом не найден'} onRetry={() => res.reload()} /></>);
  const a = res.data;
  const tracks = a.tracks ?? [];
  const artwork = { albumId: a.library_album_id, title: a.title, artist: a.artist,
    src: a.cover_url?.replace(/size=\d+/, 'size=1000'), tracks };
  return (
    <div>
      <PageHeader
        hero
        title={a.title}
        sub={a.artist}
        back="/library"
        right={artwork.albumId != null && <LibraryAlbumMenu id={artwork.albumId} title={a.title}
          onCover={() => openAlbumArtwork(artwork, 'edit')}
          onSaved={(saved) => res.setData((prev) => prev && { ...prev, title: saved.title, artist: saved.album_artist })} />}
      />
      <AlbumHero
        album={{ title: a.title, cover: a.cover_url?.replace(/size=\d+/, 'size=600') }}
        mark={a.library_album_id ? { album: a.library_album_id } : null}
        artists={[{ name: a.artist, to: a.artist_id ? `/lib/artist/${enc(a.artist_id)}` : null }]}
        facts={[a.year, tracksWord(a.song_count), a.duration_s ? fmtLength(a.duration_s) : null]}
        onCover={() => openAlbumArtwork(artwork)}
        tracks={tracks}
      />
      <TrackTable tracks={tracks} numbered kind="album">
        <ul>
          {tracks.length > 0 && <li><TrackTableHeader numbered /></li>}
          {tracks.map((t, i) => (
            <li key={`${t.nd_song_id}:${i}`}>
              <TrackRow track={t} index={t.track_no ?? i + 1} queue={tracks} link={false} />
            </li>
          ))}
        </ul>
      </TrackTable>
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
