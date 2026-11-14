import { tagKey } from '../lib/tags';
import { PlayLine } from './FileInfo';
import { useLayoutEffect, useRef, type TouchEvent } from 'react';
import { stopListening, toggle, useListen } from '../lib/listen';
import { STANDALONE } from '../lib/embedded';
import { Link, enc } from '../lib/router';
import { Icon } from './Icon';
import { SaveButton } from './PlaylistsButton';
import { Cover, Spinner } from './ui';

/** What plays, above the tabs of a phone (inside the app: at the bottom of the Hub page, which the app keeps above
 * its own player): the cover, the name, the artist — and two buttons, ♥ (a track that isn't downloaded is downloaded
 * first) and play. A line along its bottom is how far the track is. Everything else is one tap away, in the full
 * player (`/player`; inside the app — the album); swept down, it goes. Publishes its height as `--player-h`. */
export function MiniPlayer() {
  const track = useListen((s) => s.track);
  const phase = useListen((s) => s.phase);
  const info = useListen((s) => s.info);
  const error = useListen((s) => s.error);
  const position = useListen((s) => s.position);
  const duration = useListen((s) => s.duration);
  const onWave = useListen((s) => s.context.startsWith('wave:'));
  const touchY = useRef<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const shown = !!track;

  useLayoutEffect(() => {
    const el = box.current;
    const root = document.documentElement;
    if (!shown || !el) return;
    const publish = () => root.style.setProperty('--player-h', `${el.offsetHeight}px`);
    publish();
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.removeProperty('--player-h');
    };
  }, [shown]);

  if (!track) return null;

  const waiting = phase === 'resolving' || phase === 'loading';
  const playing = phase === 'playing' || waiting;
  const head = (
    <>
      <Cover src={track.cover_small_url || track.cover_url} size={40} alt="" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[15px] font-medium leading-tight">{track.title}</div>
        {phase === 'error' ? (
          // a narrow bar has two lines: what went wrong takes the artists' while it is so
          <PlayLine phase={phase} info={info} error={error} className="block truncate text-[13px] leading-tight" />
        ) : (
          <div className="flex items-center gap-1.5 text-[13px] leading-tight text-muted">
            {waiting && <PlayLine phase={phase} info={info} className="shrink-0" />}
            {/* the preview is all there is: no source has the full track */}
            {onWave && (
              <span title="Моя волна" className="shrink-0 text-accent">
                <Icon name="wave" size={14} />
              </span>
            )}
            {track.rec && (
              <span title={track.rec.label} className="shrink-0 text-accent">
                <Icon name="sparkle" size={14} />
              </span>
            )}
            {info?.kind === 'preview' && <span className="shrink-0 rounded bg-warn/15 px-1 text-[11px] font-semibold text-warn">превью 30 сек</span>}
            <span className="truncate">{track.artist}</span>
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
    <div ref={box} className="fixed inset-x-0 bottom-[calc(var(--tabbar-h)+env(safe-area-inset-bottom))] z-40 mx-auto max-w-(--app-w) px-2 pb-1" role="region" aria-label="Прослушивание">
      <div {...swipe} className="anim-up relative overflow-hidden rounded-xl bg-raised shadow-2xl">
        <div className="flex items-center gap-1 py-1.5 pl-2 pr-1">
          {STANDALONE ? (
            <Link to="/player" className="flex min-w-0 flex-1 items-center gap-3" title="Открыть плеер">
              {head}
            </Link>
          ) : track.album_ref ? (
            <Link to={`/album/${enc(track.album_ref)}`} className="flex min-w-0 flex-1 items-center gap-3" title="Открыть альбом">
              {head}
            </Link>
          ) : (
            <div className="flex min-w-0 flex-1 items-center gap-3">{head}</div>
          )}
          <SaveButton songId={info?.nd_song_id} refId={track.ref} listKey={tagKey({ library_track_id: info?.track_id ?? track.library_track_id })} title={track.title} data={{ ...track }} size={22} />
          <button
            type="button"
            onClick={toggle}
            aria-label={phase === 'error' ? 'Повторить' : playing ? 'Пауза' : 'Слушать'}
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-fg transition active:scale-90"
          >
            {phase === 'error' ? (
              <Icon name="refresh" size={24} stroke={2.4} />
            ) : waiting ? (
              <Spinner size={22} />
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

/** Keeps the end of the page above the player (the app's style fixes `main`'s bottom padding). */
export function PlayerSpacer() {
  const shown = useListen((s) => !!s.track);
  return shown ? <div aria-hidden="true" style={{ height: 'var(--player-h, 0px)' }} /> : null;
}
