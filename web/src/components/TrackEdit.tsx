import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { TrackEditOut } from '../api/types';
import { fmtAdded } from '../lib/format';
import { EDITED_EVENT, closeTrackEdit, useTrackEdit, type EditedTrack } from '../lib/trackEdit';
import { attempt, toast } from '../lib/ui';
import { Button, Cover, ErrorState, Field, Sheet, Spinner } from './ui';

/** «Переименовать» of a track's menu: its name and artists, and its cover — for everyone on the server, so it says
 * so, shows what the catalog calls the track (and goes back to that), and who changed it last. The cover is the
 * track's own (a single in a collection, a file that came without a picture) or its album's, each changed here.
 * Drawn once (`components/Overlays.tsx`), asked for with `openTrackEdit` (`lib/trackEdit.ts`). */
export function TrackEdit() {
  const track = useTrackEdit();
  if (!track) return null;
  return <Window key={`${track.library_track_id}:${track.ref}:${track.nd_song_id}`} track={track} />;
}

function Window({ track }: { track: EditedTrack }) {
  const [info, setInfo] = useState<TrackEditOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState(track.title);
  const [artist, setArtist] = useState(track.artist);
  const [busy, setBusy] = useState<string | null>(null);
  const [changed, setChanged] = useState(false);
  const ownFile = useRef<HTMLInputElement>(null);
  const albumFile = useRef<HTMLInputElement>(null);

  const load = () => {
    setError(null);
    api
      .trackEdit({ track_id: track.library_track_id ?? undefined, ref: track.ref ?? undefined, nd_song_id: track.nd_song_id ?? undefined })
      .then((r) => {
        setInfo(r);
        setTitle(r.title);
        setArtist(r.artist);
      })
      .catch((e: Error) => setError(e.message || 'Не удалось открыть трек'));
  };
  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  const close = () => {
    if (changed) window.dispatchEvent(new Event(EDITED_EVENT));
    closeTrackEdit();
  };
  const save = async (t = title, a = artist) => {
    if (!info) return;
    setBusy('save');
    const ok = await attempt(() => api.patchTrack(info.track_id, { title: t.trim(), artist: a.trim() }));
    setBusy(null);
    if (!ok) return;
    toast('Название изменено у всех', 'success');
    window.dispatchEvent(new Event(EDITED_EVENT));
    closeTrackEdit();
  };
  const cover = async (kind: 'own' | 'album', file: File | undefined) => {
    if (!info || !file) return;
    setBusy(kind);
    const r = kind === 'own' ? await attempt(() => api.setTrackCover(info.track_id, file)) : await attempt(() => api.setAlbumCover(info.album_id as number, file).then(() => api.trackEdit({ track_id: info.track_id })));
    setBusy(null);
    if (!r) return;
    // an album's picture keeps its address: the page asks for it anew by the time it was changed
    setInfo(kind === 'album' && r.cover_url && !r.own_cover ? { ...r, cover_url: `${r.cover_url}?v=${Date.now()}` } : r);
    setChanged(true);
    toast(kind === 'own' ? 'У трека своя обложка' : 'Обложка альбома изменена', 'success');
  };
  const dropOwn = async () => {
    if (!info) return;
    setBusy('own');
    const r = await attempt(() => api.dropTrackCover(info.track_id));
    setBusy(null);
    if (!r) return;
    setInfo(r);
    setChanged(true);
  };

  const same = !!info && title.trim() === info.title && artist.trim() === info.artist;
  const fromCatalog = !!info?.catalog_title && (info.catalog_title !== info.title || (info.catalog_artist ?? info.artist) !== info.artist);
  return (
    <Sheet
      open
      onClose={close}
      title="Трек: название и обложка"
      footer={
        info ? (
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={close}>
              {changed ? 'Готово' : 'Отмена'}
            </Button>
            <Button busy={busy === 'save'} disabled={same || !title.trim() || !artist.trim()} onClick={() => void save()}>
              Сохранить
            </Button>
          </div>
        ) : undefined
      }
    >
      {error ? (
        <ErrorState text={error} onRetry={load} />
      ) : !info ? (
        <div className="flex justify-center py-10">
          <Spinner className="text-accent" />
        </div>
      ) : (
        <div className="space-y-3">
          <Field label="Название">
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} autoComplete="off" />
          </Field>
          <Field label="Исполнители (через запятую)">
            <input className="input" value={artist} onChange={(e) => setArtist(e.target.value)} maxLength={300} autoComplete="off" />
          </Field>
          {fromCatalog && (
            <p className="text-[13px] text-muted">
              В каталоге: «{info.catalog_title}»{info.catalog_artist && info.catalog_artist !== info.artist ? ` — ${info.catalog_artist}` : ''} ·{' '}
              <button type="button" className="text-fg underline-offset-2 hover:underline" onClick={() => void save(info.catalog_title as string, info.catalog_artist ?? info.artist)}>
                вернуть
              </button>
            </p>
          )}
          <p className="text-[13px] text-dim">
            Изменится у всех на этом сервере{info.has_file ? ' и запишется в файл' : ''}.
            {info.last_by && info.last_at ? ` Последним менял ${info.last_by}, ${fmtAdded(info.last_at)}.` : ''}
          </p>

          <div className="flex items-center gap-3 border-t border-fg/10 pt-3">
            <Cover src={info.cover_url} size={64} alt="" />
            <div className="min-w-0 flex-1 space-y-1.5">
              <div className="text-[13px] text-muted">{info.own_cover ? 'У трека своя обложка' : info.album_id ? `Обложка альбома «${info.album_title}»` : 'Обложка из каталога'}</div>
              <div className="flex flex-wrap gap-2">
                <Button small variant="ghost" busy={busy === 'own'} onClick={() => ownFile.current?.click()}>
                  {info.own_cover ? 'Другая для трека' : 'Своя для трека'}
                </Button>
                {info.own_cover && (
                  <Button small variant="ghost" onClick={() => void dropOwn()}>
                    Убрать свою
                  </Button>
                )}
                {info.album_id != null && (
                  <Button small variant="ghost" busy={busy === 'album'} onClick={() => albumFile.current?.click()}>
                    Сменить у альбома
                  </Button>
                )}
              </div>
            </div>
          </div>
          <input ref={ownFile} type="file" accept="image/*" hidden onChange={(e) => void cover('own', e.target.files?.[0]).finally(() => (e.target.value = ''))} />
          <input ref={albumFile} type="file" accept="image/*" hidden onChange={(e) => void cover('album', e.target.files?.[0]).finally(() => (e.target.value = ''))} />
        </div>
      )}
    </Sheet>
  );
}
