import { TrackTitle } from '../components/TrackTitle';
import { useContext, useEffect } from 'react';
import { api } from '../api/client';
import { playAll } from '../components/catalog';
import { Button, Cover, Empty, ErrorState, Loading, PageHeader } from '../components/ui';
import { useAsync } from '../lib/hooks';
import { RouteBackground, enc, navigate } from '../lib/router';

/** A «HomeMusic» link someone shared, opened in this library (`/open?dz=…&isrc=…&sp=…&a=…&t=…`; the page that
 * belongs to no server sends its visitor here — docs/index.html). The track the library has is shown with ▶; one it
 * has not got opens in the catalog, on its album's page, to be downloaded; one the catalog does not know either
 * becomes a search by its names. */
export function OpenPage({ query }: { query: URLSearchParams }) {
  const background = useContext(RouteBackground);
  const q = { dz: query.get('dz'), isrc: query.get('isrc'), sp: query.get('sp'), a: query.get('a'), t: query.get('t'), url: query.get('url') };
  const res = useAsync(async (s) => {
    const found = await api.openLink(q, s);
    const song = found.nd_song_id ? (await api.ndSongsByIds([found.nd_song_id], s))[0] : undefined;
    return { found, song };
  }, [query.toString()]);
  const found = res.data?.found;
  const song = res.data?.song;

  useEffect(() => {
    if (background || !found || song) return;
    if (found.artist_ref) navigate(`/artist/${enc(found.artist_ref)}`, { replace: true });
    else if (found.playlist_id) navigate(`/spotify/playlist/${enc(found.playlist_id)}`, { replace: true });
    else if (found.album_ref) navigate(`/album/${enc(found.album_ref)}`, { replace: true });
    else if (found.query) navigate(`/search?q=${enc(found.query)}`, { replace: true });
  }, [background, found, song]);

  return (
    <div>
      <PageHeader title={q.url ? 'Открыть ссылку Spotify' : 'Трек по ссылке'} back="/home" />
      {res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !found ? (
        <Loading text="Открываю ссылку…" />
      ) : song ? (
        <div className="mx-auto flex max-w-sm flex-col items-center px-6 pt-4 text-center">
          <Cover src={song.cover_url} alt="" className="aspect-square w-56 shadow-2xl" />
          <h2 className="pt-5 text-xl font-bold"><TrackTitle title={song.title} track={song} /></h2>
          <p className="pb-5 text-sm text-muted">{[song.artist, song.album_title].filter(Boolean).join(' · ')}</p>
          <Button variant="primary" block icon="play" onClick={() => playAll([song], { context: 'link' })}>
            Слушать
          </Button>
          {song.nd_album_id && (
            <Button block className="mt-2" icon="disc" onClick={() => navigate(`/lib/album/${enc(song.nd_album_id!)}`)}>
              Открыть альбом
            </Button>
          )}
          <p className="pt-4 text-xs text-dim">Этот трек уже есть в вашей библиотеке.</p>
        </div>
      ) : found.artist_ref || found.playlist_id || found.album_ref || found.query ? (
        <Loading text="Открываю в каталоге…" />
      ) : (
        <Empty icon="link" title={q.url ? 'Ссылка Spotify недоступна' : 'В ссылке нет трека'} text="Откройте её целиком — такой, какой вам её прислали." />
      )}
    </div>
  );
}
