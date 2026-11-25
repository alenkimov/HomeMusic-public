import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Link } from '../lib/router';
import { menuAtButton, menuPressProps, type MenuAt } from './ContextMenu';
import { Cover, IconButton, cx } from './ui';

/** An artist the way every view of one draws them, whatever they came as (the catalog's, the library's, a credit of a
 * track). The views: the head of their page (`ArtistBanner`, pages/Artist.tsx); `ArtistCard` — the round photo over
 * the name, for shelves and pickers; `ArtistRow` — a round 48 px photo beside the name, for lists. */
export interface ArtistLook {
  name: string;
  /** their page; none: not a link */
  to?: string | null;
  photo?: string | null;
}

/** The line under an artist's name, the same everywhere: «Исполнитель», then what the place knows of them. */
export function artistLine(...more: (string | null | undefined | false)[]): string {
  return ['Исполнитель', ...more].filter(Boolean).join(' · ');
}

/** The round photo over the name and its line. `overlay` lies on the photo (a tick, the wave's mark); `selected`
 * rings it; `onClick` makes it a button (`button`: its role and state) instead of a link. */
export function ArtistCard({ artist, sub, overlay, selected, onClick, button, onMenu, size = 104, className }: {
  /** false: no line (a picker of artists) */
  artist: ArtistLook; sub?: ReactNode | false; overlay?: ReactNode; selected?: boolean; onClick?: () => void;
  button?: ButtonHTMLAttributes<HTMLButtonElement>; onMenu?: (at?: MenuAt) => void; size?: number; className?: string;
}) {
  const body = (
    <>
      <span className="relative">
        <Cover src={artist.photo} size={size} round icon="user" alt="" className={cx('transition', selected && 'ring-4 ring-accent')} />
        {overlay}
      </span>
      <span className={cx('line-clamp-2 w-full text-sm font-medium leading-tight', selected && 'text-accent')}>{artist.name}</span>
      {sub !== false && <span className="-mt-1.5 block w-full truncate text-xs text-muted">{sub ?? artistLine()}</span>}
    </>
  );
  const cls = cx('flex shrink-0 select-none flex-col items-center gap-2 text-center [-webkit-touch-callout:none]', className);
  const press = onMenu ? menuPressProps(onMenu) : {};
  const style = { width: size + 8 };
  if (onClick) return <button type="button" {...button} {...press} onClick={onClick} className={cls} style={style}>{body}</button>;
  if (artist.to) return <Link to={artist.to} {...press} className={cls} style={style}>{body}</Link>;
  return <div {...press} className={cls} style={style}>{body}</div>;
}

/** A round 48 px photo beside the artist's name and line. `lead` before the photo, `actions` after the name;
 * `onMenu`: their menu by a right click, a held finger and ⋯. */
export function ArtistRow({ artist, sub, lead, actions, onMenu, picture, className }: {
  artist: ArtistLook; sub?: ReactNode; lead?: ReactNode; actions?: ReactNode; onMenu?: (at?: MenuAt) => void;
  /** the photo drawn by the place itself (one asked for as the row shows); null: none here (`lead` has it) */
  picture?: ReactNode; className?: string;
}) {
  const body = (
    <>
      {picture === undefined ? <Cover src={artist.photo} size={48} round icon="user" alt="" /> : picture}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[15px] font-medium group-hover/artist:underline">{artist.name}</span>
        <span className="block truncate text-[13px] text-muted">{sub ?? artistLine()}</span>
      </span>
    </>
  );
  const main = 'group/artist flex min-w-0 flex-1 items-center gap-3 py-2 text-left';
  return (
    <div className={cx('flex min-h-16 select-none items-center gap-3 px-4 [-webkit-touch-callout:none]', className)} {...(onMenu ? menuPressProps(onMenu) : {})}>
      {lead}
      {artist.to ? <Link to={artist.to} className={main}>{body}</Link> : <div className={main}>{body}</div>}
      {actions}
      {onMenu && <IconButton icon="more" size={20} label={`Меню исполнителя «${artist.name}»`} onClick={(e) => onMenu(menuAtButton(e.currentTarget))} className="!h-10 !w-10 shrink-0 text-muted" />}
    </div>
  );
}
