import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, errorMessage } from '../api/client';
import { seek, useListen } from '../lib/listen';
import { KIND_LABEL, activeLine, breakAt, setLyricsLook, setTranslatePrefs, useLyricsLook, useTranslatePrefs, type LyricsLine, type LyricsLook, type LyricsOut, type LyricsReading, type LyricsTranslation, type LyricsTranslators, type TranslatePrefs } from '../lib/lyrics';
import { attempt, confirmDialog, toast } from '../lib/ui';
import { Icon } from './Icon';
import { Button, Checkbox, IconButton, Loading, Sheet, Spinner, cx } from './ui';

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
  // the translation: asked for when it is on, for this text, in the listener's language (lib/lyrics.ts)
  const prefs = useTranslatePrefs();
  const [tr, setTr] = useState<LyricsTranslation | null>(null);
  const [trSince, setTrSince] = useState<number | null>(null);
  const [trError, setTrError] = useState<string | null>(null);
  const [forced, setForced] = useState<number | null>(null); // «translate anyway» of this track
  const [trOpen, setTrOpen] = useState(false);
  const look = useLyricsLook();
  const [lookOpen, setLookOpen] = useState(false);
  const [holding, setHolding] = useState(false); // the listener is scrolling the text themselves
  const [, setTick] = useState(0);
  const position = useListen((s) => s.position);
  const playing = useListen((s) => s.phase === 'playing');
  const [ms, setMs] = useState(0);
  const clock = useRef({ pos: 0, at: 0 });
  const box = useRef<HTMLDivElement>(null);
  const held = useRef(0);
  const holdTimer = useRef<number | undefined>(undefined);
  const saver = useRef<number | undefined>(undefined);
  // the text stays where the listener scrolled it for a while, sharp (no blur), then follows the song again
  const hold = () => {
    held.current = performance.now() + HOLD_MS;
    setHolding(true);
    window.clearTimeout(holdTimer.current);
    holdTimer.current = window.setTimeout(() => setHolding(false), HOLD_MS);
  };
  useEffect(() => () => window.clearTimeout(holdTimer.current), []);

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
  const pause = timed ? breakAt(lines, at, ms - offset) : null;
  const reading = data?.reading ?? null;
  const latin = look.reading ? reading?.lines : undefined;
  const ruby = look.furigana ? reading?.ruby : undefined;

  useEffect(() => {
    if (at < 0 || performance.now() < held.current) return;
    // only the text's own box is scrolled: `scrollIntoView` would move the whole page too, once the last lines cannot
    // be centred inside the box
    const el = box.current?.querySelector<HTMLElement>(`[data-line="${at}"]`);
    if (!box.current || !el) return;
    const frame = box.current.getBoundingClientRect();
    const line = el.getBoundingClientRect();
    box.current.scrollTo({ top: box.current.scrollTop + line.top - frame.top - (frame.height - line.height) / 2, behavior: 'smooth' });
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

  const trackId = data?.track_id ?? null;
  const textKey = data ? `${data.kind}:${data.source ?? ''}:${data.lines?.length ?? data.plain?.length ?? 0}` : '';
  const skipKey = prefs.skip.join(',');
  useEffect(() => {
    setTr(null);
    setTrError(null);
    if (!prefs.on || trackId === null || !textKey || textKey.startsWith('none')) return void setTrSince(null);
    let alive = true;
    setTrSince(performance.now());
    api.lyricsTranslation(trackId, prefs.lang, skipKey ? skipKey.split(',') : [], forced === trackId)
      .then((r) => alive && setTr(r))
      .catch((e) => alive && setTrError(errorMessage(e)))
      .finally(() => alive && setTrSince(null));
    return () => {
      alive = false;
    };
  }, [prefs.on, prefs.lang, skipKey, trackId, textKey, forced]);
  useEffect(() => {
    if (trSince === null) return;
    const t = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [trSince]);
  const tl = tr?.status === 'ready' ? tr.lines : null;
  const replace = prefs.mode === 'replace';

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
      {data.kind !== 'none' && (
        <button
          type="button"
          onClick={() => setTrOpen(true)}
          title={trError ?? 'Перевод текста'}
          className={cx('flex items-center gap-1.5 rounded-full px-3 py-1.5 hover:bg-fg/10 hover:text-fg', prefs.on && !trError && 'text-accent', trError && 'text-warn')}
        >
          {trSince !== null && <Spinner size={12} />}
          {trSince !== null ? `Перевожу… ${Math.round((performance.now() - trSince) / 1000)} с` : trError ? 'Перевод не удался' : prefs.on ? `Перевод: ${prefs.lang.toUpperCase()}` : 'Перевод'}
        </button>
      )}
      {data.kind !== 'none' && (
        <IconButton icon="sliders" size={16} label="Вид текста" onClick={() => setLookOpen(true)} className="!h-8 !w-8 text-muted hover:text-fg" />
      )}
      <button type="button" onClick={() => setPicking(true)} className="rounded-full px-3 py-1.5 hover:bg-fg/10 hover:text-fg">
        Другой вариант
      </button>
    </div>
  );
  const far = (i: number) => (look.blur && !holding && at >= 0 && i !== at ? { filter: `blur(${Math.min(3, Math.abs(i - at)) * 1.1}px)` } : undefined);

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
          onWheel={hold}
          onTouchMove={hold}
          className="no-scrollbar min-h-0 flex-1 overflow-y-auto px-1 py-[20%] [mask-image:linear-gradient(transparent,black_18%,black_82%,transparent)]"
        >
          {timed ? (
            <>
              {pause && at < 0 && <Break left={pause.left} />}
              {lines.map((ln, i) => (
                <div key={i}>
                  <button
                    type="button"
                    data-line={i}
                    onClick={() => {
                      held.current = 0;
                      setHolding(false);
                      seek(Math.max(0, (ln.t + offset) / 1000));
                    }}
                    style={far(i)}
                    className={cx(
                      'block w-full origin-left rounded-lg px-3 py-1.5 text-left text-2xl font-bold leading-snug transition-[color,opacity,transform] duration-300 hover:bg-fg/5',
                      i === at && !pause ? 'scale-100 text-fg' : 'scale-[0.97] text-fg/40 hover:text-fg/70',
                    )}
                  >
                    {replace && tl?.[i] ? tl[i] : i === at && ln.words && !pause ? <Words line={ln} ms={ms - offset} /> : ruby?.[i]?.length ? <Ruby pieces={ruby[i]} /> : ln.text || '♪'}
                    {latin?.[i] && <span className="mt-0.5 block text-sm font-medium leading-snug tracking-wide text-fg/55">{latin[i]}</span>}
                    {!replace && tl?.[i] && <span className="mt-0.5 block text-base font-semibold leading-snug text-fg/60">{tl[i]}</span>}
                  </button>
                  {pause && i === at && <Break left={pause.left} />}
                </div>
              ))}
            </>
          ) : (
            tl ? (
              <div className="px-3 text-lg leading-relaxed text-fg/85">
                {(data.plain ?? '').split('\n').map((line, i) => (
                  <p key={i} className="min-h-[1.2em] pb-1">
                    {replace && tl[i] ? tl[i] : ruby?.[i]?.length ? <Ruby pieces={ruby[i]} /> : line}
                    {latin?.[i] && <span className="block text-sm leading-snug tracking-wide text-muted">{latin[i]}</span>}
                    {!replace && tl[i] && <span className="block text-base leading-snug text-muted">{tl[i]}</span>}
                  </p>
                ))}
              </div>
            ) : latin || ruby ? (
              <div className="px-3 text-lg leading-relaxed text-fg/85">
                {(data.plain ?? '').split('\n').map((line, i) => (
                  <p key={i} className="min-h-[1.2em] pb-1">
                    {ruby?.[i]?.length ? <Ruby pieces={ruby[i]} /> : line}
                    {latin?.[i] && <span className="block text-sm leading-snug tracking-wide text-muted">{latin[i]}</span>}
                  </p>
                ))}
              </div>
            ) : (
              <p className="whitespace-pre-line px-3 text-lg leading-relaxed text-fg/85">{data.plain}</p>
            )
          )}
        </div>
      )}
      {footer}
      {trOpen && data.track_id !== null && (
        <TranslateSheet
          trackId={data.track_id}
          prefs={prefs}
          tr={tr}
          busy={trSince !== null}
          error={trError}
          original={timed ? lines.map((x) => x.text) : (data.plain ?? '').split('\n')}
          onForce={() => setForced(data.track_id)}
          onChanged={(r) => {
            setTrError(null);
            setTr(r);
          }}
          onClose={() => setTrOpen(false)}
        />
      )}
      {lookOpen && <LookSheet look={look} reading={reading} timed={timed} onClose={() => setLookOpen(false)} />}
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

/** A break in the singing: how long until the next line. */
function Break({ left }: { left: number }) {
  return (
    <div className="flex items-center gap-3 px-3 py-2 text-fg/70" aria-label="Проигрыш">
      <span className="flex gap-1.5" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span key={i} className="h-2 w-2 animate-pulse rounded-full bg-current" style={{ animationDelay: `${i * 220}ms` }} />
        ))}
      </span>
      <span className="text-sm tabular-nums">{Math.max(1, Math.ceil(left / 1000))} с</span>
    </div>
  );
}

/** A Japanese line with the kana over its kanji. */
function Ruby({ pieces }: { pieces: [string, string | null][] }) {
  return (
    <>
      {pieces.map(([text, kana], i) =>
        kana ? (
          <ruby key={i}>
            {text}
            <rt className="text-[0.45em] font-medium tracking-tight opacity-80">{kana}</rt>
          </ruby>
        ) : (
          <span key={i}>{text}</span>
        ),
      )}
    </>
  );
}

const READING_NAME: Record<string, string> = { ja: 'японский', zh: 'китайский', ko: 'корейский' };

/** «Вид текста»: things to try on and take off — how the text is read, the blur of the lines that are not sung, the
 * page's light, the whole screen. Kept in the browser; each is off until switched on. */
function LookSheet({ look, reading, timed, onClose }: { look: LyricsLook; reading: LyricsReading | null; timed: boolean; onClose: () => void }) {
  const row = 'flex items-center justify-between gap-3 py-2 text-sm';
  const hint = 'block text-xs text-muted';
  return (
    <Sheet open onClose={onClose} title="Вид текста">
      <div className="space-y-1">
        <label className={cx(row, 'cursor-pointer')}>
          <span>
            Произношение латиницей
            <span className={hint}>{reading ? `Под каждой строкой: этот текст — ${READING_NAME[reading.lang] ?? reading.lang}` : 'Для японских, китайских и корейских текстов; у этого его нет'}</span>
          </span>
          <Checkbox checked={look.reading} label="Произношение латиницей" onChange={(reading) => setLyricsLook({ reading })} />
        </label>
        <label className={cx(row, 'cursor-pointer')}>
          <span>
            Фуригана
            <span className={hint}>Чтение над иероглифами в японских текстах{reading?.lang === 'ja' ? '' : ' — этот текст не японский'}</span>
          </span>
          <Checkbox checked={look.furigana} label="Фуригана" onChange={(furigana) => setLyricsLook({ furigana })} />
        </label>
        <label className={cx(row, 'cursor-pointer')}>
          <span>
            Размытие соседних строк
            <span className={hint}>{timed ? 'Чем дальше строка от той, что звучит, тем сильнее; пока листаете — резко' : 'Работает в текстах с таймингами'}</span>
          </span>
          <Checkbox checked={look.blur} label="Размытие соседних строк" onChange={(blur) => setLyricsLook({ blur })} />
        </label>
      </div>
    </Sheet>
  );
}

const LANG_FALLBACK = [{ code: 'ru', title: 'Русский' }, { code: 'en', title: 'English' }];

/** «Перевод»: on or off, the language, under the line or in its place, the languages the listener reads (songs in
 * them are left alone); for a translation that is made — who made it, «again» with another translator, and the
 * lines to correct by hand. The translation itself is the Hub's: made once per language, kept for everyone. */
function TranslateSheet({ trackId, prefs, tr, busy, error, original, onForce, onChanged, onClose }: {
  trackId: number;
  prefs: TranslatePrefs;
  tr: LyricsTranslation | null;
  busy: boolean;
  error: string | null;
  original: string[];
  onForce: () => void;
  onChanged: (r: LyricsTranslation) => void;
  onClose: () => void;
}) {
  const [info, setInfo] = useState<LyricsTranslators | null>(null);
  const [redoing, setRedoing] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    let alive = true;
    api.lyricsTranslators().then((r) => alive && setInfo(r)).catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  const langs = info?.langs ?? LANG_FALLBACK;
  const name = (code?: string | null) => langs.find((l) => l.code === code)?.title ?? code ?? '';
  const redo = async (provider: string) => {
    setRedoing(provider);
    const r = await attempt(() => api.lyricsRetranslate(trackId, prefs.lang, provider));
    setRedoing(null);
    if (r) onChanged(r);
  };
  const saveLine = async (index: number, text: string) => {
    if (!tr || text.trim() === (tr.lines[index] ?? '')) return;
    const r = await attempt(() => api.lyricsTranslationLine(trackId, prefs.lang, index, text));
    if (r) onChanged(r);
  };
  const row = 'flex items-center justify-between gap-3 py-2 text-sm';

  return (
    <Sheet open onClose={onClose} title="Перевод текста">
      <div className="space-y-1">
        <label className={cx(row, 'cursor-pointer')}>
          <span>Показывать перевод</span>
          <Checkbox checked={prefs.on} label="Показывать перевод" onChange={(on) => setTranslatePrefs({ on })} />
        </label>
        <label className={row}>
          <span>Переводить на</span>
          <select className="input !w-44" value={prefs.lang} onChange={(e) => setTranslatePrefs({ lang: e.target.value, skip: prefs.skip.filter((x) => x !== e.target.value) })} aria-label="Язык перевода">
            {langs.map((l) => (
              <option key={l.code} value={l.code}>{l.title}</option>
            ))}
          </select>
        </label>
        <div className={row}>
          <span>Где показывать</span>
          <span className="flex rounded-full bg-fg/10 p-0.5 text-xs">
            {(['below', 'replace'] as const).map((m) => (
              <button key={m} type="button" onClick={() => setTranslatePrefs({ mode: m })} aria-pressed={prefs.mode === m} className={cx('rounded-full px-3 py-1.5', prefs.mode === m ? 'bg-fg text-bg' : 'text-muted')}>
                {m === 'below' ? 'Под строкой' : 'Вместо оригинала'}
              </button>
            ))}
          </span>
        </div>

        {prefs.on && (
          <div className="rounded-xl bg-fg/5 px-3 py-2 text-sm" aria-live="polite">
            {busy ? (
              <span className="flex items-center gap-2 text-muted"><Spinner size={14} /> Перевожу — локальной модели на песню нужно до пары минут…</span>
            ) : error ? (
              <span className="text-warn">{error}</span>
            ) : tr?.status === 'ready' ? (
              <span className="text-muted">
                Перевёл: {tr.provider_title ?? tr.provider}
                {tr.detected ? ` · с языка: ${name(tr.detected)}` : ''}
                {tr.edited.length ? ` · исправлено строк: ${tr.edited.length}` : ''}
              </span>
            ) : tr?.status === 'same' || tr?.status === 'skipped' ? (
              <span className="flex flex-wrap items-center justify-between gap-2 text-muted">
                {tr.status === 'same' ? `Текст уже на этом языке (${name(tr.detected)}).` : `Язык песни — ${name(tr.detected)}: он в списке «не переводить».`}
                <Button small onClick={onForce}>Перевести всё равно</Button>
              </span>
            ) : (
              <span className="text-muted">У этого трека нет текста.</span>
            )}
          </div>
        )}

        <div className="pt-2">
          <div className="pb-1.5 text-xs text-muted">Не переводить песни на языках, которые я читаю</div>
          <div className="flex flex-wrap gap-1.5">
            {langs.filter((l) => l.code !== prefs.lang).map((l) => {
              const on = prefs.skip.includes(l.code);
              return (
                <button key={l.code} type="button" aria-pressed={on} onClick={() => setTranslatePrefs({ skip: on ? prefs.skip.filter((x) => x !== l.code) : [...prefs.skip, l.code] })} className={cx('rounded-full px-3 py-1 text-xs', on ? 'bg-accent text-on-accent' : 'bg-fg/10 text-muted hover:text-fg')}>
                  {l.title}
                </button>
              );
            })}
          </div>
          <p className="pt-1.5 text-[11px] text-dim">Язык песни Хаб определяет по буквам: японский, корейский, китайский, русский, украинский и английский.</p>
        </div>

        {prefs.on && tr?.status === 'ready' && (
          <div className="space-y-2 border-t border-line/60 pt-3">
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              Перевести заново:
              {(info?.providers ?? []).filter((x) => x.on && (x.id !== 'llm' || info?.llm)).map((x) => (
                <Button key={x.id} small busy={redoing === x.id} disabled={!!redoing} onClick={() => void redo(x.id)}>
                  {x.title}
                </Button>
              ))}
              <Button small icon="edit" onClick={() => setEditing((v) => !v)}>
                {editing ? 'Скрыть правку' : 'Править строки'}
              </Button>
            </div>
            {editing && (
              <ul className="max-h-[45dvh] space-y-2 overflow-y-auto pr-1">
                {original.map((src, i) =>
                  src.trim() ? (
                    <li key={`${i}:${tr.lines[i] ?? ''}`}>
                      <div className="truncate text-xs text-muted">{src}</div>
                      <input className={cx('input mt-0.5', tr.edited.includes(i) && '!border-accent')} defaultValue={tr.lines[i] ?? ''} aria-label={`Перевод строки: ${src}`} onBlur={(e) => void saveLine(i, e.target.value)} />
                    </li>
                  ) : null,
                )}
              </ul>
            )}
            {editing && <p className="text-[11px] text-dim">Строка сохраняется, когда вы уходите из поля. Правки видят все и они переживают новый перевод.</p>}
          </div>
        )}
      </div>
    </Sheet>
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

const SOURCE_HINT: Record<string, string> = { file: 'теги и .lrc рядом с файлом', lrclib: 'lrclib.net', lrcred: 'lrc.red — по коду записи', lyricsplus: 'lyricsplus — по коду записи или названию', netease: 'music.163.com', ovh: 'api.lyrics.ovh' };
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
