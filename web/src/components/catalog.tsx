import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { useOfHiddenArtist } from '../lib/dislikes';
import { KeptMark, useUnreachable } from './Offline';
import { FileInfo } from './FileInfo';
import type { AlbumOut, CatalogArtist, TrackOut } from '../api/types';
import { fmtAdded, fmtDuration, parseTime, recordTypeLabel, yearOf } from '../lib/format';
import { useDesktop } from '../lib/desktop';
import { addToQueue, downloadRef, listen, listenAll, shuffled, touchTrack, useListen, watchOnScreen, type ListenTrack } from '../lib/listen';
import { canPlay, canPlayAll, playSongs, playTracks } from '../lib/player';
import { STANDALONE } from '../lib/embedded';
import { Link, enc, navigate } from '../lib/router';
import { beginDragAction, dragActionOver, useDragMark } from '../lib/dragAction';
import { beginDragSelect, dragSelectMoved, dragSelectOver, selectRange, showList, toggleSelected, useSelected, useSelectionCount, type SelTrack } from '../lib/selection';
import { useInPlaylists } from '../lib/inPlaylists';
import { openTagsSheet, tagKey } from '../lib/tags';
import { useLiked } from '../lib/stars';
import { usePlayCounts } from '../lib/stats';
import { PlaysCell } from './PlaysCell';
import { TRACK_COLUMN_LABELS, setTrackColumn, trackGrid, useTrackColumns, type TrackColumns } from '../lib/trackColumns';
import { toast } from '../lib/ui';
import { Icon } from './Icon';
import { openShare } from '../lib/shareTrack';
import { useTags } from '../lib/tags';
import { QueueButton } from './QueueButton';
import { SwipeRow } from './SwipeRow';
import { SaveButton, TrackPlaylists } from './PlaylistsButton';
import { TrackMenuButton, type MenuExtra, type MenuTrack } from './TrackMenu';
import { Checkbox, Cover, ExplicitBadge, IconButton, OwnedBadge, Spinner, cx } from './ui';

export function TrackStatus({
  owned,
  downloading,
  onDownload,
  onPlay,
  busy,
}: {
  owned: boolean;
  downloading: boolean;
  onDownload: () => void;
  /** Owned and playable in the app's player: a play button instead of the «есть» mark. */
  onPlay?: () => void;
  busy?: boolean;
}) {
  if (owned && onPlay) return <IconButton icon="play" label="Слушать" onClick={onPlay} className="!text-accent" />;
  if (owned)
    return (
      <span className="flex h-11 shrink-0 items-center gap-1 px-2 text-xs font-medium text-accent" title="Есть в библиотеке">
        <Icon name="check" size={16} stroke={3} />
        есть
      </span>
    );
  if (downloading)
    return (
      <span className="flex h-11 shrink-0 items-center gap-1.5 px-2 text-xs text-info" title="Скачивается">
        <Spinner size={14} />
        качается
      </span>
    );
  return <IconButton icon="download" label="Скачать трек" onClick={onDownload} busy={busy} className="text-muted hover:text-fg" />;
}

/** What the page player needs to know about a catalog track. */
export function listenTrack(t: TrackOut & { nd_artist_id?: string | null; nd_artist_ids?: (string | null)[] }): ListenTrack {
  // every credited artist with the page it opens, as a track's row has them: the catalog's, else the library's
  const credited = t.artists?.length ? t.artists : [t.artist];
  const artists = credited.map((name, i) => {
    const ref = t.artist_refs?.[i] ?? (name === t.artist ? t.artist_ref : null);
    const nd = t.nd_artist_ids?.[i] ?? (credited.length === 1 ? t.nd_artist_id : null);
    return { name, ...(ref ? { ref } : {}), ...(nd ? { nd } : {}) };
  });
  return {
    ref: t.ref,
    nd_song_id: t.nd_song_id,
    library_track_id: t.library_track_id,
    title: t.title,
    artist: t.artists?.length ? t.artists.join(', ') : t.artist,
    cover_url: t.cover_url,
    cover_small_url: t.cover_small_url,
    album_ref: t.album_ref,
    artist_ref: t.artist_ref,
    artists,
    album: t.album_title ?? undefined, isrc: t.isrc ?? undefined,
    year: t.release_date ? Number(t.release_date.slice(0, 4)) || undefined : undefined,
    duration_s: t.duration_s,
  };
}

/** «Слушать» / «Перемешать» of a whole list (an album, a playlist, «Любимое»): in the app's player when it
 * plays catalog tracks (1.2.0+), otherwise in the page's own one. */
export function playAll(list: readonly TrackOut[], opts: { shuffle?: boolean; context?: string; page?: string } = {}) {
  if (!list.length) return;
  if (canPlayAll()) {
    const q = opts.shuffle ? shuffled(list) : list;
    playTracks(q, q[0], opts.context);
  } else listenAll(list.map(listenTrack), opts);
}

/** ▷ for a track that is not downloaded yet (or a provider's file): listens in the page (⏸ while it plays). */
export function ListenButton({ track, onListen }: { track: { ref: string; title: string }; onListen: () => void }) {
  const phase = useListen((s) => (s.track?.ref === track.ref ? s.phase : null));
  const waiting = phase === 'resolving' || phase === 'loading';
  const on = phase === 'playing' || waiting;
  return (
    <IconButton
      icon={on ? 'pause' : 'play'}
      label={on ? 'Пауза' : `Послушать «${track.title}»`}
      busy={waiting}
      onClick={onListen}
      className={phase ? '!text-accent' : 'text-muted hover:text-fg'}
    />
  );
}

/** A catalog track with ♥, ownership status and a download button. Owned tracks play in the app's player (in a
 * browser: in the page); the rest can be listened to before downloading (a tap on the row or ▷), and the player
 * goes on through `queue` (the album or the list) after it. With app 1.2.0+ every track of the list plays in the
 * app's player; older apps and a browser play the ones that are not downloaded in the page. */
/** A track of a list as what is picked of it (`lib/selection.ts`). */
const asSel = (t: TrackOut): SelTrack => ({ ref: t.ref, title: t.title, nd_song_id: t.owned ? t.nd_song_id : null, tag_key: tagKey(t), listen: listenTrack(t) });

export function TrackRow({
  track,
  number,
  showCover = true,
  showAlbum = false,
  showAdded = false,
  showPlays = false,
  playsOf,
  showTags = false,
  link = true,
  onPlay,
  queue,
  menuExtra,
  index,
}: {
  track: TrackOut & { nd_album_id?: string | null; nd_artist_id?: string | null; nd_artist_ids?: (string | null)[] };
  number?: number;
  showCover?: boolean;
  showAlbum?: boolean;
  /** the «Дата добавления» column of a playlist (`added_at`) */
  showAdded?: boolean;
  /** the «Прослушивания» column: how many times the user listened to the track */
  showPlays?: boolean;
  /** the number that column shows instead (an «автоплейлист»: the listens of its period); the column is then always there */
  playsOf?: number;
  /** the «Язык», «Жанр» and «Теги» columns of a library list (`lib/tags.ts`) */
  showTags?: boolean;
  link?: boolean;
  /** Plays with context in the app's player (the rest of the album or list); by default the track alone. */
  onPlay?: () => void;
  /** The list this row is in: the page player plays on through it. */
  queue?: readonly TrackOut[];
  /** More actions of the row's ⋯ menu (a playlist's «Убрать из плейлиста»). */
  menuExtra?: MenuExtra[];
  /** The row's place in its list, for the «#» column of the table on a computer (next to the cover; `number` is
   * drawn instead of the cover everywhere). */
  index?: number;
}) {
  const [busy, setBusy] = useState(false);
  const dl = useListen((s) => s.downloads.get(track.ref));
  const phase = useListen((s) => s.track?.ref === track.ref ? s.phase : null);
  const current = phase !== null;
  const waiting = phase === 'resolving' || phase === 'loading';
  const playing = phase === 'playing' || waiting;
  const rowRef = useRef<HTMLDivElement>(null);
  // on screen: the Hub picks the file in the background, so a tap starts at once
  useEffect(() => (rowRef.current && !track.owned ? watchOnScreen(rowRef.current, track.ref) : undefined), [track.ref, track.owned]);
  const download = async () => {
    setBusy(true);
    await downloadRef(track.ref);
    setBusy(false);
  };
  const all = canPlayAll();
  const listenHere = all
    ? () => playTracks(queue?.includes(track) ? queue : [track], track)
    : () => listen(listenTrack(track), queue?.map(listenTrack));
  const songId = track.owned && canPlay() ? track.nd_song_id : null;
  // owned: the app's player; in a browser there is none, so the page plays the library file
  const play = all ? listenHere : songId ? (onPlay ?? (() => playSongs([songId]))) : track.owned && !canPlay() ? listenHere : undefined;
  const rowPlays = STANDALONE && (!track.owned || (!(link && track.album_ref) && !!play));
  const playLabel = playing ? `Пауза «${track.title}»` : `Слушать «${track.title}»`;
  const sub = track.artists?.length ? track.artists.join(', ') : track.artist; // the album is a column of its own, or nowhere
  const body = (
    <>
      {/* the number of the row, and the cover — a tap on the cover is what plays the track, so a list has covers even
          where every row has the same one; the mark of playing is over it, or over the number where there is none */}
      {number !== undefined && showCover && <span className="w-6 shrink-0 text-center text-sm tabular-nums text-dim">{number}</span>}
      {number !== undefined || showCover ? (
        <span className={cx('relative flex h-12 shrink-0 items-center justify-center', showCover ? 'w-12' : 'w-7')}>
          {showCover ? (
            <Cover src={track.cover_small_url || track.cover_url} size={48} alt="" />
          ) : (
            <span className={cx('text-sm tabular-nums text-dim', rowPlays && 'group-hover/track:opacity-0 group-focus-within/track:opacity-0', rowPlays && current && 'opacity-0')}>{number}</span>
          )}
          {rowPlays && (
            <span aria-hidden="true" className={cx(
              'pointer-events-none absolute inset-0 flex items-center justify-center rounded-md transition-opacity group-hover/track:opacity-100 group-focus-within/track:opacity-100',
              showCover ? 'bg-black/60 text-white' : 'text-fg',
              current ? 'opacity-100' : 'opacity-0',
            )}>
              {waiting ? <Spinner size={22} /> : <Icon name={playing ? 'pause' : 'play'} size={22} filled />}
            </span>
          )}
        </span>
      ) : null}
      <div className="min-w-0 flex-1">
        <div className={cx('truncate text-[15px]', current ? 'text-accent' : 'text-fg')}>{track.title}</div>
        <div className="flex items-center gap-1.5 text-[13px] text-muted">
          {track.explicit && <ExplicitBadge />}
          <span className="truncate">{sub}</span>
        </div>
      </div>
      <span className="shrink-0 text-xs tabular-nums text-dim">{fmtDuration(track.duration_s)}</span>
    </>
  );
  const desktop = useDesktop();
  const cols = useTrackColumns();
  const [picking, setPicking] = useState(false);

  const likeTarget = { targetType: 'track' as const, trackId: track.library_track_id, songId: track.owned ? track.nd_song_id : null, ref: track.ref, data: { ...track } };
  const liked = useLiked(likeTarget);
  const kept = useInPlaylists(tagKey(track)) || liked; // the row's button shows ♥ or ✓
  // no network and not kept on the device, or of an artist the user does not listen to: it does not play by itself
  const offline = useUnreachable(track.ref);
  const hiddenArtist = useOfHiddenArtist(track);
  const unreachable = offline || hiddenArtist;
  const artistLine = track.artists?.length ? track.artists.join(', ') : track.artist;
  const menuTrack: MenuTrack = {
    title: track.title,
    artist: artistLine,
    ref: track.ref,
    nd_song_id: track.nd_song_id,
    key: tagKey(track),
    library_track_id: track.library_track_id,
    has_file: track.owned,
    artist_ref: track.artist_ref,
    album_ref: track.album_ref,
    nd_album_id: track.nd_album_id,
    nd_artist_id: track.nd_artist_id,
    listen: listenTrack(track),
  };
  // picking several rows to act on together (`lib/selection.ts`)
  const selected = useSelected(track.ref);
  const tagState = useTags();
  const playCounts = usePlayCounts(showPlays || playsOf !== undefined);
  const dragMark = useDragMark(track.ref); // this row is among the rows a button is being dragged over
  const selecting = useSelectionCount() > 0;
  // the row is of this list: Ctrl+A picks all of it (`lib/selection.ts`)
  useEffect(() => (queue?.length ? showList(queue, () => queue.map(asSel)) : undefined), [queue]);
  const onPick = (e: MouseEvent) => {
    // the row's menu is drawn outside the row (a portal), yet its clicks pass through here: they are not the row's
    if (!(e.currentTarget as HTMLElement).contains(e.target as Node)) return;
    const mod = e.ctrlKey || e.metaKey || e.shiftKey;
    if (!mod && !selecting) return;
    // while rows are being picked the row's own buttons (♥, ⋯) still do their job
    if (!mod && (e.target as HTMLElement).closest('[data-row-actions], [data-row-pick]')) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.shiftKey && queue?.length) selectRange(queue.map(asSel), asSel(track));
    else toggleSelected(asSel(track));
  };
  // a phone has no checkbox in the row: its menu is where picking starts (a computer's table has one)
  const selectExtra: MenuExtra = selected
    ? { icon: 'x', label: 'Снять выделение', onClick: () => toggleSelected(asSel(track)) }
    : { icon: 'check', label: 'Выбрать', hint: 'Отметьте несколько треков, чтобы отправить их разом', onClick: () => toggleSelected(asSel(track)) };
  // a browser draws its own rows: a table on a computer, a row that is swiped aside on a phone
  if (STANDALONE) {
    const action = !track.owned ? listenHere : play;
    const toAlbum = link && track.owned && track.album_ref ? `/album/${enc(track.album_ref)}` : null;
    const downloading = track.downloading || dl === 'queued';
    const titleColor = current ? 'text-accent' : 'text-fg';
    const playMark = waiting ? <Spinner size={18} /> : <Icon name={playing ? 'pause' : 'play'} size={18} filled />;
    const lead = (hover: boolean): ReactNode =>
      number !== undefined && showCover ? (
        <>
          <span className="w-5 shrink-0 text-center text-sm tabular-nums text-dim">{number}</span>
          <span className="relative flex h-10 w-10 shrink-0 items-center justify-center">
            <Cover src={track.cover_small_url || track.cover_url} size={40} alt="" />
            <span aria-hidden="true" className={cx('absolute inset-0 flex items-center justify-center rounded-md bg-black/60 text-white', !current && 'opacity-0', hover && action && 'group-hover/track:opacity-100')}>
              {playMark}
            </span>
          </span>
        </>
      ) : number !== undefined ? (
        <span className="relative flex h-10 w-8 shrink-0 items-center justify-center text-sm tabular-nums text-dim">
          <span className={cx(hover && action && 'group-hover/track:opacity-0', current && 'opacity-0')}>{number}</span>
          <span aria-hidden="true" className={cx('absolute inset-0 flex items-center justify-center', current ? 'text-accent' : 'text-fg opacity-0', hover && action && 'group-hover/track:opacity-100')}>
            {playMark}
          </span>
        </span>
      ) : showCover ? (
        <span className="relative flex h-10 w-10 shrink-0 items-center justify-center">
          <Cover src={track.cover_small_url || track.cover_url} size={40} alt="" />
          <span aria-hidden="true" className={cx('absolute inset-0 flex items-center justify-center rounded-md bg-black/60 text-white', !current && 'opacity-0', hover && action && 'group-hover/track:opacity-100')}>
            {playMark}
          </span>
        </span>
      ) : null;
    const names = (sub: string) => (
      <span className="block min-w-0 flex-1">
        <span className={cx('block truncate text-[15px]', titleColor)}>{track.title}</span>
        <span className="flex items-center gap-1.5 text-[13px] text-muted">
          <KeptMark refId={track.ref} />
          {track.explicit && <ExplicitBadge />}
          <span className="truncate">{sub}</span>
        </span>
      </span>
    );
    const tap = (className: string, children: ReactNode) =>
      toAlbum ? (
        <Link to={toAlbum} className={className}>{children}</Link>
      ) : action ? (
        <button type="button" onClick={action} onPointerDown={track.owned ? undefined : () => touchTrack(track.ref)} className={cx(className, 'cursor-pointer')} aria-label={playLabel}>
          {children}
        </button>
      ) : (
        <div className={className}>{children}</div>
      );
    const shown = 'opacity-0 focus-visible:opacity-100 group-hover/track:opacity-100';
    const marked = '!opacity-100'; // shown on every row the drag passes, in the accent colour
    const dragList = () => (queue?.length ? queue : [track]).map(asSel);

    if (desktop) {
      const album = showAlbum && cols.album;
      const added = showAdded && cols.added;
      const plays = playsOf !== undefined || (showPlays && cols.plays);
      const tagCols = showTags ? { lang: cols.tagLang, genre: cols.tagGenre, own: cols.tagOwn } : null;
      const myTags = showTags && tagKey(track) ? (tagState.map[tagKey(track) as string] ?? []).map((id) => tagState.byId.get(id)).filter((t) => !!t) : [];
      const tagCell = (group: 'lang' | 'genre' | 'own', max: number) => {
        const list = myTags.filter((t) => t.group === group);
        return (
          <span className="flex min-w-0 items-center gap-1 overflow-hidden" title={list.map((t) => t.name).join(', ') || undefined}>
            {list.slice(0, max).map((t) => (
              <span key={t.id} className="truncate rounded-full bg-fg/10 px-2 py-0.5 text-[11px] text-muted">{t.name}</span>
            ))}
            {list.length > max && <span className="shrink-0 rounded-full bg-fg/10 px-1.5 py-0.5 text-[11px] text-muted">+{list.length - max}</span>}
          </span>
        );
      };
      const rowNumber = number ?? index;
      // every credited artist opens its own page: the catalog's when the Hub knows the ref, else the library's
      const credited = track.artists?.length ? track.artists : [track.artist];
      const credits = credited.map((name, i) => {
        const ref = track.artist_refs?.[i] ?? (name === track.artist ? track.artist_ref : null);
        const nd = track.nd_artist_ids?.[i] ?? (credited.length === 1 ? track.nd_artist_id : null);
        return { name, to: ref ? `/artist/${enc(ref)}` : nd ? `/lib/artist/${enc(nd)}` : null };
      });
      const albumTo = track.album_ref ? `/album/${enc(track.album_ref)}` : track.nd_album_id ? `/lib/album/${enc(track.nd_album_id)}` : null;
      return (
        <div
          ref={rowRef}
          onClickCapture={onPick}
          onMouseDown={(e) => e.shiftKey && e.preventDefault()}
          onMouseEnter={() => {
            dragSelectOver(asSel(track));
            dragActionOver(asSel(track));
          }}
          data-track-row
          aria-selected={selected}
          className={cx('group/track mx-2 grid min-h-14 items-center gap-3 rounded-lg px-2 transition-colors', unreachable && 'opacity-40', selected ? 'bg-accent/15 hover:bg-accent/20' : dragMark ? 'bg-accent/10' : 'hover:bg-fg/5 focus-within:bg-fg/5')}
          style={{ gridTemplateColumns: trackGrid(rowNumber !== undefined, album, cols.duration, added, tagCols, plays, cols.file) }}
        >
          {/* the number is the queue button: «в очередь» on hovering the row, green once the track is queued */}
          {rowNumber !== undefined && (
            <span data-row-actions className="contents" onMouseDown={(e) => e.button === 0 && !e.shiftKey && !e.ctrlKey && !e.metaKey && beginDragAction('queue', dragList(), asSel(track))}>
              <QueueButton
                track={menuTrack.listen as ListenTrack}
                instead={<span className={cx(current && 'text-accent')}>{current && !showCover ? playMark : rowNumber}</span>}
                marked={dragMark === 'queue' || dragMark === 'unqueue'}
                className={cx(dragMark === 'queue' ? cx(marked, '!text-accent') : dragMark === 'unqueue' && cx(marked, '!text-muted'))}
              />
            </span>
          )}
          <div
            onClick={toAlbum ? () => navigate(toAlbum) : action}
            onPointerDown={track.owned ? undefined : () => touchTrack(track.ref)}
            className={cx('flex min-w-0 items-center gap-3 py-1.5 text-left', (toAlbum || action) && 'cursor-pointer')}
          >
            {showCover &&
              (action ? (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    action();
                  }}
                  aria-label={playLabel}
                  className="relative h-10 w-10 shrink-0 cursor-pointer"
                >
                  <Cover src={track.cover_small_url || track.cover_url} size={40} alt="" />
                  <span aria-hidden="true" className={cx('absolute inset-0 flex items-center justify-center rounded-md bg-black/60 text-white', !current && 'opacity-0 group-hover/track:opacity-100')}>
                    {playMark}
                  </span>
                </button>
              ) : (
                <Cover src={track.cover_small_url || track.cover_url} size={40} alt="" />
              ))}
            <span className="block min-w-0 flex-1">
              {tap(cx('block max-w-full truncate text-left text-[15px]', titleColor), track.title)}
              <span className="flex items-center gap-1.5 text-[13px] text-muted">
                <KeptMark refId={track.ref} />
                {track.explicit && <ExplicitBadge />}
                <span className="truncate">
                  {credits.map((a, i) => (
                    <span key={`${a.name}:${i}`}>
                      {i > 0 && ', '}
                      {a.to ? (
                        <Link to={a.to} onClick={(e) => e.stopPropagation()} className="hover:text-fg hover:underline">{a.name}</Link>
                      ) : (
                        a.name
                      )}
                    </span>
                  ))}
                </span>
              </span>
            </span>
          </div>
          {album &&
            (albumTo && track.album_title ? (
              <Link to={albumTo} className="truncate text-sm text-muted hover:text-fg hover:underline">{track.album_title}</Link>
            ) : (
              <span className="truncate text-sm text-muted">{track.album_title}</span>
            ))}
          {tagCols?.lang && tagCell('lang', 2)}
          {tagCols?.genre && tagCell('genre', 1)}
          {tagCols?.own && tagCell('own', 2)}
          {added && <span className="truncate text-sm text-muted" title={track.added_at ? parseTime(track.added_at).toLocaleString('ru-RU') : undefined}>{fmtAdded(track.added_at)}</span>}
          {plays && <PlaysCell songId={track.nd_song_id} count={playsOf ?? ((track.nd_song_id && playCounts[track.nd_song_id]) || 0)} title={track.title} artist={track.artist} />}
          {/* always a cell, so the columns line up: a track with no file of its own has it empty */}
          {cols.file && (
            <span className="truncate text-xs text-muted">
              {track.file &&
                (track.file.journal && track.library_track_id ? (
                  <Link to={`/file/${track.library_track_id}`} onClick={(e) => e.stopPropagation()} className="hover:text-fg hover:underline" title="Как скачан этот файл">
                    <FileInfo info={track.file} />
                  </Link>
                ) : (
                  <FileInfo info={track.file} />
                ))}
            </span>
          )}
          <div data-row-actions className="flex h-11 items-center justify-end">
            {track.owned || dl === 'done' ? null : downloading ? (
              <span className="flex h-11 w-9 items-center justify-center text-info" title="Скачивается"><Spinner size={14} /></span>
            ) : (
              <IconButton icon="download" size={20} label="Скачать трек" onClick={download} busy={busy} className={cx('!w-9 text-muted hover:text-fg', !busy && shown)} />
            )}
            {/* held and dragged over other rows, a button is theirs too: done to all of them when let go (lib/dragAction.ts) */}
            {track.owned && (
              <span className="contents" onMouseDown={(e) => e.button === 0 && beginDragAction('share', dragList(), asSel(track))}>
                <IconButton icon="share" size={19} label={`Поделиться: «${track.title}»`} onClick={() => openShare(menuTrack)} className={cx('!w-9', dragMark === 'share' ? cx(marked, '!text-accent') : cx('text-muted hover:text-fg', shown))} />
              </span>
            )}
            {/* a list without numbers keeps «в очередь» here */}
            {rowNumber === undefined && (
              <span className="contents" onMouseDown={(e) => e.button === 0 && beginDragAction('queue', dragList(), asSel(track))}>
                <QueueButton track={menuTrack.listen as ListenTrack} className={cx('!w-9', dragMark === 'queue' ? cx(marked, '!text-accent') : dragMark === 'unqueue' && cx(marked, '!text-muted'))} />
              </span>
            )}
            {tagKey(track) && (
              <span className="contents" onMouseDown={(e) => e.button === 0 && beginDragAction('tags', dragList(), asSel(track))}>
                <IconButton icon="tag" size={19} label={`Теги: «${track.title}»`} onClick={() => openTagsSheet([tagKey(track) as string], track.title)} className={cx('!w-9', dragMark === 'tags' ? cx(marked, '!text-accent') : cx('text-muted hover:text-fg', shown))} />
              </span>
            )}
            <span className="contents" onMouseDown={(e) => e.button === 0 && beginDragAction('like', dragList(), asSel(track))}>
              <SaveButton songId={likeTarget.songId} refId={track.ref} listKey={tagKey(track)} title={track.title} data={likeTarget.data} className={cx('!w-9', dragMark === 'like' || dragMark === 'lists' ? cx(marked, '!text-accent') : !kept && shown)} />
            </span>
          </div>
          {cols.duration && <span className="text-right text-xs tabular-nums text-dim">{fmtDuration(track.duration_s)}</span>}
          {/* ⋯ held and dragged over other rows picks them all; a row that is picked wears a tick in its place */}
          <span
            data-row-actions
            className="contents"
            onMouseDown={(e) => {
              if (e.button !== 0 || e.shiftKey || e.ctrlKey || e.metaKey) return;
              e.preventDefault(); // no text is selected while the mouse drags
              beginDragSelect(dragList(), asSel(track));
            }}
            onClickCapture={(e) => {
              if (!dragSelectMoved()) return;
              e.stopPropagation(); // the drag came back to where it began: not a click on the button
              e.preventDefault();
            }}
          >
            <TrackMenuButton track={menuTrack} extra={menuExtra} noTags picked={selected} onPick={selecting ? () => toggleSelected(asSel(track)) : undefined} className={cx('!h-8 !w-8', shown)} />
          </span>
        </div>
      );
    }

    const extras: MenuExtra[] = [
      ...(!track.owned && dl !== 'done' && !downloading ? [{ icon: 'download' as const, label: 'Скачать', onClick: () => void download() }] : []),
      selectExtra,
      ...(menuExtra ?? []),
    ];
    return (
      <div ref={rowRef} onClickCapture={onPick} aria-selected={selected}>
        <SwipeRow
          right={{ icon: 'queue', onAction: () => { addToQueue([menuTrack.listen as ListenTrack]); toast(`В очереди: «${track.title}»`, 'success'); } }}
          left={{ icon: 'plus', armedIcon: 'check', onAction: () => setPicking(true) }}
        >
          <div className={cx('flex min-h-16 items-center gap-1 pl-4 pr-1', selected && 'bg-accent/15', unreachable && 'opacity-40')}>
            {tap('flex min-w-0 flex-1 items-center gap-3 py-2 text-left', (
              <>
                {lead(false)}
                {names(artistLine)}
              </>
            ))}
            {downloading && <span className="flex w-6 justify-center text-info" title="Скачивается"><Spinner size={14} /></span>}
            {selected && <Icon name="check" size={18} stroke={3} className="shrink-0 text-accent" />}
            <span data-row-actions className="contents">
              <TrackMenuButton track={menuTrack} extra={extras} className="w-10" />
            </span>
          </div>
        </SwipeRow>
        {picking && <TrackPlaylists track={menuTrack} onClose={() => setPicking(false)} />}
      </div>
    );
  }

  const rowClass = cx('flex min-w-0 flex-1 items-center gap-3 py-2 text-left', rowPlays && 'cursor-pointer');
  return (
    <div ref={rowRef} className={cx('flex min-h-16 items-center gap-3 pl-4 pr-1', rowPlays && 'group/track transition-colors hover:bg-fg/5 focus-within:bg-fg/5')}>
      {!track.owned ? (
        <button type="button" onClick={listenHere} onPointerDown={() => touchTrack(track.ref)} className={rowClass} aria-label={rowPlays ? playLabel : `Послушать «${track.title}»`}>
          {body}
        </button>
      ) : link && track.album_ref ? (
        <Link to={`/album/${enc(track.album_ref)}`} className={rowClass}>
          {body}
        </Link>
      ) : play ? (
        <button type="button" onClick={play} className={rowClass} aria-label={rowPlays ? playLabel : `Слушать «${track.title}»`}>
          {body}
        </button>
      ) : (
        <div className={rowClass}>{body}</div>
      )}
      <div className="-ml-2 flex shrink-0 items-center">
        <SaveButton songId={track.owned ? track.nd_song_id : null} refId={track.ref} listKey={tagKey(track)} title={track.title} data={{ ...track }} className="!w-10" />
        {track.owned ? (
          // a plain browser: the row itself plays it, a second ▷ would only take the title's room
          STANDALONE && !(link && track.album_ref) && play ? null : <TrackStatus owned downloading={false} onDownload={download} onPlay={play} />
        ) : (
          <>
            <ListenButton track={track} onListen={listenHere} />
            <TrackStatus owned={dl === 'done'} downloading={track.downloading || dl === 'queued'} onDownload={download} busy={busy} />
          </>
        )}
        <TrackMenuButton
          track={{
            title: track.title,
            artist: track.artists?.length ? track.artists.join(', ') : track.artist,
            ref: track.ref,
            nd_song_id: track.nd_song_id,
            artist_ref: track.artist_ref,
            album_ref: track.album_ref,
            nd_album_id: track.nd_album_id,
            nd_artist_id: track.nd_artist_id,
            listen: listenTrack(track),
          }}
          extra={menuExtra}
          className="-ml-1 w-8"
        />
      </div>
    </div>
  );
}

export function ArtistBubble({ artist }: { artist: CatalogArtist }) {
  return (
    <Link to={`/artist/${enc(artist.ref)}`} className="flex w-28 shrink-0 flex-col items-center gap-2 text-center">
      <Cover src={artist.picture_small_url || artist.picture_url} size={104} round icon="user" alt={artist.name} />
      <span className="line-clamp-2 text-sm leading-tight">{artist.name}</span>
    </Link>
  );
}

/** Release card for grids and horizontal rows; optional download button. */
export function ReleaseCard({ album, showArtist = false, onDownload, busy }: { album: AlbumOut; showArtist?: boolean; onDownload?: () => void; busy?: boolean }) {
  const year = yearOf(album);
  return (
    <div className="relative min-w-0">
      <Link to={`/album/${enc(album.ref)}`} className="block">
        <Cover src={album.cover_small_url || album.cover_url} alt={album.title} className="aspect-square w-full" />
        <div className="mt-2 line-clamp-2 text-sm font-medium leading-tight">
          {album.explicit && (
            <span className="mr-1 inline-block align-[-2px]">
              <ExplicitBadge />
            </span>
          )}
          {album.title}
        </div>
        <div className="mt-0.5 truncate text-xs text-muted">
          {[showArtist ? album.artist : null, year, recordTypeLabel(album.record_type)].filter(Boolean).join(' · ')}
        </div>
      </Link>
      <div className="mt-1.5 flex items-center justify-between gap-1">
        <OwnedBadge status={album.owned_status} count={album.owned_count} total={album.nb_tracks} />
        {onDownload && album.owned_status !== 'full' && (
          <IconButton icon="download" label={`Скачать «${album.title}»`} onClick={onDownload} busy={busy} className="-my-2 -mr-2 ml-auto text-muted" size={20} />
        )}
      </div>
    </div>
  );
}

/** The head of a track list on a computer: the names of the columns over `TrackRow`s (the same grid), and the menu
 * that switches the optional ones (a click on ⋯ or a right click on the head). `numbered`, `album` — as the rows
 * of this list are drawn (`number`, `showAlbum`). On a phone and inside the app there is no table and no head. */
export type ListSort = 'new' | 'old' | 'most' | 'least' | null;

/** `added`: the list has «Дата добавления»; a click on that column sorts by it — the newest first, then the oldest
 * first, then the list's own order again (`sort`, `onSort`). `plays`: the «Прослушивания» column, sorted the same
 * way — the most listened to first, then the least, then the list's own order. */
export function TrackTableHeader({ numbered = false, album = false, added = false, tags = false, plays = false, playsLabel, sort = null, onSort }: { numbered?: boolean; album?: boolean; added?: boolean; tags?: boolean; plays?: boolean; playsLabel?: string; sort?: ListSort; onSort?: (next: ListSort) => void }) {
  const desktop = useDesktop();
  const cols = useTrackColumns();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', close);
    document.body.dataset.colsMenu = '1'; // the list's floating search button steps aside (ListSearch.tsx)
    return () => {
      delete document.body.dataset.colsMenu;
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', close);
    };
  }, [open]);
  if (!desktop) return null;
  const offered = (Object.keys(TRACK_COLUMN_LABELS) as (keyof TrackColumns)[]).filter((c) => (c !== 'album' || album) && (c !== 'added' || added) && (c !== 'plays' || (plays && !playsLabel)) && (!c.startsWith('tag') || tags));
  return (
    <div
      ref={ref}
      onContextMenu={(e) => {
        e.preventDefault();
        setOpen(true);
      }}
      className="group/head relative mx-2 mb-1 grid h-9 items-center gap-3 border-b border-line/60 px-2 text-xs text-dim"
      style={{ gridTemplateColumns: trackGrid(numbered, album && cols.album, cols.duration, added && cols.added, tags ? { lang: cols.tagLang, genre: cols.tagGenre, own: cols.tagOwn } : null, !!playsLabel || (plays && cols.plays), cols.file) }}
    >
      {numbered && <span className="text-center">#</span>}
      <span>Название</span>
      {album && cols.album && <span>Альбом</span>}
      {tags && cols.tagLang && <span>Язык</span>}
      {tags && cols.tagGenre && <span>Жанр</span>}
      {tags && cols.tagOwn && <span>Теги</span>}
      {added && cols.added && (
        <button
          type="button"
          onClick={() => onSort?.(sort === 'new' ? 'old' : sort === 'old' ? null : 'new')}
          title={sort === 'new' ? 'Сначала новые — нажмите: сначала старые' : sort === 'old' ? 'Сначала старые — нажмите: порядок плейлиста' : 'Сортировать: сначала новые'}
          className={cx('flex items-center gap-1.5 text-left transition hover:text-fg', (sort === 'new' || sort === 'old') && 'text-fg')}
        >
          Дата добавления
          {(sort === 'new' || sort === 'old') && <span className="text-[10px] text-accent" aria-hidden="true">{sort === 'new' ? '▼' : '▲'}</span>}
        </button>
      )}
      {playsLabel ? (
        <span title="Сколько раз вы слушали трек за период этого списка">{playsLabel}</span>
      ) : plays && cols.plays && (
        <button
          type="button"
          onClick={() => onSort?.(sort === 'most' ? 'least' : sort === 'least' ? null : 'most')}
          title={sort === 'most' ? 'Сначала самые слушаемые — нажмите: сначала редкие' : sort === 'least' ? 'Сначала редкие — нажмите: обычный порядок' : 'Сколько раз вы слушали трек (от 30 секунд). Нажмите, чтобы отсортировать'}
          className={cx('flex items-center gap-1.5 text-left transition hover:text-fg', (sort === 'most' || sort === 'least') && 'text-fg')}
        >
          Прослушивания
          {(sort === 'most' || sort === 'least') && <span className="text-[10px] text-accent" aria-hidden="true">{sort === 'most' ? '▼' : '▲'}</span>}
        </button>
      )}
      {cols.file && <span title="Откуда файл, его формат и битрейт">Файл</span>}
      <span />
      {cols.duration && <span className="text-right">Время</span>}
      <IconButton icon="more" size={18} label="Колонки таблицы" aria-expanded={open} onClick={() => setOpen((v) => !v)} className={cx('!h-8 !w-8 text-dim hover:text-fg focus-visible:opacity-100', !open && 'opacity-0 group-hover/head:opacity-100')} />
      {open && (
        <div role="menu" aria-label="Колонки" className="absolute right-0 top-9 z-20 w-56 rounded-xl border border-line bg-raised p-2 shadow-lg">
          <div className="px-2 pb-1 pt-1 text-xs font-semibold text-muted">Колонки</div>
          {offered.map((c) => (
            <label key={c} className="flex cursor-pointer items-center justify-between rounded-lg pl-2 text-sm text-fg hover:bg-fg/5">
              {TRACK_COLUMN_LABELS[c]}
              <Checkbox checked={cols[c]} onChange={(v) => setTrackColumn(c, v)} label={TRACK_COLUMN_LABELS[c]} />
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
