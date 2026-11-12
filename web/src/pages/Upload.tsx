import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, errorMessage, libraryCoverUrl, uploadCoverUrl } from '../api/client';
import type { LibAlbumOut, ReleaseType, UploadCommitIn, UploadOut, UploadTrackIn } from '../api/types';
import { Icon } from '../components/Icon';
import { AlbumPicker } from '../components/pickers';
import { PlaylistsPick } from '../components/PlaylistsButton';
import { Button, Checkbox, Cover, ErrorState, Field, IconButton, Loading, Notice, PageHeader, ProgressBar, SectionTitle, Sheet, cx } from '../components/ui';
import { fmtDuration, fmtSize, plural, stripExt, toIntOrNull, tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { navigate } from '../lib/router';
import { openTagsPick, useTags } from '../lib/tags';
import { attempt, confirmDialog, toast } from '../lib/ui';
import { ReleaseTypeSelect } from './Library';

/** «Загрузить файлы»: the user's own audio files into the library. Files are dropped anywhere on the page (a folder
 * too) or picked; the Hub reads their tags and the page shows them as a table that can be edited in place. By
 * default every file goes to the album its tags name («Из тегов»): the files are grouped by album and album artist,
 * a group whose album the library already has is added to it, a file without an album becomes a single. The whole
 * batch can go into one new album or one existing album instead. The server takes one album per commit, so the
 * page commits group by group. */

export const AUDIO_ACCEPT = 'audio/*,.mp3,.m4a,.flac,.ogg,.opus,.wav';
const MAX_SIZE = 500 * 1024 * 1024;
const VIEW_KEY = 'hm-upload-view';
const AUDIO_EXT = /\.(mp3|m4a|aac|flac|ogg|oga|opus|wav)$/i;

export const isAudioFile = (f: File) => f.type.startsWith('audio/') || AUDIO_EXT.test(f.name);

// Files handed over by the Share page (Android "Share" → HomeMusic); consumed on mount.
let handoff: File[] = [];
export function queueFilesForUpload(files: File[]) {
  handoff = files;
}

interface Transfer {
  key: string;
  file: File;
  progress: number;
  error?: string;
}

interface RowState {
  include: boolean;
  title: string;
  artist: string;
  album: string;
  albumArtist: string;
  year: string;
  genre: string;
  trackNo: string;
  discNo: string;
}

type AlbumMode = 'tags' | 'new' | 'existing';
type CoverChoice = { kind: 'none' } | { kind: 'upload'; id: number } | { kind: 'file'; file: File; url: string };

/** The files of one album of the «Из тегов» mode. */
interface Group {
  key: string;
  title: string;
  albumArtist: string;
  year: string;
  genre: string;
  single: boolean;
  uploads: UploadOut[];
}

const str = (v: unknown) => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');
const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

function rowFromUpload(u: UploadOut): RowState {
  return {
    include: true,
    title: str(u.tags.title) || stripExt(u.orig_name),
    artist: str(u.tags.artist) || str(u.tags.album_artist),
    album: str(u.tags.album),
    albumArtist: str(u.tags.album_artist),
    year: str(u.tags.year).slice(0, 4),
    genre: str(u.tags.genre),
    trackNo: str(u.tags.track_no),
    discNo: str(u.tags.disc_no),
  };
}

/** The files of a drop: plain files and, through the entries API, everything inside dropped folders. */
async function filesFromDrop(dt: DataTransfer): Promise<File[]> {
  type Entry = { isFile: boolean; isDirectory: boolean; file?: (ok: (f: File) => void, err: () => void) => void; createReader?: () => { readEntries: (ok: (e: Entry[]) => void, err: () => void) => void } };
  const entries = Array.from(dt.items ?? [])
    .map((i) => (i.kind === 'file' ? ((i as unknown as { webkitGetAsEntry?: () => Entry | null }).webkitGetAsEntry?.() ?? null) : null))
    .filter((e): e is Entry => !!e);
  if (!entries.some((e) => e.isDirectory)) return Array.from(dt.files ?? []);
  const out: File[] = [];
  const walk = async (e: Entry): Promise<void> => {
    if (e.isFile && e.file) {
      const f = await new Promise<File | null>((res) => e.file!(res, () => res(null)));
      if (f) out.push(f);
    } else if (e.isDirectory && e.createReader) {
      const reader = e.createReader();
      for (;;) {
        const batch = await new Promise<Entry[]>((res) => reader.readEntries(res, () => res([])));
        if (!batch.length) break;
        for (const child of batch) await walk(child);
      }
    }
  };
  for (const e of entries) await walk(e);
  return out.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
}

const cell = 'w-full min-w-0 truncate rounded-md bg-transparent px-2 py-1.5 text-sm outline-none transition hover:bg-fg/5 focus:bg-raised focus:ring-1 focus:ring-accent';

export function UploadPage() {
  const pendingRes = useAsync((s) => api.uploads('uploaded', s), []);
  const [uploads, setUploads] = useState<UploadOut[]>([]);
  const [rows, setRows] = useState<Record<number, RowState>>({});
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const coverRef = useRef<HTMLInputElement>(null);

  const [mode, setMode] = useState<AlbumMode>('tags');
  const [albumTitle, setAlbumTitle] = useState('');
  const [albumArtist, setAlbumArtist] = useState('');
  const [year, setYear] = useState('');
  const [genre, setGenre] = useState('');
  const [type, setType] = useState<ReleaseType>('album');
  const [prefilled, setPrefilled] = useState(false);
  const [existing, setExisting] = useState<LibAlbumOut | null>(null);
  const [albumPicker, setAlbumPicker] = useState(false);
  const [cover, setCover] = useState<CoverChoice>({ kind: 'none' });
  const [coverTouched, setCoverTouched] = useState(false);
  const [playlists, setPlaylists] = useState<{ id: string; name: string }[]>([]);
  const [view, setView] = useState<'list' | 'albums'>(() => {
    try {
      return localStorage.getItem(VIEW_KEY) === 'albums' ? 'albums' : 'list';
    } catch {
      return 'list';
    }
  });
  const chooseView = (v: 'list' | 'albums') => {
    setView(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* storage unavailable: the choice lives until the page is closed */
    }
  };
  const [like, setLike] = useState(false);
  const [tagIds, setTagIds] = useState<number[]>([]); // the imported tracks get these tags of one's own
  const [plPicker, setPlPicker] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [bulk, setBulk] = useState(false);
  /** group key → the library album with the same title and artist (null: the library has none) */
  const [known, setKnown] = useState<Record<string, LibAlbumOut | null>>({});

  const addUploads = useCallback((list: UploadOut[]) => {
    setUploads((prev) => {
      const seen = new Set(prev.map((u) => u.id));
      return [...prev, ...list.filter((u) => !seen.has(u.id))];
    });
    setRows((prev) => {
      const next = { ...prev };
      for (const u of list) if (!next[u.id]) next[u.id] = rowFromUpload(u);
      return next;
    });
  }, []);

  useEffect(() => {
    if (pendingRes.data) addUploads(pendingRes.data);
  }, [pendingRes.data, addUploads]);

  // Pre-fill the «one new album» form and its cover from the first file's tags (once).
  useEffect(() => {
    if (prefilled || uploads.length === 0) return;
    const t = uploads[0].tags;
    setAlbumTitle((v) => v || str(t.album));
    setAlbumArtist((v) => v || str(t.album_artist) || str(t.artist));
    setYear((v) => v || str(t.year).slice(0, 4));
    setGenre((v) => v || str(t.genre));
    setPrefilled(true);
  }, [uploads, prefilled]);

  useEffect(() => {
    if (coverTouched || cover.kind !== 'none') return;
    const withCover = uploads.find((u) => u.tags.has_cover);
    if (withCover) setCover({ kind: 'upload', id: withCover.id });
  }, [uploads, cover.kind, coverTouched]);

  useEffect(() => () => void (cover.kind === 'file' && URL.revokeObjectURL(cover.url)), [cover]);

  const startUpload = useCallback(
    async (files: File[]) => {
      const ok: File[] = [];
      let skipped = 0;
      for (const f of files) {
        if (f.size > MAX_SIZE) toast(`«${f.name}» больше 500 МБ — пропущен`, 'error');
        else if (!isAudioFile(f)) skipped++;
        else ok.push(f);
      }
      if (skipped) toast(ok.length ? `Не аудио, пропущено: ${skipped}` : 'Здесь нет аудиофайлов', ok.length ? 'info' : 'error');
      const batch: Transfer[] = ok.map((file, i) => ({ key: `${Date.now()}-${i}-${file.name}`, file, progress: 0 }));
      setTransfers((prev) => [...prev, ...batch]);
      // Sequential: one slow mobile uplink is better used by one file at a time.
      for (const t of batch) {
        try {
          const res = await api.uploadFiles([t.file], (p) => setTransfers((prev) => prev.map((x) => (x.key === t.key ? { ...x, progress: p } : x))));
          addUploads(res);
          setTransfers((prev) => prev.filter((x) => x.key !== t.key));
        } catch (e) {
          setTransfers((prev) => prev.map((x) => (x.key === t.key ? { ...x, error: errorMessage(e) } : x)));
        }
      }
    },
    [addUploads],
  );

  useEffect(() => {
    if (handoff.length) {
      const files = handoff;
      handoff = [];
      void startUpload(files);
    }
  }, [startUpload]);

  // Files dropped anywhere on the page.
  useEffect(() => {
    let depth = 0;
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files');
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth++;
      setDragging(true);
    };
    const over = (e: DragEvent) => hasFiles(e) && e.preventDefault();
    const leave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (!depth) setDragging(false);
    };
    const drop = (e: DragEvent) => {
      if (!hasFiles(e) || !e.dataTransfer) return;
      e.preventDefault();
      depth = 0;
      setDragging(false);
      void filesFromDrop(e.dataTransfer).then((files) => { if (files.length) void startUpload(files); });
    };
    window.addEventListener('dragenter', enter);
    window.addEventListener('dragover', over);
    window.addEventListener('dragleave', leave);
    window.addEventListener('drop', drop);
    return () => {
      window.removeEventListener('dragenter', enter);
      window.removeEventListener('dragover', over);
      window.removeEventListener('dragleave', leave);
      window.removeEventListener('drop', drop);
    };
  }, [startUpload]);

  const removeUpload = async (u: UploadOut) => {
    const ok = await confirmDialog({ title: 'Удалить файл?', body: u.orig_name, okLabel: 'Удалить', danger: true });
    if (!ok) return;
    if (await attempt(() => api.deleteUpload(u.id))) {
      setUploads((prev) => prev.filter((x) => x.id !== u.id));
      if (cover.kind === 'upload' && cover.id === u.id) setCover({ kind: 'none' });
    }
  };

  const setRow = (id: number, patch: Partial<RowState>) => setRows((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));

  const included = uploads.filter((u) => rows[u.id]?.include);
  const invalid = included.filter((u) => !rows[u.id].title.trim() || !rows[u.id].artist.trim());

  const groups = useMemo<Group[]>(() => {
    const map = new Map<string, Group>();
    for (const u of uploads) {
      const r = rows[u.id];
      if (!r?.include) continue;
      const title = r.album.trim();
      const by = (r.albumArtist || r.artist).trim();
      const key = title ? `${title.toLowerCase()}|${by.toLowerCase()}` : `single:${u.id}`;
      const g = map.get(key) ?? { key, title: title || r.title.trim(), albumArtist: by, year: r.year, genre: r.genre, single: !title, uploads: [] };
      g.uploads.push(u);
      map.set(key, g);
    }
    return [...map.values()];
  }, [uploads, rows]);

  // Which of the albums the library already has: their files are added to it instead of making a twin.
  useEffect(() => {
    if (mode !== 'tags') return;
    const todo = groups.filter((g) => !g.single && g.albumArtist && !(g.key in known));
    if (!todo.length) return;
    const ctl = new AbortController();
    const timer = window.setTimeout(() => {
      for (const g of todo)
        void api.libAlbums(g.title, 0, 20, ctl.signal)
          .then((list) => setKnown((prev) => ({ ...prev, [g.key]: list.find((a) => same(a.title, g.title) && same(a.album_artist, g.albumArtist)) ?? null })))
          .catch(() => undefined);
    }, 400);
    return () => {
      window.clearTimeout(timer);
      ctl.abort();
    };
  }, [groups, known, mode]);

  const albumOk = mode === 'tags' ? true : mode === 'existing' ? !!existing : !!albumTitle.trim() && !!albumArtist.trim();
  const uploadingNow = transfers.some((t) => !t.error);
  const canCommit = included.length > 0 && invalid.length === 0 && albumOk && !uploadingNow;

  const trackIn = (u: UploadOut, g: string | null): UploadTrackIn => {
    const r = rows[u.id];
    return { upload_id: u.id, title: r.title.trim(), artist: r.artist.trim(), track_no: toIntOrNull(r.trackNo), disc_no: toIntOrNull(r.discNo), genre: g };
  };

  const commit = async () => {
    setCommitting(true);
    let imported = 0;
    let lastAlbum: number | null = null;
    let albums = 0;
    if (mode === 'tags') {
      for (const g of groups) {
        const lib = g.single ? null : known[g.key];
        const album: UploadCommitIn['album'] = lib
          ? { mode: 'existing', album_id: lib.id }
          : { mode: 'new', title: g.title, album_artist: g.albumArtist, year: toIntOrNull(g.year), genre: g.genre.trim() || null, release_type: g.single ? 'single' : 'album' };
        const res = await attempt(() =>
          api.commitUploads({
            album,
            tracks: g.uploads.map((u) => trackIn(u, lib ? null : rows[u.id].genre.trim() || null)),
            // an album of the library keeps its cover
            cover_from_upload_id: lib ? null : (g.uploads.find((u) => u.tags.has_cover)?.id ?? null),
            playlist_ids: playlists.map((p) => p.id),
            star: like,
            tag_ids: tagIds,
          }, null),
        );
        if (!res) break;
        imported += res.imported;
        lastAlbum = res.album_id;
        albums++;
        const done = new Set(g.uploads.map((u) => u.id));
        setUploads((prev) => prev.filter((u) => !done.has(u.id)));
      }
    } else {
      const res = await attempt(() =>
        api.commitUploads(
          {
            album:
              mode === 'existing'
                ? { mode: 'existing', album_id: existing!.id }
                : { mode: 'new', title: albumTitle.trim(), album_artist: albumArtist.trim(), year: toIntOrNull(year), genre: genre.trim() || null, release_type: type },
            tracks: included.map((u) => trackIn(u, mode === 'new' ? genre.trim() || null : null)),
            cover_from_upload_id: cover.kind === 'upload' ? cover.id : null,
            playlist_ids: playlists.map((p) => p.id),
            star: like,
            tag_ids: tagIds,
          },
          cover.kind === 'file' ? cover.file : null,
        ),
      );
      if (res) {
        imported = res.imported;
        lastAlbum = res.album_id;
        albums = 1;
      }
    }
    setCommitting(false);
    if (!albums) return;
    toast(`Импортировано: ${tracksWord(imported)}`, 'success');
    if (albums === 1 && lastAlbum !== null && (mode !== 'tags' || groups.length === 1)) navigate(`/library/album/${lastAlbum}`, { replace: true });
    else if (mode !== 'tags' || albums === groups.length) navigate('/library', { replace: true });
  };

  const coversAvailable = uploads.filter((u) => u.tags.has_cover);
  const totalSize = included.reduce((n, u) => n + u.size, 0);
  const allOn = uploads.length > 0 && included.length === uploads.length;
  const oneAlbumName = mode === 'new' ? albumTitle.trim() || 'Новый альбом' : (existing?.title ?? 'Альбом не выбран');

  const summary = (() => {
    if (mode !== 'tags' || !groups.length) return null;
    const fresh = groups.filter((g) => !g.single && !known[g.key]).length;
    const into = groups.filter((g) => !g.single && known[g.key]).reduce((n, g) => n + g.uploads.length, 0);
    const singles = groups.filter((g) => g.single).length;
    return [
      fresh ? `${plural(fresh, ['Создастся', 'Создадутся', 'Создадутся'])} ${fresh} ${plural(fresh, ['альбом', 'альбома', 'альбомов'])}` : null,
      into ? `${tracksWord(into)} ${plural(into, ['добавится', 'добавятся', 'добавятся'])} в альбомы библиотеки` : null,
      singles ? `${tracksWord(singles)} без альбома ${plural(singles, ['станет синглом', 'станут синглами', 'станут синглами'])}` : null,
    ].filter(Boolean).join(' · ');
  })();

  const importButton = uploads.length > 0 && (
    <Button variant="primary" small icon="check" busy={committing} disabled={!canCommit} onClick={commit}>
      Импортировать{included.length ? ` ${tracksWord(included.length)}` : ''}
    </Button>
  );

  return (
    <div className="pb-10">
      <PageHeader title="Загрузить файлы" back="/more" sub={included.length ? `${plural(included.length, ['Выбран', 'Выбрано', 'Выбрано'])} ${included.length} из ${uploads.length} · ${fmtSize(totalSize)}` : undefined} right={importButton || undefined} />

      {dragging && (
        <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" aria-hidden="true">
          <div className="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-accent bg-surface px-10 py-8 text-accent">
            <Icon name="upload" size={32} />
            <span className="font-semibold">Отпустите, чтобы загрузить</span>
          </div>
        </div>
      )}

      <div className="px-4">
        <input
          ref={fileRef}
          type="file"
          multiple
          accept={AUDIO_ACCEPT}
          className="hidden"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = '';
            if (files.length) void startUpload(files);
          }}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          className={cx(
            'flex w-full items-center gap-3 rounded-2xl border-2 border-dashed border-line bg-surface text-left transition hover:border-accent',
            uploads.length ? 'px-4 py-3' : 'flex-col px-4 py-8 text-center',
          )}
        >
          <span className={cx('flex shrink-0 items-center justify-center rounded-full bg-accent text-on-accent', uploads.length ? 'h-9 w-9' : 'h-12 w-12')}>
            <Icon name="upload" size={uploads.length ? 18 : 24} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block font-semibold">{uploads.length ? 'Добавить ещё файлы' : 'Перетащите сюда аудиофайлы или папку'}</span>
            <span className="block text-xs text-muted">{uploads.length ? 'Перетащите в любое место страницы или нажмите' : 'или нажмите, чтобы выбрать · MP3, M4A, FLAC, OGG/Opus, WAV · до 500 МБ на файл'}</span>
          </span>
        </button>
      </div>

      {transfers.length > 0 && (
        <ul className="mt-3 space-y-2 px-4" aria-label="Передача файлов">
          {transfers.map((t) => (
            <li key={t.key} className="card p-3">
              <div className="flex items-center gap-2 text-sm">
                <span className="min-w-0 flex-1 truncate">{t.file.name}</span>
                <span className="shrink-0 text-xs text-muted">{t.error ? '' : `${Math.round(t.progress * 100)}% · `}{fmtSize(t.file.size)}</span>
                {t.error && <IconButton icon="x" label="Убрать" onClick={() => setTransfers((prev) => prev.filter((x) => x.key !== t.key))} />}
              </div>
              {t.error ? <div className="mt-1 text-xs text-danger">{t.error}</div> : <ProgressBar value={t.progress} className="mt-2" />}
            </li>
          ))}
        </ul>
      )}

      {pendingRes.loading && !uploads.length ? (
        <Loading text="Проверяю незавершённые загрузки…" />
      ) : pendingRes.error && !uploads.length ? (
        <ErrorState text={pendingRes.error} onRetry={() => pendingRes.reload()} />
      ) : uploads.length === 0 ? (
        <p className="px-6 py-8 text-center text-sm text-muted">Загруженные файлы появятся здесь таблицей: проверьте названия и альбомы и нажмите «Импортировать».</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 pt-5">
            <span className="text-sm text-muted">Альбомы</span>
            <div className="flex overflow-hidden rounded-full bg-surface p-0.5 text-sm" role="radiogroup" aria-label="Куда импортировать">
              {([['tags', 'Из тегов файлов'], ['new', 'Один новый альбом'], ['existing', 'В существующий']] as const).map(([m, label]) => (
                <button key={m} type="button" role="radio" aria-checked={mode === m} onClick={() => setMode(m)} className={cx('min-h-9 rounded-full px-3 transition', mode === m ? 'bg-raised font-semibold text-fg' : 'text-muted hover:text-fg')}>
                  {label}
                </button>
              ))}
            </div>
            <span className="flex-1" />
            {mode === 'tags' && (
              <div className="flex overflow-hidden rounded-full bg-surface p-0.5 text-sm" role="radiogroup" aria-label="Как показать файлы">
                {([['list', 'Списком'], ['albums', 'По альбомам']] as const).map(([v, label]) => (
                  <button key={v} type="button" role="radio" aria-checked={view === v} onClick={() => chooseView(v)} className={cx('min-h-9 rounded-full px-3 transition', view === v ? 'bg-raised font-semibold text-fg' : 'text-muted hover:text-fg')}>
                    {label}
                  </button>
                ))}
              </div>
            )}
            <Button small icon="edit" disabled={!included.length} onClick={() => setBulk(true)}>
              Изменить выбранные
            </Button>
          </div>

          {mode === 'new' && (
            <div className="card mx-4 mt-3 grid gap-3 p-3 md:grid-cols-[2fr_2fr_5rem_1fr_9rem]">
              <Field label="Название альбома">
                <input className="input" value={albumTitle} onChange={(e) => setAlbumTitle(e.target.value)} />
              </Field>
              <Field label="Исполнитель альбома">
                <input className="input" value={albumArtist} onChange={(e) => setAlbumArtist(e.target.value)} />
              </Field>
              <Field label="Год">
                <input className="input" inputMode="numeric" value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, '').slice(0, 4))} />
              </Field>
              <Field label="Жанр">
                <input className="input" value={genre} onChange={(e) => setGenre(e.target.value)} />
              </Field>
              <Field label="Тип">
                <ReleaseTypeSelect value={type} onChange={setType} />
              </Field>
            </div>
          )}
          {mode === 'existing' && (
            <div className="card mx-4 mt-3 flex items-center gap-3 p-3">
              {existing ? (
                <>
                  <Cover src={existing.has_cover ? libraryCoverUrl(existing.id, 100) : null} size={48} icon="disc" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate">{existing.title}</div>
                    <div className="truncate text-xs text-muted">{[existing.album_artist, existing.year, tracksWord(existing.track_count)].filter(Boolean).join(' · ')}</div>
                  </div>
                  <Button small onClick={() => setAlbumPicker(true)}>
                    Изменить
                  </Button>
                </>
              ) : (
                <Button small icon="search" onClick={() => setAlbumPicker(true)}>
                  Выбрать альбом
                </Button>
              )}
            </div>
          )}

          {invalid.length > 0 && <div className="px-4 pt-3"><Notice kind="error">Заполните название и исполнителя у {tracksWord(invalid.length)}</Notice></div>}

          {mode === 'tags' && view === 'albums' ? (
            <AlbumsView
              uploads={uploads}
              rows={rows}
              known={known}
              setRow={setRow}
              patchRows={(ids, patch) => setRows((prev) => ({ ...prev, ...Object.fromEntries(ids.filter((id) => prev[id]).map((id) => [id, { ...prev[id], ...patch }])) }))}
              onRemove={removeUpload}
            />
          ) : (
          <div className="mx-4 mt-3 overflow-hidden rounded-xl bg-surface" role="table" aria-label="Файлы">
            <div role="row" className="hidden grid-cols-[2.75rem_minmax(0,3fr)_minmax(0,2fr)_minmax(0,2fr)_3.5rem_3.5rem_2.75rem] items-center gap-1 border-b border-line/60 pr-1 text-xs text-dim md:grid">
              <Checkbox checked={allOn} onChange={(v) => setRows((prev) => Object.fromEntries(Object.entries(prev).map(([id, r]) => [id, { ...r, include: v }])))} label="Выбрать все файлы" />
              <span className="px-2">Название</span>
              <span className="px-2">Исполнитель</span>
              <span className="px-2">Альбом</span>
              <span className="px-2">№</span>
              <span className="px-2 text-right">Время</span>
              <span />
            </div>
            {uploads.map((u) => {
              const r = rows[u.id];
              if (!r) return null;
              const bad = (v: string) => r.include && !v.trim() && 'ring-1 ring-danger/70';
              const g = mode === 'tags' && r.include ? groups.find((x) => x.uploads.includes(u)) : undefined;
              const inLibrary = g && !g.single && known[g.key];
              return (
                <div key={u.id} role="row" className={cx('grid grid-cols-[2.75rem_minmax(0,1fr)_2.75rem] items-center gap-x-1 border-b border-line/60 py-1 pr-1 last:border-0 md:grid-cols-[2.75rem_minmax(0,3fr)_minmax(0,2fr)_minmax(0,2fr)_3.5rem_3.5rem_2.75rem] md:py-0', !r.include && 'opacity-50')}>
                  <Checkbox checked={r.include} onChange={(v) => setRow(u.id, { include: v })} label={`Импортировать ${u.orig_name}`} />
                  <div className="flex min-w-0 items-center gap-1">
                    {u.tags.has_cover ? (
                      <img src={uploadCoverUrl(u.id)} alt="" loading="lazy" className="h-10 w-10 shrink-0 rounded-md object-cover" />
                    ) : (
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-raised text-dim" title="В файле нет обложки">
                        <Icon name="note" size={18} />
                      </span>
                    )}
                    <div className="min-w-0 flex-1">
                      <input className={cx(cell, 'font-medium', bad(r.title))} value={r.title} placeholder="Название" aria-label="Название" onChange={(e) => setRow(u.id, { title: e.target.value })} />
                      <div className="truncate px-2 pb-1 text-[11px] text-dim" title={u.orig_name}>
                        {u.orig_name} · {fmtSize(u.size)}
                        {u.error ? <span className="text-danger"> · {u.error}</span> : null}
                      </div>
                    </div>
                  </div>
                  <IconButton icon="trash" label={`Удалить ${u.orig_name}`} size={18} className="text-dim md:order-last" onClick={() => removeUpload(u)} />
                  <input className={cx(cell, 'col-start-2 md:col-start-auto', bad(r.artist))} value={r.artist} placeholder="Исполнитель" aria-label="Исполнитель" onChange={(e) => setRow(u.id, { artist: e.target.value })} />
                  {mode === 'tags' ? (
                    <div className="col-start-2 flex min-w-0 items-center gap-1 md:col-start-auto">
                      <input className={cell} value={r.album} placeholder="Без альбома — сингл" aria-label="Альбом" onChange={(e) => setRow(u.id, { album: e.target.value })} />
                      {inLibrary && <span className="shrink-0 rounded-full bg-info/15 px-2 py-0.5 text-[11px] text-info" title="Такой альбом уже есть в библиотеке — трек добавится в него">уже есть</span>}
                    </div>
                  ) : (
                    <span className="col-start-2 truncate px-2 py-1.5 text-sm text-muted md:col-start-auto">{oneAlbumName}</span>
                  )}
                  <input className={cx(cell, 'hidden md:block')} inputMode="numeric" value={r.trackNo} placeholder="—" aria-label="Номер трека" onChange={(e) => setRow(u.id, { trackNo: e.target.value.replace(/\D/g, '') })} />
                  <span className="hidden px-2 text-right text-xs tabular-nums text-dim md:block">{u.tags.duration_s ? fmtDuration(u.tags.duration_s) : ''}</span>
                </div>
              );
            })}
          </div>
          )}
          {summary && <p className="px-5 pt-2 text-xs text-muted">{summary}. Обложки берутся из самих файлов.</p>}

          {mode !== 'tags' && (
            <>
              <SectionTitle>Обложка</SectionTitle>
              <div className="no-scrollbar flex gap-3 overflow-x-auto px-4 pb-1">
                <CoverOption selected={cover.kind === 'none'} label={mode === 'existing' ? 'Как у альбома' : 'Без обложки'} onClick={() => { setCoverTouched(true); setCover({ kind: 'none' }); }}>
                  <Icon name="x" className="text-dim" />
                </CoverOption>
                {coversAvailable.map((u) => (
                  <CoverOption key={u.id} selected={cover.kind === 'upload' && cover.id === u.id} label={`Из «${stripExt(u.orig_name)}»`} onClick={() => { setCoverTouched(true); setCover({ kind: 'upload', id: u.id }); }}>
                    <img src={uploadCoverUrl(u.id)} alt="" className="h-full w-full object-cover" />
                  </CoverOption>
                ))}
                {cover.kind === 'file' && (
                  <CoverOption selected label="Своя картинка" onClick={() => coverRef.current?.click()}>
                    <img src={cover.url} alt="" className="h-full w-full object-cover" />
                  </CoverOption>
                )}
                <CoverOption selected={false} label="Выбрать картинку" onClick={() => coverRef.current?.click()}>
                  <Icon name="image" className="text-muted" />
                </CoverOption>
                <input
                  ref={coverRef}
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    e.target.value = '';
                    if (f) {
                      setCoverTouched(true);
                      setCover({ kind: 'file', file: f, url: URL.createObjectURL(f) });
                    }
                  }}
                />
              </div>
            </>
          )}

          <AfterImport
            count={included.length}
            like={like}
            onLike={setLike}
            playlists={playlists}
            onPlaylists={setPlaylists}
            onPickPlaylists={() => setPlPicker(true)}
            tagIds={tagIds}
            onTags={setTagIds}
          />
        </>
      )}

      <AlbumPicker
        open={albumPicker}
        onClose={() => setAlbumPicker(false)}
        title="Выберите альбом"
        allowCreate={false}
        onPick={(a) => {
          setExisting(a);
          setAlbumPicker(false);
        }}
      />
      {plPicker && (
        <PlaylistsPick
          title={`${tracksWord(included.length)} — в плейлисты`}
          picked={playlists}
          liked={like}
          onClose={() => setPlPicker(false)}
          onDone={(list, liked) => {
            setPlaylists(list);
            setLike(liked);
            setPlPicker(false);
          }}
        />
      )}
      {bulk && (
        <BulkEdit
          count={included.length}
          withAlbum={mode === 'tags'}
          onClose={() => setBulk(false)}
          onApply={(patch) => {
            setRows((prev) => Object.fromEntries(Object.entries(prev).map(([id, r]) => [id, r.include ? { ...r, ...patch } : r])));
            setBulk(false);
          }}
        />
      )}
    </div>
  );
}

/** One value for every checked file: only the fields that were filled in are changed. */
function BulkEdit({ count, withAlbum, onClose, onApply }: { count: number; withAlbum: boolean; onClose: () => void; onApply: (patch: Partial<RowState>) => void }) {
  const [artist, setArtist] = useState('');
  const [album, setAlbum] = useState('');
  const [albumArtist, setAlbumArtist] = useState('');
  const [year, setYear] = useState('');
  const patch: Partial<RowState> = {
    ...(artist.trim() ? { artist: artist.trim() } : {}),
    ...(withAlbum && album.trim() ? { album: album.trim() } : {}),
    ...(withAlbum && albumArtist.trim() ? { albumArtist: albumArtist.trim() } : {}),
    ...(withAlbum && year ? { year } : {}),
  };
  return (
    <Sheet
      open
      onClose={onClose}
      title={`Изменить ${tracksWord(count)}`}
      footer={
        <Button variant="primary" block disabled={!Object.keys(patch).length} onClick={() => onApply(patch)}>
          Применить
        </Button>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-muted">Заполните только то, что нужно поменять у всех отмеченных файлов.</p>
        <Field label="Исполнитель">
          <input className="input" value={artist} onChange={(e) => setArtist(e.target.value)} />
        </Field>
        {withAlbum && (
          <>
            <Field label="Альбом">
              <input className="input" value={album} onChange={(e) => setAlbum(e.target.value)} />
            </Field>
            <Field label="Исполнитель альбома">
              <input className="input" value={albumArtist} onChange={(e) => setAlbumArtist(e.target.value)} placeholder="Если отличается от исполнителя трека" />
            </Field>
            <Field label="Год">
              <input className="input" inputMode="numeric" value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, '').slice(0, 4))} />
            </Field>
          </>
        )}
      </div>
    </Sheet>
  );
}

function CoverOption({ selected, label, onClick, children }: { selected: boolean; label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={selected} className="flex w-24 shrink-0 flex-col items-center gap-1.5 text-center">
      <span className={cx('flex aspect-square w-full items-center justify-center overflow-hidden rounded-lg bg-raised ring-2', selected ? 'ring-accent' : 'ring-transparent')}>{children}</span>
      <span className="line-clamp-2 text-xs leading-tight text-muted">{label}</span>
    </button>
  );
}

/** «После загрузки»: where the imported tracks go besides the library — «Любимое», playlists, tags. The same icons
 * as the selection bar's and the same windows; what is chosen stands beside them and is taken off by its ✕. */
export function AfterImport({ count, like, onLike, playlists, onPlaylists, onPickPlaylists, tagIds, onTags, label = 'После загрузки', className = 'mx-4 mt-5' }: { label?: string; className?: string; count: number; like: boolean; onLike: (v: boolean) => void; playlists: { id: string; name: string }[]; onPlaylists: (list: { id: string; name: string }[]) => void; onPickPlaylists: () => void; tagIds: number[]; onTags: (ids: number[]) => void }) {
  const tags = useTags();
  const picked = tagIds.map((id) => tags.byId.get(id)).filter((t) => !!t);
  const chip = 'flex min-w-0 items-center gap-1.5 rounded-full bg-fg/10 py-1 pl-3 pr-1 text-sm';
  const off = (label: string, onClick: () => void) => (
    <button type="button" aria-label={label} title={label} onClick={onClick} className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-muted hover:bg-fg/10 hover:text-fg">
      <Icon name="x" size={13} stroke={2.5} />
    </button>
  );
  const nothing = !like && !playlists.length && !picked.length;
  return (
    <div className={cx(className, 'flex flex-wrap items-center gap-x-1 gap-y-2 rounded-xl bg-surface px-3 py-2')}>
      <span className="pr-2 text-sm text-muted">{label}</span>
      <IconButton icon="playlist" label="В «Любимое» и плейлисты" onClick={onPickPlaylists} className={like || playlists.length ? 'text-accent' : 'text-muted hover:text-fg'} />
      <IconButton icon="tag" label="Теги" onClick={() => openTagsPick(`${tracksWord(count)} — теги`, tagIds, onTags)} className={picked.length ? 'text-accent' : 'text-muted hover:text-fg'} />
      <span className="mx-2 h-5 w-px bg-line" aria-hidden="true" />
      {nothing && <span className="text-xs text-dim">Только в библиотеку. Можно сразу добавить в «Любимое», плейлисты и поставить теги.</span>}
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        {like && (
          <span className={chip}>
            <Icon name="heart" size={14} filled className="text-accent" />
            Любимое
            {off('Не добавлять в «Любимое»', () => onLike(false))}
          </span>
        )}
        {playlists.map((p) => (
          <span key={p.id} className={chip}>
            <Icon name="playlist" size={14} className="shrink-0 text-muted" />
            <span className="truncate">{p.name}</span>
            {off(`Не добавлять в «${p.name}»`, () => onPlaylists(playlists.filter((x) => x.id !== p.id)))}
          </span>
        ))}
        {picked.map((t) => (
          <span key={t.id} className={chip}>
            <Icon name="tag" size={14} className="shrink-0 text-muted" />
            <span className="truncate">{t.name}</span>
            {off(`Не ставить тег «${t.name}»`, () => onTags(tagIds.filter((x) => x !== t.id)))}
          </span>
        ))}
      </div>
    </div>
  );
}

interface ViewGroup {
  key: string;
  title: string;
  albumArtist: string;
  uploads: UploadOut[];
}

/** «По альбомам»: the files under the album their tags name — its cover, artist and length once, the tracks below
 * (a group folds). The tick of an album is the tick of all its files; ✎ changes the album of them all. Files without
 * an album are one group at the end: each becomes a single. */
function AlbumsView({ uploads, rows, known, setRow, patchRows, onRemove }: { uploads: UploadOut[]; rows: Record<number, RowState>; known: Record<string, LibAlbumOut | null>; setRow: (id: number, patch: Partial<RowState>) => void; patchRows: (ids: number[], patch: Partial<RowState>) => void; onRemove: (u: UploadOut) => void }) {
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<ViewGroup | null>(null);
  const groups = useMemo(() => {
    const map = new Map<string, ViewGroup>();
    const loose: UploadOut[] = [];
    for (const u of uploads) {
      const r = rows[u.id];
      if (!r) continue;
      const title = r.album.trim();
      if (!title) {
        loose.push(u);
        continue;
      }
      const by = (r.albumArtist || r.artist).trim();
      const key = `${title.toLowerCase()}|${by.toLowerCase()}`;
      const g = map.get(key) ?? { key, title, albumArtist: by, uploads: [] };
      g.uploads.push(u);
      map.set(key, g);
    }
    return [...map.values(), ...(loose.length ? [{ key: 'none', title: 'Без альбома', albumArtist: '', uploads: loose }] : [])];
  }, [uploads, rows]);
  const grid = 'grid grid-cols-[2.75rem_minmax(0,1fr)_2.75rem] items-center gap-x-1 md:grid-cols-[2.75rem_3.5rem_minmax(0,3fr)_minmax(0,2fr)_3.5rem_2.75rem]';

  return (
    <div className="mx-4 mt-3 space-y-2" aria-label="Файлы по альбомам">
      {groups.map((g) => {
        const none = g.key === 'none';
        const ids = g.uploads.map((u) => u.id);
        const on = g.uploads.filter((u) => rows[u.id].include).length;
        const length = g.uploads.reduce((n, u) => n + (u.tags.duration_s ?? 0), 0);
        const withCover = g.uploads.find((u) => u.tags.has_cover);
        const lib = none ? undefined : known[g.key];
        const open = !closed[g.key];
        return (
          <section key={g.key} className="overflow-hidden rounded-xl bg-surface">
            <div className={cx('flex items-center gap-2 py-2 pr-2', !on && 'opacity-50')}>
              <Checkbox checked={on === g.uploads.length} onChange={(v) => patchRows(ids, { include: v })} label={`Импортировать все файлы: ${g.title}`} />
              {withCover ? (
                <img src={uploadCoverUrl(withCover.id)} alt="" loading="lazy" className="h-12 w-12 shrink-0 rounded-md object-cover" />
              ) : (
                <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-md bg-raised text-dim">
                  <Icon name={none ? 'note' : 'disc'} size={22} />
                </span>
              )}
              <button type="button" onClick={() => setClosed((c) => ({ ...c, [g.key]: open }))} aria-expanded={open} className="min-w-0 flex-1 text-left">
                <span className="block truncate font-semibold">{g.title}</span>
                <span className="block truncate text-xs text-muted">
                  {none
                    ? `${tracksWord(g.uploads.length)} ${plural(g.uploads.length, ['станет синглом', 'станут синглами', 'станут синглами'])}`
                    : [g.albumArtist, tracksWord(g.uploads.length), length ? fmtDuration(length) : null].filter(Boolean).join(' · ')}
                </span>
              </button>
              {lib ? (
                <span className="shrink-0 rounded-full bg-info/15 px-2 py-0.5 text-[11px] text-info" title="Такой альбом уже есть в библиотеке — треки добавятся в него">уже есть в библиотеке</span>
              ) : lib === null ? (
                <span className="shrink-0 rounded-full bg-accent/15 px-2 py-0.5 text-[11px] text-accent">новый альбом</span>
              ) : null}
              {!none && <IconButton icon="edit" size={18} label={`Изменить альбом ${g.title}`} className="text-muted hover:text-fg" onClick={() => setEditing(g)} />}
              <IconButton icon={open ? 'down' : 'chevron'} size={18} label={open ? 'Свернуть' : 'Развернуть'} className="text-dim" onClick={() => setClosed((c) => ({ ...c, [g.key]: open }))} />
            </div>
            {open &&
              g.uploads.map((u) => {
                const r = rows[u.id];
                const bad = (v: string) => r.include && !v.trim() && 'ring-1 ring-danger/70';
                return (
                  <div key={u.id} role="row" className={cx(grid, 'border-t border-line/60 py-1 pl-4 pr-1 md:py-0', !r.include && 'opacity-50')}>
                    <Checkbox checked={r.include} onChange={(v) => setRow(u.id, { include: v })} label={`Импортировать ${u.orig_name}`} />
                    <input className={cx(cell, 'hidden text-center md:block')} inputMode="numeric" value={r.trackNo} placeholder="—" aria-label="Номер трека" onChange={(e) => setRow(u.id, { trackNo: e.target.value.replace(/\D/g, '') })} />
                    <div className="min-w-0">
                      <input className={cx(cell, 'font-medium', bad(r.title))} value={r.title} placeholder="Название" aria-label="Название" onChange={(e) => setRow(u.id, { title: e.target.value })} />
                      <div className="truncate px-2 pb-1 text-[11px] text-dim" title={u.orig_name}>
                        {u.orig_name} · {fmtSize(u.size)}
                        {u.error ? <span className="text-danger"> · {u.error}</span> : null}
                      </div>
                    </div>
                    <IconButton icon="trash" label={`Удалить ${u.orig_name}`} size={18} className="text-dim md:order-last" onClick={() => onRemove(u)} />
                    <input className={cx(cell, 'col-start-2 md:col-start-auto', bad(r.artist))} value={r.artist} placeholder="Исполнитель" aria-label="Исполнитель" onChange={(e) => setRow(u.id, { artist: e.target.value })} />
                    <span className="hidden px-2 text-right text-xs tabular-nums text-dim md:block">{u.tags.duration_s ? fmtDuration(u.tags.duration_s) : ''}</span>
                  </div>
                );
              })}
          </section>
        );
      })}
      {editing && (
        <AlbumEdit
          group={editing}
          first={rows[editing.uploads[0].id]}
          onClose={() => setEditing(null)}
          onApply={(patch) => {
            patchRows(editing.uploads.map((u) => u.id), patch);
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

/** ✎ of an album of «По альбомам»: its name, artist, year and genre, for all its files at once. */
function AlbumEdit({ group, first, onClose, onApply }: { group: ViewGroup; first: RowState; onClose: () => void; onApply: (patch: Partial<RowState>) => void }) {
  const [album, setAlbum] = useState(group.title);
  const [albumArtist, setAlbumArtist] = useState(group.albumArtist);
  const [year, setYear] = useState(first.year);
  const [genre, setGenre] = useState(first.genre);
  return (
    <Sheet
      open
      onClose={onClose}
      title={`Альбом — ${tracksWord(group.uploads.length)}`}
      footer={
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Отмена</Button>
          <Button variant="primary" disabled={!album.trim() || !albumArtist.trim()} onClick={() => onApply({ album: album.trim(), albumArtist: albumArtist.trim(), year, genre: genre.trim() })}>
            Применить
          </Button>
        </div>
      }
    >
      <div className="space-y-3">
        <Field label="Название альбома">
          <input className="input" value={album} onChange={(e) => setAlbum(e.target.value)} autoFocus />
        </Field>
        <Field label="Исполнитель альбома">
          <input className="input" value={albumArtist} onChange={(e) => setAlbumArtist(e.target.value)} />
        </Field>
        <Field label="Год">
          <input className="input" inputMode="numeric" value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, '').slice(0, 4))} />
        </Field>
        <Field label="Жанр">
          <input className="input" value={genre} onChange={(e) => setGenre(e.target.value)} />
        </Field>
        <p className="text-xs text-dim">Если назвать альбом так же, как другой из этой загрузки или из библиотеки, файлы объединятся с ним.</p>
      </div>
    </Sheet>
  );
}
