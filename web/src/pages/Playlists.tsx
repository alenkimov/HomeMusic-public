import { useState, type FormEvent, type ReactNode } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { ServiceIcon, ServiceText } from '../components/ServiceBadge';
import { Button, Cover, Empty, ErrorState, PageHeader, SkeletonRows } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { fmtLength, tracksWord } from '../lib/format';
import { STANDALONE } from '../lib/embedded';
import { useAsync } from '../lib/hooks';
import { playlistMenuProps } from '../lib/playlistMenu';
import { Link, enc, navigate } from '../lib/router';
import { attempt } from '../lib/ui';
import { spotifyURL } from '../lib/spotify';

export function PlaylistsPage() {
  const res = useAsync((s) => api.playlists(s), [], true, 'playlists');
  // made, changed or deleted anywhere: the Hub says so
  useHubEvent('library', () => res.reload(true));
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
      <PageHeader title="Плейлисты" back={STANDALONE ? '/library' : '/more'} />
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
        <ServiceText text={STANDALONE
          ? 'Плейлисты хранятся в Navidrome: те же, что в приложении HomeMusic.'
          : 'Плейлисты хранятся в Navidrome: слушайте и скачивайте их для офлайна в плеере (Tempus).'} />
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
            <li key={p.id} className="select-none [-webkit-touch-callout:none]" {...playlistMenuProps(p)}>
              <PlaylistRow to={STANDALONE ? `/playlist/${enc(p.id)}` : undefined}>
              {/* the playlist's own picture, as everywhere else: the user's, or the one made of its albums' covers */}
              <Cover src={p.cover_url ? `${p.cover_url}&size=160` : null} size={48} icon="playlist" />
              <span className="min-w-0 flex-1">
                <span className="block truncate">{p.name}</span>
                <span className="block text-xs text-muted">{[tracksWord(p.song_count + (p.pending_count ?? 0)), p.duration_s ? fmtLength(p.duration_s) : null].filter(Boolean).join(' · ')}</span>
              </span>
              {STANDALONE && <Icon name="chevron" size={18} className="text-dim" />}
              </PlaylistRow>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** A playlist opens (its songs, «Слушать») in a browser; inside the app the app's own library has it. */
function PlaylistRow({ to, children }: { to?: string; children: ReactNode }) {
  const cls = 'flex min-h-16 items-center gap-3 px-4 py-2';
  return to ? (
    <Link to={to} className={cls + ' hover:bg-fg/5'}>
      {children}
    </Link>
  ) : (
    <div className={cls}>{children}</div>
  );
}
