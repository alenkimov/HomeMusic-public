import { useState, type FormEvent } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { ServiceIcon, ServiceText } from '../components/ServiceBadge';
import { Button, Empty, ErrorState, PageHeader, SkeletonRows } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { useAsync, useCoalesced } from '../lib/hooks';
import { openPlaylistMenu } from '../lib/playlistMenu';
import { PlaylistRow, playlistLook } from '../components/Playlist';
import { enc, navigate } from '../lib/router';
import { attempt } from '../lib/ui';
import { spotifyURL } from '../lib/spotify';

export function PlaylistsPage() {
  const res = useAsync((s) => api.playlists(s), [], true, 'playlists');
  // made, changed or deleted anywhere: the Hub says so
  const later = useCoalesced(() => res.reload(true));
  useHubEvent('library', later);
  useHubEvent('reconnect', () => res.reload(true));
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  // a playlist of another service copied by its link: a transfer of that one playlist, opened at its preview
  const [link, setLink] = useState('');
  const [copying, setCopying] = useState(false);
  const copy = async (e: FormEvent) => {
    e.preventDefault();
    if (!link.trim()) return;
    setCopying(true);
    const imp = await attempt(() => api.createImport({ source: spotifyURL(link) ? 'spotify' : 'yandex', method: 'link', url: link.trim() }));
    setCopying(false);
    if (imp) navigate(`/import/${imp.id}`);
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    const p = await attempt(() => api.createPlaylist(name.trim()), 'Плейлист создан');
    setBusy(false);
    if (p) {
      setName('');
      res.setData((prev) => [p, ...(prev ?? [])]);
    }
  };

  return (
    <div>
      <PageHeader title="Плейлисты" back="/library" />
      <form onSubmit={create} className="flex gap-2 px-4 pb-3">
        <input className="input flex-1" placeholder="Название нового плейлиста" aria-label="Название нового плейлиста" value={name} onChange={(e) => setName(e.target.value)} />
        <Button type="submit" variant="primary" icon="plus" busy={busy} disabled={!name.trim()}>
          Создать
        </Button>
      </form>
      <form onSubmit={copy} className="flex items-center gap-2 px-4 pb-3">
        {link.trim() ? <ServiceIcon service={spotifyURL(link) ? 'spotify' : 'yandex'} size={20} /> : <Icon name="link" size={20} />}
        <input className="input min-w-0 flex-1" inputMode="url" placeholder="Ссылка на плейлист Яндекс Музыки или Spotify" aria-label="Ссылка на плейлист" value={link} onChange={(e) => setLink(e.target.value)} />
        <Button type="submit" icon="transfer" busy={copying} disabled={!link.trim()}>
          Скопировать
        </Button>
      </form>
      <p className="px-4 pb-2 text-xs text-muted">
        <ServiceText text="Плейлисты хранятся в Navidrome: их видят и другие плееры, подключённые к Navidrome." />
      </p>
      {res.loading ? (
        <SkeletonRows />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !res.data?.length ? (
        <Empty icon="playlist" title="Плейлистов пока нет" text="Создайте плейлист здесь или добавьте в него альбом со страницы альбома." />
      ) : (
        <ul>
          {res.data.map((p) => (
            <li key={p.id}>
              <PlaylistRow playlist={{ ...playlistLook(p), to: `/playlist/${enc(p.id)}` }}
                onMenu={(at) => openPlaylistMenu(p, at)} className="hover:bg-fg/5" />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

