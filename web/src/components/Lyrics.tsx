import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import { seek, useListen } from '../lib/listen';
import { KIND_LABEL, activeLine, type LyricsCandidate, type LyricsLine, type LyricsOut } from '../lib/lyrics';
import { attempt, toast } from '../lib/ui';
import { Button, IconButton, Loading, Sheet, Spinner, cx } from './ui';

const STEP_MS = 250;
const HOLD_MS = 4000; // after the user scrolls the text themselves it stays where they left it this long

/** The lyrics of the playing track in the full player: the line that is sung is lit and kept in the middle, a
 * click on a line seeks to it; where the source times the words they light up one by one. The text comes from the
 * Hub's database (found on the first open, then at once). Under it: where it is from, a shift of the timings for
 * a text that runs early or late, another source's text, «this track has no lyrics». */
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
  const none = async () => {
    if (!data?.track_id) return;
    const r = await attempt(() => api.lyricsChoose(data.track_id as number, null), 'Запомнил: у этого трека нет текста');
    if (r) setData(r);
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
      {data.kind !== 'none' && (
        <button type="button" onClick={none} className="rounded-full px-3 py-1.5 hover:bg-fg/10 hover:text-fg">
          Убрать текст
        </button>
      )}
    </div>
  );

  return (
    <div className="flex h-full flex-col">
      {data.kind === 'none' ? (
        <Blank text={data.pinned ? 'Для этого трека текст отключён.' : 'Текст не нашёлся ни в одном источнике.'} />
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

function Blank({ text }: { text: string }) {
  return <div className="flex min-h-0 flex-1 items-center justify-center px-6 text-center text-sm text-muted">{text}</div>;
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

/** «Другой вариант»: what each source has for the track. The choice is kept for it. */
function Variants({ trackId, current, onClose, onPicked }: { trackId: number; current?: string | null; onClose: () => void; onPicked: (r: LyricsOut) => void }) {
  const [list, setList] = useState<LyricsCandidate[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api.lyricsCandidates(trackId)
      .then((r) => alive && setList(r))
      .catch(() => {
        if (alive) {
          toast('Не удалось опросить источники', 'error');
          onClose();
        }
      });
    return () => {
      alive = false;
    };
  }, [trackId, onClose]);
  const pick = async (source: string) => {
    setBusy(source);
    const r = await attempt(() => api.lyricsChoose(trackId, source));
    setBusy(null);
    if (r) onPicked(r);
  };
  return (
    <Sheet open onClose={onClose} title="Текст из другого источника">
      {!list ? (
        <Loading text="Опрашиваю источники…" />
      ) : !list.length ? (
        <p className="py-6 text-center text-sm text-muted">Ни в одном источнике текста нет.</p>
      ) : (
        <ul className="space-y-2">
          {list.map((c) => (
            <li key={c.source} className="card p-3">
              <div className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">
                    {c.source_title}
                    {c.source === current && <span className="ml-2 text-xs font-normal text-accent">сейчас</span>}
                  </div>
                  <div className="text-xs text-muted">{KIND_LABEL[c.kind]}</div>
                </div>
                <Button small variant={c.source === current ? undefined : 'primary'} busy={busy === c.source} disabled={!!busy} onClick={() => void pick(c.source)}>
                  Взять
                </Button>
              </div>
              <p className="mt-2 line-clamp-4 whitespace-pre-line text-sm text-muted">{c.preview}</p>
            </li>
          ))}
        </ul>
      )}
    </Sheet>
  );
}
