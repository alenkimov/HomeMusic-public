import { TrackTitle } from './TrackTitle';
import { TrackCell } from './Track';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { VersionOut, VersionsOut } from '../api/types';
import { fmtDuration } from '../lib/format';
import { useAsync, useWindowEvent } from '../lib/hooks';
import { imageUrl } from '../lib/img';
import { listen, useListen } from '../lib/listen';
import { EDITED_EVENT } from '../lib/trackEdit';
import { attempt, toast } from '../lib/ui';
import { closeVersions, useVersions, type VersionsOf } from '../lib/versions';
import { Icon } from './Icon';
import { Cover, ErrorState, Sheet, Spinner, cx } from './ui';

function playVersion(v: VersionOut, ref: string) {
  listen({
    ref,
    nd_song_id: v.nd_song_id,
    library_track_id: v.track_id,
    title: v.title,
    artist: v.artist,
    cover_url: v.cover_url,
    cover_small_url: v.cover_url,
    album: v.album ?? undefined,
    duration_s: v.duration_s ?? null,
  });
}

/** The versions linked to the recording playing now. A known id lets the player look them up without registering a catalog track. */
export function OtherVersions({ trackId, songId }: { trackId?: number | null; songId?: string | null }) {
  const { data, reload } = useAsync(
    (signal) => api.versions(trackId != null ? { track_id: trackId } : { nd_song_id: songId ?? undefined }, signal),
    [trackId, songId],
  );
  useWindowEvent(EDITED_EVENT, () => reload(true));

  const versions = data?.versions.filter((v) => v.track_id !== data.track_id);
  if (!versions?.length) return null;

  return (
    <section aria-label="Другие версии" className="mx-4 mt-3 rounded-2xl bg-surface px-4 pb-2 pt-3">
      <h2 className="pb-1 text-base font-bold">Другие версии</h2>
      <ul className="-mx-2">
        {versions.map((v) => {
          // Play the linked recording directly when it has a file indexed by Navidrome.
          const ref = v.nd_song_id ? `nd:${v.nd_song_id}` : v.ref;
          return (
            <li key={v.track_id}>
              <button
                type="button"
                aria-label={`Слушать: ${[v.title, v.label, v.artist].filter(Boolean).join(' · ')}`}
                disabled={!ref}
                onClick={() => ref && playVersion(v, ref)}
                className="flex min-h-14 w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left enabled:hover:bg-fg/5 focus-visible:bg-fg/10"
              >
                <TrackCell track={{ title: v.title, cover: v.cover_url, label: v.label ?? null }} sub={[v.artist, fmtDuration(v.duration_s)].filter(Boolean).join(' · ')} />
                {ref && (
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-fg/30" aria-hidden="true">
                    <Icon name="play" size={14} filled />
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** «Версии песни»: the recordings of one song — the album cut, an older take, a remix, a live one. Each is a track
 * of its own, in its album, with its artists and its file; here they stand together, as the rows of a playlist:
 * the cover that plays the recording, its name with a word for what it is, whose it is and where from, its length.
 *
 * One of them is the preferred version (★): what a list that shows the song once shows, and what plays by itself.
 * It is said by hand, for everyone — what a listener likes is said by ♥ on the track. A track of the library is
 * joined as a version by looking for it below; the broken chain says a track is another song after all. The word
 * for what a version is can be written by a tap on it. */
export function Versions() {
  const of = useVersions();
  return of ? <Window key={`${of.library_track_id}:${of.ref}:${of.nd_song_id}`} of={of} /> : null;
}

function Window({ of }: { of: VersionsOf }) {
  const res = useAsync((s) => api.versions({ track_id: of.library_track_id ?? undefined, ref: of.library_track_id ? undefined : (of.ref ?? undefined), nd_song_id: of.library_track_id || of.ref ? undefined : (of.nd_song_id ?? undefined) }, s), [of.library_track_id, of.ref, of.nd_song_id]);
  const [said, setSaid] = useState<VersionsOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState('');
  const [writing, setWriting] = useState<number | null>(null); // the version whose word is being written
  const [changed, setChanged] = useState(false);
  const d = said ?? res.data;
  const id = d?.track_id;
  const words = q.trim();
  const found = useAsync((s) => (id && words.length > 1 ? api.versionsFind(id, words, s) : Promise.resolve([] as VersionOut[])), [id, words]);
  // …and the catalog's songs the same words find: a version need not have a file here
  const inCatalog = useAsync((s) => (words.length > 1 ? api.searchTracks(words, 0, s) : Promise.resolve(null)), [words]);
  const playing = useListen((s) => (s.phase === 'playing' ? s.track?.ref : null));

  const close = () => {
    if (changed) window.dispatchEvent(new Event(EDITED_EVENT));
    closeVersions();
  };
  useEffect(() => () => void (changed && window.dispatchEvent(new Event(EDITED_EVENT))), [changed]);
  const run = async (what: () => Promise<VersionsOut>, done?: string) => {
    setBusy(true);
    const next = await attempt(what);
    setBusy(false);
    if (!next) return false;
    setSaid(next);
    setChanged(true);
    if (done) toast(done, 'success');
    return true;
  };
  const row = (v: VersionOut, actions: React.ReactNode, joined: boolean) => {
    const ref = v.ref ?? (v.nd_song_id ? `nd:${v.nd_song_id}` : null);
    const on = !!ref && playing === ref;
    return (
      <li key={v.track_id} className="flex items-center gap-3 border-b border-line/60 py-1.5 last:border-0">
        <button type="button" disabled={!ref} aria-label={`Слушать: ${v.title}`} onClick={() => ref && playVersion(v, ref)} className="group/c relative flex h-10 w-10 shrink-0 items-center justify-center">
          <Cover src={imageUrl(v.cover_url)} size={40} alt="" />
          {ref && (
            <span aria-hidden="true" className={cx('absolute inset-0 flex items-center justify-center rounded-md bg-black/60 text-white', !on && 'opacity-0 group-hover/c:opacity-100')}>
              <Icon name={on ? 'pause' : 'play'} size={16} filled />
            </span>
          )}
        </button>
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-1.5">
            <span className={cx('min-w-0 truncate text-[15px]', on && 'text-accent')}><TrackTitle title={v.title} track={joined ? undefined : v} /></span>
            {joined &&
              (writing === v.track_id ? (
                <input
                  autoFocus
                  className="input !h-6 w-36 !px-2 !py-0 text-xs"
                  defaultValue={v.label ?? ''}
                  placeholder="старая версия"
                  aria-label="Что это за версия"
                  maxLength={64}
                  onBlur={(e) => {
                    setWriting(null);
                    if (e.target.value.trim() !== (v.label ?? '')) void run(() => api.versionPatch(v.track_id, { label: e.target.value.trim() }).then(() => api.versions({ track_id: id })));
                  }}
                  onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
                />
              ) : (
                <button type="button" title="Что это за версия — нажмите, чтобы вписать" onClick={() => setWriting(v.track_id)} className={cx('shrink-0 rounded-full px-2 text-[11px]', v.label ? 'bg-fg/10 text-muted hover:text-fg' : 'border border-dashed border-fg/25 text-dim hover:text-fg')}>
                  {v.label || 'подпись'}
                </button>
              ))}
          </span>
          <span className="block truncate text-[13px] text-muted">{[v.artist, v.album, v.has_file ? [v.codec?.toUpperCase(), v.bitrate_kbps].filter(Boolean).join(' ') : 'нет файла'].filter(Boolean).join(' · ')}</span>
        </span>
        <span className="shrink-0 text-xs tabular-nums text-dim">{fmtDuration(v.duration_s)}</span>
        {actions}
      </li>
    );
  };
  // of the catalog's songs: the ones that are neither a version already nor among the library's own found above
  const known = new Set([...(d?.versions ?? []).map((v) => v.track_id), ...(found.data ?? []).map((v) => v.track_id)]);
  const knownRefs = new Set((d?.versions ?? []).map((v) => v.ref).filter(Boolean));
  const catalogOnly: VersionOut[] = (inCatalog.data?.tracks ?? [])
    .filter((t) => !knownRefs.has(t.ref) && !(t.library_track_id && known.has(t.library_track_id)))
    .slice(0, 8)
    .map((t, i) => ({ track_id: -1 - i, title: t.title, artist: t.artists?.length ? t.artists.join(', ') : t.artist, album: t.album_title, duration_s: t.duration_s, label: null, preferred: false, cover_url: t.cover_small_url ?? t.cover_url, ref: t.ref, nd_song_id: t.nd_song_id, has_file: !!t.owned }));
  const small = 'flex h-9 w-9 shrink-0 items-center justify-center rounded-full hover:bg-fg/10';
  const many = (d?.versions.length ?? 0) > 1;

  return (
    <Sheet open onClose={close} title="Версии песни">
      {res.error && !d ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !d ? (
        <div className="flex justify-center py-10">
          <Spinner className="text-accent" />
        </div>
      ) : (
        <div className={cx('space-y-3 text-fg', busy && 'pointer-events-none opacity-60')}>
          <p className="text-[13px] text-muted">Одна песня, разные записи. Каждая остаётся своим треком в своём альбоме.</p>
          <ul>
            {d.versions.map((v) =>
              row(
                v,
                many ? (
                  <>
                    <button type="button" aria-pressed={v.preferred} aria-label={v.preferred ? 'Предпочитаемая версия' : `Сделать предпочитаемой: ${v.title}`} title={v.preferred ? 'Предпочитаемая версия: её показывают списки и берут миксы' : 'Сделать предпочитаемой — для всех'} onClick={() => !v.preferred && void run(() => api.versionPatch(v.track_id, { preferred: true }).then(() => api.versions({ track_id: id })), 'Предпочитаемая версия изменена у всех')} className={cx(small, v.preferred ? 'text-warn' : 'text-dim hover:text-fg')}>
                      <Icon name="star" size={18} filled={v.preferred} />
                    </button>
                    <button type="button" aria-label={`Это другая песня: ${v.title}`} title="Это другая песня — отвязать" onClick={() => void run(() => api.versionDrop(v.track_id, v.track_id === id ? undefined : id))} className={cx(small, 'text-dim hover:text-fg')}>
                      <Icon name="unlink" size={17} />
                    </button>
                  </>
                ) : null,
                many,
              ),
            )}
          </ul>
          <div>
            <input className="input w-full" placeholder="Добавить версию: название или исполнитель" aria-label="Добавить версию" value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" />
            {words.length > 1 && (
              <ul className="pt-1">
                {(found.data ?? []).map((v) =>
                  row(
                    v,
                    <button type="button" className="shrink-0 rounded-full bg-fg/10 px-3 py-1 text-xs hover:bg-fg/20" onClick={() => void run(() => api.versionAdd(id as number, { track_id: v.track_id })).then((ok) => ok && setQ(''))}>
                      это версия
                    </button>,
                    false,
                  ),
                )}
                {catalogOnly.map((v) =>
                  row(
                    v,
                    <button type="button" className="shrink-0 rounded-full bg-fg/10 px-3 py-1 text-xs hover:bg-fg/20" onClick={() => void run(() => api.versionAdd(id as number, { ref: v.ref as string })).then((ok) => ok && setQ(''))}>
                      это версия
                    </button>,
                    false,
                  ),
                )}
                {!found.loading && !inCatalog.loading && (found.data ?? []).length === 0 && catalogOnly.length === 0 && <li className="py-3 text-center text-[13px] text-muted">Ничего не нашлось ни в библиотеке, ни в каталоге.</li>}
              </ul>
            )}
          </div>
          <p className="text-[12px] text-dim">Связь и предпочитаемую версию видят все на этом сервере. Что нравится вам — отмечается ♥ на самом треке.</p>
        </div>
      )}
    </Sheet>
  );
}
