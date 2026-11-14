import type { FileOut, ListenOut } from '../api/types';
import { providerLabel } from '../lib/format';
import { Link } from '../lib/router';
import { BrandIcon } from './BrandIcon';
import { Icon } from './Icon';
import { cx } from './ui';

/** What plays, as a player says it: where the file came from — the mark of its source — then what it is and its
 * bitrate («MP3 · 320 кбит/с»). The Hub tells it with the file it picked (`ListenOut`): an own file says all of it, a
 * file still at its source only the source and, when the source names one, the bitrate. */

const CODECS: Record<string, string> = { mp3: 'MP3', flac: 'FLAC', opus: 'Opus', aac: 'AAC', vorbis: 'OGG', alac: 'ALAC', wav: 'WAV', pcm_s16le: 'WAV' };
const LOSSLESS = new Set(['flac', 'alac', 'wav', 'pcm_s16le']);

/** The source's own mark — YouTube's, SoundCloud's, hitmos.fm's (hitmoz is the same site); a file brought by the
 * listener — «upload». */
export function SourceMark({ source, size = 14, className }: { source?: string | null; size?: number; className?: string }) {
  const box = cx('inline-block shrink-0 align-[-0.15em]', className);
  if (source === 'ytdlp' || source === 'yt_reupload') return <BrandIcon service="youtube" size={size} className={box} />;
  if (source === 'soundcloud') return <BrandIcon service="soundcloud" size={size} className={box} />;
  if (source === 'hitmoz' || source === 'hitmos_fm') return <BrandIcon service="hitmos" size={size} className={box} />;
  if (source === 'upload') return <Icon name="upload" size={size} className={box} />;
  return source ? <Icon name="note" size={size} className={box} /> : null;
}

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
      <SourceMark source={info.source} size={13} className="mr-1" />
      {words || providerLabel(info.source)}
      {info.sounds_like_kbps ? `, по звуку ~${info.sounds_like_kbps}` : ''}
    </span>
  );
}

/** The file's line of a player, whatever the player is doing with the track. Playing: the file (`FileInfo`). Being
 * looked for and loaded: a bar that runs — after a spinner while no source is picked yet, after the source's mark
 * once one is (the Hub tells no share of the work done, so the bar says «at work», not how much). Not playing: the
 * source and what went wrong, in red («YouTube · не отдал файл»), the whole of it under the mouse. The line of the
 * artists above it stays the artists'. */
export function PlayLine({ phase, info, error, className }: { phase: string; info: ListenOut | null | undefined; error?: string | null; className?: string }) {
  if (phase === 'error') {
    const from = info?.source ? providerLabel(info.source) : '';
    const text = [from, error || 'не играет'].filter(Boolean).join(' · ');
    return (
      <span className={cx(className, '!text-danger')} title={text}>
        {info?.source ? <SourceMark source={info.source} size={13} className="mr-1" /> : <Icon name="alert" size={13} className="mr-1 inline-block align-[-0.15em]" />}
        {text}
      </span>
    );
  }
  if (phase === 'resolving' || phase === 'loading') {
    const found = !!info?.source;
    return (
      <span className={className} role="status" aria-label={found ? 'Загрузка' : 'Ищу файл'} title={found ? `Загрузка: ${providerLabel(info?.source)}` : 'Ищу файл'}>
        {/* a row of its own inside: the line it stands in may be a block that cuts its text */}
        <span className="inline-flex h-full items-center gap-1.5 align-middle">
          {found ? <SourceMark source={info?.source} size={13} /> : <span className="hm-spin h-3 w-3 shrink-0 rounded-full border-[1.5px] border-fg/25 border-t-fg/80" />}
          <span className="hm-busy" />
        </span>
      </span>
    );
  }
  if (!info?.source || info.kind === 'preview') return null;
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
          <SourceMark source={info.source} size={20} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[15px]">{words || providerLabel(info.source)}</span>
          <span className="block text-[13px] text-muted">
            {providerLabel(info.source)}
            {info.kind === 'full' ? ' · играет из источника, ещё не скачан' : info.kind === 'preview' ? ' · отрывок 30 секунд' : ''}
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
