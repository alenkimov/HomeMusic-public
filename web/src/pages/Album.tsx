import { useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import type { TrackOut } from '../api/types';
import { AlbumEdit } from '../components/AlbumEdit';
import { TrackRow, TrackTableHeader, listenTrack } from '../components/catalog';
import { ContextMenu, useContextMenu } from '../components/ContextMenu';
import { addToQueue } from '../lib/listen';
import { TrackTable } from '../components/TrackTable';
import { ListBar } from '../components/ListBar';
import { BrandIcon } from '../components/BrandIcon';
import { Cover, ErrorState, ExplicitBadge, HeroSkeleton, IconButton, OwnedBadge, PageHeader } from '../components/ui';
import { startDownload } from '../lib/actions';
import { openAlbumArtwork } from '../lib/coverPicker';
import { useHubEvent } from '../lib/events';
import { needsTrackFile, withRecordingAvailability } from '../lib/findFile';
import { fmtLength, recordTypeLabel, tracksWord, yearOf } from '../lib/format';
import { SourceStatuses } from '../components/SourceStatuses';
import { useSourceRequest } from '../lib/hooks';
import { canPlay, canPlayAll, playList } from '../lib/player';
import { Link, albumPath, artistPath, enc, navigate, useCanonicalPath } from '../lib/router';
import { copyText } from '../lib/share';
import { attempt, confirmDialog, toast } from '../lib/ui';
import { Hero } from './MediaLibrary';

/** A release of the catalog (`/album/<ref>`), in the shape of every list: the head — the cover, the kind of release
 * over its name, the artist, the year, the tracks and their length, ▶ and the shuffle — then a row of buttons
 * (at the top of the page: ⋯ — the link, the release in its catalog, the artist — and «Скачать
 * альбом»), the tracks by their numbers and,
 * under them, the day it came out. */

export function AlbumPage({ refId }: { refId: string }) {
  const menu = useContextMenu();
  const [rename, setRename] = useState(false);
  const [rev, setRev] = useState(0);
  const { res, sources } = useSourceRequest(`album:${refId}`, (s, update) => api.album(refId, s, rev > 0, update), rev);
  const status = sources.length > 0 && <SourceStatuses sources={sources} label="Статусы источников альбома" />;
  useCanonicalPath(`/album/${enc(refId)}`, res.data?.release_id ? albumPath(res.data) : undefined);
  const [dlBusy, setDlBusy] = useState(false);

  const reloadTimer = useRef<number | undefined>(undefined);

  // New imports may complete tracks of this album: refetch (debounced, bypassing the cache).
  useHubEvent('library', (e) => {
    if (e.kind !== 'imported') return;
    window.clearTimeout(reloadTimer.current);
    reloadTimer.current = window.setTimeout(() => setRev((r) => r + 1), 1500);
  });
  useHubEvent('recording', (event) => res.setData((prev) => {
    if (!prev?.tracks) return prev;
    const tracks = withRecordingAvailability(prev.tracks, event);
    return tracks === prev.tracks ? prev : { ...prev, tracks };
  }));

  const tracks = useMemo(() => res.data?.tracks ?? [], [res.data]);
  const playableTracks = useMemo(() => tracks.filter((track) => !needsTrackFile(track)), [tracks]);
  const discs = useMemo(() => {
    const m = new Map<number, TrackOut[]>();
    for (const t of tracks) {
      const d = t.disc_no ?? 1;
      if (!m.has(d)) m.set(d, []);
      m.get(d)!.push(t);
    }
    return [...m.entries()].sort((a, b) => a[0] - b[0]);
  }, [tracks]);

  if (res.loading && !res.data)
    return (
      <>
        <PageHeader hero title="Альбом" back="/search" right={status} />
        <HeroSkeleton buttons />
      </>
    );
  if (res.error || !res.data)
    return (
      <>
        <PageHeader hero title="Альбом" back="/search" right={status} />
        <ErrorState text={res.error ?? 'Альбом не найден'} onRetry={() => res.reload()} />
      </>
    );

  const album = res.data;
  const year = yearOf(album);
  const total = tracks.reduce((s, t) => s + (t.duration_s ?? 0), 0);
  const missing = tracks.filter((t) => !t.owned);
  const owned = tracks.filter((t) => t.owned);
  // Older apps: owned tracks Navidrome has scanned play in the app's player, in album order.
  const playable = canPlay() && !canPlayAll() ? owned.filter((t) => t.nd_song_id) : [];
  const play = (from?: TrackOut) => playList(playable, (t) => t.nd_song_id, from);
  const markDownloading = (refs: Set<string>) =>
    res.setData((prev) => (prev ? { ...prev, tracks: (prev.tracks ?? []).map((t) => (refs.has(t.ref) ? { ...t, downloading: true } : t)) } : prev));

  const downloadAlbum = async () => {
    setDlBusy(true);
    const r = await startDownload({ album_ref: String(album.release_id ?? album.ref) });
    setDlBusy(false);
    if (r) markDownloading(new Set(missing.map((t) => t.ref)));
  };

  /** The album into the chosen playlists: the tracks the library has at once, the rest as they are downloaded. */

  const share = async () => {
    const url = `${window.location.origin}/#${albumPath(album)}`;
    toast((await copyText(url)) ? 'Ссылка на релиз скопирована' : url, 'success');
  };
  /** The catalog keeps namesakes under one artist: this release is not of the one whose page led here. */
  const namesake = async () => {
    const artistRef = album.artist_ref;
    if (!artistRef) return;
    const ok = await confirmDialog({
      title: `Это другой ${album.artist}?`,
      body: `Каталог держит тёзок под одним исполнителем. «${album.title}» пропадёт со страницы этого исполнителя, из его дискографии и из «Релизов» — у всех на этом сервере. Сами треки останутся доступны. Вернуть можно в «Меню → Скрытое».`,
      okLabel: 'Убрать у исполнителя',
      danger: true,
    });
    if (!ok) return;
    if (await attempt(() => api.namesakesExclude(artistRef, [album.ref]))) toast('Релиз убран у исполнителя', 'success', { label: 'Скрытое', to: '/hidden' });
  };
  const allHere = tracks.length > 0 && missing.length === 0;
  const facts = [year, tracks.length ? tracksWord(tracks.length) : null, total ? fmtLength(total) : null].filter(Boolean);
  const artwork = { albumId: album.library_album_id, catalogRef: album.ref, title: album.title, artist: album.artist, src: album.cover_url, tracks };

  return (
    <div>
      <ListBar
        title={album.title}
        back="/search"
        tracks={playableTracks}
        context="album"
        // what is done with the release as a whole stands at the top, as on a playlist's page
        extra={
          <>
            {(res.loading || sources.some((s) => s.state === 'searching')) && status}
            <IconButton icon="more" label="Ещё" onClick={menu.fromButton} />
            <IconButton
              icon={allHere ? 'check' : 'download'}
              label={allHere ? 'Всё скачано' : owned.length ? `Скачать недостающие ${missing.length} из ${tracks.length}` : 'Скачать альбом'}
              busy={dlBusy}
              disabled={allHere || !tracks.length}
              onClick={() => void downloadAlbum()}
              className={allHere ? '!text-accent' : undefined}
            />
          </>
        }
      />
      <Hero
        kicker={recordTypeLabel(album.record_type) || 'Релиз'}
        cover={album.cover_url}
        onCover={() => openAlbumArtwork(artwork)}
        title={album.title}
        sub={
          <span className="flex flex-wrap items-center gap-x-1.5">
            {album.explicit && <ExplicitBadge />}
            {/* everyone the release is credited to, each with their page */}
            {(album.artists?.length ? album.artists.map((name, i) => ({ name, ref: album.artist_refs?.[i], id: album.hub_artist_ids?.[i] })) : [{ name: album.artist, ref: album.artist_ref, id: album.hub_artist_id }]).map((a, i) => (
              <span key={`${a.name}:${i}`} className="font-semibold text-fg">
                {i > 0 && <span className="pr-1.5 font-normal text-muted">,</span>}
                {a.ref || a.id ? (
                  <Link to={artistPath({ ref: a.ref ?? '', hub_artist_id: a.id })} className="hover:underline">
                    {a.name}
                  </Link>
                ) : (
                  a.name
                )}
              </span>
            ))}
            {facts.length > 0 && <span>· {facts.join(' · ')}</span>}
          </span>
        }
        tracks={playableTracks}
        context="album"
      />

      {!allHere && owned.length > 0 && (
        <div className="px-4 pb-3 lg:px-6">
          <OwnedBadge status={album.owned_status} count={album.owned_count} total={tracks.length || album.nb_tracks} />
        </div>
      )}

      <section aria-label="Треки" className="pt-2">
        <TrackTable tracks={tracks} numbered kind="album">
          {tracks.length > 0 && <TrackTableHeader numbered />}
          {discs.map(([disc, list]) => (
            <div key={disc}>
              {discs.length > 1 && <div className="px-4 pb-1 pt-3 text-sm font-semibold text-muted">Диск {disc}</div>}
              {list.map((t, i) => (
                <TrackRow key={t.ref} track={t} number={t.track_no ?? i + 1} link={false} onPlay={() => play(t)} queue={playableTracks} />
              ))}
            </div>
          ))}
        </TrackTable>
        {!tracks.length && <p className="px-4 py-6 text-center text-muted">Треклист недоступен</p>}
      </section>

      {album.release_date && <p className="px-4 pb-4 pt-4 text-xs text-dim lg:px-6">{new Date(album.release_date).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })}</p>}

      {menu.shown && (
        <ContextMenu
          at={menu.at}
          head={{ title: album.title, sub: album.artist, cover: <Cover src={album.cover_url} size={44} icon="disc" /> }}
          onClose={menu.close}
          entries={[
            tracks.length > 0 && (allHere
              ? { icon: 'download', label: 'Скачано', active: true, done: true, disabled: true, onSelect: () => {} }
              : { icon: 'download', label: owned.length ? `Скачать недостающие (${missing.length})` : 'Скачать альбом', shortcut: 'D', onSelect: () => void downloadAlbum() }),
            playableTracks.length > 0 && !canPlayAll() && { icon: 'queue', label: 'Добавить в очередь', shortcut: 'Q', onSelect: () => void addToQueue(playableTracks.map(listenTrack)) },
            'sep',
            (album.artist_ref || album.hub_artist_id) && { icon: 'user', label: 'Перейти к исполнителю', shortcut: 'Alt+A', onSelect: () => navigate(artistPath({ ref: album.artist_ref ?? '', hub_artist_id: album.hub_artist_id })) },
            'sep',
            { icon: 'edit', label: 'Переименовать', shortcut: 'F2', onSelect: () => setRename(true) },
            { icon: 'image', label: 'Сменить обложку', onSelect: () => openAlbumArtwork(artwork, 'edit') },
            'sep',
            { icon: 'link', label: 'Скопировать ссылку', shortcut: 'C', onSelect: () => void share() },
            // every service that has the release: the catalog its record is of, and the other one where it is too
            ...(album.links ?? []).map((x) => ({
              glyph: <BrandIcon service={x.service} size={18} />,
              label: x.service === 'yandex' ? 'Открыть в Яндекс Музыке' : x.service === 'spotify' ? 'Открыть в Spotify' : 'Открыть в Deezer',
              onSelect: () => void window.open(x.url, '_blank', 'noopener,noreferrer'),
            })),
            'sep',
            album.artist_ref?.startsWith('dz:') && { icon: 'ban', label: `Это другой ${album.artist}`, danger: true, onSelect: () => void namesake() },
          ]}
        />
      )}
      {rename && <AlbumEdit target={{ ref: String(album.release_id ?? album.ref) }} onClose={() => setRename(false)} onSaved={(saved) => {
        res.setData((prev) => prev && { ...prev, title: saved.title, artist: saved.album_artist,
          artists: saved.album_artist === prev.artist ? prev.artists : [saved.album_artist], library_album_id: saved.album_id });
        res.reload(true);
      }} />}

    </div>
  );
}
