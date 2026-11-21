import { useState } from 'react';
import { api, withQuery } from '../api/client';
import { TrackRow, TrackTableHeader } from '../components/catalog';
import { TrackTable } from '../components/TrackTable';
import { ServiceBadge } from '../components/ServiceBadge';
import { Button, Cover, ErrorState, Loading, PageHeader } from '../components/ui';
import { tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { navigate } from '../lib/router';
import { attempt } from '../lib/ui';

export function SpotifyPlaylistPage({ id, query }: { id: string; query: string | null }) {
  const res = useAsync((signal) => api.spotifyPlaylist(id, signal), [id]);
  const [busy, setBusy] = useState(false);
  const back = withQuery('/search', { q: query || undefined, tab: query ? 'playlists' : undefined });
  const data = res.data;
  const run = async () => {
    setBusy(true);
    const imp = await attempt(() => api.createImport({ source: 'spotify', method: 'link', url: `spotify:playlist:${id}` }));
    setBusy(false);
    if (imp) navigate(`/import/${imp.id}`);
  };
  return (
    <div>
      <PageHeader title={data?.playlist.title || 'Плейлист Spotify'} back={back} />
      {res.error && <ErrorState text={res.error} onRetry={() => res.reload()} />}
      {!data && !res.error && <Loading text="Читаю плейлист Spotify…" />}
      {data && (
        <>
          <div className="flex flex-wrap items-center gap-4 px-4 py-3 lg:px-6">
            <Cover src={data.playlist.cover_url} size={64} alt="" icon="playlist" />
            <div className="min-w-0 flex-1 text-sm text-muted">
              <ServiceBadge service="spotify" label={data.playlist.owner ? `Spotify · ${data.playlist.owner}` : 'Spotify'} size={16} />
              <div>{tracksWord(data.tracks.length + data.local_tracks)}</div>
            </div>
            <Button icon="transfer" variant="primary" onClick={() => void run()} busy={busy}>Импортировать</Button>
          </div>
          {data.local_tracks > 0 && <p className="px-4 pb-2 text-sm text-muted lg:px-6">Локальные треки войдут в перенос — {data.local_tracks}</p>}
          {data.skipped > 0 && <p className="px-4 pb-2 text-sm text-muted lg:px-6">Пропущено недоступных записей и подкастов — {data.skipped}</p>}
          <TrackTable tracks={data.tracks} numbered album>
            {!!data.tracks.length && <TrackTableHeader numbered album />}
            <div className="pb-4">
              {data.tracks.map((track, i) => <TrackRow key={`${track.ref}:${i}`} track={track} index={i + 1} showAlbum />)}
            </div>
          </TrackTable>
        </>
      )}
    </div>
  );
}
