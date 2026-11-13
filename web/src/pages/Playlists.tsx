import { useState, type FormEvent, type ReactNode } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { Button, Cover, Empty, ErrorState, PageHeader, SkeletonRows } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { fmtLength, tracksWord } from '../lib/format';
import { STANDALONE } from '../lib/embedded';
import { useAsync } from '../lib/hooks';
import { Link, enc } from '../lib/router';
import { attempt } from '../lib/ui';

export function PlaylistsPage() {
  const res = useAsync((s) => api.playlists(s), []);
  // made, changed or deleted anywhere: the Hub says so
  useHubEvent('library', () => res.reload(true));
  useHubEvent('reconnect', () => res.reload(true));
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);

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
      <p className="px-4 pb-2 text-xs text-muted">
        {STANDALONE
          ? 'Плейлисты хранятся в Navidrome: те же, что в приложении HomeMusic.'
          : 'Плейлисты хранятся в Navidrome: слушайте и скачивайте их для офлайна в плеере (Tempus).'}
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
