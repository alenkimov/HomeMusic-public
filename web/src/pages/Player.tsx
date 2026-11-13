import { tagKey } from '../lib/tags';
import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { LikeButton } from '../components/LikeButton';
import { Lyrics } from '../components/Lyrics';
import { setLyricsLook, useLyricsLook } from '../lib/lyrics';
import { SaveButton } from '../components/PlaylistsButton';
import { TrackMenuButton } from '../components/TrackMenu';
import { Cover, Empty, IconButton, Sheet, Spinner, cx } from '../components/ui';
import { fmtDuration, providerLabel, tracksWord } from '../lib/format';
import { queueAsked } from '../lib/playerQueue';
import {
  cycleRepeat,
  cycleShuffle,
  downloadRef,
  getListenState,
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

const WIDE = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(min-width: 1024px)') : null;
const watchWide = (cb: () => void) => {
  WIDE?.addEventListener('change', cb);
  return () => WIDE?.removeEventListener('change', cb);
};
/** A window wide enough for two columns (Tailwind's `lg`). */
const useWide = () => useSyncExternalStore(watchWide, () => !!WIDE?.matches, () => false);

/** The full player of the PWA in a plain browser (`/player`, from a tap on the mini player): the big cover (or the
 * text in its place), ♥, seek, the controls; under them ⋯ on the left, «Поделиться» and the queue's button on the
 * right; below — «Участники»: who plays it, a tap away from their pages and their ♥. The queue is shown when asked
 * for: a sheet from below on a phone, the right column on a wide screen (where the text or «Участники» otherwise
 * are). Inside the app the player is the app's own. */
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
  const wide = useWide();
  const [queueOpen, setQueueOpen] = useState(queueAsked);
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
  // what the right column of a wide screen holds; a narrow one has the text in the cover's place and the queue in a sheet
  const side = !wide ? null : queueOpen ? 'queue' : lyrics ? 'lyrics' : 'credits';
  const credits = <Credits track={track} />;
  const text = <Lyrics refId={track.ref} songId={info?.nd_song_id ?? track.nd_song_id} />;

  return (
    <div className="pt-safe relative isolate mx-auto flex min-h-dvh max-w-2xl flex-col pb-[calc(24px+env(safe-area-inset-bottom))] lg:max-w-6xl">
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
        <IconButton icon="lyrics" label={lyrics ? 'Скрыть текст' : 'Показать текст'} aria-pressed={lyrics} onClick={toggleLyrics} className={lyrics ? '!text-accent' : 'text-muted'} />
      </div>

      <div className="lg:grid lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)] lg:items-start lg:gap-x-8 lg:px-4">
        {side ? (
          <div className={cx('order-2 min-w-0', side !== 'credits' && 'sticky top-2 h-[calc(100dvh-5.5rem)]')}>
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
            ) : side === 'lyrics' ? text : credits}
          </div>
        ) : (
          lyrics && <div className={cx('min-h-72 px-3 pt-2', full ? 'h-[calc(100dvh-13rem)]' : 'h-[58dvh]')}>{text}</div>
        )}
        <div className="min-w-0">
      <div className={cx('px-6 pt-2', lyrics && !wide && 'hidden')}>
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
        {/* a track that is not in the library yet can be downloaded into it; one that is needs no mark */}
        {owned || dl === 'done' ? null : dl ? (
          <span className="flex h-11 w-11 shrink-0 items-center justify-center text-info" title="Скачивается в библиотеку">
            <Spinner size={18} />
          </span>
        ) : (
          <IconButton icon="download" label="Скачать в библиотеку" onClick={download} busy={busy} />
        )}
        <SaveButton songId={info?.nd_song_id} refId={track.ref} listKey={tagKey({ library_track_id: info?.track_id ?? track.library_track_id })} title={track.title} data={{ ...track }} size={26} />
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

      <div className="flex items-center justify-between px-4 pt-2">
        <TrackMenuButton track={{ ...track, nd_song_id: info?.nd_song_id, library_track_id: info?.track_id ?? track.library_track_id }} inPlayer lean />
        <div className="flex items-center">
          <IconButton icon="share" label="Поделиться" onClick={() => openShare({ title: track.title, artist: track.artist, ref: track.ref, nd_song_id: info?.nd_song_id })} className="text-muted" />
          <IconButton icon="queue" label={queueOpen ? 'Скрыть очередь' : 'Очередь'} aria-pressed={queueOpen} onClick={() => setQueueOpen((v) => !v)} className={queueOpen ? '!text-accent' : 'text-muted'} />
        </div>
      </div>
      {side !== 'credits' && credits}
        </div>
      </div>

      <Sheet open={queueOpen && !wide} onClose={() => setQueueOpen(false)} title="Очередь" footer={<QueueModes onWave={onWave} />}>
        <QueueList queue={queue} index={index} onWave={onWave} shuffle={shuffle} />
      </Sheet>
    </div>
  );
}

/** «Участники»: who plays the track — each a link to their page, with their ♥ (to follow them). */
function Credits({ track }: { track: ListenTrack }) {
  const people = track.artists?.length ? track.artists : [{ name: track.artist, ref: track.artist_ref ?? undefined }];
  return (
    <section aria-label="Участники" className="mx-4 mt-5 rounded-2xl bg-surface px-4 pb-2 pt-3 lg:mx-0">
      <h2 className="pb-1 text-base font-bold">Участники</h2>
      <ul>
        {people.map((a, i) => {
          const who = (
            <>
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-fg/10 text-muted">
                <Icon name="user" size={20} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[15px]">{a.name}</span>
                <span className="block text-[13px] text-muted">Исполнитель</span>
              </span>
            </>
          );
          return (
            <li key={`${a.ref ?? a.name}:${i}`} className="flex min-h-14 items-center gap-1">
              {a.ref ? (
                <Link to={`/artist/${enc(a.ref)}`} className="flex min-w-0 flex-1 items-center gap-3 py-1.5 hover:underline">{who}</Link>
              ) : (
                <div className="flex min-w-0 flex-1 items-center gap-3 py-1.5">{who}</div>
              )}
              {a.ref && <LikeButton targetType="artist" refId={a.ref} title={a.name} data={{ name: a.name }} className="-mr-2" />}
            </li>
          );
        })}
      </ul>
    </section>
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

/** The queue: the track that plays and what comes after it. A tap plays a track, × takes it out, the handle on the
 * right drags it to another place — the order changes as it is dragged. */
function QueueList({ queue, index, onWave, shuffle }: { queue: readonly ListenTrack[]; index: number; onWave: boolean; shuffle: boolean }) {
  const list = useRef<HTMLUListElement>(null);
  const ids = useRef(new WeakMap<ListenTrack, number>());
  const lastId = useRef(0);
  const [dragged, setDragged] = useState<string | null>(null);
  const upcoming = queue.length - index - 1;
  // a row keeps its key as it is moved (the same track may stand in the queue twice: then the keys are counted)
  const seen = new Map<number, number>();
  const keyOf = (t: ListenTrack) => {
    let id = ids.current.get(t);
    if (id === undefined) ids.current.set(t, (id = ++lastId.current));
    const nth = seen.get(id) ?? 0;
    seen.set(id, nth + 1);
    return `${id}:${nth}`;
  };

  const grab = (e: ReactPointerEvent, key: string) => {
    e.preventDefault();
    setDragged(key);
    const move = (ev: PointerEvent) => {
      const ul = list.current;
      if (!ul) return;
      const box = ul.closest('.overflow-y-auto');
      if (box) {
        const r = box.getBoundingClientRect();
        if (ev.clientY < r.top + 56) box.scrollTop -= 14;
        else if (ev.clientY > r.bottom - 56) box.scrollTop += 14;
      }
      const rows = [...ul.querySelectorAll<HTMLElement>('li[data-i]')];
      const own = rows.find((r) => r.dataset.k === key);
      const over = rows.find((r) => {
        const b = r.getBoundingClientRect();
        return ev.clientY >= b.top && ev.clientY < b.bottom;
      });
      if (!own || !over) return;
      const from = Number(own.dataset.i);
      const to = Number(over.dataset.i);
      if (to !== from && to > getListenState().index) moveInQueue(from, to); // never above the track that plays
    };
    const drop = () => {
      setDragged(null);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', drop);
      window.removeEventListener('pointercancel', drop);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', drop);
    window.addEventListener('pointercancel', drop);
  };

  return (
    <ul ref={list} className={cx(dragged && 'select-none')}>
      {queue.map((t, i) => {
        if (i < index) return null;
        const key = keyOf(t);
        return (
          <li key={key} data-i={i} data-k={key} className={cx('flex min-h-14 items-center gap-1 rounded-lg', dragged === key && 'bg-fg/10')}>
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
            {i !== index && (
              <>
                <IconButton icon="x" label="Убрать из очереди" onClick={() => removeFromQueue(i)} size={18} className="w-9 text-muted" />
                <span
                  role="button"
                  aria-label={`Перетащить «${t.title}»`}
                  title="Перетащить"
                  onPointerDown={(e) => grab(e, key)}
                  className="-mr-2 flex h-11 w-9 shrink-0 cursor-grab touch-none items-center justify-center text-muted hover:text-fg active:cursor-grabbing"
                >
                  <Icon name="grip" size={18} />
                </span>
              </>
            )}
          </li>
        );
      }).flatMap((row, i) =>
        // between the track that plays and the rest: what comes next, and how much of it
        i === index
          ? [row, (
              <li key="next" className="flex items-center gap-1.5 pb-1 pt-3 text-xs text-muted">
                {shuffle && !onWave && <Icon name="shuffle" size={13} />}
                {onWave ? 'Далее · подбирается на ходу' : upcoming > 0 ? `${shuffle ? 'Вперемешку' : 'Далее'} · ${tracksWord(upcoming)}` : 'Очередь закончится на этом треке'}
              </li>
            )]
          : [row],
      )}
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
