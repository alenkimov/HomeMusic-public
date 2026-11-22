import { useVersionLabelsSync } from './lib/versionLabels';
import { setListeningAccount } from './lib/plays';
import { forgetLast } from './lib/hooks';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiError, UNAUTHORIZED_EVENT, api, errorMessage, isAbort, setApiAccount } from './api/client';
import { clearAccountMessages, toastError } from './lib/ui';
import type { MeOut } from './api/types';
import { DesktopPlayer } from './components/DesktopPlayer';
import { PlaybackTransferOffer } from './components/PlaybackDevices';
import { MiniPlayer, PlayerSpacer } from './components/MiniPlayer';
import { Sidebar } from './components/Sidebar';
import { closeAccountOverlays, Overlays } from './components/Overlays';
import { SelectionBar } from './components/SelectionBar';
import { TabBar, type Tab } from './components/TabBar';
import { ErrorState, Spinner, cx } from './components/ui';
import { useDesktop } from './lib/desktop';
import { STANDALONE } from './lib/embedded';
import { hubEvents } from './lib/events';
import { restoreListening, stopListening, watchManualQueue } from './lib/listen';
import { watchPlaybackDevices } from './lib/playbackDevices';
import { restoreWave } from './lib/wave';
import { resetInPlaylists } from './lib/inPlaylists';
import { resetReleases } from './lib/releases';
import { resetStars } from './lib/stars';
import { refreshLoadedTags, resetTags } from './lib/tags';
import { resetPlayCounts } from './lib/stats';
import { clearSelection } from './lib/selection';
import { cancelDragAction } from './lib/dragAction';
import { startNotificationWatcher, stopNotificationWatcher } from './lib/notify';
import { SessionContext } from './lib/session';
import { RouteBackground, backgroundRoute, matchPath, navigate, parseHash, useRoute, type Route } from './lib/router';
import { usePagePull } from './lib/topPull';
import { AdminPage } from './pages/Admin';
import { AdminUserPage } from './pages/AdminUser';
import { TasteMatrixPage } from './pages/insights/Matrix';
import { InsightsOverviewPage } from './pages/insights/Overview';
import { MixDetailPage, WaveDetailPage } from './pages/insights/Recs';
import { TrackWeightPage } from './pages/insights/Track';
import { AlbumPage } from './pages/Album';
import { ArtistNamedPage, ArtistPage } from './pages/Artist';
import { AssistantChatsPage, AssistantPage } from './pages/Assistant';
import { AssistantTaskPage } from './pages/AssistantTask';
import { setAssistantAccount } from './pages/AssistantChat';
import { DownloadDetailPage } from './pages/DownloadDetail';
import { DownloadsPage } from './pages/Downloads';
import { ArtistMapPage } from './pages/ArtistMapPage';
import { HomePage } from './pages/Home';
import { ImportPage, ImportsPage } from './pages/Import';
import { FilesPage } from './pages/Files';
import { QualityPage } from './pages/Quality';
import { LibraryPage, LibrarySearchPage } from './pages/Library';
import { LibraryAlbumPage } from './pages/LibraryAlbum';
import { LoginPage } from './pages/Login';
import { LyricsSettingsPage } from './pages/LyricsSettings';
import { ArtistsPage, LikedPage, NdAlbumPage, NdAlbumsPage, NdArtistPage, PlaylistPage, SelectionPage } from './pages/MediaLibrary';
import { TagsPage } from './pages/Tags';
import { FileJournalPage } from './pages/FileJournal';
import { DiscographyPage } from './pages/Discography';
import { HiddenPage } from './pages/Hidden';
import { HistoryPage } from './pages/History';
import { OfflinePage } from './pages/Offline';
import { refreshDislikes, resetDislikes } from './lib/dislikes';
import { forgetAccount, initOffline } from './lib/offline';
import { setOfflineWaveAccount } from './lib/offlineWave';
import { ForYouPage } from './pages/ForYou';
import { TastePage } from './pages/Mix';
import { MorePage } from './pages/More';
import { OpenPage } from './pages/Open';
import { MobilePlayerSheet, PlayerPage } from './pages/Player';
import { PlaylistsPage } from './pages/Playlists';
import { SearchPage, searchTab } from './pages/Search';
import { SpotifyPlaylistPage } from './pages/SpotifyPlaylist';
import { SharePage } from './pages/Share';
import { IntegrationsPage } from './pages/Integrations';
import { SubscriptionsPage } from './pages/Subscriptions';
import { StatsPage } from './pages/Stats';
import { UploadPage } from './pages/Upload';

/** How wide a page is on a computer. Nothing: the whole panel — a list of tracks, an artist, an album, a grid,
 * the home: they are made to fill what they are given. 'text': a column in the middle (`--app-w`) — settings, forms,
 * a page of rows to read, which stretched over a wide window is a line of text a metre long. 'wide': the admin's
 * analytics, tables up to their own width. A phone's page is the screen's width whatever this says.
 *
 * It is said here, by the route, once: a page that fills the panel was the exception before, kept in a list by
 * name, and every new one (an artist, an album, the home) came out as a narrow column until somebody noticed. */
type Width = 'text' | 'wide';
type RouteDef = [pattern: string, tab: Tab | null, render: (p: Record<string, string>, r: Route) => ReactNode, width?: Width];

/** Screens of the standalone service (a plain browser, `STANDALONE`): inside the app they are its native ones, so
 * these routes don't exist there (an unknown route goes to the catalog). */
const STANDALONE_ROUTES: RouteDef[] = [
  ['/home', 'home', () => <HomePage />],
  ['/player', null, () => <PlayerPage />],
  ['/liked', 'library', () => <LikedPage />],
  ['/artists', 'library', () => <ArtistsPage />],
  ['/playlist/:id', 'library', (p) => <PlaylistPage key={p.id} id={p.id} />],
  ['/selection/:id', 'library', (p) => <SelectionPage key={p.id} id={p.id} />],
  ['/tags', 'library', () => <TagsPage />, 'text'],
  ['/stats', 'more', () => <StatsPage />, 'text'],
  ['/history', 'more', () => <HistoryPage />],
  ['/hidden', 'more', () => <HiddenPage />, 'text'],
  ['/trash', 'more', () => <FilesPage key="trash" trash />],
  ['/offline', 'more', () => <OfflinePage />, 'text'],
  ['/subscriptions', 'library', () => <SubscriptionsPage />],
  ['/discoveries', 'library', () => <ForYouPage key="weekly" kind="weekly" />],
  ['/integrations', 'more', () => <IntegrationsPage />, 'text'],
  ['/auto/:kind', 'library', (p) => <ForYouPage key={p.kind} kind={p.kind} />],
  ['/lib/albums', 'library', () => <NdAlbumsPage />],
  ['/lib/album/:id', 'library', (p) => <NdAlbumPage key={p.id} id={p.id} />],
  ['/lib/artist/:id', 'library', (p) => <NdArtistPage key={p.id} id={p.id} />],
];

/** Where an unknown or empty route goes. */
const START = STANDALONE ? '/home' : '/search';

const ROUTES: RouteDef[] = [
  ...(STANDALONE ? STANDALONE_ROUTES : []),
  ['/search', 'search', (_, r) => <SearchPage initialQuery={r.query.get('q') ?? ''} initialTab={searchTab(r.query.get('tab'))} />],
  ['/spotify/playlist/:id', 'search', (p, r) => <SpotifyPlaylistPage key={p.id} id={p.id} query={r.query.get('q')} />],
  ['/artist/:ref/discography', 'search', (p) => <DiscographyPage key={p.ref} refId={p.ref} />],
  ['/artist/:ref', 'search', (p) => <ArtistPage key={p.ref} refId={p.ref} />],
  ['/artist-named/:name', 'search', (p) => <ArtistNamedPage key={p.name} name={p.name} />],
  ['/album/:ref', 'search', (p) => <AlbumPage key={p.ref} refId={p.ref} />],
  ['/mix/:id', 'library', (p) => <ForYouPage key={`mix:${p.id}`} kind="mix" mix={Number(p.id)} />],
  // «Микс дня» by its name: no mix has the number 0, so the page opens today's
  ['/mix', 'library', () => <ForYouPage key="mix" kind="mix" />],
  ['/taste', 'search', () => <TastePage />, 'text'],
  ['/map/:ref', 'search', (p) => <ArtistMapPage refId={p.ref} />],
  ['/map', 'search', () => <ArtistMapPage />],
  ['/downloads', 'downloads', () => <DownloadsPage />],
  ['/downloads/:id', 'downloads', (p) => <DownloadDetailPage key={p.id} id={Number(p.id)} />, 'wide'],
  ['/library/search', 'library', () => <LibrarySearchPage />],
  ['/library', 'library', () => <LibraryPage />, 'text'],
  ['/files', 'more', () => <FilesPage key="files" />],
  ['/quality', 'more', () => <QualityPage />],
  ['/library/album/:id', 'more', (p) => <LibraryAlbumPage key={p.id} id={Number(p.id)} />, 'text'],
  ['/assistant', 'assistant', (_, r) => <AssistantPage chatId={Number(r.query.get('chat')) || null} />, 'text'],
  ['/assistant/chats', 'assistant', () => <AssistantChatsPage />, 'text'],
  ['/assistant/:id', 'assistant', (p) => <AssistantTaskPage key={p.id} id={Number(p.id)} />, 'text'],
  ['/upload', 'more', () => <UploadPage />],
  ['/playlists', STANDALONE ? 'library' : 'more', () => <PlaylistsPage />],
  ['/file/:id', 'library', (p) => <FileJournalPage key={p.id} id={Number(p.id)} />, 'text'],
  ['/import', 'more', (_, r) => <ImportsPage key={r.query.get('from') ?? ''} from={(['yandex', 'spotify', 'vk'] as const).find((x) => x === r.query.get('from')) ?? null} />, 'text'],
  ['/import/:id', 'more', (p) => <ImportPage key={p.id} id={Number(p.id)} />, 'text'],
  ['/more', 'more', () => <MorePage />, 'text'],
  ['/admin', 'more', () => <AdminPage />, 'text'],
  ['/lyrics-sources', 'more', () => <LyricsSettingsPage />, 'text'],
  ['/admin/user/:id', 'more', (p) => <AdminUserPage key={p.id} id={Number(p.id)} />, 'wide'],
  ['/admin/insights', 'more', () => <InsightsOverviewPage />, 'wide'],
  ['/admin/taste', 'more', () => <TasteMatrixPage />, 'wide'],
  ['/admin/user/:id/track', 'more', (p) => <TrackWeightPage key={p.id} userId={Number(p.id)} />, 'wide'],
  ['/admin/mix/:id', 'more', (p) => <MixDetailPage key={p.id} id={Number(p.id)} />, 'wide'],
  ['/admin/wave/:sid', 'more', (p) => <WaveDetailPage key={p.sid} sid={p.sid} />, 'wide'],
  ['/share', null, () => <SharePage />, 'text'],
  ['/open', null, (_, r) => <OpenPage key={r.full} query={r.query} />, 'text'],
];

const lastByTab: Partial<Record<Tab, string>> = {};

function resolve(route: Route): { tab: Tab | null; node: ReactNode; width?: Width } | null {
  for (const [pattern, tab, render, width] of ROUTES) {
    const params = matchPath(pattern, route.path);
    if (params) return { tab, node: render(params, route), width };
  }
  return null;
}

export function App() {
  const [me, setMe] = useState<MeOut | null | undefined>(undefined);
  const [bootError, setBootError] = useState<string>();
  const route = useRoute();
  // a computer: the sections and the playlists in a panel on the left, the player in a bar along the bottom
  const desktop = useDesktop();
  const accountRef = useRef<number | null>(null);
  const accountChannel = useRef<BroadcastChannel | null>(null);

  // who is signed in is told to what outlives a logout in the page's memory, before that user's screens are shown
  const updateSession = useCallback((next: MeOut | null) => {
    const account = next?.id ?? null;
    if (accountRef.current !== account) {
      if (accountRef.current != null) {
        hubEvents.stop();
        stopListening();
        forgetLast();
        forgetAccount();
        resetStars();
        resetDislikes();
        resetInPlaylists();
        resetReleases();
        resetTags();
        resetPlayCounts();
        clearSelection();
        cancelDragAction();
        closeAccountOverlays();
        clearAccountMessages();
        for (const tab of Object.keys(lastByTab) as Tab[]) delete lastByTab[tab];
      }
      accountRef.current = account;
      setApiAccount(account);
      setOfflineWaveAccount(STANDALONE ? account : null);
      setAssistantAccount(account);
    }
    setMe(next);
  }, []);

  const refresh = useCallback(async () => {
    const next = await api.me().catch(() => undefined);
    if (next) updateSession(next);
  }, [updateSession]);

  const boot = useCallback(() => {
    setBootError(undefined);
    api
      .me()
      .then(updateSession)
      .catch((e) => {
        if (isAbort(e)) return;
        if (e instanceof ApiError && e.status === 401) updateSession(null);
        else setBootError(errorMessage(e));
      });
  }, []);

  useEffect(boot, [boot]);

  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return;
    const channel = new BroadcastChannel('homemusic-account');
    accountChannel.current = channel;
    channel.onmessage = ({ data }) => {
      if (data === accountRef.current) return;
      updateSession(null);
      boot();
    };
    return () => {
      accountChannel.current = null;
      channel.close();
    };
  }, [boot, updateSession]);

  useEffect(() => {
    const account = me?.id ?? null;
    setListeningAccount(account);
    restoreListening(account);
    restoreWave(account);
  }, [me?.id]);

  useEffect(() => {
    const onUnauthorized = () => {
      updateSession(null);
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  useEffect(() => {
    if (!me) return;
    const offGenres = hubEvents.on('genres', refreshLoadedTags);
    const offReconnect = hubEvents.on('reconnect', refreshLoadedTags);
    const offLibrary = hubEvents.on('library', ({ kind }) => {
      if (kind === 'imported') refreshLoadedTags();
    });
    hubEvents.start();
    startNotificationWatcher();
    return () => {
      offGenres();
      offReconnect();
      offLibrary();
      stopNotificationWatcher();
      hubEvents.stop();
    };
  }, [me]);

  const fullPlayer = route.path === '/player';
  const mobilePlayer = STANDALONE && !desktop && fullPlayer;
  const page = useRef<HTMLElement>(null);
  usePagePull(page, me != null && STANDALONE && !desktop && !fullPlayer, route.full);
  const background = useMemo(() => mobilePlayer ? backgroundRoute() ?? parseHash(START) : null, [mobilePlayer, route]);
  // Keep the previous screen mounted so it shows through as the mobile player slides away.
  const resolved = background ? resolve(background) ?? resolve(parseHash(START)) : resolve(route);
  // Each tab remembers where you were (Spotify-like); tapping the active tab goes to its root.
  if (!background && resolved?.tab) lastByTab[resolved.tab] = route.full;
  useEffect(() => {
    if (me && !resolved) navigate(START, { replace: true });
  }, [me, resolved]);

  // what is kept on the device is read once, and what was not fetched to the end goes on
  useEffect(() => {
    void initOffline();
  }, []);
  // whom the user does not listen to: the player needs it from the start, to go past their tracks
  const uid = me?.id;
  useEffect(() => uid != null ? watchManualQueue() : undefined, [uid]);
  useEffect(() => uid != null && STANDALONE ? watchPlaybackDevices() : undefined, [uid]);
  useVersionLabelsSync(uid);
  useEffect(() => {
    if (uid != null) void refreshDislikes(true);
  }, [uid]);
  const logout = useCallback(async () => {
    const account = accountRef.current;
    try {
      await api.logout();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) toastError(e);
      return;
    }
    if (accountRef.current != null && accountRef.current !== account) return;
    updateSession(null);
    accountChannel.current?.postMessage(null);
    navigate(START, { replace: true });
  }, [updateSession]);

  if (me === undefined) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-4">
        {bootError ? (
          <ErrorState text={`Не удалось связаться с сервером. ${bootError}`} onRetry={boot} />
        ) : (
          <>
            <img src="/icons/icon.svg" alt="" width={72} height={72} className="rounded-2xl" />
            <Spinner className="text-accent" />
          </>
        )}
        <Overlays />
      </div>
    );
  }

  if (me === null) {
    return (
      <>
        <LoginPage onLogin={(next) => {
          updateSession(next);
          accountChannel.current?.postMessage(next.id);
        }} />
        <Overlays />
      </>
    );
  }

  const playerPage = fullPlayer && !mobilePlayer;
  const panel = desktop && !playerPage;
  // a computer's page fills the panel unless its route says otherwise (`Width`); a phone's is a phone's column
  const width = !panel ? 'max-w-(--app-w)' : resolved?.width === 'text' ? 'max-w-(--app-w)' : resolved?.width === 'wide' ? 'max-w-6xl' : '';
  return (
    <SessionContext.Provider key={me.id} value={{ me, logout, refresh }}>
      <RouteBackground.Provider value={background}>
        <div inert={mobilePlayer}>
          {/* The frame over the desktop page keeps its corners round while the window scrolls beneath it. */}
          <div className={cx(panel && 'pl-[calc(var(--side-w)+8px)] pr-2 pt-(--page-top)')}>
            <div className={cx('min-h-dvh', panel && 'hm-page')}>
              <main ref={page} className={cx('mx-auto', width, !playerPage && 'pb-[calc(var(--tabbar-h)+16px+env(safe-area-inset-bottom))]')}>
                {resolved?.node}
                {!playerPage && <PlayerSpacer />}
              </main>
            </div>
          </div>
          {panel && <div aria-hidden="true" className="pointer-events-none fixed bottom-(--tabbar-h) left-[calc(var(--side-w)+8px)] right-2 top-(--page-top) z-[25] rounded-2xl shadow-[0_0_0_100vmax_var(--color-bg)]" />}
          {!playerPage && (desktop ? (
            <>
              <Sidebar active={resolved?.tab ?? null} last={lastByTab} />
              <DesktopPlayer />
            </>
          ) : (
            <>
              <MiniPlayer />
              <TabBar active={resolved?.tab ?? null} last={lastByTab} />
            </>
          ))}
          {!fullPlayer && <SelectionBar />}
        </div>
      </RouteBackground.Provider>
      {mobilePlayer && <MobilePlayerSheet />}
      <PlaybackTransferOffer />
      <Overlays />
    </SessionContext.Provider>
  );
}
