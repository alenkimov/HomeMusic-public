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
    if (!info?.source)
      return (
        <span className={className} role="status">
          <span className="hm-spin mr-1.5 inline-block h-3 w-3 rounded-full border-[1.5px] border-fg/25 border-t-fg/80 align-[-0.15em]" />
          Ищу файл…
        </span>
      );
    return (
      <span className={className} role="status">
        <ServiceBadge service={info.source} size={13} /> · загрузка…
      </span>
    );
  }
  if (!info?.source) return null;
  return <FileInfo info={info} className={className} />;
}

/** «Файл»: the card of the full player, under «Участники» — the source with its mark, the file's words, and what the
 * check after the download said of it. */
export function FileCard({ info }: { info: ListenOut | null | undefined }) {
  if (!info?.source) return null;
  const words = fileWords(info);
  return (
    <section aria-label="Файл" className="mx-4 mt-3 rounded-2xl bg-surface px-4 pb-3 pt-3">
      <h2 className="pb-2 text-base font-bold">Файл</h2>
      <div className="flex min-h-12 items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-fg/10">
          <ServiceIcon service={info.source} size={20} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[15px]">{words || providerLabel(info.source)}</span>
          <span className="block text-[13px] text-muted">
            {words && providerLabel(info.source)}
            {info.kind === 'full' && <>{words && ' · '}играет из источника, на сервер ещё не скачан</>}
          </span>
        </span>
        {info.page_url && (
          <a href={info.page_url} target="_blank" rel="noopener noreferrer" aria-label="Открыть страницу файла" title="Открыть страницу файла" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted hover:text-fg">
            <Icon name="external" size={18} />
          </a>
        )}
      </div>
      {info.sounds_like_kbps ? <p className="pt-1 text-[13px] text-warn">По звуку около {info.sounds_like_kbps} кбит/с: похоже, файл сделан из худшего.</p> : null}
      {/* the journal of the download that brought the file: where it was looked for, why this one was taken */}
      {info.journal && info.track_id ? (
        <Link to={`/file/${info.track_id}`} className="-mx-2 mt-1 flex min-h-11 items-center gap-2 rounded-lg px-2 text-[15px] hover:bg-fg/5">
          <Icon name="history" size={18} className="text-muted" />
          <span className="flex-1">Как скачан</span>
          <Icon name="chevron" size={18} className="text-dim" />
        </Link>
      ) : null}
    </section>
  );
}
