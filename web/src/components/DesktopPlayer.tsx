import { askForQueue } from '../lib/playerQueue';
import { openTagsSheet, tagKey } from '../lib/tags';
import type { CSSProperties } from 'react';
import { fmtDuration } from '../lib/format';
import { artistTo, creditedOf, cycleRepeat, cycleShuffle, hasNext, hasPrev, next, prev, setVolume, toggle, useListen } from '../lib/listen';
import { useSeek } from '../lib/seek';
import { Link, enc } from '../lib/router';
import { openShare } from '../lib/shareTrack';
import { PlayLine } from './FileInfo';
import { Icon } from './Icon';
import { SaveButton } from './PlaylistsButton';
import { SeekLine } from './SeekLine';
import { TrackMenuButton } from './TrackMenu';
import { Cover, IconButton, Marquee, Spinner, cx } from './ui';

/** The player of a computer: a bar along the bottom of the window (where a phone has its tab bar), always there —
 * empty and inactive until something plays. On the left the track (a click opens the full player with the queue)
 * and ♥, in the middle shuffle, ⏮ ▶ ⏭, repeat and the seek bar, on the right the queue and the volume. Its height is
 * `--tabbar-h`: the pages and their bottom bars keep clear of it as they do of the tab bar. */
export function DesktopPlayer() {
  const track = useListen((s) => s.track);
  const phase = useListen((s) => s.phase);
  const info = useListen((s) => s.info);
  const error = useListen((s) => s.error);
  const position = useListen((s) => s.position);
  const duration = useListen((s) => s.duration);
  const volume = useListen((s) => s.volume);
  const shuffle = useListen((s) => s.shuffle);
  const smart = useListen((s) => s.smart);
  const repeat = useListen((s) => s.repeat);
  const onWave = useListen((s) => s.context.startsWith('wave:'));
  const canNext = useListen((s) => hasNext(s) || (s.repeat === 'all' && !s.context.startsWith('wave:')));
  const canPrev = useListen(hasPrev);
  const { shownPos, aim, aimPct, range } = useSeek(position, duration);

  const waiting = phase === 'resolving' || phase === 'loading';
  const playing = phase === 'playing' || waiting;

  const trackTags = track ? tagKey({ library_track_id: info?.track_id ?? track.library_track_id }) : null;
  return (
    <div className="pb-safe fixed inset-x-0 bottom-0 z-40 bg-bg" role="region" aria-label="Плеер">
      <SeekLine aim={aim} refId={track?.ref} songId={info?.nd_song_id ?? track?.nd_song_id} className="absolute bottom-full left-1/2 mb-3 w-[min(90vw,40rem)] -translate-x-1/2" />
      <div className="grid h-(--tabbar-h) grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_minmax(0,1fr)] items-center gap-4 px-4">
        <div className="flex min-w-0 items-center gap-1">
          {track ? (
            <>
              <Link to="/player" title="Открыть плеер и очередь" className="shrink-0">
                <Cover src={track.cover_small_url || track.cover_url} size={52} alt="" />
              </Link>
              {/* the name leads to the album, each artist to their page; a long line runs under the mouse (`Marquee`) */}
              <span className="ml-2 min-w-0">
                <Marquee className="text-sm font-medium leading-5">
                  <Link to={track.album_ref ? `/album/${enc(track.album_ref)}` : '/player'} className="hover:underline">
                    {track.title}
                  </Link>
                </Marquee>
                <span className="flex items-center gap-1.5 text-xs text-muted">
                  {onWave && <Icon name="wave" size={13} className="shrink-0 text-accent" />}
                  {info?.kind === 'preview' && <span className="shrink-0 rounded bg-warn/15 px-1 text-[11px] font-semibold text-warn">превью 30 сек</span>}
                  {/* the artists, always: what the player is doing is said by the line under them */}
                  <Marquee>
                    {creditedOf(track).map((a, i) => (
                      <span key={`${a.name}:${i}`}>
                        {i > 0 && ', '}
                        {artistTo(a) ? (
                          <Link to={artistTo(a)!} className="hover:text-fg hover:underline">
                            {a.name}
                          </Link>
                        ) : (
                          a.name
                        )}
                      </span>
                    ))}
                  </Marquee>
                </span>
                {/* the file's line: what plays — its source's mark, what it is, its bitrate — or what the player is
                    doing about it */}
                <PlayLine phase={phase} info={info} error={error} className="block truncate text-[11px] leading-4 text-dim" />
              </span>
              <SaveButton songId={info?.nd_song_id} refId={track.ref} listKey={tagKey({ library_track_id: info?.track_id ?? track.library_track_id })} title={track.title} data={{ ...track }} className="ml-1 !w-9" />
            </>
          ) : (
            <span className="flex items-center gap-3 text-sm text-dim">
              <span className="flex h-13 w-13 items-center justify-center rounded-md bg-surface">
                <Icon name="note" size={22} />
              </span>
              Ничего не играет
            </span>
          )}
        </div>

        <div className="flex min-w-0 flex-col items-center gap-0.5">
          <div className="flex items-center gap-1">
            <IconButton
              icon={smart ? 'shuffleSmart' : 'shuffle'}
              size={18}
              label={smart ? 'Умное перемешивание включено' : shuffle ? 'Перемешивание включено' : 'Перемешать'}
              aria-pressed={shuffle}
              disabled={!track || onWave}
              onClick={cycleShuffle}
              className={cx('!h-9 !w-9', shuffle && !onWave ? '!text-accent' : 'text-muted')}
            />
            <IconButton icon="prev" size={20} label={canPrev ? 'Предыдущий трек' : 'Сначала'} disabled={!track} onClick={prev} className="!h-9 !w-9 text-muted hover:text-fg" />
            <button
              type="button"
              onClick={toggle}
              disabled={!track}
              aria-label={phase === 'error' ? 'Повторить' : playing ? 'Пауза' : 'Слушать'}
              className="mx-1 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-fg text-bg transition hover:scale-110 active:scale-95 disabled:opacity-40"
            >
              {phase === 'error' ? <Icon name="refresh" size={18} stroke={2.4} /> : waiting ? <Spinner size={18} /> : playing ? <Icon name="pause" size={18} stroke={3} /> : <Icon name="play" size={18} stroke={2.4} className="translate-x-px" />}
            </button>
            <IconButton icon="next" size={20} label="Следующий трек" disabled={!track || !canNext} onClick={() => void next()} className="!h-9 !w-9 text-muted hover:text-fg" />
            <IconButton
              icon={repeat === 'one' ? 'repeat1' : 'repeat'}
              size={18}
              label={repeat === 'off' ? 'Повтор выключен' : repeat === 'all' ? 'Повтор очереди' : 'Повтор трека'}
              disabled={!track || (onWave && repeat !== 'one')}
              onClick={cycleRepeat}
              className={cx('!h-9 !w-9', repeat !== 'off' ? '!text-accent' : 'text-muted')}
            />
          </div>
          <div className="flex w-full items-center gap-2 text-[11px] tabular-nums text-muted">
            <span className="w-9 shrink-0 text-right">{track ? fmtDuration(shownPos) || '0:00' : ''}</span>
            <span className="relative flex min-w-0 flex-1">
              <input {...range} className="hm-range min-w-0 flex-1" disabled={!track || !duration || phase === 'resolving' || phase === 'error'} />
              {/* the time aimed at goes with the handle (the eye is there anyway); the line of the text stands still, above */}
              {aim !== null && (
                <span className="pointer-events-none absolute bottom-full mb-0.5 -translate-x-1/2 rounded bg-fg px-1.5 py-px text-bg" style={{ left: `calc(6px + (100% - 12px) * ${aimPct / 100})` }}>
                  {fmtDuration(aim) || '0:00'}
                </span>
              )}
            </span>
            <span className="w-9 shrink-0">{track ? fmtDuration(duration) || '–:––' : ''}</span>
          </div>
        </div>

        <div className="flex min-w-0 items-center justify-end gap-1">
          {track && <TrackMenuButton track={{ ...track, nd_song_id: info?.nd_song_id, library_track_id: info?.track_id ?? track.library_track_id }} className="!h-9 !w-9" inPlayer lean noTags />}
          {track && <IconButton icon="share" size={18} label="Поделиться" onClick={() => openShare({ title: track.title, artist: track.artist, ref: track.ref, nd_song_id: info?.nd_song_id })} className="!h-9 !w-9 text-muted hover:text-fg" />}
          {/* the tags of the track: its own mark here, not a line of ⋯ (a track the library has not got has none) */}
          {track && (
            <IconButton
              icon="tag"
              size={18}
              label={trackTags ? 'Теги' : 'Теги появятся, когда трек будет в библиотеке'}
              disabled={!trackTags}
              onClick={() => trackTags && openTagsSheet([trackTags], track.title)}
              className="!h-9 !w-9 text-muted hover:text-fg"
            />
          )}
          <Link to="/player" onClick={askForQueue} title="Очередь" aria-label="Очередь" className={cx('flex h-9 w-9 items-center justify-center rounded-full text-muted transition hover:scale-115 hover:text-fg', !track && 'pointer-events-none opacity-40')}>
            <Icon name="queue" size={20} />
          </Link>
          <Icon name="volume" size={18} className="ml-1 shrink-0 text-muted" />
          <input
            type="range"
            className="hm-range w-28 min-w-0"
            aria-label="Громкость"
            aria-valuetext={`${Math.round(volume * 100)}%`}
            min={0}
            max={100}
            step={1}
            value={Math.round(volume * 100)}
            style={{ '--p': `${volume * 100}%` } as CSSProperties}
            onChange={(e) => setVolume(Number(e.target.value) / 100)}
          />
        </div>
      </div>
    </div>
  );
}
