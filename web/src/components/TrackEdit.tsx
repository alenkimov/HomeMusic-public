import { TrackTitle, VersionBadge } from './TrackTitle';
import { useVersionLabel } from '../lib/versionLabels';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { TrackEditEntry, TrackEditOut } from '../api/types';
import { fmtAdded } from '../lib/format';
import { EDITED_EVENT, closeTrackEdit, useTrackEdit, type EditedTrack } from '../lib/trackEdit';
import { attempt, toast } from '../lib/ui';
import { OtherTitles } from './OtherTitles';
import { Button, ErrorState, Field, Sheet, Spinner } from './ui';

/** «Переименовать» of a track's menu: its name and artists, what the catalog calls it, and who changed it last.
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
  const [history, setHistory] = useState<TrackEditEntry[] | null>(null);

  const load = () => {
    setError(null);
    api
      .trackEdit({ track_id: track.library_track_id ?? undefined, ref: track.ref ?? undefined, nd_song_id: track.nd_song_id ?? undefined })
      .then((r) => {
        setInfo(r);
        setTitle(r.title);
        setArtist(r.artist);
        if (r.last_at) api.trackEdits(r.track_id).then(setHistory, () => undefined);
      })
      .catch((e: Error) => setError(e.message || 'Не удалось открыть трек'));
  };
  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

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
  /** One more name the track is known by — or one less: the search reads them. */
  const otherName = async (what: () => Promise<TrackEditOut>) => {
    const r = await attempt(what);
    if (!r) return false;
    setInfo(r);
    return true;
  };
  // the changes of the name: each can be gone back to (what it was before it)
  const named = (history ?? []).filter((e) => e.changes.title || e.changes.artist);
  const same = !!info && title.trim() === info.title && artist.trim() === info.artist;
  const version = useVersionLabel(info ? { track_id: info.track_id } : track);
  const fromCatalog = !!info?.catalog_title && (info.catalog_title !== info.title || (info.catalog_artist ?? info.artist) !== info.artist);
  return (
    <Sheet
      open
      onClose={closeTrackEdit}
      title="Переименовать трек"
      footer={
        info ? (
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={closeTrackEdit}>
              Отмена
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
            <div className="flex min-w-0 items-center gap-2">
              <input className="input min-w-0 flex-1" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} autoComplete="off" />
              <VersionBadge label={version} />
            </div>
          </Field>
          <Field label="Исполнители (через запятую)">
            <input className="input" value={artist} onChange={(e) => setArtist(e.target.value)} maxLength={300} autoComplete="off" />
          </Field>
          {fromCatalog && (
            <p className="text-[13px] text-muted">
              В каталоге: «<TrackTitle title={info.catalog_title} label={version ?? null} />»{info.catalog_artist && info.catalog_artist !== info.artist ? ` — ${info.catalog_artist}` : ''} ·{' '}
              <button type="button" className="text-fg underline-offset-2 hover:underline" onClick={() => void save(info.catalog_title as string, info.catalog_artist ?? info.artist)}>
                вернуть
              </button>
            </p>
          )}
          {info.producer && <p className="text-[13px] text-muted">Продюсер: <span className="text-fg">{info.producer}</span></p>}
          <OtherTitles subject="трек" names={info.names} busy={!!busy} maxLength={300}
            onChoose={(name) => void save(name, artist)}
            onAdd={(name) => otherName(() => api.addTrackName(info.track_id, name))}
            onRemove={(id) => void otherName(() => api.dropTrackName(info.track_id, id))} />
          <p className="text-[13px] text-dim">
            Изменится у всех на этом сервере{info.has_file ? ' и запишется в файл' : ''}.
            {info.last_by && info.last_at ? ` Последним менял ${info.last_by}, ${fmtAdded(info.last_at)}.` : ''}
          </p>

          {named.length > 0 && (
            <details className="border-t border-fg/10 pt-3">
              <summary className="cursor-pointer select-none text-[13px] text-muted hover:text-fg">История названия · {named.length}</summary>
              <ul className="mt-2 space-y-2">
                {named.map((e) => {
                  const was = { title: (e.changes.title?.[0] as string | undefined) ?? null, artist: (e.changes.artist?.[0] as string | undefined) ?? null };
                  return (
                    <li key={e.id} className="flex items-start gap-2 text-[13px]">
                      <span className="min-w-0 flex-1">
                        <span className="block truncate">
                          {was.title ? <TrackTitle title={`«${was.title}»`} label={version ?? null} /> : null}{was.artist ? `${was.title ? ' — ' : ''}${was.artist}` : ''}
                          <span className="text-dim"> → </span>
                          {e.changes.title ? <TrackTitle title={`«${e.changes.title[1] as string}»`} label={version ?? null} /> : null}{e.changes.artist ? `${e.changes.title ? ' — ' : ''}${e.changes.artist[1] as string}` : ''}
                        </span>
                        <span className="block text-xs text-dim">{[e.by, fmtAdded(e.at)].filter(Boolean).join(', ')}</span>
                      </span>
                      <button type="button" className="shrink-0 text-muted underline-offset-2 hover:text-fg hover:underline" title="Вернуть то, что было до этой правки" onClick={() => void save(was.title ?? info.title, was.artist ?? info.artist)}>
                        вернуть
                      </button>
                    </li>
                  );
                })}
              </ul>
            </details>
          )}
        </div>
      )}
    </Sheet>
  );
}
