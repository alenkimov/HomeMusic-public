import { tagKey } from '../lib/tags';
import { PlayerLight } from '../components/PlayerLight';
import { FileRow, PlayLine } from '../components/FileInfo';
import { ChoosePlaybackFile } from '../components/FindFile';
import { useEffect, useRef, useState, useSyncExternalStore, type PointerEvent as ReactPointerEvent } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { LikeButton } from '../components/LikeButton';
import { PlaybackDevicesButton } from '../components/PlaybackDevices';
import { Lyrics } from '../components/Lyrics';
import { SaveButton } from '../components/PlaylistsButton';
import { TrackMenuButton, menuTrackOf } from '../components/TrackMenu';
import { trackMenuAt } from '../lib/trackMenu';
import { OtherVersions } from '../components/Versions';
import { ServiceBadge } from '../components/ServiceBadge';
import { ArtistRow } from '../components/Artist';
import { PlayingArtists, PlayingMarks, PlayingTitle } from '../components/NowPlaying';
import { TrackCell } from '../components/Track';
import { Cover, Empty, IconButton, Sheet, Spinner, cx } from '../components/ui';
import { fmtDuration, fmtNum, tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { lyricsAsked, queueAsked } from '../lib/playerQueue';
import { useSeek } from '../lib/seek';
import { SeekLine } from '../components/SeekLine';
import { listenTopPull } from '../lib/topPull';
import { isUpload,
  cycleRepeat,
  cycleShuffle,
  downloadRef,
  getListenState,
  hasNext,
  hasPrev,
  moveInQueue,
  moveManualQueue,
  next,
  playManualQueueAt,
  playQueueAt,
  prev,
  removeFromQueue,
  removeManualQueue,
  toggle,
  useListen,
  type ListenTrack,
  artistTo,
  creditedOf,
} from '../lib/listen';
import { Link, albumPath, enc, goBack } from '../lib/router';
import { STANDALONE } from '../lib/embedded';
import type { ListenOut } from '../api/types';
import { openShare } from '../lib/shareTrack';
import { addRec, hideRec } from '../lib/smartShuffle';

const WIDE = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(min-width: 1024px)') : null;
const watchWide = (cb: () => void) => {
  WIDE?.addEventListener('change', cb);
  return () => WIDE?.removeEventListener('change', cb);
};
/** A window wide enough for two columns (Tailwind's `lg`). */
const useWide = () => useSyncExternalStore(watchWide, () => !!WIDE?.matches, () => false);

export function MobilePlayerSheet() {
  const sheet = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const layer = sheet.current!;
    const scroll = scroller.current!;
    let animation: Animation | null = null;
    let closing = false;
    const stop = listenTopPull(scroll, {
      ready: () => !closing && !animation && scroll.scrollTop <= 1,
      move: (distance) => {
        if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
          layer.style.transform = `translateY(${distance}px)`;
        }
      },
      end: (distance) => {
        closing = distance >= 85;
        const finish = () => {
          animation = null;
          if (closing) goBack('/home');
        };
        const from = layer.style.transform;
        const to = closing ? 'translateY(100%)' : '';
        layer.style.transform = to;
        if (!from) {
          finish();
          return;
        }
        animation = layer.animate([{ transform: from }, { transform: to || 'none' }], {
          duration: 190, easing: 'ease-out',
        });
        void animation.finished.then(finish, () => {});
      },
    });
    return () => {
      stop();
      animation?.cancel();
    };
  }, []);

  return (
    <div ref={sheet} role="dialog" aria-modal="true" aria-label="Плеер" className="fixed inset-0 z-[45] bg-bg">
      <div ref={scroller} className="no-scrollbar h-full overflow-y-auto overscroll-y-none">
        <PlayerPage />
      </div>
    </div>
  );
}

/** The full player of the PWA in a plain browser (`/player`, from a tap on the mini player): the big cover (or the
 * text in its place), ♥, seek, the controls; under them ⋯ on the left, «Поделиться» and the queue's button on the
 * right; below — «Участники»: who plays it, a tap away from their pages and their ♥. The queue is shown when asked
 * for: a sheet from below on a phone, the right column on a wide screen — the column of the text too: an open queue stands over
 * the text, which is back when the queue is closed; with neither, the player is one column in the middle. Inside the app the player is the app's own. */
export function PlayerPage() {
  const track = useListen((s) => s.track);
  const phase = useListen((s) => s.phase);
  const info = useListen((s) => s.info);
  const error = useListen((s) => s.error);
  const position = useListen((s) => s.position);
  const duration = useListen((s) => s.duration);
  const shuffle = useListen((s) => s.shuffle);
  const smart = useListen((s) => s.smart);
  const repeat = useListen((s) => s.repeat);
  const queue = useListen((s) => s.queue);
  const hasManual = useListen((s) => s.manualQueue.length > 0);
  const index = useListen((s) => s.index);
  const context = useListen((s) => s.context);
  const page = useListen((s) => s.page);
  const dl = useListen((s) => (s.track ? s.downloads.get(s.track.ref) : undefined));
  const canNext = useListen((s) => hasNext(s) || (s.repeat === 'all' && !s.context.startsWith('wave:')));
  const canPrev = useListen(hasPrev);
  const { shownPos, aim, range } = useSeek(position, duration);
  const [busy, setBusy] = useState(false);
  // asked for by the bar's lyrics button: open with the text, whatever was chosen last; else as it was left
  const [lyricsWanted] = useState(lyricsAsked);
  const [lyrics, setLyrics] = useState(() => {
    if (lyricsWanted) return true;
    try {
      return localStorage.getItem('hm-player-lyrics') === '1';
    } catch {
      return false;
    }
  });
  const wide = useWide();
  const [queueOpen, setQueueOpen] = useState(() => queueAsked() && !lyricsWanted);
  // «Во весь экран» (a wide screen): the page takes the whole screen and keeps it awake. What is so is read from the
  // browser: the whole screen is also left by Esc
  const [full, setFull] = useState(() => !!document.fullscreenElement);
  useEffect(() => {
    const changed = () => setFull(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', changed);
    return () => {
      document.removeEventListener('fullscreenchange', changed);
      if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => undefined);
    };
  }, []);
  useEffect(() => {
    if (!full) return;
    let lock: { release: () => Promise<void> } | null = null;
    let over = false;
    const wake = (navigator as Navigator & { wakeLock?: { request: (type: 'screen') => Promise<{ release: () => Promise<void> }> } }).wakeLock;
    void wake?.request('screen').then((l) => (over ? void l.release().catch(() => undefined) : (lock = l)), () => undefined);
    return () => {
      over = true;
      void lock?.release().catch(() => undefined);
    };
  }, [full]);
  const canFull = !!document.documentElement.requestFullscreen;
  const toggleFull = () =>
    void (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen()).catch(() => undefined);
  // a wide screen has one column for the text and the queue: the queue, while open, stands over the text
  const textHidden = wide && queueOpen;
  const textShown = lyrics && !textHidden;
  const toggleLyrics = () => {
    const on = !textShown; // under the queue the text is not seen: its button then asks for it, and the queue closes
    if (textHidden) setQueueOpen(false);
    setLyrics(on);
    try {
      localStorage.setItem('hm-player-lyrics', on ? '1' : '0');
    } catch {
      /* the choice lives until the page is closed */
    }
  };

  const cover = track?.cover_small_url || track?.cover_url;

  if (!track)
    return (
      <div className="pt-safe">
        <div className="flex min-h-14 items-center px-2">
          <IconButton icon="down" label="Свернуть" onClick={() => goBack('/home')} />
        </div>
        {hasManual ? (
          <div className="mx-auto max-w-xl px-5 pb-6">
            <h1 className="mb-3 text-xl font-bold">Очередь</h1>
            <QueueList queue={queue} index={index} onWave={false} shuffle={false} />
          </div>
        ) : <Empty icon="note" title="Сейчас ничего не играет" text="Выберите трек, альбом или «Мою волну»." />}
      </div>
    );

  const onWave = context.startsWith('wave:');
  const waiting = phase === 'resolving' || phase === 'loading';
  const playing = phase === 'playing' || waiting;
  const owned = info?.kind === 'library';
  const versionTrackId = info?.track_id ?? track.library_track_id;
  const versionSongId = versionTrackId == null ? (info?.nd_song_id ?? track.nd_song_id) : null;
  const download = async () => {
    setBusy(true);
    await downloadRef(track.ref);
    setBusy(false);
  };
  // what the right column of a wide screen holds; a narrow one has the text in the cover's place and the queue in a sheet
  const side = !wide ? null : queueOpen ? 'queue' : lyrics ? 'lyrics' : null;
  const text = <Lyrics refId={track.ref} songId={info?.nd_song_id ?? track.nd_song_id} />;

  return (
    <div className={cx('pt-safe relative isolate mx-auto flex min-h-dvh max-w-2xl flex-col pb-[calc(24px+env(safe-area-inset-bottom))]', side && 'lg:max-w-6xl')}>
      {/* the page's own light: the look chosen on this device («Меню» → «Фон плеера») */}
      <PlayerLight cover={cover} />
      <div className="flex min-h-14 items-center gap-1 px-2">
        <IconButton icon="down" label="Свернуть" onClick={() => goBack('/home')} />
        {wide && canFull && (
          <IconButton icon={full ? 'shrink' : 'expand'} label={full ? 'Выйти из полноэкранного режима' : 'Во весь экран'} aria-pressed={full} onClick={toggleFull} className={full ? '!text-accent' : 'text-muted'} />
        )}
        <PlayingFrom context={context} page={page} />
        {/* as much on the right as on the left: the name stays in the middle */}
        {wide && canFull && <span className="w-11 shrink-0" />}
        <IconButton icon="lyrics" label={textShown ? 'Скрыть текст' : 'Показать текст'} aria-pressed={textShown} onClick={toggleLyrics} className={textShown ? '!text-accent' : 'text-muted'} />
      </div>

      <div className={cx(side && 'lg:grid lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)] lg:items-start lg:gap-x-8 lg:px-4')}>
        {side ? (
          <div className="sticky top-2 order-2 h-[calc(100dvh-5.5rem)] min-w-0">
            {side === 'queue' ? (
              <section aria-label="Очередь" className="flex h-full flex-col overflow-hidden rounded-2xl bg-surface">
                <div className="flex items-center gap-2 pl-4 pr-1 pt-1">
                  <h2 className="flex-1 text-lg font-bold">Очередь</h2>
                  <IconButton icon="x" label="Закрыть очередь" onClick={() => setQueueOpen(false)} className="text-muted" />
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-3">
                  <QueueList queue={queue} index={index} onWave={onWave} shuffle={shuffle} />
                </div>
              </section>
            ) : text}
          </div>
        ) : (
          lyrics && <div data-pull-ignore className="h-[58dvh] min-h-72 px-3 pt-2">{text}</div>
        )}
        <div className="min-w-0">
      <div className={cx('px-6 pt-2', lyrics && !wide && 'hidden')}>
        {/* the line of the text aimed at while seeking lies over the foot of the cover */}
        <div className="relative mx-auto aspect-square w-full max-w-[22rem] overflow-hidden rounded-md shadow-2xl">
          <Cover src={track.cover_url || track.cover_small_url} alt={track.title} className="h-full w-full" />
          <SeekLine aim={aim} refId={track.ref} songId={info?.nd_song_id ?? track.nd_song_id} time over className="absolute inset-x-0 bottom-0" />
        </div>
      </div>

      <div className="flex items-center gap-2 px-6 pt-6">
        <div className="min-w-0 flex-1">
          <PlayingTitle track={track} info={info} mark className="block truncate text-xl font-bold" />
          <div className="flex items-center gap-1.5 text-[15px] text-muted">
            <PlayingMarks track={track} onWave={onWave} size={16} />
            {/* every credited artist opens their own page */}
            <span className="truncate"><PlayingArtists track={track} /></span>
          </div>
        </div>
        {/* a track that is not in the library yet can be downloaded into it; one that is needs no mark */}
        {owned || dl === 'done' ? null : dl ? (
          <span className="flex h-11 w-11 shrink-0 items-center justify-center text-info" title="Скачивается в библиотеку">
            <Spinner size={18} />
          </span>
        ) : (
          <IconButton icon="download" label="Скачать в библиотеку" onClick={download} busy={busy} />
        )}
        {!isUpload(track) && <SaveButton songId={info?.nd_song_id ?? track.nd_song_id} refId={track.ref} listKey={tagKey({ library_track_id: info?.track_id ?? track.library_track_id })} title={track.title} data={{ ...track }} size={26} />}
      </div>

      {track.rec && (
        <div className="mx-6 mt-4 flex items-center gap-1 rounded-xl bg-accent/10 py-1 pl-3 pr-1" role="group" aria-label="Рекомендация">
          <Icon name="sparkle" size={18} className="shrink-0 text-accent" />
          <span className="min-w-0 flex-1 text-[13px] leading-tight">{track.rec.label}</span>
          <IconButton icon="plus" label={track.rec.label.includes('любим') ? 'В любимое' : 'Добавить в плейлист'} onClick={() => void addRec(track)} className="!text-accent" />
          <IconButton icon="minus" label="Не рекомендовать здесь" onClick={() => void hideRec(track)} className="text-muted" />
        </div>
      )}

      <div className="relative px-6 pt-4">
        <input {...range} className="hm-range w-full" disabled={!duration || phase === 'resolving' || phase === 'error'} />
        <div className="flex justify-between pt-1 text-xs tabular-nums text-muted">
          <span>{fmtDuration(shownPos) || '0:00'}</span>
          <span className="min-w-0 truncate px-2">
            {(phase === 'error' || waiting) && <PlayLine phase={phase} info={info} error={error} className="max-w-full truncate" />}
          </span>
          <span>{fmtDuration(duration) || '–:––'}</span>
        </div>
      </div>

      <div className="flex items-center justify-between px-4 pt-3">
        <IconButton
          icon={smart ? 'shuffleSmart' : 'shuffle'}
          label={smart ? 'Умное перемешивание включено' : shuffle ? 'Перемешивание включено' : 'Перемешать'}
          aria-pressed={shuffle}
          disabled={onWave}
          onClick={cycleShuffle}
          className={shuffle && !onWave ? '!text-accent' : 'text-muted'}
        />
        <IconButton icon="prev" filled label={canPrev ? 'Предыдущий трек' : 'Сначала'} onClick={prev} size={30} className="h-14 w-14" />
        <button
          type="button"
          onClick={toggle}
          aria-label={phase === 'error' ? 'Повторить' : playing ? 'Пауза' : 'Слушать'}
          className="inline-flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-fg text-bg transition active:scale-95"
        >
          {phase === 'error' ? (
            <Icon name="refresh" size={28} stroke={2.4} />
          ) : playing ? (
            <Icon name="pause" size={28} stroke={3} />
          ) : (
            <Icon name="play" size={28} filled className="translate-x-0.5" />
          )}
        </button>
        <IconButton icon="next" filled label="Следующий трек" onClick={() => void next()} disabled={!canNext} size={30} className="h-14 w-14" />
        <IconButton
          icon={repeat === 'one' ? 'repeat1' : 'repeat'}
          label={repeat === 'off' ? 'Повтор выключен' : repeat === 'all' ? 'Повтор очереди' : 'Повтор трека'}
          disabled={onWave && repeat !== 'one'}
          onClick={cycleRepeat}
          className={repeat !== 'off' ? '!text-accent' : 'text-muted'}
        />
      </div>

      <div className="flex items-center justify-between px-4 pt-2">
        {isUpload(track) ? <span /> : <TrackMenuButton track={menuTrackOf(track, info)} inPlayer />}
        <ChoosePlaybackFile />
        <div className="flex items-center">
          {!isUpload(track) && <IconButton icon="share" label="Поделиться" onClick={() => openShare({ title: track.title, artist: track.artist, ref: track.ref, nd_song_id: info?.nd_song_id })} className="text-muted" />}
          <PlaybackDevicesButton />
          <IconButton icon="queue" label={queueOpen ? 'Скрыть очередь' : 'Очередь'} aria-pressed={queueOpen} onClick={() => setQueueOpen((v) => !v)} className={queueOpen ? '!text-accent' : 'text-muted'} />
        </div>
      </div>
      <Credits track={track} info={info} />
      {!isUpload(track) && <SpotifyTrackPlays key={track.ref} track={track} songId={info?.nd_song_id || track.nd_song_id} />}
      {!isUpload(track) && (versionTrackId != null || versionSongId) && (
        <OtherVersions key={versionTrackId ?? versionSongId} trackId={versionTrackId} songId={versionSongId} />
      )}
        </div>
      </div>

      <Sheet open={queueOpen && !wide} onClose={() => setQueueOpen(false)} title="Очередь" footer={<QueueModes onWave={onWave} />}>
        <QueueList queue={queue} index={index} onWave={onWave} shuffle={shuffle} />
      </Sheet>
    </div>
  );
}

function SpotifyTrackPlays({ track, songId }: { track: ListenTrack; songId?: string | null }) {
  const fetched = useAsync((signal) => api.spotifyTrack({ nd_song_id: songId, ref: track.ref }, signal),
    [track.ref, songId], track.spotify_plays == null);
  const count = track.spotify_plays ?? fetched.data?.spotify_plays;
  if (count == null) return null;
  return (
    <div className="mx-4 mt-3 rounded-2xl bg-surface px-4 py-3">
      <ServiceBadge service="spotify" label={`${fmtNum(count)} прослушиваний в Spotify`} size={16} />
    </div>
  );
}

/** Under the buttons, one card with no heading: who plays the track — each a link to their page, with their ♥ (to
 * follow them) —, the album it is of, and the file that plays (a tap: «Как скачан»). */
function Credits({ track, info }: { track: ListenTrack; info: ListenOut | null | undefined }) {
  const people = creditedOf(track);
  const albumTo = track.album_ref || track.release_id ? albumPath({ ref: track.album_ref ?? '', release_id: track.release_id })
    : STANDALONE && track.nd_album_id ? `/lib/album/${enc(track.nd_album_id)}` : null;
  const album = track.album ? (
    <>
      <Cover src={track.cover_small_url ?? track.cover_url} size={48} alt="" icon="disc" />
      <span className="min-w-0 flex-1">
        <span className={cx('block truncate text-[15px] font-medium', albumTo && 'group-hover/album:underline')}>{track.album}</span>
        <span className="block truncate text-[13px] text-muted">{['Альбом', track.year].filter(Boolean).join(' · ')}</span>
      </span>
    </>
  ) : null;
  const albumRow = 'group/album flex min-h-16 items-center gap-3 py-2';
  return (
    <section aria-label="Исполнители, альбом и файл" className="mx-4 mt-5 rounded-2xl bg-surface px-4 py-1.5">
      <ul>
        {people.map((a, i) => (
          <li key={`${a.ref ?? a.name}:${i}`}>
            <ArtistRow
              artist={{ name: a.name, to: artistTo(a) }}
              picture={<CreditPhoto refId={a.ref} />}
              actions={a.ref && <LikeButton targetType="artist" refId={a.ref} title={a.name} data={{ name: a.name }} className="-mr-2" />}
              className="!px-0"
            />
          </li>
        ))}
      </ul>
      {album && (albumTo ? <Link to={albumTo} className={albumRow}>{album}</Link> : <div className={albumRow}>{album}</div>)}
      <FileRow info={info} />
    </section>
  );
}

function CreditPhoto({ refId }: { refId?: string }) {
  const res = useAsync((signal) => api.catalogArtistPhoto(refId!, signal), [refId], !!refId);
  return <Cover src={res.data?.url} size={48} round alt="" icon="user" />;
}

/** The head of the player: what is playing — «Играет из плейлиста · JAPAN», a link to that list. */
function PlayingFrom({ context, page }: { context: string; page: string }) {
  const id = context === 'playlist' ? /^\/playlist\/([^/]+)$/.exec(page)?.[1] : undefined;
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    setName(null);
    if (!id) return;
    let alive = true;
    api.playlists()
      .then((all) => alive && setName(all.find((p) => p.id === decodeURIComponent(id))?.name ?? null))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [id]);
  const title = context === 'playlist' ? name : context === 'liked' ? 'Любимое' : null;
  const to = context === 'liked' ? '/liked' : page;
  if (!title)
    return <div className="min-w-0 flex-1 text-center text-xs uppercase tracking-wide text-muted">{contextLabel(context)}</div>;
  return (
    <div className="min-w-0 flex-1 text-center leading-tight">
      <div className="text-[11px] uppercase tracking-wide text-muted">{context === 'playlist' ? 'Играет из плейлиста' : 'Играет из'}</div>
      <Link to={to} className="block truncate text-sm font-semibold hover:underline">{title}</Link>
    </div>
  );
}

function contextLabel(context: string): string {
  if (context.startsWith('wave:')) return 'Моя волна';
  if (context.startsWith('mix:')) return 'Микс дня';
  return (
    { album: 'Альбом', artist: 'Исполнитель', playlist: 'Плейлист', liked: 'Любимое', search: 'Поиск', queue: 'Очередь' } as Record<string, string>
  )[context] ?? 'Сейчас играет';
}

/** The queue: the track that plays and what comes after it. A tap plays a track, × takes it out, the handle on the
 * right drags it to another place — the order changes as it is dragged. */
function QueueList({ queue, index, onWave, shuffle }: { queue: readonly ListenTrack[]; index: number; onWave: boolean; shuffle: boolean }) {
  const manual = useListen((s) => s.manualQueue);
  const list = useRef<HTMLUListElement>(null);
  const ids = useRef(new WeakMap<ListenTrack, number>());
  const lastId = useRef(0);
  const [dragged, setDragged] = useState<string | null>(null);
  const following = queue.slice(index + 1);
  // a row keeps its key as it is moved (the same track may stand in the queue twice: then the keys are counted)
  const seen = new Map<number, number>();
  const keyOf = (t: ListenTrack) => {
    let id = ids.current.get(t);
    if (id === undefined) ids.current.set(t, (id = ++lastId.current));
    const nth = seen.get(id) ?? 0;
    seen.set(id, nth + 1);
    return `${id}:${nth}`;
  };

  const grab = (e: ReactPointerEvent, key: string, manualId?: number) => {
    e.preventDefault();
    setDragged(key);
    let manualOver: number | undefined;
    const move = (ev: PointerEvent) => {
      const ul = list.current;
      if (!ul) return;
      const box = ul.closest('.overflow-y-auto');
      if (box) {
        const r = box.getBoundingClientRect();
        if (ev.clientY < r.top + 56) box.scrollTop -= 14;
        else if (ev.clientY > r.bottom - 56) box.scrollTop += 14;
      }
      const rows = [...ul.querySelectorAll<HTMLElement>(manualId == null ? 'li[data-i]' : 'li[data-manual]')];
      const own = rows.find((r) => r.dataset.k === key);
      const over = rows.find((r) => {
        const b = r.getBoundingClientRect();
        return ev.clientY >= b.top && ev.clientY < b.bottom;
      });
      if (manualId != null) {
        manualOver = own && over && own !== over ? Number(over.dataset.manual) : undefined;
        return;
      }
      if (!own || !over) return;
      const from = Number(own.dataset.i);
      const to = Number(over.dataset.i);
      if (to !== from && to > getListenState().index) moveInQueue(from, to); // never above the track that plays
    };
    const drop = (ev: PointerEvent) => {
      setDragged(null);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', drop);
      window.removeEventListener('pointercancel', drop);
      if (ev.type === 'pointerup' && manualId != null && manualOver != null) void moveManualQueue(manualId, manualOver);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', drop);
    window.addEventListener('pointercancel', drop);
  };

  const row = (t: ListenTrack, i?: number, manualId?: number) => {
    const key = manualId == null ? keyOf(t) : `manual:${manualId}`;
    const current = i === index;
    const unqueue = () => (manualId != null ? void removeManualQueue([manualId]) : i != null && removeFromQueue(i));
    // the menu of a queued track is the track's own, with «Убрать из очереди» among its lines
    const menu = isUpload(t) ? null : menuTrackOf(t);
    const extra = current ? undefined : [{ icon: 'x' as const, label: 'Убрать из очереди', shortcut: 'Del', onClick: unqueue }];
    return (
      <li key={key} data-i={i} data-manual={manualId} data-k={key} onContextMenu={menu ? trackMenuAt(menu, extra, true) : undefined} className={cx('flex min-h-14 items-center gap-1 rounded-lg', dragged === key && 'bg-fg/10')}>
        <button type="button" onClick={() => manualId != null ? playManualQueueAt(manualId) : i != null && playQueueAt(i)} className="flex min-w-0 flex-1 items-center gap-3 py-1.5 text-left" aria-label={`Слушать «${t.title}»`}>
          <TrackCell
            track={{ title: t.title, cover: t.cover_small_url || t.cover_url, identity: t }}
            playing={current}
            sub={<span className="flex items-center gap-1">
              {t.rec && <span title="Рекомендация" className="shrink-0 text-accent"><Icon name="sparkle" size={12} /></span>}
              <span className="truncate">{t.artist}</span>
            </span>}
          />
        </button>
        {menu && <TrackMenuButton track={menu} extra={extra} inPlayer className="w-9" />}
        {!current && (
          <>
            <IconButton icon="x" label="Убрать из очереди" onClick={unqueue} size={18} className="w-9 text-muted" />
            <span
              role="button"
              aria-label={`Перетащить «${t.title}»`}
              title="Перетащить"
              onPointerDown={(e) => grab(e, key, manualId)}
              className="-mr-2 flex h-11 w-9 shrink-0 cursor-grab touch-none items-center justify-center text-muted hover:text-fg active:cursor-grabbing"
            >
              <Icon name="grip" size={18} />
            </span>
          </>
        )}
      </li>
    );
  };

  return (
    <ul ref={list} className={cx(dragged && 'select-none')}>
      {queue[index] && row(queue[index], index)}
      {manual.length > 0 && (
        <li className="pb-1 pt-3 text-xs text-muted">Ручная очередь · {tracksWord(manual.length)}</li>
      )}
      {manual.map(({ id, track }) => row(track, undefined, id))}
      {(onWave || following.length > 0) && (
        <li className="flex items-center gap-1.5 pb-1 pt-3 text-xs text-muted">
          {shuffle && !onWave && <Icon name="shuffle" size={13} />}
          {onWave ? 'Далее · подбирается на ходу' : `${shuffle ? 'Вперемешку' : 'Далее'} · ${tracksWord(following.length)}`}
        </li>
      )}
      {following.map((t, i) => row(t, index + 1 + i))}
    </ul>
  );
}

/** «Вперемешку» and «Повтор» at the foot of a phone's queue: the same switches as by the controls — here the new
 * order is seen at once. */
function QueueModes({ onWave }: { onWave: boolean }) {
  const shuffle = useListen((s) => s.shuffle);
  const smart = useListen((s) => s.smart);
  const repeat = useListen((s) => s.repeat);
  const btn = 'flex flex-1 flex-col items-center gap-1 rounded-xl bg-fg/5 py-2 text-xs font-medium transition active:scale-95 disabled:opacity-40';
  return (
    <div className="flex gap-2">
      <button type="button" onClick={cycleShuffle} disabled={onWave} aria-pressed={shuffle} className={cx(btn, shuffle && !onWave ? 'text-accent' : 'text-muted')}>
        <Icon name={smart ? 'shuffleSmart' : 'shuffle'} size={20} />
        {smart ? 'Умное перемешивание' : 'Вперемешку'}
      </button>
      <button type="button" onClick={cycleRepeat} disabled={onWave && repeat !== 'one'} aria-pressed={repeat !== 'off'} className={cx(btn, repeat !== 'off' ? 'text-accent' : 'text-muted')}>
        <Icon name={repeat === 'one' ? 'repeat1' : 'repeat'} size={20} />
        {repeat === 'one' ? 'Повтор трека' : repeat === 'all' ? 'Повтор очереди' : 'Повтор'}
      </button>
    </div>
  );
}
