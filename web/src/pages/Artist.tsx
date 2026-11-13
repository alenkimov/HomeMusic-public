import { useState } from 'react';
import { api } from '../api/client';
import type { AlbumOut } from '../api/types';
import { ReleaseCard, TrackRow, TrackTableHeader } from '../components/catalog';
import { LikeButton } from '../components/LikeButton';
import { Icon } from '../components/Icon';
import { Button, Cover, ErrorState, IconButton, Loading, SectionTitle, Spinner } from '../components/ui';
import { startDownload } from '../lib/actions';
import { useAsync } from '../lib/hooks';
import { playList } from '../lib/player';
import { goBack } from '../lib/router';
import { plural } from '../lib/format';
import { confirmDialog } from '../lib/ui';
import { canWave, seedKey, startWave, useWave } from '../lib/wave';

export function ArtistPage({ refId }: { refId: string }) {
  const res = useAsync((s) => api.artist(refId, s), [refId]);
  const [showAllTop, setShowAllTop] = useState(false);
  const [busyRef, setBusyRef] = useState<string | null>(null);
  const [allBusy, setAllBusy] = useState(false);
  const wave = useWave();

  if (res.loading)
    return (
      <>
        <TopBar />
        <Loading />
      </>
    );
  if (res.error || !res.data)
    return (
      <>
        <TopBar />
        <ErrorState text={res.error ?? 'Исполнитель не найден'} onRetry={() => res.reload()} />
      </>
    );

  const { artist, top_tracks, albums, singles, compilations } = res.data;

  const downloadRelease = async (a: AlbumOut) => {
    setBusyRef(a.ref);
    await startDownload({ album_ref: a.ref });
    setBusyRef(null);
  };

  const downloadAll = async () => {
    const missing = (xs: AlbumOut[]) => xs.filter((a) => a.owned_status !== 'full');
    const base = [...missing(albums), ...missing(singles)];
    const tracks = base.reduce((n, a) => n + Math.max(0, (a.nb_tracks ?? 0) - (a.owned_count ?? 0)), 0);
    const comp = missing(compilations).length;
    if (!base.length && !comp) {
      await confirmDialog({ title: 'Всё уже есть', body: 'Все альбомы и синглы этого исполнителя уже в библиотеке.', okLabel: 'Понятно', cancelLabel: 'Закрыть' });
      return;
    }
    const body =
      `Будет скачано релизов: ${base.length} (альбомы, синглы и EP)` +
      (tracks ? `, примерно ${tracks} ${plural(tracks, ['трек', 'трека', 'треков'])}` : '') +
      '.\nУже скачанные треки пропускаются.' +
      (comp ? `\nСборники (${comp}) не включаются.` : '');
    const ok = await confirmDialog({ title: `Скачать всю дискографию ${artist.name}?`, body, okLabel: 'Скачать' });
    if (!ok) return;
    setAllBusy(true);
    await startDownload({ artist_ref: artist.ref, record_types: ['album', 'single', 'ep'] });
    setAllBusy(false);
    res.reload(true);
  };

  const top = showAllTop ? top_tracks : top_tracks.slice(0, 5);
  const groups: Array<[string, AlbumOut[]]> = [
    ['Альбомы', albums],
    ['Синглы и EP', singles],
    ['Сборники', compilations],
  ];

  return (
    <div>
      <div className="relative">
        <div className="absolute inset-0 overflow-hidden">
          {artist.picture_url && <img src={artist.picture_url} alt="" className="h-full w-full scale-110 object-cover opacity-35 blur-2xl" referrerPolicy="no-referrer" />}
          <div className="absolute inset-0 bg-gradient-to-b from-transparent via-bg/60 to-bg" />
        </div>
        <TopBar overlay />
        <div className="relative px-4 pb-4 pt-2">
          <div className="flex justify-center pb-4">
            <Cover src={artist.picture_url} size={176} round icon="user" alt={artist.name} className="shadow-2xl" />
          </div>
          <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
          <h1 className="line-clamp-2 text-3xl font-extrabold leading-tight">{artist.name}</h1>
          <p className="mt-1 text-sm text-muted">
            {[artist.nb_fans ? `${artist.nb_fans.toLocaleString('ru-RU')} ${plural(artist.nb_fans, ['поклонник', 'поклонника', 'поклонников'])}` : null, `${albums.length + singles.length + compilations.length} релизов`]
              .filter(Boolean)
              .join(' · ')}
          </p>
          </div>
          <LikeButton targetType="artist" refId={artist.ref} title={artist.name} size={26} data={{ name: artist.name }} />
          {canWave() && (
            <button
              type="button"
              aria-label="Волна по исполнителю"
              title="Волна по исполнителю"
              disabled={wave.starting !== null}
              onClick={() => void startWave({ artist_ref: artist.ref }, 'artist_page')}
              className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition hover:scale-105 active:scale-95 disabled:opacity-60"
            >
              {wave.starting === seedKey({ artist_ref: artist.ref }) ? <Spinner size={24} /> : <Icon name="wave" size={26} />}
            </button>
          )}
          </div>
          <Button icon="download" className="mt-4" busy={allBusy} onClick={downloadAll}>
            Скачать всю дискографию
          </Button>
        </div>
      </div>

      {top_tracks.length > 0 && (
        <section aria-label="Популярные треки">
          <SectionTitle>Популярные треки</SectionTitle>
          <TrackTableHeader numbered album />
          {top.map((t, i) => (
            <TrackRow key={t.ref} track={t} number={i + 1} showAlbum onPlay={() => playList(top_tracks.filter((x) => x.owned), (x) => x.nd_song_id, t)} queue={top_tracks} />
          ))}
          {top_tracks.length > 5 && (
            <div className="px-4 pt-1">
              <Button small variant="ghost" onClick={() => setShowAllTop((v) => !v)}>
                {showAllTop ? 'Свернуть' : 'Показать все'}
              </Button>
            </div>
          )}
        </section>
      )}

      {groups.map(([title, list]) =>
        list.length ? (
          <section key={title} aria-label={title}>
            <SectionTitle>
              {title} <span className="text-base font-normal text-dim">{list.length}</span>
            </SectionTitle>
            <div className="grid grid-cols-2 gap-x-4 gap-y-5 px-4 min-[520px]:grid-cols-3">
              {list.map((a) => (
                <ReleaseCard key={a.ref} album={a} onDownload={() => downloadRelease(a)} busy={busyRef === a.ref} />
              ))}
            </div>
          </section>
        ) : null,
      )}
    </div>
  );
}

function TopBar({ overlay }: { overlay?: boolean }) {
  return (
    <div className={`pt-safe ${overlay ? 'relative' : 'sticky top-(--page-top) z-20 bg-bg/90'} flex h-14 items-center px-2`}>
      <IconButton icon="back" label="Назад" onClick={() => goBack('/search')} className="bg-black/30 !text-white" />
    </div>
  );
}
