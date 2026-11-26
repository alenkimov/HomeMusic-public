import type { ReactNode } from 'react';
import type { AlbumOut, TrackOut } from '../api/types';
import { recordTypeLabel, yearOf } from '../lib/format';
import { Link, albumPath } from '../lib/router';
import { menuPressProps, useContextMenu, type MenuAt } from './ContextMenu';
import { IconButton, Cover, cx } from './ui';
import { Hero } from '../pages/MediaLibrary';
import type { MarkSubject } from '../lib/tidyMarks';
import { TidyMark } from './TidyMark';

/** An album the way every view of it draws one, whatever it came as (the catalog's, the library's, a part of
 * «Объединение альбомов»). The four views: `AlbumHero` — the head of a page; `AlbumCard` — a cover over its name,
 * for grids and shelves; `AlbumRow` — a 48 px cover beside its name, for lists; `AlbumLink` — its name as a link. */
export interface AlbumLook {
  title: string;
  /** its page; none: not a link */
  to?: string | null;
  cover?: string | null;
  /** the catalog's record type (album / single / ep / compile); none: «Альбом» */
  type?: string | null;
  artist?: string | null;
  year?: number | null;
}

/** the menu of a right click, a held finger or ⋯ */
export type AlbumMenuRender = (at: MenuAt | null, onClose: () => void) => ReactNode;

/** The line under an album's name, the same everywhere: type · artist · year (no artist on that artist's own page). */
export function albumLine(a: AlbumLook, showArtist = true): string {
  return [recordTypeLabel(a.type) || 'Альбом', showArtist ? a.artist : null, a.year].filter(Boolean).join(' · ');
}

export const catalogLook = (a: AlbumOut): AlbumLook => ({
  title: a.title, to: albumPath(a), cover: a.cover_small_url || a.cover_url, type: a.record_type, artist: a.artist,
  year: yearOf(a),
});

/** A cover over the album's name and line. `overlay` lies on the cover (a tick, a download button), `extra` goes
 * under the line (what the library has of it, where it came from). */
export function AlbumCard({ album, showArtist = true, overlay, extra, menu, className }: {
  album: AlbumLook; showArtist?: boolean; overlay?: ReactNode; extra?: ReactNode; menu?: AlbumMenuRender; className?: string;
}) {
  const m = useContextMenu();
  const cover = <Cover src={album.cover} icon="disc" alt={album.title} className="aspect-square w-full" />;
  return (
    <>
      <div className={cx('relative min-w-0 select-none [-webkit-touch-callout:none]', className)} {...(menu ? menuPressProps(m.openAt) : {})}>
        <div className="relative">
          {album.to ? <Link to={album.to} className="block" tabIndex={-1} aria-hidden="true">{cover}</Link> : cover}
          {overlay}
        </div>
        {album.to ? (
          <Link to={album.to} className="mt-2 line-clamp-2 text-sm font-medium leading-tight hover:underline">{album.title}</Link>
        ) : (
          <div className="mt-2 line-clamp-2 text-sm font-medium leading-tight">{album.title}</div>
        )}
        <div className="mt-0.5 truncate text-xs text-muted">{albumLine(album, showArtist)}</div>
        {extra}
      </div>
      {menu && m.shown && menu(m.at, m.close)}
    </>
  );
}

/** A 48 px cover beside the album's name and line. `lead` goes before the cover (a place, a tick), `actions` after
 * the name, `below` under the row (the album's tracks); `onClick` makes the row a button instead of a link. */
export function AlbumRow({ album, showArtist = true, lead, actions, below, menu, onClick, disabled, className }: {
  album: AlbumLook; showArtist?: boolean; lead?: ReactNode; actions?: ReactNode; below?: ReactNode; menu?: AlbumMenuRender;
  onClick?: () => void; disabled?: boolean; className?: string;
}) {
  const m = useContextMenu();
  const body = (
    <>
      <Cover src={album.cover} size={48} icon="disc" alt="" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[15px] font-medium group-hover/album:underline">{album.title}</span>
        <span className="block truncate text-[13px] text-muted">{albumLine(album, showArtist)}</span>
      </span>
    </>
  );
  const main = 'group/album flex min-w-0 flex-1 items-center gap-3 py-2 text-left';
  return (
    <div className={cx('select-none [-webkit-touch-callout:none]', className)} {...(menu ? menuPressProps(m.openAt) : {})}>
      <div className="flex min-h-16 items-center gap-3 px-4">
        {lead}
        {onClick ? (
          <button type="button" onClick={onClick} disabled={disabled} className={cx(main, 'disabled:opacity-50')}>{body}</button>
        ) : album.to ? (
          <Link to={album.to} className={main}>{body}</Link>
        ) : (
          <div className={main}>{body}</div>
        )}
        {actions}
        {menu && <IconButton icon="more" size={20} label={`Меню альбома «${album.title}»`} onClick={m.fromButton} className="!h-10 !w-10 shrink-0 text-muted" />}
      </div>
      {below}
      {menu && m.shown && menu(m.at, m.close)}
    </div>
  );
}

/** The album's name as a link: the album column of a track list. */
export function AlbumLink({ title, to, className }: { title?: string | null; to?: string | null; className?: string }) {
  if (!title) return null;
  return to ? (
    <Link to={to} className={cx('truncate text-sm text-muted hover:text-fg hover:underline', className)}>{title}</Link>
  ) : (
    <span className={cx('truncate text-sm text-muted', className)}>{title}</span>
  );
}

/** The head of an album's page: its kind, its name, each artist with their page, the facts after them. */
export function AlbumHero({ album, mark, artists, facts, onCover, tracks, context = 'album', right }: {
  album: AlbumLook;
  /** the library's album: the mark of «Наведение порядка» after its name */
  mark?: MarkSubject | null;
  /** each credited artist and their page; `id`: the Hub's artist, for the mark after the name */
  artists: { name: string; to?: string | null; id?: number | null }[];
  /** year, how many tracks, how long */
  facts: (string | number | null | undefined)[];
  onCover?: () => void;
  tracks?: readonly TrackOut[];
  context?: string;
  right?: ReactNode;
}) {
  const shown = facts.filter((x) => x !== null && x !== undefined && x !== '');
  return (
    <Hero
      kicker={recordTypeLabel(album.type) || 'Альбом'}
      cover={album.cover}
      onCover={onCover}
      title={album.title}
      mark={<TidyMark on={mark} size="md" />}
      right={right}
      sub={
        <span className="flex flex-wrap items-center gap-x-1.5">
          {artists.map((a, i) => (
            <span key={`${a.name}:${i}`} className="font-semibold text-fg">
              {i > 0 && <span className="pr-1.5 font-normal text-muted">,</span>}
              {a.to ? <Link to={a.to} className="hover:underline">{a.name}</Link> : a.name}
              {a.id ? <TidyMark on={{ artist: a.id }} className="ml-1.5" /> : null}
            </span>
          ))}
          {shown.length > 0 && <span>· {shown.join(' · ')}</span>}
        </span>
      }
      tracks={tracks}
      context={context}
    />
  );
}
