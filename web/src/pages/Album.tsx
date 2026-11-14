import { Icon } from '../components/Icon';
import { retryImage } from '../lib/img';
import { useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import type { TrackOut } from '../api/types';
import { TrackRow, TrackTableHeader, listenTrack } from '../components/catalog';
import { PlaylistsPick } from '../components/PlaylistsButton';
import { Button, Cover, ErrorState, ExplicitBadge, IconButton, Loading, OwnedBadge } from '../components/ui';
import { startDownload } from '../lib/actions';
import { useHubEvent } from '../lib/events';
import { listen } from '../lib/listen';
import { fmtLength, recordTypeLabel, tracksWord, yearOf } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { canPlay, canPlayAll, playList, playTracks } from '../lib/player';
import { Link, enc, goBack } from '../lib/router';
import { attempt, toast } from '../lib/ui';

export function AlbumPage({ refId }: { refId: string }) {
  const [rev, setRev] = useState(0);
  const res = useAsync((s) => api.album(refId, s, rev > 0), [refId, rev]);
  const [dlBusy, setDlBusy] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  const [starBusy, setStarBusy] = useState(false);
  const [starred, setStarred] = useState(false);
  const reloadTimer = useRef<number | undefined>(undefined);

  // New imports may complete tracks of this album: refetch (debounced, bypassing the cache).
  useHubEvent('library', (e) => {
    if (e.kind !== 'imported') return;
    window.clearTimeout(reloadTimer.current);
    reloadTimer.current = window.setTimeout(() => setRev((r) => r + 1), 1500);
  });

  const tracks = useMemo(() => res.data?.tracks ?? [], [res.data]);
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
        <Bar />
        <Loading />
      </>
    );
  if (res.error || !res.data)
    return (
      <>
        <Bar />
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
  // The whole album (own files and the rest from sources): in the app's player (1.2.0+), in a browser in the page.
  const listenAll =
    tracks.length === 0
      ? null
      : canPlayAll()
        ? () => playTracks(tracks)
        : !canPlay()
          ? () => listen(listenTrack(tracks[0]), tracks.map(listenTrack))
          : null;

  const markDownloading = (refs: Set<string>) =>
    res.setData((prev) => (prev ? { ...prev, tracks: (prev.tracks ?? []).map((t) => (refs.has(t.ref) ? { ...t, downloading: true } : t)) } : prev));

  const downloadAlbum = async () => {
    setDlBusy(true);
    const r = await startDownload({ album_ref: album.ref });
    setDlBusy(false);
    if (r) markDownloading(new Set(missing.map((t) => t.ref)));
  };

  /** The album into the chosen playlists: the tracks the library has at once, the rest as they are downloaded. */
  const addToPlaylists = async (lists: { id: string; name: string }[]) => {
    setPickerOpen(false);
    if (!lists.length) return;
    const ids = lists.map((p) => p.id);
    const names = lists.map((p) => `«${p.name}»`).join(', ');
    if (missing.length) {
      const r = await attempt(() => api.download({ album_ref: album.ref, playlist_ids: ids }));
      if (!r) return;
      markDownloading(new Set(missing.map((t) => t.ref)));
    }
    if (owned.length) {
      const track_ids = owned.map((t) => t.library_track_id).filter((x): x is number => x != null);
      for (const id of ids) {
        const ok = await attempt(() => api.addToPlaylist(id, { track_ids }));
        if (!ok) return;
      }
    }
    toast(
      missing.length ? `${names}: ${owned.length} добавлено, ${missing.length} появятся после скачивания` : `Добавлено в ${names}: ${tracksWord(owned.length)}`,
      'success',
      missing.length ? { label: 'Загрузки', to: '/downloads' } : undefined,
    );
  };

  const star = async () => {
    setStarBusy(true);
    const ok = await attempt(() => api.star({ catalog_album_ref: album.ref }), 'Добавлено в коллекцию');
    setStarBusy(false);
    if (ok) setStarred(true);
  };

  return (
    <div>
      <div className="relative">
        <div className="absolute inset-0 overflow-hidden">
          {album.cover_url && <img src={album.cover_small_url || album.cover_url} alt="" onError={retryImage} className="h-full w-full scale-125 object-cover opacity-40 blur-3xl" referrerPolicy="no-referrer" />}
          <div className="absolute inset-0 bg-gradient-to-b from-transparent via-bg/50 to-bg" />
        </div>
        <Bar overlay />
        <div className="relative px-4 pb-4">
          <div className="flex justify-center pb-4">
            <Cover src={album.cover_url} alt={album.title} className="aspect-square w-[min(56vw,240px)] shadow-2xl" icon="disc" />
          </div>
          <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
          <h1 className="line-clamp-2 text-2xl font-bold leading-tight">{album.title}</h1>
          {album.artist_ref ? (
            <Link to={`/artist/${enc(album.artist_ref)}`} className="mt-1 block truncate font-semibold hover:underline">
              {album.artist}
            </Link>
          ) : (
            <div className="mt-1 truncate font-semibold">{album.artist}</div>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-x-1.5 text-sm text-muted">
            {album.explicit && <ExplicitBadge />}
            <span>{[recordTypeLabel(album.record_type), year, tracks.length ? tracksWord(tracks.length) : null, total ? fmtLength(total) : null].filter(Boolean).join(' · ')}</span>
          </div>
          <div className="mt-2">
            <OwnedBadge status={album.owned_status} count={album.owned_count} total={tracks.length || album.nb_tracks} />
          </div>
          </div>
          {(playable.length > 0 || listenAll) && (
            <button
              type="button"
              aria-label="Слушать"
              title={playable.length > 0 && playable.length < tracks.length ? `Слушать ${playable.length} из ${tracks.length}` : 'Слушать'}
              onClick={() => (playable.length > 0 ? play() : listenAll?.())}
              className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition hover:scale-105 active:scale-95"
            >
              <Icon name="play" size={26} filled className="translate-x-0.5" />
            </button>
          )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 px-4 pb-2">
        {!(playable.length > 0 && missing.length === 0) && (
          <Button icon="download" className="col-span-2" busy={dlBusy} disabled={missing.length === 0} onClick={downloadAlbum}>
            {missing.length === 0 ? 'Всё скачано' : owned.length ? `Скачать недостающие ${missing.length} из ${tracks.length}` : 'Скачать альбом'}
          </Button>
        )}
        <Button icon="playlist" onClick={() => setPickerOpen(true)}>
          В плейлист
        </Button>
        <Button icon={starred ? 'check' : 'star'} busy={starBusy} onClick={star} className={starred ? '!text-accent' : ''}>
          {starred ? 'В коллекции' : 'В коллекцию'}
        </Button>
      </div>

      <section aria-label="Треки" className="pt-2">
        {tracks.length > 0 && <TrackTableHeader numbered />}
        {discs.map(([disc, list]) => (
          <div key={disc}>
            {discs.length > 1 && <div className="px-4 pb-1 pt-3 text-sm font-semibold text-muted">Диск {disc}</div>}
            {list.map((t, i) => (
              <TrackRow key={t.ref} track={t} number={t.track_no ?? i + 1} link={false} onPlay={() => play(t)} queue={tracks} />
            ))}
          </div>
        ))}
        {!tracks.length && <p className="px-4 py-6 text-center text-muted">Треклист недоступен</p>}
      </section>

      {album.release_date && <p className="px-4 pb-2 pt-4 text-xs text-dim">Дата выхода: {new Date(album.release_date).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })}</p>}

      {pickerOpen && (
        <PlaylistsPick
          title={`«${album.title}» — в плейлисты`}
          note={missing.length ? `В библиотеке ${owned.length} из ${tracks.length}: остальные скачаются и появятся в выбранных плейлистах.` : undefined}
          picked={[]}
          onClose={() => setPickerOpen(false)}
          onDone={(lists) => void addToPlaylists(lists)}
        />
      )}
    </div>
  );
}

function Bar({ overlay }: { overlay?: boolean }) {
  return (
    <div className={`pt-safe ${overlay ? 'relative' : 'sticky top-(--page-top) z-20 bg-bg/90'} flex h-14 items-center px-2`}>
      <IconButton icon="back" label="Назад" onClick={() => goBack('/search')} className="bg-black/30 !text-white" />
    </div>
  );
}
