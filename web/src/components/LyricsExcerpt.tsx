import { useEffect, useMemo, useState, type ReactNode } from 'react';
import type { TidyProposalOut, TrackOut } from '../api/types';
import { activeLine, type LyricsOut } from '../lib/lyrics';
import { fixesOf } from '../lib/lyricsFixes';
import { listen, listenFrom, useListen } from '../lib/listen';
import { askForLyrics } from '../lib/playerQueue';
import { navigate } from '../lib/router';
import { knownLyrics, loadLyrics } from '../lib/trackLyrics';
import { fmtDuration, plural } from '../lib/format';
import { listenTrack } from './catalog';
import { FixedLine } from './Lyrics';
import { Icon } from './Icon';
import { cx } from './ui';

const CONTEXT = 2; // lines shown before and after what changes
const SHOWN = 2; // pieces of the text shown before «Ещё»

type View = 'after' | 'before';

/** What a proposal of «Наведение порядка» does to a song's text, shown on the text itself (`pages/Tidy.tsx`): the
 * lines it changes with two lines around them, as it will be — a line that goes struck through, the words put back
 * lit — or as it was. A line plays the song from it, as a line of the player's text does (an untimed text opens the
 * player with the text). The decision's buttons stand under the text: read first, then decide. */
export function LyricsExcerpt({ p, track, actions }: { p: TidyProposalOut; track: TrackOut; actions?: ReactNode }) {
  const [data, setData] = useState<LyricsOut | null>(() => knownLyrics(track.ref, track.nd_song_id) ?? null);
  const [view, setView] = useState<View>('after');
  const [all, setAll] = useState(false);
  useEffect(() => {
    let alive = true;
    void loadLyrics(track.ref, track.nd_song_id).then((r) => alive && setData(r));
    return () => { alive = false; };
  }, [track.ref, track.nd_song_id]);

  const timed = !!data?.lines?.length;
  const text = useMemo(() => (data?.lines?.length ? data.lines.map((x) => x.text) : (data?.plain ?? '').split('\n')), [data]);
  const fixes = useMemo(() => fixesOf(text, [p]), [text, p]);
  const pieces = useMemo(() => {
    const at = [...fixes.keys()].sort((a, b) => a - b);
    const out: { from: number; to: number; changes: number }[] = [];
    for (const i of at) {
      const last = out[out.length - 1];
      if (last && i - CONTEXT <= last.to + 1) {
        last.to = Math.min(text.length - 1, i + CONTEXT);
        last.changes++;
      } else out.push({ from: Math.max(0, i - CONTEXT), to: Math.min(text.length - 1, i + CONTEXT), changes: 1 });
    }
    return out;
  }, [fixes, text]);

  const current = useListen((s) => (s.track?.ref === track.ref || (track.library_track_id != null && s.track?.library_track_id === track.library_track_id)));
  const position = useListen((s) => s.position);
  const offset = data?.offset_ms ?? 0;
  const playingLine = current && timed ? activeLine(data!.lines!, position * 1000 - offset) : -1;

  const play = (i: number) => {
    const ln = data?.lines?.[i];
    if (ln) return listenFrom(listenTrack(track), Math.max(0, (ln.t + offset) / 1000));
    if (!current) listen(listenTrack(track));
    askForLyrics();
    navigate('/player');
  };

  const n = p.kind === 'lyrics' ? (p.show.drop_at?.length ?? 0) : (p.show.fix_at?.length ?? 0);
  const what = p.kind === 'lyrics' ? p.to : `Вернуть слова в ${n} ${plural(n, ['строке', 'строках', 'строках'])}`;
  const shown = all ? pieces : pieces.slice(0, SHOWN);
  const hidden = pieces.slice(SHOWN).reduce((k, x) => k + x.changes, 0);

  return (
    <div className="space-y-2.5 rounded-xl bg-raised px-3 pb-3 pt-3 sm:px-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="min-w-0 text-[13px]">
          <span className="font-semibold text-ai">{what}</span>
          {p.why && <span className="text-muted"> · {p.why}</span>}
        </span>
        {fixes.size > 0 && (
          <button
            type="button"
            aria-pressed={view === 'after'}
            onClick={() => setView((v) => (v === 'after' ? 'before' : 'after'))}
            title={view === 'after' ? 'Показать текст, каким он был' : 'Показать текст таким, каким он станет после исправления'}
            className={cx('flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] font-semibold', view === 'after' ? 'bg-ai text-bg' : 'bg-ai/15 text-ai hover:bg-ai/25')}
          >
            <Icon name="sparkle" size={13} filled stroke={0} />
            Как будет
          </button>
        )}
      </div>

      {!data ? (
        <div className="space-y-2 px-2 py-1">{[0, 1, 2].map((i) => <div key={i} className="h-5 w-2/3 animate-pulse rounded bg-fg/10" />)}</div>
      ) : fixes.size === 0 ? (
        // the text is not the one the proposal saw (or not read): its own words, as it said them
        <div className="space-y-1 px-2">
          {p.kind === 'lyrics'
            ? (p.show.lines ?? []).map((line, i) => <Gone key={i} text={line} />)
            : (p.show.pairs ?? []).map((x, i) => (
              <div key={i} className="text-[15px] font-bold leading-snug">
                <s className="block text-muted decoration-danger/70">{x.was}</s>
                <span className="block text-ai">{x.to}</span>
              </div>
            ))}
        </div>
      ) : (
        <div>
          {shown.map((piece, k) => (
            <div key={piece.from}>
              {k > 0 && <div className="px-2.5 pb-1 text-lg leading-none tracking-[4px] text-dim" aria-hidden="true">···</div>}
              {Array.from({ length: piece.to - piece.from + 1 }, (_, j) => piece.from + j).map((i) => {
                const fix = fixes.get(i);
                const ln = data.lines?.[i];
                const lit = i === playingLine;
                return (
                  <button key={i} type="button" onClick={() => play(i)}
                    title={ln ? `Слушать с ${fmtDuration((ln.t + offset) / 1000)}` : 'Открыть плеер с текстом'}
                    className="flex w-full items-baseline gap-3 rounded-lg px-2.5 py-1 text-left hover:bg-fg/5">
                    <span className={cx('min-w-0 flex-1 text-[15px] font-bold leading-snug sm:text-[17px]',
                      lit ? 'text-fg' : fix ? 'text-fg/90' : 'text-fg/50')}>
                      {fix && fix.to === null
                        ? (view === 'after' ? <Gone text={text[i]} inline /> : text[i] || '♪')
                        : fix ? <FixedLine text={text[i]} fix={fix} preview={view === 'after'} />
                        : text[i] || '♪'}
                    </span>
                    {ln && <span className={cx('shrink-0 text-xs font-medium tabular-nums', lit ? 'text-accent' : 'text-dim')}>{fmtDuration((ln.t + offset) / 1000)}</span>}
                  </button>
                );
              })}
            </div>
          ))}
          {hidden > 0 && (
            <button type="button" onClick={() => setAll((x) => !x)} className="ml-2 mt-1 rounded-full bg-ai/15 px-3 py-1.5 text-[13px] font-semibold text-ai hover:bg-ai/25">
              {all ? 'Свернуть' : `Ещё ${hidden} ${plural(hidden, ['исправление', 'исправления', 'исправлений'])}`}
            </button>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-end gap-3">
        {fixes.size === 0 && data && (
          <span className="min-w-0 flex-1 basis-52 text-[12px] text-dim">Текст изменился с тех пор, как это предложено</span>
        )}
        {actions}
      </div>
    </div>
  );
}

/** A line that goes: struck through, in red. */
function Gone({ text, inline }: { text: string; inline?: boolean }) {
  return (
    <s className={cx('rounded bg-danger/10 px-1 text-danger decoration-danger/70', inline ? '' : 'block text-[15px] font-bold leading-snug')}>
      {text || '♪'}
    </s>
  );
}
