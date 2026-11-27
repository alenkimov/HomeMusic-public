import { TrackTitle } from './TrackTitle';
import { titleWithVersion, useVersionLabel } from '../lib/versionLabels';
import { plural } from '../lib/format';
import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { useOfHiddenArtist } from '../lib/dislikes';
import { KeptMark, useUnreachable } from './Offline';
import { FileInfo } from './FileInfo';
import { filePickerTarget, needsTrackFile, openFindFile } from '../lib/findFile';
import type { AlbumOut, CatalogArtist, TrackOut } from '../api/types';
import { fmtAdded, fmtCompactCount, fmtDuration, fmtNum, parseTime } from '../lib/format';
import { useDesktop } from '../lib/desktop';
import { addToQueue, downloadRef, listen, listenAll, touchTrack, useListen, watchOnScreen, type ListenTrack } from '../lib/listen';
import { Link, albumPath, artistPath, enc, navigate } from '../lib/router';
import { beginDragAction, dragActionOver, useDragMark } from '../lib/dragAction';
import { beginDragSelect, dragSelectMoved, dragSelectOver, selectRange, showList, toggleSelected, useSelected, useSelectionCount, type SelTrack } from '../lib/selection';
import { useInPlaylists } from '../lib/inPlaylists';
import { openTagsSheet, tagKey } from '../lib/tags';
import { useLiked } from '../lib/stars';
import { usePlayCounts } from '../lib/stats';
import { PlaysCell } from './PlaysCell';
import { GenreCell } from './GenreSources';
import { ServiceIcon } from './ServiceBadge';
import { AlbumSourceMarks, CatalogSourceMarks } from './CatalogSourceMarks';
import { TRACK_COLUMN_LABELS, setTrackColumn, trackGrid, useTrackColumns, type TrackColumns } from '../lib/trackColumns';
import { audienceKey } from '../lib/trackAudience';
import { toast } from '../lib/ui';
import { Icon } from './Icon';
import { openShare } from '../lib/shareTrack';
import { useTags } from '../lib/tags';
import { QueueButton } from './QueueButton';
import { SwipeRow } from './SwipeRow';
import { SaveButton, TrackPlaylists } from './PlaylistsButton';
import { TrackMenuButton, type MenuExtra, type MenuTrack } from './TrackMenu';
import { openTrackMenu, trackMenuAt } from '../lib/trackMenu';
import { menuPressProps, useContextMenu } from './ContextMenu';
import { AlbumMenu, ArtistMenu } from './EntityMenus';
import { AlbumCard, AlbumLink, catalogLook } from './Album';
import { ArtistCard } from './Artist';
import { Chip } from './Chip';
import { useTrackTable } from './TrackTable';
import { Checkbox, Cover, IconButton, OwnedBadge, PlayingBars, Spinner, cx } from './ui';

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
  /** Owned and playable: a play button instead of the «есть» mark. */
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
export function listenTrack(t: TrackOut & { nd_album_id?: string | null; nd_artist_id?: string | null; nd_artist_ids?: (string | null)[]; page_url?: string | null }): ListenTrack {
  // every credited artist with the page it opens, as a track's row has them: the catalog's, else the library's
  const credited = t.artists?.length ? t.artists : [t.artist];
  const artists = credited.map((name, i) => {
    const ref = t.artist_refs?.[i] ?? (name === t.artist ? t.artist_ref : null);
    const nd = t.nd_artist_ids?.[i] ?? (credited.length === 1 ? t.nd_artist_id : null);
    const id = t.hub_artist_ids?.[i] ?? (name === t.artist ? t.hub_artist_id : null);
    return { name, ...(ref ? { ref } : {}), ...(nd ? { nd } : {}), ...(id ? { id } : {}) };
  });
  return {
    ref: t.ref,
    nd_song_id: t.nd_song_id,
    library_track_id: t.library_track_id,
    title: t.title,
    artist: t.artists?.length ? t.artists.join(', ') : t.artist,
    cover_url: t.cover_url,
    cover_small_url: t.cover_small_url,
    page_url: t.page_url,
    album_ref: t.album_ref,
    release_id: t.release_id,
    nd_album_id: t.nd_album_id,
    nd_artist_id: t.nd_artist_id,
    artist_ref: t.artist_ref,
    hub_artist_id: t.hub_artist_id,
    artists,
    album: t.album_title ?? undefined, isrc: t.isrc ?? undefined,
    year: t.release_date ? Number(t.release_date.slice(0, 4)) || undefined : undefined,
    duration_s: t.duration_s,
    spotify_plays: t.spotify_plays,
  };
}

/** A track of the catalog or the library for its menu where there is no row of `TrackRow` (the history, the
 * statistics): the same lines, but «Скачать» and «Выбрать файл» — those belong to a row. */
export function menuTrackOfOut(t: TrackOut & { nd_album_id?: string | null; nd_artist_id?: string | null; page_url?: string | null }): MenuTrack {
  return {
    title: t.title,
    artist: t.artists?.length ? t.artists.join(', ') : t.artist,
    ref: t.ref,
    nd_song_id: t.nd_song_id,
    key: tagKey(t),
    library_track_id: t.library_track_id,
    has_file: t.owned,
    unknown: t.unknown,
    artist_ref: t.artist_ref,
    hub_artist_id: t.hub_artist_id,
    album_ref: t.album_ref,
    release_id: t.release_id,
    nd_album_id: t.nd_album_id,
    nd_artist_id: t.nd_artist_id,
    listen: needsTrackFile(t) ? undefined : listenTrack(t),
    data: { ...t },
    cover: t.cover_small_url || t.cover_url,
  };
}

/** «Слушать» / «Перемешать» of a whole list (an album, a playlist, «Любимое»). */
export function playAll(list: readonly TrackOut[], opts: { shuffle?: boolean; context?: string; page?: string } = {}) {
  if (!list.length) return;
  listenAll(list.map(listenTrack), opts);
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
      onClick={onListen}
      className={phase ? '!text-accent' : 'text-muted hover:text-fg'}
    />
  );
}

/** A catalog track with ♥, ownership status and a download button. Owned tracks play, the rest can be listened to
 * before downloading (a tap on the row or ▷), and the player goes on through `queue` (the album or the list). */
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
  queue,
  rows,
  menuExtra,
  note,
  primaryAction,
  onFindFile,
  index,
  foundIn,
  releaseFeed = false,
}: {
  track: TrackOut & { nd_album_id?: string | null; nd_artist_id?: string | null; nd_artist_ids?: (string | null)[]; page_url?: string | null };
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
  /** The list this row is in: the page player plays on through it. */
  queue?: readonly TrackOut[];
  /** Every row of the list as shown, the grey ones (no file yet) too, where `queue` leaves them out: what a pick
   * (Ctrl+A, Shift+click, a drag of ⋯) and a button dragged over the rows go through. By default `queue`. */
  rows?: readonly TrackOut[];
  /** More actions of the row's ⋯ menu (a playlist's «Убрать из плейлиста»). */
  menuExtra?: MenuExtra[];
  note?: ReactNode;
  primaryAction?: ReactNode;
  onFindFile?: () => void | Promise<void>;
  foundIn?: readonly string[];
  /** Compact rows with wrapped titles and duration inside the release feed. */
  releaseFeed?: boolean;
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
  const desktop = useDesktop();
  const table = useTrackTable();
  const unknown = !!track.unknown;
  const needsFile = needsTrackFile(track);
  const grey = needsFile && '[&_img]:opacity-40 [&_img]:grayscale';
  const titleColor = needsFile ? 'text-dim' : current ? 'text-accent' : 'text-fg';
  const pickerTarget = filePickerTarget(track);
  const findFile = !track.owned && ((unknown && onFindFile) || pickerTarget) ? async () => {
    if (busy) return;
    if (!unknown || !onFindFile) {
      if (pickerTarget) openFindFile(pickerTarget);
      return;
    }
    setBusy(true);
    try {
      await onFindFile();
    } finally {
      setBusy(false);
    }
  } : undefined;
  const searchOnCover = !!findFile && (needsFile || phase === 'error');
  // among an artist's most listened: how many listened to the track on Last.fm, after who it is by
  const heard = track.lastfm_listeners ? (
    <span className="shrink-0 whitespace-nowrap tabular-nums text-dim" title="Слушателей на Last.fm">
      · {fmtNum(track.lastfm_listeners)} {plural(track.lastfm_listeners, ['слушатель', 'слушателя', 'слушателей'])}
    </span>
  ) : null;
  const spotify = track.spotify_plays != null ? (
    <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap tabular-nums text-dim"
      title={`${fmtNum(track.spotify_plays)} прослушиваний в Spotify`}
      aria-label={`${fmtNum(track.spotify_plays)} прослушиваний в Spotify`}>
      <ServiceIcon service="spotify" size={13} />
      {fmtNum(track.spotify_plays)}
    </span>
  ) : null;
  // on screen: the Hub picks the file in the background, so a tap starts at once
  useEffect(() => (rowRef.current && !track.owned && !needsFile ? watchOnScreen(rowRef.current, track.ref) : undefined), [track.ref, track.owned, needsFile]);
  const download = async () => {
    setBusy(true);
    await downloadRef(track.ref);
    setBusy(false);
  };
  const listenHere = () => listen(listenTrack(track), queue?.map(listenTrack));
  const play = track.owned ? listenHere : undefined;
  const label = useVersionLabel(track);
  const displayTitle = titleWithVersion(track.title, track, label);
  const playLabel = playing ? `Пауза «${displayTitle}»` : `Слушать «${displayTitle}»`;
  const findLabel = `Найти файл: «${displayTitle}»`;
  const trackName = <TrackTitle title={releaseFeed ? <span className="line-clamp-2 whitespace-normal wrap-anywhere">{track.title}</span> : track.title} label={label} mark={track.owned ? track.library_track_id : null} />;
  const sourceMarks = foundIn?.length ? <CatalogSourceMarks services={foundIn} /> : null;
  const action = needsFile || searchOnCover ? undefined : !track.owned ? listenHere : play;
  const toAlbum = link && track.owned && (track.album_ref || track.release_id)
    ? albumPath({ ref: track.album_ref ?? '', release_id: track.release_id }) : null;
  const openTrack = toAlbum ? () => navigate(toAlbum) : action;
  const tap = (className: string, children: ReactNode, linkToAlbum = true) =>
    toAlbum && linkToAlbum ? (
      <Link to={toAlbum} onClick={(e) => e.stopPropagation()} className={className}>{children}</Link>
    ) : action ? (
      <button type="button" onClick={(e) => { e.stopPropagation(); action(); }} onPointerDown={track.owned ? undefined : () => touchTrack(track.ref)} className={cx(className, 'cursor-pointer')} aria-label={playLabel}>
        {children}
      </button>
    ) : (
      <div className={className}>{children}</div>
    );
  const missingCover = (size: number) => (
    <button
      type="button"
      data-row-actions
      aria-label={findLabel}
      title={findLabel}
      aria-busy={busy || undefined}
      disabled={busy}
      onClick={(e) => { e.stopPropagation(); void findFile?.(); }}
      className="relative shrink-0 cursor-pointer rounded-md"
    >
      <Cover src={track.cover_small_url || track.cover_url} size={size} alt="" />
      <span aria-hidden="true" className={cx('absolute inset-0 flex items-center justify-center rounded-md bg-black/60 text-white transition-opacity', desktop && !busy && 'opacity-0 group-hover/track:opacity-100 group-focus-within/track:opacity-100')}>
        {busy ? <Spinner size={18} /> : <Icon name="search" size={18} />}
      </span>
    </button>
  );
  const cols = useTrackColumns();
  const [picking, setPicking] = useState(false);

  const likeTarget = { targetType: 'track' as const, trackId: track.library_track_id, songId: track.owned ? track.nd_song_id : null, ref: track.ref, data: { ...track } };
  const liked = useLiked(likeTarget);
  const kept = useInPlaylists(tagKey(track)) || liked; // the row's button shows ♥ or ✓
  // no network and not kept on the device, or of an artist the user does not listen to: it does not play by itself
  const offline = useUnreachable(track.ref, track.library_track_id);
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
    unknown: track.unknown,
    onFindFile: findFile,
    artist_ref: track.artist_ref,
    hub_artist_id: track.hub_artist_id,
    album_ref: track.album_ref,
    release_id: track.release_id,
    nd_album_id: track.nd_album_id,
    nd_artist_id: track.nd_artist_id,
    listen: needsFile ? undefined : listenTrack(track),
    data: { ...track },
    cover: track.cover_small_url || track.cover_url,
    downloading: !needsFile && !track.owned && (track.downloading || dl === 'queued'),
    onDownload: !primaryAction && !track.owned && dl !== 'done' && !needsFile ? () => void download() : undefined,
  };
  // picking several rows to act on together (`lib/selection.ts`)
  const selected = useSelected(track.ref);
  const tagState = useTags();
  const playCounts = usePlayCounts(showPlays || playsOf !== undefined);
  const dragMark = useDragMark(track.ref); // this row is among the rows a button is being dragged over
  const selecting = useSelectionCount() > 0;
  // the row is of this list: Ctrl+A picks all of it (`lib/selection.ts`)
  const shownList = rows ?? queue;
  useEffect(() => (shownList?.length ? showList(shownList, () => shownList.map(asSel)) : undefined), [shownList]);
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
  const downloading = !needsFile && (track.downloading || dl === 'queued');
  // over the cover (or in place of the number): the wheel while the file is being got — the one mark of that in
  // the row — else ▶ / ‖ under the mouse; a track that plays is told by its bars and its name, not by a ‖ left on it
  const playMark = <Icon name={playing ? 'pause' : 'play'} size={18} filled />;
  const lead = (hover: boolean): ReactNode =>
    showCover ? (
      <>
        {number !== undefined && <span className="w-5 shrink-0 text-center text-sm tabular-nums text-dim">{number}</span>}
        {searchOnCover ? missingCover(40) : (
          <span className="relative flex h-10 w-10 shrink-0 items-center justify-center">
            <Cover src={track.cover_small_url || track.cover_url} size={40} alt="" />
            <span aria-hidden="true" className={cx('absolute inset-0 flex items-center justify-center rounded-md bg-black/60 text-white', !waiting && !(releaseFeed && current) && 'opacity-0', hover && action && 'group-hover/track:opacity-100', releaseFeed && action && 'group-focus-within/track:opacity-100')}>
              {playMark}
            </span>
          </span>
        )}
      </>
    ) : number !== undefined ? (
      <span className="relative flex h-10 w-8 shrink-0 items-center justify-center text-sm tabular-nums text-dim">
        <span className={cx(hover && action && 'group-hover/track:opacity-0', waiting && 'opacity-0')}>{number}</span>
        <span aria-hidden="true" className={cx('absolute inset-0 flex items-center justify-center', waiting ? 'text-accent' : 'text-fg opacity-0', hover && action && 'group-hover/track:opacity-100')}>
          {playMark}
        </span>
      </span>
    ) : null;
  const nameClass = cx('min-w-0 text-[15px]', !releaseFeed && 'truncate', titleColor);
  const names = (sub: ReactNode, separateAction = false) => (
    <span className="block min-w-0 flex-1">
      <span className="flex min-w-0 items-center gap-1.5">
        {/* the track that is playing: on a phone the bars stand before its name (a computer has them in place of
            the row's number, where the row has one) */}
        {current && !needsFile && (!desktop || releaseFeed || (number ?? index) === undefined) && <PlayingBars on={phase === 'playing'} />}
        {separateAction ? (
          <>
            {tap(cx(nameClass, 'text-left'), trackName)}
          </>
        ) : (
          <span className={nameClass}>{trackName}</span>
        )}
      </span>
      <span className={cx('flex items-center gap-1.5 text-[13px]', needsFile ? 'text-dim' : 'text-muted')}>
        <KeptMark refId={track.ref} />
        <span className={releaseFeed ? 'line-clamp-2 min-w-0 wrap-anywhere' : 'truncate'}>{sub}</span>
        {sourceMarks}
      </span>
      {note}
    </span>
  );
  const shown = 'opacity-0 focus-visible:opacity-100 group-hover/track:opacity-100';
  const marked = '!opacity-100'; // shown on every row the drag passes, in the accent colour
  const dragList = () => (shownList?.length ? shownList : [track]).map(asSel);
  // Each credited artist opens its own catalog or library page.
  const artistLinks = () => {
    const credited = track.artists?.length ? track.artists : [track.artist];
    return credited.map((name, i) => {
      const ref = track.artist_refs?.[i] ?? (name === track.artist ? track.artist_ref : null);
      const nd = track.nd_artist_ids?.[i] ?? (credited.length === 1 ? track.nd_artist_id : null);
      const id = track.hub_artist_ids?.[i] ?? (name === track.artist ? track.hub_artist_id : null);
      const to = ref || id ? artistPath({ ref: ref ?? '', hub_artist_id: id }) : nd ? `/lib/artist/${enc(nd)}` : null;
      return (
        <span key={`${name}:${i}`}>
          {i > 0 && ', '}
          {to ? <Link to={to} onClick={(e) => e.stopPropagation()} className="hover:text-fg hover:underline">{name}</Link> : name}
        </span>
      );
    });
  };

  if (desktop && !table?.compact && !releaseFeed) {
    const visible = table?.visible ?? {
      ...cols, album: showAlbum && cols.album, added: showAdded && cols.added,
      plays: playsOf !== undefined || (showPlays && cols.plays), spotify: false, lastfm: false,
      tagLang: showTags && cols.tagLang, tagGenre: showTags && cols.tagGenre, tagOwn: showTags && cols.tagOwn,
    };
    const audience = table?.audience.get(audienceKey(track));
    const trackTagKey = tagKey(track);
    const myTags = showTags && trackTagKey ? (tagState.map[trackTagKey] ?? []).map((id) => tagState.byId.get(id)).filter((t) => !!t) : [];
    const tagCell = (group: 'lang' | 'own', max: number) => {
      const list = myTags.filter((t) => t.group === group);
      return (
        <span className="flex min-w-0 items-center gap-1 overflow-hidden" title={list.map((t) => t.name).join(', ') || undefined}>
          {list.slice(0, max).map((t) => <Chip key={t.id} size="xs" className="shrink">{t.name}</Chip>)}
          {list.length > max && <Chip size="xs">+{list.length - max}</Chip>}
        </span>
      );
    };
    const rowNumber = number ?? index;
    const albumTo = track.album_ref || track.release_id ? albumPath({ ref: track.album_ref ?? '', release_id: track.release_id })
      : track.nd_album_id ? `/lib/album/${enc(track.nd_album_id)}` : null;
    return (
      <div
        ref={rowRef}
        onClickCapture={onPick}
        onMouseDown={(e) => e.shiftKey && e.preventDefault()}
        onMouseEnter={() => {
          dragSelectOver(asSel(track));
          dragActionOver(asSel(track));
        }}
        onContextMenu={trackMenuAt(menuTrack, menuExtra)}
        data-track-row
        data-track-audience={table ? audienceKey(track) : undefined}
        aria-selected={selected}
        className={cx('group/track mx-2 grid min-h-14 items-center gap-3 rounded-lg px-2 transition-colors', unreachable && 'opacity-40', grey, selected ? 'bg-accent/15 hover:bg-accent/20' : dragMark ? 'bg-accent/10' : 'hover:bg-fg/5 focus-within:bg-fg/5')}
        style={{ gridTemplateColumns: trackGrid(rowNumber !== undefined, visible) }}
      >
        {/* the number is the queue button: «в очередь» on hovering the row, green once the track is queued */}
        {rowNumber !== undefined && needsFile && <span className="flex h-10 w-8 items-center justify-center text-sm tabular-nums text-dim">{rowNumber}</span>}
        {rowNumber !== undefined && !needsFile && (
          <span data-row-actions className="contents" onMouseDown={(e) => e.button === 0 && !e.shiftKey && !e.ctrlKey && !e.metaKey && beginDragAction('queue', dragList(), asSel(track))}>
            <QueueButton
              track={menuTrack.listen as ListenTrack}
              instead={current ? <PlayingBars on={phase === 'playing'} /> : <span>{rowNumber}</span>}
              marked={dragMark === 'queue' || dragMark === 'unqueue'}
              className={cx(dragMark === 'queue' ? cx(marked, '!text-accent') : dragMark === 'unqueue' && cx(marked, '!text-muted'))}
            />
          </span>
        )}
        <div
          onClick={openTrack}
          onPointerDown={!action || track.owned ? undefined : () => touchTrack(track.ref)}
          className={cx('flex min-w-0 items-center gap-3 py-1.5 text-left', (toAlbum || action) && 'cursor-pointer')}
        >
          {showCover &&
            (searchOnCover ? missingCover(40) : action ? (
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
                <span aria-hidden="true" className={cx('absolute inset-0 flex items-center justify-center rounded-md bg-black/60 text-white', !waiting && 'opacity-0 group-hover/track:opacity-100 group-focus-within/track:opacity-100')}>
                  {playMark}
                </span>
              </button>
            ) : (
              <Cover src={track.cover_small_url || track.cover_url} size={40} alt="" />
            ))}
          <span className="block min-w-0 flex-1">
            <span className="flex min-w-0 items-center gap-1">
              {tap(cx('min-w-0 truncate text-left text-[15px]', titleColor), trackName)}
            </span>
            <span className="flex items-center gap-1.5 text-[13px] text-muted">
              <KeptMark refId={track.ref} />
              <span className="truncate">{artistLinks()}</span>
              {sourceMarks}
              {!table && heard}
              {!table && spotify}
            </span>
            {note}
          </span>
        </div>
        {visible.album &&
          (track.album_title ? <AlbumLink title={track.album_title} to={albumTo} /> : <span />)}
        {visible.tagLang && tagCell('lang', 2)}
        {visible.tagGenre && <GenreCell genres={myTags.filter((t) => t.group === 'genre')}
          sources={tagState.genre_sources[trackTagKey ?? ''] ?? {}} title={track.title} />}
        {visible.tagOwn && tagCell('own', 2)}
        {visible.added && <span className="truncate text-sm text-muted" title={track.added_at ? parseTime(track.added_at).toLocaleString('ru-RU') : undefined}>{fmtAdded(track.added_at)}</span>}
        {visible.plays && <PlaysCell songId={track.nd_song_id} count={playsOf ?? ((track.nd_song_id && playCounts[track.nd_song_id]) || 0)} title={track.title} artist={track.artist} />}
        {visible.spotify && <AudienceCell value={track.spotify_plays ?? audience?.spotify_plays} label={TRACK_COLUMN_LABELS.spotify} />}
        {visible.lastfm && <AudienceCell value={track.lastfm_listeners ?? audience?.lastfm_listeners} label={TRACK_COLUMN_LABELS.lastfm} />}
        {/* always a cell, so the columns line up: a track with no file of its own has it empty */}
        {visible.file && (
          <span className="truncate text-xs text-muted">
            {track.file &&
              (track.file.journal && track.library_track_id ? (
                <Link to={`/file/${track.library_track_id}`} onClick={(e) => e.stopPropagation()} className="hover:text-fg hover:underline" title="Как скачан этот файл">
                  <FileInfo info={track.file} />
                </Link>
              ) : track.page_url ? (
                <a href={track.page_url} target="_blank" rel="noreferrer" className="hover:text-fg hover:underline">
                  <FileInfo info={track.file} />
                </a>
              ) : (
                <FileInfo info={track.file} />
              ))}
          </span>
        )}
        <div data-row-actions className="flex h-11 items-center justify-end">
          {primaryAction ?? <>
          {track.owned || dl === 'done' || needsFile ? null : downloading ? (
            <span className="flex h-11 w-9 items-center justify-center text-info" title="Сохраняется в библиотеку"><Spinner size={14} /></span>
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
          {rowNumber === undefined && !needsFile && (
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
          </>}
        </div>
        {visible.duration && <span className="text-right text-xs tabular-nums text-dim">{needsFile ? '' : fmtDuration(track.duration_s)}</span>}
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
          <TrackMenuButton track={menuTrack} extra={menuExtra} picked={selected} onPick={selecting ? () => toggleSelected(asSel(track)) : undefined} className={cx('!h-8 !w-8', shown)} />
        </span>
      </div>
    );
  }

  const extras: MenuExtra[] = [
    selectExtra,
    ...(menuExtra ?? []),
  ];
  // the right button, or a finger held on the row: its menu
  const press = menuPressProps((at) => openTrackMenu({ track: menuTrack, extra: extras, at }));
  return (
    <div ref={rowRef} {...press} onClickCapture={(e) => {
      press.onClickCapture(e);
      if (!e.defaultPrevented) onPick(e);
    }} aria-selected={selected} data-track-row={releaseFeed || undefined}>
      <SwipeRow
        right={{ icon: 'queue', onAction: async () => { if (needsFile) return void toast('У трека нет файла: выберите его в меню', 'info'); if (await addToQueue([menuTrack.listen as ListenTrack])) toast(`В очереди: «${displayTitle}»`, 'success'); } }}
        left={{ icon: 'plus', armedIcon: 'check', onAction: () => setPicking(true) }}
      >
        <div className={cx('flex min-h-16 items-center', releaseFeed ? 'group/track min-w-0 gap-2 rounded-lg px-2 py-1 hover:bg-fg/5 focus-within:bg-fg/5' : 'gap-1 pl-4 pr-1', selected && 'bg-accent/15', unreachable && 'opacity-40', grey)}>
          {releaseFeed ? (
            <>
              {tap('flex min-h-11 shrink-0 items-center gap-2 rounded-md', lead(desktop), false)}
              {names(artistLinks(), true)}
            </>
          ) : sourceMarks ? (
            <div className="flex min-w-0 flex-1 items-center gap-3 py-2 text-left" onClick={openTrack}>
              {(showCover || number !== undefined) && tap('flex shrink-0 items-center gap-3', lead(false))}
              {names(artistLine, true)}
            </div>
          ) : tap('flex min-w-0 flex-1 items-center gap-3 py-2 text-left', (
            <>
              {lead(false)}
              {names(artistLine)}
            </>
          ))}
          {downloading && <span className="flex w-6 justify-center text-info" title="Сохраняется в библиотеку"><Spinner size={14} /></span>}
          {selected && <Icon name="check" size={18} stroke={3} className="shrink-0 text-accent" />}
          {releaseFeed && !needsFile && track.duration_s != null && <span className="shrink-0 text-xs tabular-nums text-dim">{fmtDuration(track.duration_s)}</span>}
          <span data-row-actions className="contents">
            {primaryAction}
            <TrackMenuButton track={menuTrack} extra={extras} className="w-10" />
          </span>
        </div>
      </SwipeRow>
      {picking && <TrackPlaylists track={menuTrack} onClose={() => setPicking(false)} />}
    </div>
  );
}

export function ArtistBubble({ artist }: { artist: CatalogArtist }) {
  const menu = useContextMenu();
  return (
    <>
      <ArtistCard artist={{ name: artist.name, to: artistPath(artist), photo: artist.picture_small_url || artist.picture_url }} onMenu={menu.openAt} />
      {menu.shown && <ArtistMenu artist={artist} at={menu.at} onClose={menu.close} />}
    </>
  );
}

/** A catalog release as a card (`AlbumCard`): what the library has of it under its line, the services that know it,
 * a download button on its cover. */
export function ReleaseCard({ album, showArtist = false, onDownload, busy, showSources = false }: {
  album: AlbumOut; showArtist?: boolean; onDownload?: () => void; busy?: boolean; showSources?: boolean;
}) {
  return (
    <AlbumCard
      album={catalogLook(album)}
      showArtist={showArtist}
      menu={(at, onClose) => <AlbumMenu album={album} at={at} onClose={onClose} />}
      overlay={onDownload && album.owned_status !== 'full' && (
        <IconButton icon="download" label={`Скачать «${album.title}»`} onClick={onDownload} busy={busy} size={18}
          className="!absolute bottom-2 right-2 !h-10 !w-10 bg-black/60 !text-white hover:bg-black/75" />
      )}
      extra={
        <>
          {showSources && <AlbumSourceMarks album={album} className="mt-1" />}
          <div className="mt-1.5"><OwnedBadge status={album.owned_status} count={album.owned_count} total={album.nb_tracks} /></div>
        </>
      }
    />
  );
}

/** The head of a track list on a computer: the names of the columns over `TrackRow`s (the same grid), and the menu
 * that switches the optional ones (a click on ⋯ or a right click on the head). `numbered`, `album` — as the rows
 * of this list are drawn (`number`, `showAlbum`). On a phone there is no table and no head. */
export type ListSort = 'new' | 'old' | 'most' | 'least' | null;

/** `added`: the list has «Дата добавления»; a click on that column sorts by it — the newest first, then the oldest
 * first, then the list's own order again (`sort`, `onSort`). `plays`: the «Прослушивания» column, sorted the same
 * way — the most listened to first, then the least, then the list's own order. */
/** `heads`: the columns a list is sorted by with a click on their names beside those two («Все файлы»: the name,
 * the album, the file) — what the column is called just now, the arrow of its order, and the click. */
export interface SortHead {
  label?: string;
  mark?: '▲' | '▼' | null;
  title: string;
  onClick: () => void;
}

function AudienceCell({ value, label }: { value?: number | null; label: string }) {
  return (
    <span title={value == null ? undefined : `${label} · ${fmtNum(value)}`} className="text-right text-xs tabular-nums text-muted">
      {value == null ? '—' : fmtCompactCount(value)}
    </span>
  );
}

export function TrackTableHeader({ numbered = false, album = false, added = false, tags = false, plays = false, playsLabel, sort = null, onSort, heads }: { heads?: Partial<Record<'title' | 'album' | 'file', SortHead>>; numbered?: boolean; album?: boolean; added?: boolean; tags?: boolean; plays?: boolean; playsLabel?: string; sort?: ListSort; onSort?: (next: ListSort) => void }) {
  const desktop = useDesktop();
  const table = useTrackTable();
  const cols = useTrackColumns();
  const visible = table?.visible ?? {
    ...cols, album: album && cols.album, added: added && cols.added, plays: !!playsLabel || (plays && cols.plays),
    tagLang: tags && cols.tagLang, tagGenre: tags && cols.tagGenre, tagOwn: tags && cols.tagOwn, spotify: false, lastfm: false,
  };
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const showTable = desktop && !table?.compact;
  useEffect(() => {
    if (!open) return;
    if (!showTable) {
      setOpen(false);
      return;
    }
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
  }, [open, showTable]);
  if (!showTable) return null;
  const head = (h: SortHead | undefined, label: string, hint?: string) =>
    h ? (
      <button type="button" onClick={h.onClick} title={h.title} className={cx('flex items-center gap-1.5 text-left transition hover:text-fg', h.mark && 'text-fg')}>
        {h.label ?? label}
        {h.mark && <span className="text-[10px] text-accent" aria-hidden="true">{h.mark}</span>}
      </button>
    ) : (
      <span title={hint}>{label}</span>
    );
  const offered = (Object.keys(TRACK_COLUMN_LABELS) as (keyof TrackColumns)[]).filter((c) => (c !== 'album' || album)
    && (c !== 'added' || added) && (c !== 'plays' || (plays && !playsLabel)) && (!c.startsWith('tag') || tags)
    && ((c !== 'spotify' && c !== 'lastfm') || table));
  const audienceHeader = (metric: 'spotify' | 'lastfm', name: string) => (
    <span title={TRACK_COLUMN_LABELS[metric]} className="flex items-center justify-end gap-1 whitespace-nowrap">
      <ServiceIcon service={metric} size={13} />
      <span aria-hidden="true">{name}</span>
      <span className="sr-only">{TRACK_COLUMN_LABELS[metric]}</span>
    </span>
  );
  return (
    <div
      ref={ref}
      onContextMenu={(e) => {
        e.preventDefault();
        setOpen(true);
      }}
      className="group/head relative mx-2 mb-1 grid h-9 items-center gap-3 border-b border-line/60 px-2 text-xs text-dim"
      style={{ gridTemplateColumns: trackGrid(numbered, visible) }}
    >
      {numbered && <span className="text-center">#</span>}
      {head(heads?.title, 'Название')}
      {visible.album && head(heads?.album, 'Альбом')}
      {visible.tagLang && <span>Язык</span>}
      {visible.tagGenre && <span>Жанр</span>}
      {visible.tagOwn && <span>Теги</span>}
      {visible.added && (
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
      {visible.plays && (playsLabel ? (
        <span title="Сколько раз вы слушали трек за период этого списка">{playsLabel}</span>
      ) : (
        <button
          type="button"
          onClick={() => onSort?.(sort === 'most' ? 'least' : sort === 'least' ? null : 'most')}
          title={sort === 'most' ? 'Сначала самые слушаемые — нажмите: сначала редкие' : sort === 'least' ? 'Сначала редкие — нажмите: обычный порядок' : 'Сколько раз вы слушали трек (от 30 секунд). Нажмите, чтобы отсортировать'}
          className={cx('flex items-center gap-1.5 text-left transition hover:text-fg', (sort === 'most' || sort === 'least') && 'text-fg')}
        >
          Мои прослушивания
          {(sort === 'most' || sort === 'least') && <span className="text-[10px] text-accent" aria-hidden="true">{sort === 'most' ? '▼' : '▲'}</span>}
        </button>
      ))}
      {visible.spotify && audienceHeader('spotify', 'Spotify')}
      {visible.lastfm && audienceHeader('lastfm', 'Last.fm')}
      {visible.file && head(heads?.file, 'Файл', 'Откуда файл, его формат и битрейт')}
      <span />
      {visible.duration && <span className="text-right">Время</span>}
      <IconButton icon="more" size={18} label="Колонки таблицы" aria-expanded={open} onClick={() => setOpen((v) => !v)} className={cx('!h-8 !w-8 text-dim hover:text-fg focus-visible:opacity-100', !open && 'opacity-0 group-hover/head:opacity-100')} />
      {open && (
        <div role="menu" aria-label="Колонки" className="absolute right-0 top-9 z-20 w-60 rounded-xl border border-line bg-raised p-2 shadow-lg">
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
