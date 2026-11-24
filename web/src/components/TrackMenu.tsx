import { useState, type MouseEvent } from 'react';
import { deleteFile } from '../lib/deleteFile';
import { editTrackArtwork } from '../lib/coverPicker';
import { openFindFile } from '../lib/findFile';
import { openTrackEdit } from '../lib/trackEdit';
import { openVersions } from '../lib/versions';
import { setDisliked, useDisliked, type DislikeTarget } from '../lib/dislikes';
import { STANDALONE } from '../lib/embedded';
import { addToQueue, next, stopListening, useListen, type ListenTrack } from '../lib/listen';
import { canPlayAll } from '../lib/player';
import { albumPath, artistPath, enc, navigate } from '../lib/router';
import { useSession } from '../lib/session';
import { openShare } from '../lib/shareTrack';
import { toggleLike, useLiked, type LikeTarget } from '../lib/stars';
import { openTagsSheet, tagKey } from '../lib/tags';
import { closeTrackMenu, openTrackMenu, useTrackMenu, type TrackMenuState } from '../lib/trackMenu';
import { artistSeed, canWave, startWave, trackSeed } from '../lib/wave';
import { ContextMenu, menuAtButton, type MenuEntry } from './ContextMenu';
import { type IconName } from './Icon';
import { PlaylistsSubmenu, TrackPlaylists } from './PlaylistsButton';
import { Cover, IconButton, cx } from './ui';

export interface MenuTrack {
  title: string;
  artist: string;
  ref?: string | null;
  nd_song_id?: string | null;
  /** what the Hub calls the track (`lib/tags.ts` `tagKey`), if it knows it: given as is, or as the track's id */
  key?: string | null;
  library_track_id?: number | null;
  /** the library has a file of it: «Скачано», «Найти другой файл» and «Удалить файл» */
  has_file?: boolean;
  /** known by its name alone — no file, no record of the catalog: «Выбрать файл» is offered, what plays is not */
  unknown?: boolean;
  onFindFile?: () => void;
  /** gets the file into the library («Скачать»); none — not offered */
  onDownload?: () => void;
  /** the file is being got now */
  downloading?: boolean;
  artist_ref?: string | null;
  hub_artist_id?: number | null;
  album_ref?: string | null;
  release_id?: number | null;
  /** the song's Navidrome album and artist (the library pages of a browser) */
  nd_album_id?: string | null;
  nd_artist_id?: string | null;
  /** the track for the page player's queue («В очередь»); none — not offered */
  listen?: ListenTrack;
  /** what a ♥ of a track the Hub has not met yet remembers of it (the row's data) */
  data?: Record<string, unknown>;
  /** the cover for the head of the sheet on a phone */
  cover?: string | null;
}

/** An action a screen adds to the menu (a playlist's «Убрать из плейлиста»). `hint` is not shown: the menu has no
 * explanations. */
export interface MenuExtra {
  icon: IconName;
  label: string;
  hint?: string;
  shortcut?: string;
  onClick: () => void;
}

/** ⋯ of a track (a row, the players). It and a right click on the row open the same menu (`lib/trackMenu.ts`).
 * While rows are being picked (`onPick`) the button picks this row or takes it out, and the menu is a right click away. */
export function TrackMenuButton({ track, extra, className, inPlayer, picked, onPick }: { track: MenuTrack; extra?: MenuExtra[]; className?: string; inPlayer?: boolean; picked?: boolean; onPick?: () => void }) {
  const open = (at: TrackMenuState['at']) => openTrackMenu({ track, extra, inPlayer, at });
  const atMouse = (e: MouseEvent) => {
    e.preventDefault();
    open({ x: e.clientX, y: e.clientY });
  };
  return picked ? (
    <IconButton
      icon="check"
      size={18}
      label={`Снять выделение: «${track.title}» (меню — правой кнопкой)`}
      onClick={() => onPick?.()}
      onContextMenu={atMouse}
      className={cx(className, '!text-accent !opacity-100')}
    />
  ) : (
    <IconButton
      icon="more"
      label={onPick ? `Выбрать «${track.title}» (меню — правой кнопкой)` : `Ещё: «${track.title}»`}
      onClick={(e) => (onPick ? onPick() : open(menuAtButton(e.currentTarget)))}
      onContextMenu={atMouse}
      className={cx('text-muted hover:text-fg', className)}
    />
  );
}

/** The track menu of the page, drawn once (`components/Overlays.tsx`). */
export function TrackMenuHost() {
  const open = useTrackMenu();
  const [picking, setPicking] = useState<TrackMenuState['track'] | null>(null);
  return (
    <>
      {open && <TrackMenu key={`${open.track.ref ?? ''}|${open.track.library_track_id ?? ''}|${open.track.title}`} {...open} onPlaylists={() => setPicking(open.track)} />}
      {picking && <TrackPlaylists track={picking} onClose={() => setPicking(null)} />}
    </>
  );
}

/** The lines, in groups: keep it (playlists, «Любимое», the queue); go from it (waves, the artist, the album); change
 * it (tags, the name, the cover, the record, the file); the file; what the mixes take; share. A line not possible
 * for this track is not drawn, except «Скачано» — it says the state. */
function TrackMenu({ track, extra, at, inPlayer, onPlaylists }: TrackMenuState & { onPlaylists: () => void }) {
  const canMap = !!useSession().me.artist_map; // «Карта похожих» is there
  const trackWave = trackSeed(track);
  const artistWave = artistSeed(track);
  const key = track.key ?? tagKey(track);
  const target: DislikeTarget = { track_id: key ? Number(key) : null, ref: track.ref, nd_song_id: track.nd_song_id, artist: track.artist, title: track.title };
  const artist: DislikeTarget | null = track.artist_ref ? { artist_ref: track.artist_ref, artist: track.artist } : null;
  const disliked = useDisliked(target);
  const artistDisliked = useDisliked(artist ?? { artist_ref: '\u0000' });
  const like: LikeTarget = { targetType: 'track', trackId: key ? Number(key) : null, songId: track.has_file ? track.nd_song_id : null, ref: track.ref, data: track.data };
  const liked = useLiked(like);
  const playing = useListen((s) => !!track.ref && s.track?.ref === track.ref);
  // the page's own player plays here (a browser, an app without `playQueue`)
  const queueable = !!track.listen && !canPlayAll();
  const known = !!(track.library_track_id || track.ref || track.nd_song_id);
  const keepable = !!(track.nd_song_id || track.ref);
  const albumTo = track.album_ref || track.release_id ? albumPath({ ref: track.album_ref ?? '', release_id: track.release_id })
    : STANDALONE && track.nd_album_id ? `/lib/album/${enc(track.nd_album_id)}` : null;
  const artistTo = track.artist_ref || track.hub_artist_id ? artistPath({ ref: track.artist_ref ?? '', hub_artist_id: track.hub_artist_id })
    : STANDALONE && track.nd_artist_id ? `/lib/artist/${enc(track.nd_artist_id)}` : null;
  const fileId = track.has_file && track.library_track_id != null ? track.library_track_id : null;

  const dislike = (t: DislikeTarget, on: boolean) =>
    void setDisliked(t, !on).then((ok) => {
      if (ok && !on && playing && !next()) stopListening();
    });

  const entries: MenuEntry[] = [
    keepable && { icon: 'playlist', label: 'Добавить в плейлист', shortcut: 'P', submenu: (close) => <PlaylistsSubmenu track={track} onClose={close} />, onSelect: onPlaylists },
    keepable && { icon: 'heart', label: liked ? 'Убрать из «Любимого»' : 'Добавить в «Любимое»', shortcut: 'L', active: liked, onSelect: () => void toggleLike(like) },
    queueable && { icon: 'queue', label: 'Добавить в очередь', shortcut: 'Q', onSelect: () => void addToQueue([track.listen as ListenTrack]) },
    ...(extra ?? []).map((x) => ({ icon: x.icon, label: x.label, shortcut: x.shortcut, onSelect: x.onClick })),
    'sep',
    canWave() && trackWave && !track.unknown && { icon: 'wave', label: 'Волна по треку', shortcut: 'R', onSelect: () => void startWave(trackWave, inPlayer ? 'player' : trackWave.ref?.startsWith('file:') ? 'search_file' : 'track_menu') },
    canWave() && artistWave && { label: 'Волна по исполнителю', onSelect: () => void startWave(artistWave, inPlayer ? 'player' : 'artist_menu') },
    artistTo && { icon: 'user', label: 'Перейти к исполнителю', shortcut: 'Alt+A', onSelect: () => navigate(artistTo) },
    albumTo && { icon: 'disc', label: 'Перейти к альбому', shortcut: 'A', onSelect: () => navigate(albumTo) },
    canMap && track.artist_ref?.startsWith('dz:') && { icon: 'nodes', label: 'Карта похожих', onSelect: () => navigate(`/map/${enc(track.artist_ref!)}`) },
    'sep',
    key && { icon: 'tag', label: 'Теги', shortcut: 'T', onSelect: () => openTagsSheet([key], track.title) },
    known && { icon: 'edit', label: 'Переименовать', shortcut: 'F2', onSelect: () => openTrackEdit({ title: track.title, artist: track.artist, library_track_id: track.library_track_id, ref: track.ref, nd_song_id: track.nd_song_id }) },
    (track.library_track_id || track.nd_song_id || /^(dz|ym):/.test(track.ref ?? '')) && { icon: 'image', label: 'Сменить обложку', onSelect: () => editTrackArtwork({ ...track, src: track.listen?.cover_url ?? track.cover }) },
    known && { icon: 'disc', label: 'Версии песни', shortcut: 'V', onSelect: () => openVersions({ title: track.title, library_track_id: track.library_track_id, ref: track.ref, nd_song_id: track.nd_song_id }) },
    track.onFindFile && { icon: 'search', label: 'Выбрать файл', onSelect: () => track.onFindFile?.() },
    fileId != null && { icon: 'search', label: 'Найти другой файл', onSelect: () => openFindFile({ library_track_id: fileId, title: track.title, artist: track.artist, has_file: true }) },
    'sep',
    track.has_file ? { icon: 'download', label: 'Скачано', active: true, done: true, disabled: true, onSelect: () => {} }
      : track.downloading ? { icon: 'download', label: 'Скачивается', disabled: true, onSelect: () => {} }
      : track.onDownload && { icon: 'download', label: 'Скачать', shortcut: 'D', onSelect: track.onDownload },
    fileId != null && { icon: 'trash', label: 'Удалить файл', danger: true, onSelect: () => void deleteFile({ library_track_id: fileId, title: track.title, artist: track.artist }) },
    'sep',
    { icon: 'ban', label: 'Не нравится', active: disliked, done: disliked, onSelect: () => dislike(target, disliked) },
    artist && { label: 'Не включать исполнителя', active: artistDisliked, done: artistDisliked, onSelect: () => dislike(artist, artistDisliked) },
    'sep',
    !track.unknown && keepable && { icon: 'share', label: 'Поделиться', shortcut: 'S', onSelect: () => openShare(track) },
  ];

  const cover = track.cover ?? track.listen?.cover_small_url ?? track.listen?.cover_url;
  return (
    <ContextMenu
      at={at}
      head={{ title: track.title, sub: track.artist, cover: <Cover src={cover ?? null} size={44} icon="note" /> }}
      entries={entries}
      onClose={closeTrackMenu}
    />
  );
}

/** A track of the player (the one that plays, the queue) for its menu: the same lines as its row in a list. `songId`
 * and `trackId` — what the player has learned of it since (`useListen().info`). */
export function menuTrackOf(t: ListenTrack, known?: { nd_song_id?: string | null; track_id?: number | null } | null): MenuTrack {
  const songId = known?.nd_song_id ?? t.nd_song_id;
  return {
    ...t,
    listen: t,
    data: { ...t },
    cover: t.cover_small_url ?? t.cover_url,
    nd_song_id: songId,
    library_track_id: known?.track_id ?? t.library_track_id,
    has_file: !!songId,
  };
}
