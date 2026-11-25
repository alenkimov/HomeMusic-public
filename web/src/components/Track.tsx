import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Link } from '../lib/router';
import type { VersionIdentity } from '../lib/versionLabels';
import { TrackTitle } from './TrackTitle';
import { Cover, cx } from './ui';

/** A track the way every view of one draws it outside a track list (`TrackRow` is the list's row: it plays, it has
 * the columns, the menu, the swipes). Here: `TrackCell` — the cover beside the name (with its version's mark) and a
 * line; `TrackItem` — a whole row of that, for the places that list tracks of their own: the queue, «История»,
 * «Скрытое», «Качество», the versions, the assistant's rows, the device a track comes from. */
export interface TrackLook {
  title: string;
  artist?: string | null;
  cover?: string | null;
  /** what tells its version («Remastered», a label of one's own): `TrackTitle`'s */
  identity?: VersionIdentity | null;
  /** the version's word given outright; undefined: the one kept for `identity` */
  label?: string | null;
}

/** The cover beside the track's name and its line (the artist, unless `sub` says more). `size`: 40 in a computer's
 * table, else 48, as `TrackRow`'s. */
export function TrackCell({ track, sub, size = 48, playing, dim, after, className }: {
  track: TrackLook; sub?: ReactNode; size?: number; playing?: boolean; dim?: boolean;
  /** after the name, before the actions: a mark of the place («есть») */
  after?: ReactNode; className?: string;
}) {
  return (
    <span className={cx('flex min-w-0 flex-1 items-center gap-3', dim && 'opacity-60', className)}>
      <Cover src={track.cover} size={size} alt="" />
      <span className="min-w-0 flex-1">
        <span className={cx('block truncate text-[15px]', playing && 'font-semibold text-accent')}>
          <TrackTitle title={track.title} track={track.identity} label={track.label} />
        </span>
        {(sub ?? track.artist) && <span className="block truncate text-[13px] text-muted">{sub ?? track.artist}</span>}
      </span>
      {after}
    </span>
  );
}

/** A row of `TrackCell`: `lead` before it, `actions` after; `onClick` makes it a button (`button`: its label and
 * state), `to` a link; `below` under the row (a comparison, an editor). */
export function TrackItem({ track, sub, lead, actions, below, onClick, button, to, playing, dim, size, className }: {
  track: TrackLook; sub?: ReactNode; lead?: ReactNode; actions?: ReactNode; below?: ReactNode;
  onClick?: () => void; button?: ButtonHTMLAttributes<HTMLButtonElement>; to?: string | null; playing?: boolean; dim?: boolean;
  size?: number; className?: string;
}) {
  const cell = <TrackCell track={track} sub={sub} playing={playing} dim={dim} size={size} />;
  const main = 'flex min-w-0 flex-1 items-center py-2 text-left';
  return (
    <div className={className}>
      <div className="flex min-h-16 items-center gap-3 px-4">
        {lead}
        {onClick ? (
          <button type="button" {...button} onClick={onClick} className={main}>{cell}</button>
        ) : to ? (
          <Link to={to} className={main}>{cell}</Link>
        ) : (
          <div className={main}>{cell}</div>
        )}
        {actions}
      </div>
      {below}
    </div>
  );
}
