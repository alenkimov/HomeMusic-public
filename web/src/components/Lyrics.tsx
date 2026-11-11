import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import { seek, useListen } from '../lib/listen';
import { KIND_LABEL, activeLine, type LyricsLine, type LyricsOut } from '../lib/lyrics';
import { attempt, confirmDialog, toast } from '../lib/ui';
import { Icon } from './Icon';
import { Button, IconButton, Loading, Sheet, Spinner, cx } from './ui';

const STEP_MS = 250;
const HOLD_MS = 4000; // after the user scrolls the text themselves it stays where they left it this long

/** The lyrics of the playing track in the full player: the line that is sung is lit and kept in the middle, a
 * click on a line seeks to it; where the source times the words they light up one by one. The text comes from the
 * Hub's database (found on the first open, then at once). Under it: where it is from, a shift of the timings for
 * a text that runs early or late, and «Другой вариант» — another source's text or, there too, «this track has no
 * lyrics» (out of the way: it is not «hide»; the mic button of the player hides). A text switched off comes back
 * with «Вернуть текст». */
export function Lyrics({ refId, songId }: { refId: string; songId?: string | null }) {
  const [data, setData] = useState<LyricsOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [picking, setPicking] = useState(false);
  const position = useListen((s) => s.position);
  const playing = useListen((s) => s.phase === 'playing');
  const [ms, setMs] = useState(0);
  const clock = useRef({ pos: 0, at: 0 });
  const box = useRef<HTMLDivElement>(null);
  const held = useRef(0);
  const saver = useRef<number | undefined>(undefined);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setData(null);
    api.lyrics({ nd_song_id: songId ?? null, ref: refId })
      .then((r) => alive && setData(r))
      .catch(() => alive && setData(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [refId, songId]);

  // the player reports the position a few times a second: between the reports the clock runs on its own
  useEffect(() => {
    clock.current = { pos: position, at: performance.now() };
    setMs(position * 1000);
  }, [position]);
  const timed = !!data?.lines?.length;
  const wordy = data?.kind === 'word';
  useEffect(() => {
    if (!playing || !timed) return;
    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      // words need every frame, lines only a few updates a second
      if (wordy || now - last > 120) {
        last = now;
        setMs((clock.current.pos + (now - clock.current.at) / 1000) * 1000);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, timed, wordy]);

  const lines = useMemo<LyricsLine[]>(() => data?.lines ?? [], [data]);
  const offset = data?.offset_ms ?? 0;
  const at = timed ? activeLine(lines, ms - offset) : -1;

  useEffect(() => {
    if (at < 0 || performance.now() < held.current) return;
    box.current?.querySelector<HTMLElement>(`[data-line="${at}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [at]);

  const shift = (by: number) => {
    if (!data?.track_id) return;
    const next = Math.max(-15000, Math.min(15000, by === 0 ? 0 : offset + by));
    setData({ ...data, offset_ms: next });
    window.clearTimeout(saver.current);
    const id = data.track_id;
    saver.current = window.setTimeout(() => void api.lyricsOffset(id, next).catch(() => undefined), 600);
  };
  const [restoring, setRestoring] = useState(false);
  const restore = async () => {
    if (!data?.track_id) return;
    setRestoring(true);
    const r = await attempt(() => api.lyricsReset(data.track_id as number));
    setRestoring(false);
    if (r) {
      setData(r);
      if (r.kind === 'none') toast('Источники сейчас не дали текст — попробуйте позже', 'info');
    }
  };

  if (loading)
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-muted">
        <Spinner />
        <span className="text-sm">Ищу текст…</span>
      </div>
    );
  if (!data || data.track_id === null)
    return <Blank text="Текст показывается для треков из библиотеки. Скачайте трек — и он появится." />;

  const footer = (
    <div className="flex shrink-0 flex-wrap items-center justify-center gap-x-1 gap-y-1 pt-2 text-xs text-muted">
      {data.kind !== 'none' && <span className="px-2">{data.source_title} · {KIND_LABEL[data.kind]}</span>}
      {timed && (
        <span className="flex items-center" title="Если текст спешит или отстаёт">
          <IconButton icon="minus" size={16} label="Текст раньше на четверть секунды" onClick={() => shift(-STEP_MS)} className="!h-8 !w-8 text-muted" />
          <button type="button" onClick={() => shift(0)} className="min-w-14 text-center tabular-nums hover:text-fg" title="Сбросить сдвиг">
            {offset ? `${offset > 0 ? '+' : '−'}${(Math.abs(offset) / 1000).toFixed(2)} с` : 'сдвиг'}
          </button>
          <IconButton icon="plus" size={16} label="Текст позже на четверть секунды" onClick={() => shift(STEP_MS)} className="!h-8 !w-8 text-muted" />
        </span>
      )}
      <button type="button" onClick={() => setPicking(true)} className="rounded-full px-3 py-1.5 hover:bg-fg/10 hover:text-fg">
        Другой вариант
      </button>
    </div>
  );

  return (
    <div className="flex h-full flex-col">
      {data.kind === 'none' ? (
        <Blank text={data.pinned ? 'Вы отметили, что у этого трека нет текста.' : 'Текст не нашёлся ни в одном источнике.'}>
          <Button small busy={restoring} onClick={() => void restore()}>
            {data.pinned ? 'Вернуть текст' : 'Поискать ещё раз'}
          </Button>
        </Blank>
      ) : (
        <div
          ref={box}
          onWheel={() => (held.current = performance.now() + HOLD_MS)}
          onTouchMove={() => (held.current = performance.now() + HOLD_MS)}
          className="no-scrollbar min-h-0 flex-1 overflow-y-auto px-1 py-[20%] [mask-image:linear-gradient(transparent,black_18%,black_82%,transparent)]"
        >
          {timed ? (
            lines.map((ln, i) => (
              <button
                key={i}
                type="button"
                data-line={i}
                onClick={() => {
                  held.current = 0;
                  seek(Math.max(0, (ln.t + offset) / 1000));
                }}
                className={cx(
                  'block w-full origin-left rounded-lg px-3 py-1.5 text-left text-2xl font-bold leading-snug transition-[color,opacity,transform] duration-300 hover:bg-fg/5',
                  i === at ? 'scale-100 text-fg' : 'scale-[0.97] text-fg/40 hover:text-fg/70',
                )}
              >
                {i === at && ln.words ? <Words line={ln} ms={ms - offset} /> : ln.text || '♪'}
              </button>
            ))
          ) : (
            <p className="whitespace-pre-line px-3 text-lg leading-relaxed text-fg/85">{data.plain}</p>
          )}
        </div>
      )}
      {footer}
      {picking && data.track_id !== null && (
        <Variants
          trackId={data.track_id}
          current={data.source}
          onClose={() => setPicking(false)}
          onPicked={(r) => {
            setData(r);
            setPicking(false);
          }}
        />
      )}
    </div>
  );
}

function Blank({ text, children }: { text: string; children?: ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-6 text-center text-sm text-muted">
      {text}
      {children}
    </div>
  );
}

/** The sung line by its words: what has been sung is lit, the rest waits. */
function Words({ line, ms }: { line: LyricsLine; ms: number }) {
  return (
    <>
      {(line.words ?? []).map((w, i) => (
        <span key={i} className={cx('transition-colors duration-150', w.t <= ms ? 'text-fg' : 'text-fg/40')}>
          {w.text}
        </span>
      ))}
    </>
  );
}

type Probe = Awaited<ReturnType<typeof api.lyricsProbe>>;
interface Asked {
  id: string;
  title: string;
  /** when the source was asked (a running clock is shown until it answers) */
  since: number;
  answer: Probe | null;
}

const SOURCE_HINT: Record<string, string> = { file: 'теги и .lrc рядом с файлом', lrclib: 'lrclib.net', netease: 'music.163.com', ovh: 'api.lyrics.ovh' };
const secs = (n: number) => `${n.toFixed(1).replace('.', ',')} с`;

/** «Другой вариант»: every source is asked at once, each on its own line — its clock runs while it looks, then what
 * it has (to take at once, without waiting for the slow ones), «no text» or «did not answer» with «Ещё раз». The
 * choice is kept for the track. The last line, apart: «this track has no lyrics». */
function Variants({ trackId, current, onClose, onPicked }: { trackId: number; current?: string | null; onClose: () => void; onPicked: (r: LyricsOut) => void }) {
  const [rows, setRows] = useState<Asked[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [, setTick] = useState(0);
  const alive = useRef(true);
  // the player redraws the text many times a second, and `onClose` is a new function each time: the sources are
  // asked once per opening, not per redraw
  const close = useRef(onClose);
  close.current = onClose;

  const ask = (id: string) => {
    setRows((list) => list?.map((r) => (r.id === id ? { ...r, since: performance.now(), answer: null } : r)) ?? list);
    const put = (answer: Probe) => alive.current && setRows((list) => list?.map((r) => (r.id === id ? { ...r, answer } : r)) ?? list);
    const started = performance.now();
    api.lyricsProbe(trackId, id)
      .then(put)
      .catch(() => put({ source: id, source_title: '', status: 'error', seconds: (performance.now() - started) / 1000 }));
  };
  useEffect(() => {
    alive.current = true;
    api.lyricsSources()
      .then((list) => {
        if (!alive.current) return;
        setRows(list.map((x) => ({ id: x.id, title: x.title, since: performance.now(), answer: null })));
        list.forEach((x) => ask(x.id));
      })
      .catch(() => {
        if (alive.current) {
          toast('Не удалось получить список источников', 'error');
          close.current();
        }
      });
    return () => {
      alive.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trackId]);

  const waiting = rows?.filter((r) => !r.answer).length ?? 0;
  useEffect(() => {
    if (!waiting) return;
    const t = window.setInterval(() => setTick((n) => n + 1), 100);
    return () => window.clearInterval(t);
  }, [waiting]);

  const pick = async (source: string) => {
    setBusy(source);
    const r = await attempt(() => api.lyricsChoose(trackId, source));
    setBusy(null);
    if (r) onPicked(r);
  };
  const none = async () => {
    if (!(await confirmDialog({ title: 'У этого трека нет текста?', body: 'Текст перестанет показываться для этого трека — у всех, кто его слушает. Вернуть можно будет кнопкой «Вернуть текст».', okLabel: 'Не показывать текст', danger: true }))) return;
    setBusy('none');
    const r = await attempt(() => api.lyricsChoose(trackId, null));
    setBusy(null);
    if (r) onPicked(r);
  };
  const found = rows?.filter((r) => r.answer?.status === 'found').length ?? 0;

  return (
    <Sheet open onClose={onClose} title="Текст из другого источника">
      {!rows ? (
        <Loading />
      ) : (
        <div className="-mx-4 -my-3">
          <p className="px-4 pb-1 pt-3 text-xs text-muted" aria-live="polite">
            {waiting ? `Опрашиваю одновременно · осталось ${waiting} из ${rows.length}` : found ? `Готово: текст есть в ${found} из ${rows.length}` : 'Готово: текста нет ни в одном источнике'}
          </p>
          <ul>
            {rows.map((r) => {
              const a = r.answer;
              return (
                <li key={r.id} className="flex items-start gap-3 border-t border-line/60 px-4 py-3 first:border-t-0">
                  <span className="flex h-6 w-5 shrink-0 items-center justify-center">
                    {!a ? <Spinner size={16} /> : <Icon name={a.status === 'found' ? 'check' : a.status === 'none' ? 'minus' : 'alert'} size={18} className={a.status === 'found' ? 'text-accent' : a.status === 'none' ? 'text-dim' : 'text-warn'} />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="text-sm font-semibold">{r.title}</span>
                      {r.id === current && <span className="text-xs text-accent">сейчас</span>}
                      <span className="text-xs tabular-nums text-muted">
                        {!a
                          ? `ищу… ${secs((performance.now() - r.since) / 1000)}`
                          : a.status === 'found'
                            ? `${a.kind ? KIND_LABEL[a.kind] : ''} · ${secs(a.seconds)}`
                            : a.status === 'none'
                              ? `текста нет · ${secs(a.seconds)}`
                              : a.status === 'timeout'
                                ? `не ответил за ${Math.round(a.seconds)} с`
                                : `ошибка · ${secs(a.seconds)}`}
                      </span>
                    </div>
                    {a?.status === 'found' && a.preview ? (
                      <p className="mt-1 line-clamp-3 whitespace-pre-line text-xs text-muted">{a.preview}</p>
                    ) : (
                      !a && <p className="text-xs text-dim">{SOURCE_HINT[r.id] ?? ''}</p>
                    )}
                  </div>
                  {a?.status === 'found' && (
                    <Button small variant={r.id === current ? undefined : 'primary'} busy={busy === r.id} disabled={!!busy} onClick={() => void pick(r.id)}>
                      Взять
                    </Button>
                  )}
                  {(a?.status === 'timeout' || a?.status === 'error') && (
                    <Button small disabled={!!busy} onClick={() => ask(r.id)}>
                      Ещё раз
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
          {current && (
            <button type="button" disabled={!!busy} onClick={() => void none()} className="block w-full border-t border-line/60 px-4 py-3 text-center text-xs text-dim hover:bg-fg/5 hover:text-muted">
              Это не тот текст, а другого нет — не показывать текст для этого трека
            </button>
          )}
        </div>
      )}
    </Sheet>
  );
}
