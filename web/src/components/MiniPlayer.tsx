import { PlayingArtists, PlayingMarks, PlayingTitle } from './NowPlaying';
import { menuPressProps } from './ContextMenu';
import { menuTrackOf } from './TrackMenu';
import { openTrackMenu } from '../lib/trackMenu';
import { tagKey } from '../lib/tags';
import { PlayLine } from './FileInfo';
import { ChoosePlaybackFile } from './FindFile';
import { useLayoutEffect, useRef, type TouchEvent } from 'react';
import { isUpload, stopListening, toggle, useListen } from '../lib/listen';
import { Link } from '../lib/router';
import { Icon } from './Icon';
import { tracksWord } from '../lib/format';
import { SaveButton } from './PlaylistsButton';
import { Cover } from './ui';

/** What plays, above the tabs of a phone: the cover, the name, the artist — and two buttons, ♥ (a track that isn't downloaded is downloaded
 * first) and play. A line along its bottom is how far the track is. Everything else is one tap away, in the full
 * player (`/player`); swept down, it goes. Publishes its height as `--player-h`. */
export function MiniPlayer() {
  const track = useListen((s) => s.track);
  const manualCount = useListen((s) => s.manualQueue.length);
  const phase = useListen((s) => s.phase);
  const info = useListen((s) => s.info);
  const error = useListen((s) => s.error);
  const position = useListen((s) => s.position);
  const duration = useListen((s) => s.duration);
  const onWave = useListen((s) => s.context.startsWith('wave:'));
  const touchY = useRef<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const shown = !!track || manualCount > 0;

  useLayoutEffect(() => {
    const el = box.current;
    const root = document.documentElement;
    if (!shown || !el) return;
    // told by the observer (it reports right after layout, before the frame is painted): reading the height here
    // would lay out the whole page at once, a list of thousands of rows included
    let last = -1;
    const ro = new ResizeObserver(([entry]) => {
      const h = Math.round(entry.borderBoxSize?.[0]?.blockSize ?? el.offsetHeight);
      if (h === last) return;
      last = h;
      root.style.setProperty('--player-h', `${h}px`);
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.removeProperty('--player-h');
    };
  }, [shown]);

  const bar = 'fixed inset-x-0 bottom-[calc(var(--tabbar-h)+env(safe-area-inset-bottom))] z-40 mx-auto max-w-(--app-w) px-2 pb-1';
  if (!track) return manualCount > 0 ? (
    <div ref={box} className={bar}>
      <Link to="/player" overlay className="flex min-h-12 items-center gap-3 rounded-xl bg-raised px-3 shadow-2xl">
        <Icon name="queue" size={20} />
        <span>Ручная очередь · {tracksWord(manualCount)}</span>
      </Link>
    </div>
  ) : null;

  const waiting = phase === 'resolving' || phase === 'loading';
  const playing = phase === 'playing' || waiting;
  const head = (
    <>
      <Cover src={track.cover_small_url || track.cover_url} size={40} alt="" />
      <div className="min-w-0 flex-1">
        {/* the whole bar is a link already: the name and the artists are not */}
        <PlayingTitle track={track} info={info} link={false} className="block truncate text-[15px] font-medium leading-tight" />
        {phase === 'error' ? (
          // a narrow bar has two lines: what went wrong takes the artists' while it is so
          <PlayLine phase={phase} info={info} error={error} className="block truncate text-[13px] leading-tight" />
        ) : (
          <div className="flex items-center gap-1.5 text-[13px] leading-tight text-muted">
            {waiting && <PlayLine phase={phase} info={info} className="shrink-0" />}
            <PlayingMarks track={track} onWave={onWave} />
            <span className="truncate"><PlayingArtists track={track} link={false} /></span>
          </div>
        )}
      </div>
    </>
  );

  const pct = duration ? Math.min(100, (Math.min(position, duration) / duration) * 100) : 0;
  // swept down, it goes (and the sound stops): it has no button for that
  const swipe = {
    onTouchStart: (e: TouchEvent) => (touchY.current = e.touches[0].clientY),
    onTouchEnd: (e: TouchEvent) => {
      if (touchY.current !== null && e.changedTouches[0].clientY - touchY.current > 48) stopListening();
      touchY.current = null;
    },
  };

  return (
    <div ref={box} className={bar} role="region" aria-label="Прослушивание">
      <div {...swipe} {...(isUpload(track) ? {} : menuPressProps((at) => openTrackMenu({ track: menuTrackOf(track, info), inPlayer: true, at })))} className="anim-up relative overflow-hidden rounded-xl bg-raised shadow-2xl select-none [-webkit-touch-callout:none]">
        <div className="flex items-center gap-1 py-1.5 pl-2 pr-1">
          <Link to="/player" overlay className="flex min-w-0 flex-1 items-center gap-3" title="Открыть плеер">
            {head}
          </Link>
          <ChoosePlaybackFile compact />
          {!isUpload(track) && <SaveButton songId={info?.nd_song_id ?? track.nd_song_id} refId={track.ref} listKey={tagKey({ library_track_id: info?.track_id ?? track.library_track_id })} title={track.title} data={{ ...track }} size={22} />}
          <button
            type="button"
            onClick={toggle}
            aria-label={phase === 'error' ? 'Повторить' : playing ? 'Пауза' : 'Слушать'}
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-fg transition active:scale-90"
          >
            {phase === 'error' ? (
              <Icon name="refresh" size={24} stroke={2.4} />
            ) : playing ? (
              <Icon name="pause" size={24} stroke={3} />
            ) : (
              <Icon name="play" size={24} filled />
            )}
          </button>
        </div>
        {/* how far the track is: a line along the bottom (it is sought through in the full player) */}
        <div aria-hidden="true" className="absolute inset-x-2 bottom-0 h-0.5 overflow-hidden rounded-full bg-fg/15">
          <div className="h-full bg-fg" style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}

/** Keeps the end of the page above the player. */
export function PlayerSpacer() {
  const shown = useListen((s) => !!s.track || s.manualQueue.length > 0);
  return shown ? <div aria-hidden="true" style={{ height: 'var(--player-h, 0px)' }} /> : null;
}
