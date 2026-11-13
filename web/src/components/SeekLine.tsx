import { fmtDuration } from '../lib/format';
import { activeLine, breakAt } from '../lib/lyrics';
import { useTrackLyrics } from '../lib/trackLyrics';
import { cx } from './ui';

/** The line of the text that is sung at the place the listener aims the seek bar at (`lib/seek.ts`): the word, where
 * the text is timed by the word; how long till the singing, where there is none. Nothing for a track whose text is
 * not timed. It stands still — what changes is what it says. Being there, it also has the text asked for as the
 * track starts (`lib/trackLyrics.ts`): nothing is waited for while seeking. */
export function SeekLine({ aim, refId, songId, time, className }: { aim: number | null; refId?: string | null; songId?: string | null; /** the time aimed at is said too */ time?: boolean; className?: string }) {
  const text = useTrackLyrics(refId, songId);
  const lines = text?.lines;
  if (aim === null || !lines?.length) return null;
  const ms = aim * 1000 - (text?.offset_ms ?? 0);
  const at = activeLine(lines, ms);
  const pause = breakAt(lines, at, ms);
  const line = at >= 0 ? lines[at] : null;
  const wait = pause ? pause.left : at < 0 ? lines[0].t - ms : 0;
  return (
    <div aria-hidden="true" className={cx('pointer-events-none rounded-xl border border-line bg-raised px-4 py-2 text-center shadow-2xl', className)}>
      {time && <div className="text-[11px] tabular-nums text-muted">{fmtDuration(aim) || '0:00'}</div>}
      <div className="truncate text-[15px] font-semibold">
        {pause || !line ? (
          <span className="font-normal text-muted">♪ {pause ? 'проигрыш' : 'вступление'}{wait >= 1000 ? `, до текста ${Math.ceil(wait / 1000)} с` : ''}</span>
        ) : line.words?.length ? (
          <Aimed words={line.words} ms={ms} />
        ) : (
          line.text || '♪'
        )}
      </div>
    </div>
  );
}

/** The words of a line, the one aimed at bright. */
function Aimed({ words, ms }: { words: readonly { t: number; text: string }[]; ms: number }) {
  let on = 0;
  for (let i = 0; i < words.length; i++) if (words[i].t <= ms) on = i;
  return (
    <>
      {words.map((w, i) => (
        <span key={i} className={i === on ? undefined : 'text-fg/40'}>{w.text}</span>
      ))}
    </>
  );
}
