import { tagKey } from '../lib/tags';
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { Lyrics } from '../components/Lyrics';
import { setLyricsLook, useLyricsLook } from '../lib/lyrics';
import { SaveButton } from '../components/PlaylistsButton';
import { TrackMenuButton } from '../components/TrackMenu';
import { Cover, Empty, IconButton, Spinner, cx } from '../components/ui';
import { fmtDuration, providerLabel } from '../lib/format';
import {
  cycleRepeat,
  cycleShuffle,
  downloadRef,
  hasNext,
  hasPrev,
  moveInQueue,
  next,
  playQueueAt,
  prev,
  removeFromQueue,
  seek,
  toggle,
  useListen,
  type ListenTrack,
} from '../lib/listen';
import { Link, enc, goBack } from '../lib/router';
import { openShare } from '../lib/shareTrack';
import { addRec, hideRec } from '../lib/smartShuffle';

/** The full player of the PWA in a plain browser (`/player`, from a tap on the mini player): the big cover, seek,
 * shuffle / repeat, ♥, ⋯ and the queue — a tap plays a track, ↑ moves it up, × takes it out. Inside the app the
 * player is the app's own. */
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
  const index = useListen((s) => s.index);
  const context = useListen((s) => s.context);
  const page = useListen((s) => s.page);
  const dl = useListen((s) => (s.track ? s.downloads.get(s.track.ref) : undefined));
  const canNext = useListen((s) => hasNext(s) || (s.repeat === 'all' && !s.context.startsWith('wave:')));
  const canPrev = useListen(hasPrev);
  const [scrub, setScrub] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [lyrics, setLyrics] = useState(() => {
    try {
      return localStorage.getItem('hm-player-lyrics') === '1';
    } catch {
      return false;
    }
  });
  const look = useLyricsLook();
  // «Во весь экран» (the text's «Вид»): the page takes the whole screen and keeps it awake; leaving the whole screen
  // any way (Esc, the system's gesture) switches it off
  useEffect(() => {
    const el = document.documentElement;
    if (look.fullscreen && !document.fullscreenElement) void el.requestFullscreen?.().catch(() => setLyricsLook({ fullscreen: false }));
    if (!look.fullscreen && document.fullscreenElement) void document.exitFullscreen?.().catch(() => undefined);
    const left = () => !document.fullscreenElement && setLyricsLook({ fullscreen: false });
    document.addEventListener('fullscreenchange', left);
    let lock: { release: () => Promise<void> } | null = null;
    const wake = (navigator as Navigator & { wakeLock?: { request: (type: 'screen') => Promise<{ release: () => Promise<void> }> } }).wakeLock;
    if (look.fullscreen) void wake?.request('screen').then((l) => (lock = l), () => undefined);
    return () => {
      document.removeEventListener('fullscreenchange', left);
      void lock?.release().catch(() => undefined);
    };
  }, [look.fullscreen]);
  useEffect(() => () => {
    if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => undefined);
    setLyricsLook({ fullscreen: false });
  }, []);
  const full = look.fullscreen && lyrics;
  const toggleLyrics = () =>
    setLyrics((v) => {
      try {
        localStorage.setItem('hm-player-lyrics', v ? '0' : '1');
      } catch {
        /* the choice lives until the page is closed */
      }
      return !v;
    });

  if (!track)
    return (
      <div className="pt-safe">
        <div className="flex min-h-14 items-center px-2">
          <IconButton icon="down" label="Свернуть" onClick={() => goBack('/home')} />
        </div>
        <Empty icon="note" title="Сейчас ничего не играет" text="Выберите трек, альбом или «Мою волну»." />
      </div>
    );

  const onWave = context.startsWith('wave:');
  const waiting = phase === 'resolving' || phase === 'loading';
  const playing = phase === 'playing' || waiting;
  const owned = info?.kind === 'library';
  // where the sound comes from is said only when it is not the library's own file (the usual case needs no words)
  const source = info?.kind === 'full' ? `${providerLabel(info.provider)} · не из библиотеки` : info?.kind === 'preview' ? 'превью 30 сек' : null;
  const shownPos = scrub ?? Math.min(position, duration || position);
  const pct = duration ? Math.min(100, (shownPos / duration) * 100) : 0;
  const commit = () => {
    if (scrub !== null) seek(scrub);
    setScrub(null);
  };
  const download = async () => {
    setBusy(true);
    await downloadRef(track.ref);
    setBusy(false);
  };
  const firstArtist = track.artists?.find((a) => a.ref);

  return (
    <div className={cx('pt-safe relative isolate mx-auto flex min-h-dvh max-w-2xl flex-col', lyrics && 'lg:max-w-6xl')}>
      {(track.cover_small_url || track.cover_url) && (
        // the cover, blurred, as the page's own light
        <div aria-hidden="true" className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
          {look.background === 'drift' ? (
            // the cover's colours in slow motion: three soft copies of it, each drifting its own way
            <div className="absolute inset-0 saturate-150">
              {(['hm-drift-a', 'hm-drift-b', 'hm-drift-c'] as const).map((name, i) => (
                <img key={name} src={track.cover_small_url || track.cover_url || ''} alt="" className="absolute h-[85%] w-[85%] rounded-full object-cover opacity-45 blur-3xl motion-reduce:!animate-none"
                  style={{ left: ['-20%', '35%', '10%'][i], top: ['-15%', '5%', '40%'][i], animation: `${name} ${[34, 41, 47][i]}s ease-in-out infinite alternate` }} />
              ))}
            </div>
          ) : (
            <img src={track.cover_small_url || track.cover_url || ''} alt="" className="h-full w-full scale-125 object-cover opacity-35 blur-3xl" />
          )}
          <div className="absolute inset-0 bg-bg/40" />
        </div>
      )}
      <div className="flex min-h-14 items-center gap-1 px-2">
        <IconButton icon="down" label="Свернуть" onClick={() => goBack('/home')} />
        <PlayingFrom context={context} page={page} />
        <IconButton icon="share" label="Поделиться" onClick={() => openShare({ title: track.title, artist: track.artist, ref: track.ref, nd_song_id: info?.nd_song_id })} className="text-muted" />
        <IconButton icon="lyrics" label={lyrics ? 'Скрыть текст' : 'Показать текст'} aria-pressed={lyrics} onClick={toggleLyrics} className={lyrics ? '!text-accent' : 'text-muted'} />
      </div>

      {/* with the text on: a wide screen has it to the right of the cover; where both don't fit, the text takes the
          cover's place */}
      <div className={cx(lyrics && 'lg:grid lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)] lg:items-start lg:gap-x-8 lg:px-4')}>
        {lyrics && (
          <div className={cx('min-h-72 px-3 pt-2 lg:sticky lg:top-2 lg:order-2 lg:h-[calc(100dvh-5.5rem)] lg:px-0', full ? 'h-[calc(100dvh-13rem)]' : 'h-[58dvh]')}>
            <Lyrics refId={track.ref} songId={info?.nd_song_id ?? track.nd_song_id} />
          </div>
        )}
        <div className="min-w-0">
      <div className={cx('px-6 pt-2', lyrics && 'hidden lg:block')}>
        <Cover src={track.cover_url || track.cover_small_url} alt={track.title} className="mx-auto aspect-square w-full max-w-[22rem] shadow-2xl" />
      </div>

      <div className="flex items-center gap-2 px-6 pt-6">
        <div className="min-w-0 flex-1">
          {track.album_ref ? (
            <Link to={`/album/${enc(track.album_ref)}`} className="block truncate text-xl font-bold hover:underline">
              {track.title}
            </Link>
          ) : (
            <div className="truncate text-xl font-bold">{track.title}</div>
          )}
          <div className="flex items-center gap-1.5 text-[15px] text-muted">
            {onWave && <Icon name="wave" size={16} className="shrink-0 text-accent" />}
            {track.rec && (
              <span title="Рекомендация" className="shrink-0 text-accent">
                <Icon name="sparkle" size={15} />
              </span>
            )}
            {firstArtist?.ref ? (
              <Link to={`/artist/${enc(firstArtist.ref)}`} className="truncate hover:underline">
                {track.artist}
              </Link>
            ) : (
              <span className="truncate">{track.artist}</span>
            )}
          </div>
        </div>
        <SaveButton songId={info?.nd_song_id} refId={track.ref} listKey={tagKey({ library_track_id: info?.track_id ?? track.library_track_id })} title={track.title} data={{ ...track }} size={26} />
        {/* a track that is not in the library yet can be downloaded into it; one that is needs no mark */}
        {owned || dl === 'done' ? null : dl ? (
          <span className="flex h-11 w-11 shrink-0 items-center justify-center text-info" title="Скачивается в библиотеку">
            <Spinner size={18} />
          </span>
        ) : (
          <IconButton icon="download" label="Скачать в библиотеку" onClick={download} busy={busy} />
        )}
        <TrackMenuButton track={{ ...track, nd_song_id: info?.nd_song_id, library_track_id: info?.track_id ?? track.library_track_id }} inPlayer lean />
      </div>

      {track.rec && (
        <div className="mx-6 mt-4 flex items-center gap-1 rounded-xl bg-accent/10 py-1 pl-3 pr-1" role="group" aria-label="Рекомендация">
          <Icon name="sparkle" size={18} className="shrink-0 text-accent" />
          <span className="min-w-0 flex-1 text-[13px] leading-tight">{track.rec.label}</span>
          <IconButton icon="plus" label={track.rec.label.includes('любим') ? 'В любимое' : 'Добавить в плейлист'} onClick={() => void addRec(track)} className="!text-accent" />
          <IconButton icon="minus" label="Не рекомендовать здесь" onClick={() => void hideRec(track)} className="text-muted" />
        </div>
      )}

      <div className="px-6 pt-4">
        <input
          type="range"
          className="hm-range w-full"
          aria-label="Перемотка"
          min={0}
          max={Math.max(1, Math.round(duration))}
          step={1}
          value={Math.round(shownPos)}
          disabled={!duration || phase === 'resolving' || phase === 'error'}
          style={{ '--p': `${pct}%` } as CSSProperties}
          onChange={(e) => setScrub(Number(e.target.value))}
          onPointerUp={commit}
          onKeyUp={commit}
          onBlur={commit}
        />
        <div className="flex justify-between pt-1 text-xs tabular-nums text-muted">
          <span>{fmtDuration(shownPos) || '0:00'}</span>
          <span className={cx('truncate px-2', phase === 'error' && 'text-danger')}>
            {phase === 'error' ? error : phase === 'resolving' && !info ? 'Ищу файл…' : waiting ? 'Загрузка…' : source}
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
        <IconButton icon="prev" label={canPrev ? 'Предыдущий трек' : 'Сначала'} onClick={prev} size={30} className="h-14 w-14" />
        <button
          type="button"
          onClick={toggle}
          aria-label={phase === 'error' ? 'Повторить' : playing ? 'Пауза' : 'Слушать'}
          className="inline-flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-fg text-bg transition active:scale-95"
        >
          {phase === 'error' ? (
            <Icon name="refresh" size={28} stroke={2.4} />
          ) : waiting ? (
            <Spinner size={28} />
          ) : playing ? (
            <Icon name="pause" size={28} stroke={3} />
          ) : (
            <Icon name="play" size={28} stroke={2.4} className="translate-x-0.5" />
          )}
        </button>
        <IconButton icon="next" label="Следующий трек" onClick={() => void next()} disabled={!canNext} size={30} className="h-14 w-14" />
        <IconButton
          icon={repeat === 'one' ? 'repeat1' : 'repeat'}
          label={repeat === 'off' ? 'Повтор выключен' : repeat === 'all' ? 'Повтор очереди' : 'Повтор трека'}
          disabled={onWave && repeat !== 'one'}
          onClick={cycleRepeat}
          className={repeat !== 'off' ? '!text-accent' : 'text-muted'}
        />
      </div>
        </div>
      </div>

      <Queue queue={queue} index={index} onWave={onWave} />
    </div>
  );
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

function Queue({ queue, index, onWave }: { queue: readonly ListenTrack[]; index: number; onWave: boolean }) {
  const current = useRef<HTMLLIElement>(null);
  const upcoming = queue.length - index - 1;
  // the current track's row in view once, not on every change of the queue
  useEffect(() => {
    current.current?.scrollIntoView({ block: 'nearest' });
  }, []);

  return (
    <section aria-label="Очередь" className="mt-6 flex-1 rounded-t-2xl bg-surface pb-[calc(16px+env(safe-area-inset-bottom))]">
      <div className="flex items-center justify-between px-4 pb-1 pt-4">
        <h2 className="text-lg font-bold">Далее</h2>
        <span className="text-xs text-muted">{onWave ? 'Подбирается на ходу' : upcoming > 0 ? `${upcoming} в очереди` : 'Очередь закончится на этом треке'}</span>
      </div>
      <ul>
        {queue.map((t, i) => (
          <li
            key={`${t.ref}:${i}`}
            ref={i === index ? current : undefined}
            className={cx('flex min-h-14 items-center gap-3 pl-4 pr-1', i < index && 'opacity-50', i === index && 'bg-fg/5')}
          >
            <button type="button" onClick={() => playQueueAt(i)} className="flex min-w-0 flex-1 items-center gap-3 py-1.5 text-left" aria-label={`Слушать «${t.title}»`}>
              <Cover src={t.cover_small_url || t.cover_url} size={40} alt="" />
              <span className="min-w-0 flex-1">
                <span className={cx('block truncate text-[15px]', i === index && 'font-semibold text-accent')}>{t.title}</span>
                <span className="flex items-center gap-1 text-[13px] text-muted">
                  {t.rec && (
                    <span title="Рекомендация" className="shrink-0 text-accent">
                      <Icon name="sparkle" size={12} />
                    </span>
                  )}
                  <span className="truncate">{t.artist}</span>
                </span>
              </span>
            </button>
            {i > index + 1 && <IconButton icon="back" label="Поднять выше" onClick={() => moveInQueue(i, i - 1)} size={18} className="w-9 rotate-90 text-muted" />}
            {i !== index && <IconButton icon="x" label="Убрать из очереди" onClick={() => removeFromQueue(i)} size={18} className="w-9 text-muted" />}
            <TrackMenuButton track={t} className="w-9" inPlayer />
          </li>
        ))}
      </ul>
    </section>
  );
}
