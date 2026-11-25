import type { ButtonHTMLAttributes, ReactNode } from 'react';
import type { PlaylistOut, TrackOut } from '../api/types';
import { tracksWord } from '../lib/format';
import { Link, enc } from '../lib/router';
import { menuAtButton, menuPressProps, type MenuAt } from './ContextMenu';
import { Icon, type IconName } from './Icon';
import { Cover, IconButton, cx } from './ui';
import { Hero } from '../pages/MediaLibrary';

/** A playlist the way every view of it draws one — the user's, «Любимое», a list of «Для вас», a Spotify one. As an
 * album names its artists, a playlist names who made it. The views: `PlaylistHero` — the head of its page;
 * `PlaylistRow` — a 48 px cover beside its name, for lists and the «add to» sheets. */
export interface PlaylistLook {
  title: string;
  /** its page; none: not a link */
  to?: string | null;
  cover?: string | null;
  /** «Плейлист» unless said: «Плейлист Spotify», «Для вас», «Подборка по тегам» */
  kind?: string;
  /** who made it; none: the Hub («Микс дня», «Для вас») */
  owners?: string[];
  /** how many tracks */
  count?: number | null;
  /** a mark in place of a picture: «Любимое»'s heart, a tag selection's filter */
  mark?: { icon: IconName; filled?: boolean };
}

/** A list's mark in place of a picture, on the accent's tint; `size`: the box's pixels. */
function Mark({ mark, size }: { mark: NonNullable<PlaylistLook['mark']>; size: number }) {
  return (
    <span className="flex shrink-0 items-center justify-center rounded-md bg-accent/20 text-accent" style={{ width: size, height: size }}>
      <Icon name={mark.icon} size={Math.round(size * 0.45)} filled={mark.filled} />
    </span>
  );
}

/** «Любимое»: the heart in place of a picture. */
export const LIKED_MARK = { icon: 'heart', filled: true } as const;

/** The app's own name, as the maker of the lists it makes. */
export const HUB_NAME = 'HomeMusic';

/** The line under a playlist's name, the same everywhere: kind · who made it · how many tracks. */
export function playlistLine(p: PlaylistLook, showOwner = true): string {
  const who = p.owners?.length ? p.owners.join(', ') : HUB_NAME;
  return [p.kind || 'Плейлист', showOwner ? who : null, p.count != null ? tracksWord(p.count) : null].filter(Boolean).join(' · ');
}

/** A playlist of the Hub (`PlaylistOut`) as the views draw it; `size`: the picture's pixels to ask for. */
export const playlistLook = (p: Pick<PlaylistOut, 'id' | 'name' | 'song_count' | 'pending_count' | 'cover_url' | 'kind' | 'owners'>, size = 160): PlaylistLook => ({
  title: p.name, to: `/playlist/${enc(p.id)}`, cover: p.cover_url ? `${p.cover_url}&size=${size}` : null,
  owners: p.kind === 'mix' ? [] : (p.owners ?? []).map((o) => o.name), count: p.song_count + (p.pending_count ?? 0),
});

const picture = (p: PlaylistLook, size: number) =>
  p.mark && !p.cover ? <Mark mark={p.mark} size={size} /> : <Cover src={p.cover} size={size} icon="playlist" alt="" />;

/** A 48 px picture beside the playlist's name and line. `lead` before the picture, `actions` after the name (a
 * tick of an «add to» sheet); `onClick` makes the row a button (`button`: its role and state); `onMenu`: the
 * playlist's menu by a right click, a held finger and ⋯. */
export function PlaylistRow({ playlist, showOwner = true, lead, actions, sub, onClick, button, onMenu, className }: {
  playlist: PlaylistLook; showOwner?: boolean; lead?: ReactNode; actions?: ReactNode;
  /** a line of the place's own in place of the usual one (what a sheet will change) */
  sub?: ReactNode;
  onClick?: () => void; button?: ButtonHTMLAttributes<HTMLButtonElement>; onMenu?: (at?: MenuAt) => void; className?: string;
}) {
  const body = (
    <>
      {picture(playlist, 48)}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[15px] font-medium group-hover/playlist:underline">{playlist.title}</span>
        <span className="block truncate text-[13px] text-muted">{sub ?? playlistLine(playlist, showOwner)}</span>
      </span>
    </>
  );
  const main = 'group/playlist flex min-w-0 flex-1 items-center gap-3 py-2 text-left';
  return (
    <div className={cx('flex min-h-16 select-none items-center gap-3 px-4 [-webkit-touch-callout:none]', className)} {...(onMenu ? menuPressProps(onMenu) : {})}>
      {lead}
      {onClick ? (
        <button type="button" {...button} onClick={onClick} className={main}>{body}{actions}</button>
      ) : playlist.to ? (
        <Link to={playlist.to} className={main}>{body}</Link>
      ) : (
        <div className={main}>{body}</div>
      )}
      {!onClick && actions}
      {onMenu && <IconButton icon="more" size={20} label={`Меню плейлиста «${playlist.title}»`} onClick={(e) => onMenu(menuAtButton(e.currentTarget))} className="!h-10 !w-10 shrink-0 text-muted" />}
    </div>
  );
}

/** The head of a playlist's page: its kind, its name, who made it, the facts after them. */
export function PlaylistHero({ playlist, facts, onCover, tracks, context = 'playlist', right, art }: {
  playlist: PlaylistLook;
  /** a picture of the list's own making (the collage of «Для вас») */
  art?: ReactNode;
  /** how many tracks, how long, and what else the list says of itself */
  facts: (string | number | null | undefined)[];
  onCover?: () => void;
  tracks?: readonly TrackOut[];
  context?: string;
  right?: ReactNode;
}) {
  const shown = facts.filter((x) => x !== null && x !== undefined && x !== '');
  const who = playlist.owners?.length ? playlist.owners : [HUB_NAME];
  return (
    <Hero
      kicker={playlist.kind || 'Плейлист'}
      cover={playlist.cover}
      art={art ?? (playlist.mark && !playlist.cover ? (
        <span className="flex h-full w-full items-center justify-center bg-accent/20 text-accent">
          <Icon name={playlist.mark.icon} size={84} filled={playlist.mark.filled} />
        </span>
      ) : undefined)}
      onCover={onCover}
      title={playlist.title}
      right={right}
      sub={
        <span className="flex flex-wrap items-center gap-x-1.5">
          <span className="font-semibold text-fg">{who.join(', ')}</span>
          {shown.length > 0 && <span>· {shown.join(' · ')}</span>}
        </span>
      }
      tracks={tracks}
      context={context}
    />
  );
}
