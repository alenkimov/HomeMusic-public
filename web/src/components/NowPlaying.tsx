import type { ListenOut } from '../api/types';
import { artistTo, creditedOf, type ListenTrack } from '../lib/listen';
import { Link, albumPath } from '../lib/router';
import type { VersionIdentity } from '../lib/versionLabels';
import { Icon } from './Icon';
import { TrackTitle } from './TrackTitle';
import { cx } from './ui';

/** «Сейчас играет»: what every player says of the track it plays — the mini player on a phone, the bar of a
 * computer, the full player. Each lays it out to its size; the pieces are the same: the name with its version's mark
 * (it leads to the album), the marks of the wave and of a recommendation, every credited artist with their page. */

/** What tells the playing track's version: the file the player found (`info`) over the queue's word of it. */
export function playingIdentity(track: ListenTrack, info: ListenOut | null | undefined): VersionIdentity {
  return {
    ...track,
    ref: track.ref,
    library_track_id: info?.track_id ?? track.library_track_id,
    nd_song_id: info?.nd_song_id ?? track.nd_song_id,
    page_url: info?.page_url ?? track.page_url,
  };
}

const albumOf = (t: ListenTrack) => (t.album_ref || t.release_id ? albumPath({ ref: t.album_ref ?? '', release_id: t.release_id }) : null);

/** The track's name; `link`: it leads to its album (not where the whole player is a link already); `mark`: with the
 * mark of «Наведение порядка». */
export function PlayingTitle({ track, info, link = true, mark = false, className }: { track: ListenTrack; info: ListenOut | null | undefined; link?: boolean; mark?: boolean; className?: string }) {
  const identity = playingIdentity(track, info);
  // the mark of «Наведение порядка»: the full player and a computer's bar (the mini player has no room for it)
  const name = <TrackTitle title={track.title} track={identity} mark={mark ? identity.library_track_id : null} />;
  const to = link ? albumOf(track) : null;
  return to ? <Link to={to} className={cx('hover:underline', className)}>{name}</Link> : <span className={className}>{name}</span>;
}

/** The marks before the artists: the wave plays it, it is a recommendation. `size`: the icons' pixels. */
export function PlayingMarks({ track, onWave, size = 14 }: { track: ListenTrack; onWave?: boolean; size?: number }) {
  return (
    <>
      {onWave && <span title="Моя волна" className="shrink-0 text-accent"><Icon name="wave" size={size} /></span>}
      {track.rec && <span title={track.rec.label || 'Рекомендация'} className="shrink-0 text-accent"><Icon name="sparkle" size={size} /></span>}
    </>
  );
}

/** Every credited artist; `link`: each with their page (not where the whole player is a link already). */
export function PlayingArtists({ track, link = true }: { track: ListenTrack; link?: boolean }) {
  return (
    <>
      {creditedOf(track).map((a, i) => (
        <span key={`${a.name}:${i}`}>
          {i > 0 && ', '}
          {link && artistTo(a) ? <Link to={artistTo(a)!} className="hover:text-fg hover:underline">{a.name}</Link> : a.name}
        </span>
      ))}
    </>
  );
}
