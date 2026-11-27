import { useEffect, useState } from 'react';
import type { FileOut, ListenOut } from '../api/types';
import { providerLabel } from '../lib/format';
import { Link } from '../lib/router';
import { ServiceBadge, ServiceIcon, ServiceText } from './ServiceBadge';
import { Icon } from './Icon';
import { cx } from './ui';

/** What plays, as a player says it: where the file came from — the mark of its source — then what it is and its
 * bitrate («MP3 · 320 кбит/с»). The Hub tells it with the file it picked (`ListenOut`): an own file says all of it, a
 * file still at its source only the source and, when the source names one, the bitrate. */

const CODECS: Record<string, string> = { mp3: 'MP3', flac: 'FLAC', opus: 'Opus', aac: 'AAC', vorbis: 'OGG', alac: 'ALAC', wav: 'WAV', pcm_s16le: 'WAV' };
const LOSSLESS = new Set(['flac', 'alac', 'wav', 'pcm_s16le']);

/** «MP3 · 320 кбит/с» of the file (empty: the Hub does not know). A lossless file says no bitrate: it tells nothing. */
export function fileWords(info: FileOut | null | undefined, short = false): string {
  if (!info) return '';
  const codec = info.codec ? (CODECS[info.codec.toLowerCase()] ?? info.codec.toUpperCase()) : '';
  const rate = info.bitrate_kbps && !(info.codec && LOSSLESS.has(info.codec.toLowerCase())) ? (short ? String(info.bitrate_kbps) : `${info.bitrate_kbps} кбит/с`) : '';
  return [codec, rate].filter(Boolean).join(short ? ' ' : ' · ');
}

/** In a line of text: the source's mark, then the file's words; yellow when the file sounds like less than it says. */
export function FileInfo({ info, className }: { info: FileOut | null | undefined; className?: string }) {
  const words = fileWords(info, true);
  if (!info || (!info.source && !words)) return null;
  return (
    <span className={cx(className, info.sounds_like_kbps ? '!text-warn' : undefined)} title={[providerLabel(info.source), fileWords(info), info.sounds_like_kbps ? `по звуку около ${info.sounds_like_kbps} кбит/с` : ''].filter(Boolean).join(' · ')}>
      {info.source && <ServiceBadge service={info.source} iconOnly={!!words} size={13} className={words ? "mr-1" : undefined} />}
      {words}
    </span>
  );
}

/** The file's line of a player, whatever the player is doing with the track. Playing: the file (`FileInfo`). Being
 * looked for: a ring that turns and «Ищу файл…». Found and being opened: the source's mark and «YouTube · загрузка…»
 * — in words, not as a bar: what takes the seconds is the source opening the file, and nothing says how far along
 * that is (once it opens the track plays, the rest comes as it plays); a bar would be an ornament. Not playing: the
 * source and what went wrong, in red («YouTube · не отдал файл»), the whole of it under the mouse. The line of the
 * artists above it stays the artists'. */
export function PlayLine({ phase, info, error, className }: { phase: string; info: ListenOut | null | undefined; error?: string | null; className?: string }) {
  if (phase === 'error') {
    const message = error || 'не играет';
    const text = [providerLabel(info?.source), message].filter(Boolean).join(' · ');
    return (
      <span className={cx(className, '!text-danger')} title={text}>
        {info?.source ? <><ServiceBadge service={info.source} /> · </> : <Icon name="alert" size={13} className="mr-1 inline-block align-[-0.15em]" />}
        <ServiceText text={message} />
      </span>
    );
  }
  if (phase === 'resolving' || phase === 'loading') {
    // no source is picked yet: it is being looked for. One is: its file is being opened — «YouTube · загрузка…»
    if (!info?.source) return <Looking className={className} />;
    return (
      <span className={className} role="status">
        <ServiceBadge service={info.source} size={13} /> · загрузка…
      </span>
    );
  }
  if (!info?.source) return null;
  return <FileInfo info={info} className={className} />;
}

/** «Ищу файл…» — only once the looking has lasted (`LOOKING_SHOWN_MS`): a file is found in a fraction of a second
 * as a rule, and a word that flashes up for it at every switch of the track was noise. Until then the line is
 * there, empty, so nothing under it moves. */
const LOOKING_SHOWN_MS = 700;
function Looking({ className }: { className?: string }) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const t = window.setTimeout(() => setShown(true), LOOKING_SHOWN_MS);
    return () => window.clearTimeout(t);
  }, []);
  if (!shown) return <span className={className} aria-hidden="true">{'\u00a0'}</span>;
  return (
    <span className={className} role="status">
      <span className="hm-spin mr-1.5 inline-block h-3 w-3 rounded-full border-[1.5px] border-fg/25 border-t-fg/80 align-[-0.15em]" />
      Ищу файл…
    </span>
  );
}

/** The file's row of the full player's card (`pages/Player.tsx`, under the artists and the album): the source
 * with its mark, the file's words, what the check after the download said of it. The row opens «Как скачан» — the
 * journal of the download that brought the file — when there is one; no arrow, as the rows above it have none. */
export function FileRow({ info }: { info: ListenOut | null | undefined }) {
  if (!info?.source) return null;
  const words = fileWords(info);
  const journal = info.journal && info.track_id ? `/file/${info.track_id}` : null;
  const body = (
    <>
      <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-fg/10">
        <ServiceIcon service={info.source} size={22} />
      </span>
      <span className="min-w-0 flex-1">
        <span className={cx('block truncate text-[15px] font-medium', journal && 'group-hover/file:underline')}>{words || providerLabel(info.source)}</span>
        <span className="block text-[13px] text-muted">
          {words && providerLabel(info.source)}
          {info.kind === 'full' && <>{words && ' · '}играет из источника, на сервер ещё не скачан</>}
        </span>
        {info.sounds_like_kbps ? <span className="block text-[13px] text-warn">По звуку около {info.sounds_like_kbps} кбит/с: похоже, файл сделан из худшего</span> : null}
      </span>
    </>
  );
  const row = 'group/file flex min-h-16 items-center gap-3 py-2';
  return journal
    ? <Link to={journal} className={row} title="Как скачан этот файл">{body}</Link>
    : <div className={row}>{body}</div>;
}
