import { useState } from 'react';
import { api, withQuery } from '../api/client';
import { TrackRow, TrackTableHeader } from '../components/catalog';
import { TrackTable } from '../components/TrackTable';
import { PlaylistHero } from '../components/Playlist';
import { Button, ErrorState, Loading, PageHeader } from '../components/ui';
import { tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { navigate } from '../lib/router';
import { attempt } from '../lib/ui';
import { VirtualRows } from '../components/VirtualRows';

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
      <PageHeader hero title={data?.playlist.title || 'Плейлист Spotify'} back={back} />
      {res.error && <ErrorState text={res.error} onRetry={() => res.reload()} />}
      {!data && !res.error && <Loading text="Читаю плейлист Spotify…" />}
      {data && (
        <>
          <PlaylistHero
            playlist={{ title: data.playlist.title, kind: 'Плейлист Spotify', cover: data.playlist.cover_url, owners: data.playlist.owner ? [data.playlist.owner] : ['Spotify'] }}
            facts={[tracksWord(data.tracks.length + data.local_tracks)]}
            tracks={data.tracks}
            right={<Button icon="transfer" variant="primary" onClick={() => void run()} busy={busy}>Импортировать</Button>}
          />
          {data.local_tracks > 0 && <p className="px-4 pb-2 text-sm text-muted lg:px-6">Локальные треки войдут в перенос — {data.local_tracks}</p>}
          {data.skipped > 0 && <p className="px-4 pb-2 text-sm text-muted lg:px-6">Пропущено недоступных записей и подкастов — {data.skipped}</p>}
          <TrackTable tracks={data.tracks} numbered album>
            {!!data.tracks.length && <TrackTableHeader numbered album />}
            <VirtualRows className="pb-4" items={data.tracks} rowKey={(track, i) => `${track.ref}:${i}`}
              render={(track, i) => <TrackRow track={track} index={i + 1} showAlbum />} />
          </TrackTable>
        </>
      )}
    </div>
  );
}
