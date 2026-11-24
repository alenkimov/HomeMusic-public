import type { AlbumOut, CatalogArtist, NdAlbumOut, NdArtistOut } from '../api/types';
import { startDownload } from '../lib/actions';
import { setDisliked, useDisliked } from '../lib/dislikes';
import { albumPath, artistPath, enc, navigate } from '../lib/router';
import { useSession } from '../lib/session';
import { copyText } from '../lib/share';
import { toggleLike, useLiked } from '../lib/stars';
import { toast } from '../lib/ui';
import { canWave, startWave } from '../lib/wave';
import { BrandIcon } from './BrandIcon';
import { ContextMenu, type MenuAt } from './ContextMenu';
import { Cover } from './ui';

/** The menus of an album and an artist where they stand as a card (search, discography, the library, «похожие»):
 * a right click or a finger held on the card. Their pages have fuller ones (`pages/Album.tsx`, `pages/Artist.tsx`). */

const copyLink = async (path: string, what: string) => {
  const url = `${window.location.origin}/#${path}`;
  toast((await copyText(url)) ? `Ссылка на ${what} скопирована` : url, 'success');
};

const serviceName = (s: string) => (s === 'yandex' ? 'Открыть в Яндекс Музыке' : s === 'spotify' ? 'Открыть в Spotify' : 'Открыть в Deezer');

export function AlbumMenu({ album, at, onClose }: { album: AlbumOut; at: MenuAt | null; onClose: () => void }) {
  const to = albumPath(album);
  const artistTo = album.artist_ref || album.hub_artist_id ? artistPath({ ref: album.artist_ref ?? '', hub_artist_id: album.hub_artist_id }) : null;
  const full = album.owned_status === 'full';
  return (
    <ContextMenu
      at={at}
      head={{ title: album.title, sub: album.artist, cover: <Cover src={album.cover_small_url || album.cover_url} size={44} icon="disc" /> }}
      onClose={onClose}
      entries={[
        { icon: 'disc', label: 'Открыть альбом', onSelect: () => navigate(to) },
        full ? { icon: 'download', label: 'Скачано', active: true, done: true, disabled: true, onSelect: () => {} }
          : { icon: 'download', label: album.owned_status === 'partial' ? 'Скачать недостающие' : 'Скачать альбом', shortcut: 'D', onSelect: () => void startDownload({ album_ref: String(album.release_id ?? album.ref) }) },
        'sep',
        artistTo && { icon: 'user', label: 'Перейти к исполнителю', shortcut: 'Alt+A', onSelect: () => navigate(artistTo) },
        canWave() && album.artist_ref && { icon: 'wave', label: 'Волна по исполнителю', shortcut: 'R', onSelect: () => void startWave({ artist_ref: album.artist_ref! }, 'artist_menu') },
        'sep',
        { icon: 'link', label: 'Скопировать ссылку', shortcut: 'C', onSelect: () => void copyLink(to, 'релиз') },
        ...(album.links ?? []).map((x) => ({ glyph: <BrandIcon service={x.service} size={18} />, label: serviceName(x.service), onSelect: () => void window.open(x.url, '_blank', 'noopener,noreferrer') })),
      ]}
    />
  );
}

export function ArtistMenu({ artist, at, onClose }: { artist: CatalogArtist; at: MenuAt | null; onClose: () => void }) {
  const to = artistPath(artist);
  const canMap = !!useSession().me.artist_map;
  const follow = { targetType: 'artist' as const, ref: artist.ref, data: { name: artist.name } };
  const followed = useLiked(follow);
  const off = { artist_ref: artist.ref, artist: artist.name };
  const hidden = useDisliked(off);
  return (
    <ContextMenu
      at={at}
      head={{ title: artist.name, sub: 'Исполнитель', cover: <Cover src={artist.picture_small_url || artist.picture_url} size={44} round icon="user" /> }}
      onClose={onClose}
      entries={[
        { icon: 'user', label: 'Открыть исполнителя', onSelect: () => navigate(to) },
        { icon: 'heart', label: followed ? 'Отписаться' : 'Подписаться', shortcut: 'L', active: followed, onSelect: () => void toggleLike(follow) },
        'sep',
        canWave() && { icon: 'wave', label: 'Волна по исполнителю', shortcut: 'R', onSelect: () => void startWave({ artist_ref: artist.ref }, 'artist_menu') },
        canMap && artist.ref.startsWith('dz:') && { icon: 'nodes', label: 'Карта похожих', onSelect: () => navigate(`/map/${enc(artist.ref)}`) },
        'sep',
        { icon: 'link', label: 'Скопировать ссылку', shortcut: 'C', onSelect: () => void copyLink(to, 'исполнителя') },
        'sep',
        { icon: 'ban', label: 'Не включать исполнителя', active: hidden, done: hidden, onSelect: () => void setDisliked(off, !hidden) },
      ]}
    />
  );
}

export function NdAlbumMenu({ album, at, onClose }: { album: NdAlbumOut; at: MenuAt | null; onClose: () => void }) {
  const to = `/lib/album/${enc(album.id)}`;
  return (
    <ContextMenu
      at={at}
      head={{ title: album.title, sub: album.artist, cover: <Cover src={album.cover_url} size={44} icon="disc" /> }}
      onClose={onClose}
      entries={[
        { icon: 'disc', label: 'Открыть альбом', onSelect: () => navigate(to) },
        album.artist_id && { icon: 'user', label: 'Перейти к исполнителю', shortcut: 'Alt+A', onSelect: () => navigate(`/lib/artist/${enc(album.artist_id!)}`) },
        canWave() && album.artist_id && { icon: 'wave', label: 'Волна по исполнителю', shortcut: 'R', onSelect: () => void startWave({ nd_artist_id: album.artist_id! }, 'artist_menu') },
        'sep',
        { icon: 'link', label: 'Скопировать ссылку', shortcut: 'C', onSelect: () => void copyLink(to, 'альбом') },
      ]}
    />
  );
}

export function NdArtistMenu({ artist, at, onClose }: { artist: NdArtistOut; at: MenuAt | null; onClose: () => void }) {
  const to = `/lib/artist/${enc(artist.id)}`;
  const follow = { targetType: 'artist' as const, ndId: artist.id, data: { name: artist.name } };
  const followed = useLiked(follow);
  return (
    <ContextMenu
      at={at}
      head={{ title: artist.name, sub: 'Исполнитель', cover: <Cover src={artist.cover_url} size={44} round icon="user" /> }}
      onClose={onClose}
      entries={[
        { icon: 'user', label: 'Открыть исполнителя', onSelect: () => navigate(to) },
        { icon: 'heart', label: followed ? 'Отписаться' : 'Подписаться', shortcut: 'L', active: followed, onSelect: () => void toggleLike(follow) },
        'sep',
        canWave() && { icon: 'wave', label: 'Волна по исполнителю', shortcut: 'R', onSelect: () => void startWave({ nd_artist_id: artist.id }, 'artist_menu') },
        'sep',
        { icon: 'link', label: 'Скопировать ссылку', shortcut: 'C', onSelect: () => void copyLink(to, 'исполнителя') },
      ]}
    />
  );
}
