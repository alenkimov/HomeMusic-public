import { TrackTitle, VersionBadge } from '../components/TrackTitle';
import { titleWithVersion, useVersionLabel, type VersionIdentity } from '../lib/versionLabels';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, errorMessage, uploadCoverUrl } from '../api/client';
import type { CoverInfo, TrackOut, UploadAlbumIn, UploadOut, UploadTrackIn } from '../api/types';
import { Icon } from '../components/Icon';
import { PlaylistsPick } from '../components/PlaylistsButton';
import { Button, Cover, Empty, ErrorState, Field, IconButton, Loading, PageHeader, ProgressBar, Sheet, Spinner, cx } from '../components/ui';
import { useDesktop } from '../lib/desktop';
import { fmtDuration, fmtSize, pictureWords, plural, toIntOrNull, tracksWord } from '../lib/format';
import { useAsync, useDebounced } from '../lib/hooks';
import { imageUrl } from '../lib/img';
import { listen, useListen, type ListenTrack } from '../lib/listen';
import { useSearchField } from '../components/ListSearch';
import { listenTrack } from '../components/catalog';
import { navigate } from '../lib/router';
import { openTagsPick, useTags } from '../lib/tags';
import { attempt, confirmDialog, toast } from '../lib/ui';

/** «Загрузить файлы», the first of two steps: what the files are. Files — or a folder, with what is inside its
 * folders — are dropped anywhere on the page or picked; the Hub reads each and looks for the song it is in the
 * catalog (`library/recognize.py`).
 *
 * The page is lists of an album each. What belongs to an album is said once, in its head, which stands as an
 * album's page does: the cover, the word «Альбом» or «Сингл» over the name, the artist and the year under it. An
 * album the catalog knows is the catalog's release — its name, artist and year are the catalog's and are not
 * rewritten; the broken chain by the name takes its files off the release, and then it is an album of one's own,
 * the three written by hand. The cover is chosen either way among every picture found — the catalog's and the ones
 * inside the files, each with its size and weight: one is shown, a tap lays the rest out. Under the head its files
 * stand as the tracks of a playlist: the number, the cover that plays the file in the app's own player, the name
 * with everyone credited, the length.
 *
 * In a row what the catalog says is told apart where it stands: a value that came from the catalog is in a blue
 * pill, what the file said in its place is struck out beside it, and what did not change is plain. So a number the
 * file had not is a pill; a guest the catalog adds is a pill after the file's own artist; a name the catalog writes
 * otherwise is the file's struck out and the catalog's in a pill.
 * A single stands the same way — a head of its own, called as its track is, and its one file under it. A row opens
 * to be put right by hand (its ⋯).
 *
 * «Перенести» brings in every file of the page. The rows are a playlist's: on a computer a table whose columns of
 * the file itself (its name, its size, what it is) can be hidden; a row's ⋯ is its menu — its names put right by
 * hand, the catalog's record taken, left or changed, the file put into another album or taken off the list. Rows
 * are picked to act on together as a playlist's are — a click on a row's number, a drag from it over the rows,
 * Ctrl+click, Shift+click, Ctrl+A for all that are shown; on a phone «Выбрать» of the menu, then taps — and a bar
 * comes up with what can be done with several at once: gathered into one album, left as the files say, looked for
 * in the catalog again, taken off the list.
 *
 * A file the catalog only has something *like* is not asked about: it goes in under its own names, and the second
 * step — «Качество звука» (`pages/Quality.tsx`) — settles by the sound whether the record is its own. Nothing here
 * is about how good the files sound either: that is the second step too. */

export const AUDIO_ACCEPT = 'audio/*,.mp3,.m4a,.flac,.ogg,.opus,.wav';
const MAX_SIZE = 500 * 1024 * 1024;
const AUDIO_EXT = /\.(mp3|m4a|aac|flac|ogg|oga|opus|wav)$/i;
const CHUNK = 20; // files brought in by one request: the page says how far it has got

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
  /** the library has this recording (`UploadOut.twin`) and this file takes the place of its file — else, included,
   * it is added as a track of its own */
  replace: boolean;
  /** filed as the song of the catalog it was told to be; else under the names below */
  catalog: boolean;
  /** a single's picture: the catalog's or the file's (an album's is chosen in its head) */
  cover: 'catalog' | 'file';
  title: string;
  artist: string;
  album: string;
  year: string;
  /** put into an album by hand: that album's key (not said: the album the file is of by itself) */
  group?: string;
  /** the name the file had, where the catalog's is taken instead, is kept as a second name of the song */
  also?: boolean;
  /** another recording of the song the catalog took the file for: filed under its own names, tied to it as a version */
  version?: boolean;
  /** a record only like the file: not to be weighed against it by the sound on the second step */
  skip?: boolean;
}

/** The columns of the table that say what the file itself is: they can be hidden (the choice stays on the device). */
interface Columns {
  file: boolean;
  size: boolean;
  quality: boolean;
}
const COLUMNS: [keyof Columns, string][] = [['file', 'Файл'], ['size', 'Размер'], ['quality', 'Качество']];
const COLS_KEY = 'hm-upload-cols';
const grid = (c: Columns) => ({ gridTemplateColumns: ['3.75rem', '2.5rem', 'minmax(0,1.6fr)', c.file ? 'minmax(0,1.1fr)' : null, c.size ? '4.5rem' : null, c.quality ? '5.5rem' : null, '3rem', '2.5rem'].filter(Boolean).join(' ') });

/** What was chosen in an album's head (nothing said: the page's own pick — `Group.title` and the rest). */
interface AlbumChoice {
  title?: string;
  artist?: string;
  year?: string;
  cover?: string; // a `Picture.key`
}

/** A picture an album may take: the catalog's of an upload's record, or the one inside an uploaded file. */
interface Picture {
  key: string;
  url: string;
  from: 'catalog' | 'file';
  upload: number;
  /** in how many of the album's files it is */
  count: number;
  info?: CoverInfo | null;
}

/** The files of one album, with everything the album may be called. */
interface Group {
  key: string;
  /** a single: one file that is of no album — its head has no name to choose */
  single: boolean;
  files: UploadOut[];
  /** an upload whose catalog record stands on this release (none: the catalog does not know the album) */
  release: UploadOut | null;
  titles: string[];
  artists: string[];
  years: string[];
  /** what the catalog says of the release (none: it does not know it) — shown in a pill where the files say otherwise */
  cat: { title: string | null; artist: string | null; year: string | null };
  /** a file of the album says the same as the catalog: the value is no news, and stands plain */
  agree: { title: boolean; artist: boolean; year: boolean };
  pictures: Picture[];
  /** the page's pick, where the listener said nothing */
  title: string;
  artist: string;
  year: string;
  cover: string | null;
}

type Only = 'all' | 'fill' | 'doubt' | 'changes' | 'none' | 'twin';

const str = (v: unknown) => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');
const low = (s?: string | null) => (s ?? '').trim().replace(/\s+/g, ' ').toLocaleLowerCase('ru');
const same = (a?: string | null, b?: string | null) => low(a) === low(b);
/** The values of a list, each once, the most frequent first. */
function byCount(list: (string | null | undefined)[]): string[] {
  const n = new Map<string, number>();
  for (const v of list) if (v && v.trim()) n.set(v.trim(), (n.get(v.trim()) ?? 0) + 1);
  return [...n.entries()].sort((a, b) => b[1] - a[1]).map(([v]) => v);
}

function rowFromUpload(u: UploadOut): RowState {
  const found = u.match?.state === 'found';
  return {
    // a recording the library has is left out — unless this file is the better one: then it takes the old one's
    // place. One that only sounds like a track of the library (another version of the song) is a track of its own
    include: !u.twin || u.twin.better || !u.twin.sure,
    replace: !!u.twin?.better,
    catalog: found,
    cover: found && u.match?.cover_url ? 'catalog' : 'file',
    title: u.title,
    artist: u.artist,
    album: u.single ? '' : str(u.tags.album),
    year: str(u.tags.year).slice(0, 4),
  };
}

/** What the listener chose on the page — in the albums' heads and in the rows — is kept on the device while the
 * files wait: the page may be closed or reloaded before they are brought in. Of a row only what differs from the
 * page's own pick is kept, so a file looked for in the catalog again is not held to an old answer. */
const FORM_KEY = 'hm-upload-form';
interface SavedForm {
  rows: Record<string, Partial<RowState>>;
  chosen: Record<string, AlbumChoice>;
}
function savedForm(): SavedForm {
  try {
    const v = JSON.parse(localStorage.getItem(FORM_KEY) ?? '{}') as Partial<SavedForm>;
    return { rows: v.rows ?? {}, chosen: v.chosen ?? {} };
  } catch {
    return { rows: {}, chosen: {} };
  }
}

const fileWords = (codec?: string | null, kbps?: number | null) => [codec ? codec.toUpperCase() : null, kbps ? String(kbps) : null].filter(Boolean).join(' ');
/** The record's release as the file is filed by it: no album for a single. */
const catAlbum = (u: UploadOut): string | null => (u.match?.record_type === 'single' || !u.match?.album ? null : u.match.album);
const usesCatalog = (u: UploadOut, r: RowState) => r.catalog && !!u.match?.ref;
const valid = (u: UploadOut, r: RowState) => usesCatalog(u, r) || (!!r.title.trim() && !!r.artist.trim());
/** A record only like the file, not taken by hand: the second step settles it by the sound. */
const unsure = (u: UploadOut, r: RowState) => u.match?.state === 'doubt' && !usesCatalog(u, r) && !r.version && !r.skip;
/** Said to be another version of the song the catalog took it for (and so not filed by the catalog's record). */
const isVersion = (u: UploadOut, r: RowState) => !!r.version && !!u.match?.ref && !usesCatalog(u, r);

/** What the catalog's record changes in the track itself — its name, who is credited, its place in the album — in
 * a few words each. A name in another case («утром» for «Утром») is no change: the file's spelling is kept. */
function changesOf(u: UploadOut, inAlbum: boolean): string[] {
  const m = u.match;
  if (!m?.ref) return [];
  const out: string[] = [];
  if (!same(u.title, m.title)) out.push(`название → ${titleWithVersion(m.title ?? '', m)}`);
  const mine = u.artists ?? [];
  const theirs = m.artists?.length ? m.artists : [m.artist ?? ''];
  if (mine.map(low).join('|') !== theirs.map(low).join('|')) out.push(same(mine.join(', '), theirs.join(', ')) || (mine.length === theirs.length && mine.every((a, i) => same(a, theirs[i]))) ? 'исполнители по отдельности' : `исполнители → ${theirs.join(' · ')}`);
  else if (theirs.length > 1 && !(Array.isArray(u.tags.artists) && (u.tags.artists as unknown[]).length > 1)) out.push('исполнители по отдельности');
  if (inAlbum && m.track_no && String(m.track_no) !== str(u.tags.track_no)) out.push(`+ № ${m.track_no}`);
  return out;
}

/** Under a file the library has already: which track it is, how it was told (by the sound of the two files, or by
 * their names and length) and what the library's file is — and what to do with this one: leave it out, put it in
 * the place of the library's file (offered first when this one is better), or add it as a track of its own. */
function TwinNote({ u, r, onChange }: { u: UploadOut; r: RowState; onChange: (patch: Partial<RowState>) => void }) {
  const twin = u.twin;
  if (!twin) return null;
  const mode = !r.include ? 'skip' : r.replace ? 'replace' : 'add';
  const chip = (id: typeof mode, label: string, title: string, patch: Partial<RowState>) => (
    <button type="button" title={title} aria-pressed={mode === id} onClick={() => onChange(patch)} className={cx('rounded-full px-2 py-0.5 text-[11px] transition', mode === id ? 'bg-info/20 font-semibold text-info' : 'bg-fg/5 text-muted hover:text-fg')}>
      {label}
    </button>
  );
  const theirs = fileWords(twin.codec, twin.bitrate_kbps);
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-info">
      <span className="min-w-0 truncate" title={!twin.sure ? 'Звучит похоже, но не одинаково: так бывает у версии на другом языке или кавера на ту же музыку' : twin.by === 'sound' ? 'Та же запись — определено по звучанию файлов' : 'То же название, исполнитель и длительность'}>
        {twin.sure ? 'Уже есть' : 'Похоже на'}: «<TrackTitle title={twin.title} track={twin} />»{theirs ? ` · ${theirs}` : ''} · {twin.by === 'sound' ? 'по звуку' : 'по названию'}
        {twin.better ? ' · этот файл лучше' : ''}
        {!twin.sure ? ' · возможно, другая версия' : ''}
      </span>
      {chip('skip', 'не загружать', 'В библиотеке останется тот файл, что есть', { include: false })}
      {chip('replace', 'заменить', 'Этот файл станет файлом того же трека: плейлисты, лайки и текст останутся при нём', { include: true, replace: true })}
      {chip('add', 'добавить вторым', 'Появится отдельный трек — например, это другая версия записи', { include: true, replace: false })}
    </div>
  );
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

/** What the catalog's picture of a record is — fetched by the Hub to be measured, a few at a time, once each. */
const measured = new Map<number, Promise<CoverInfo | null>>();
let measuring: Promise<unknown> = Promise.resolve();
function useCatalogCover(upload: number | null): CoverInfo | null {
  const [info, setInfo] = useState<CoverInfo | null>(null);
  useEffect(() => {
    if (upload === null) return;
    let alive = true;
    let p = measured.get(upload);
    if (!p) {
      p = (measuring = measuring.then(() => api.uploadCatalogCover(upload).catch(() => null))) as Promise<CoverInfo | null>;
      measured.set(upload, p);
    }
    void p.then((v) => alive && setInfo(v));
    return () => {
      alive = false;
    };
  }, [upload]);
  return info;
}

export function UploadPage() {
  const desktop = useDesktop();
  const pendingRes = useAsync((s) => api.uploads('uploaded', s), []);
  const [uploads, setUploads] = useState<UploadOut[]>([]);
  const [rows, setRows] = useState<Record<number, RowState>>({});
  const saved = useRef<SavedForm>(savedForm());
  const [chosen, setChosen] = useState<Record<string, AlbumChoice>>(() => saved.current.chosen); // by an album's key
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const [only, setOnly] = useState<Only>('all');
  const sf = useSearchField('Название, исполнитель, альбом или имя файла');
  const [menu, setMenu] = useState<number | null>(null); // the row whose menu is open
  const [editing, setEditing] = useState<number | null>(null); // the file whose names are being written
  const [toAlbum, setToAlbum] = useState<number[] | null>(null); // the files an album is being chosen for
  const [cols, setColsState] = useState<Columns>(() => {
    try {
      return { file: false, size: true, quality: true, ...(JSON.parse(localStorage.getItem(COLS_KEY) ?? '{}') as Partial<Columns>) };
    } catch {
      return { file: false, size: true, quality: true };
    }
  });
  const setCols = (next: Columns) => {
    setColsState(next);
    try { localStorage.setItem(COLS_KEY, JSON.stringify(next)); } catch { /* it lasts for this page then */ }
  };
  const [finding, setFinding] = useState<UploadOut | null>(null); // the file a record is being picked for
  const [playlists, setPlaylists] = useState<{ id: string; name: string }[]>([]);
  const [like, setLike] = useState(false);
  // What is uploaded is kept together in a playlist of the day («Загружено 14 ноября»), so that tracks nobody put
  // anywhere can still be found as one list. Turned off, it stays off on this device.
  const [keep, setKeep] = useState(() => { try { return localStorage.getItem(KEEP_OFF) !== '1'; } catch { return true; } });
  const keepName = useMemo(() => `Загружено ${new Date().toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).replace(/\s*г\.$/, '')}`, []);
  const turnKeep = (on: boolean) => {
    setKeep(on);
    try { if (on) localStorage.removeItem(KEEP_OFF); else localStorage.setItem(KEEP_OFF, '1'); } catch { /* the choice lasts for this page then */ }
  };
  const [tagIds, setTagIds] = useState<number[]>([]); // the imported tracks get these tags of one's own
  const [plPicker, setPlPicker] = useState(false);
  const [moving, setMoving] = useState<{ done: number; of: number } | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set()); // the files picked to act on together
  const drag = useRef<{ from: number; moved: boolean } | null>(null); // a drag from a row's number picks the rows passed
  const anchor = useRef<number | null>(null); // the row a Shift+click picks from

  const addUploads = useCallback((list: UploadOut[]) => {
    setUploads((prev) => {
      const seen = new Set(prev.map((u) => u.id));
      return [...prev, ...list.filter((u) => !seen.has(u.id))];
    });
    setRows((prev) => {
      const next = { ...prev };
      for (const u of list) if (!next[u.id]) next[u.id] = { ...rowFromUpload(u), ...saved.current.rows[u.id] };
      return next;
    });
  }, []);

  useEffect(() => {
    if (pendingRes.data) addUploads(pendingRes.data);
  }, [pendingRes.data, addUploads]);

  // what was chosen is kept on the device (`savedForm`): once the waiting files are known — of them only
  useEffect(() => {
    if (pendingRes.loading) return;
    try {
      if (!uploads.length) {
        localStorage.removeItem(FORM_KEY);
        return;
      }
      const out: SavedForm = { rows: {}, chosen };
      for (const u of uploads) {
        const r = rows[u.id];
        if (!r) continue;
        const own = rowFromUpload(u);
        const diff = Object.fromEntries(Object.entries(r).filter(([k, v]) => v !== (own as unknown as Record<string, unknown>)[k]));
        if (Object.keys(diff).length) out.rows[u.id] = diff;
      }
      localStorage.setItem(FORM_KEY, JSON.stringify(out));
    } catch {
      /* no storage: the choices last for this page */
    }
  }, [rows, chosen, uploads, pendingRes.loading]);

  // a folder is picked by an input of its own: the attribute is not one React knows
  useEffect(() => folderRef.current?.setAttribute('webkitdirectory', ''), []);

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

  const setRow = (id: number, patch: Partial<RowState>) => setRows((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  const rowOf = (u: UploadOut) => rows[u.id] ?? rowFromUpload(u);

  const removeUpload = async (u: UploadOut) => {
    const ok = await confirmDialog({ title: 'Убрать файл?', body: u.orig_name, okLabel: 'Убрать', danger: true });
    if (!ok) return;
    if (await attempt(() => api.deleteUpload(u.id))) setUploads((prev) => prev.filter((x) => x.id !== u.id));
  };

  /** The picked files go off the list — and off the server: they were only waiting here. */
  const removePicked = async () => {
    const out = uploads.filter((u) => picked.has(u.id));
    const ok = await confirmDialog({ title: `Убрать из списка: ${tracksWord(out.length)}?`, body: 'Эти загруженные файлы удалятся с сервера. Библиотеку и исходные файлы на вашем компьютере это не затронет.', okLabel: 'Убрать', danger: true });
    if (!ok) return;
    const gone = new Set<number>();
    for (const u of out) if (await attempt(() => api.deleteUpload(u.id))) gone.add(u.id);
    setUploads((prev) => prev.filter((u) => !gone.has(u.id)));
    setPicked(new Set());
  };

  /** The record of the catalog a file is, picked by hand (or looked for again): the row is the catalog's then. */
  const rematch = async (u: UploadOut, ref?: string | null) => {
    const next = await attempt(() => api.matchUpload(u.id, ref));
    if (!next) return;
    setUploads((prev) => prev.map((x) => (x.id === u.id ? next : x)));
    const taken = ref ? true : next.match?.state === 'found';
    setRow(u.id, { catalog: taken, cover: taken && next.match?.cover_url ? 'catalog' : 'file' });
  };

  /** Every file's album: the catalog's release when its record is taken; else the file's own album — the same one
   * when a release of that name and artist is among the files (a track the catalog does not know stays with its
   * album); none — a single. */
  const all = useMemo(() => {
    const row = (u: UploadOut) => rows[u.id] ?? rowFromUpload(u);
    const releases = new Map<string, string>(); // name|artist -> the release's key
    const keys = new Map<number, string | null>();
    // what a file itself calls its album: files that say the same are one album, whatever the catalog calls its artist
    const ownAlbum = (u: UploadOut) => (u.single || !str(u.tags.album).trim() ? null : `${low(str(u.tags.album))}|${low(str(u.tags.album_artist) || u.artists?.[0])}`);
    for (const u of uploads) {
      const a = usesCatalog(u, row(u)) ? catAlbum(u) : null;
      if (!a) continue;
      const k = `c|${low(a)}|${low(u.match?.album_artist ?? u.match?.artist)}`;
      keys.set(u.id, k);
      releases.set(`${low(a)}|${low(u.match?.album_artist ?? u.match?.artist)}`, k);
      const own = ownAlbum(u);
      // …so a file's own album leads to the release its neighbours went into — when the release is of that name
      if (own && same(str(u.tags.album), a) && !releases.has(own)) releases.set(own, k);
    }
    for (const u of uploads) {
      if (keys.has(u.id)) continue;
      const r = row(u);
      const a = r.album.trim();
      // a single: an «album» of its own. So is a file filed by a record of the catalog that stands on no album —
      // whatever album its own tags name: the record is what it goes in by
      if (!a || same(a, r.title) || usesCatalog(u, r)) {
        keys.set(u.id, `s|${u.id}`);
        continue;
      }
      const by = str(u.tags.album_artist) || u.artists?.[0] || r.artist;
      keys.set(u.id, releases.get(`${low(a)}|${low(by)}`) ?? (same(a, str(u.tags.album)) ? releases.get(ownAlbum(u) ?? '') : undefined) ?? `o|${low(a)}|${low(by)}`);
    }
    // …and a file put into an album by hand is of that album, whatever it says
    const natural = new Map(keys);
    for (const u of uploads) {
      const said = row(u).group;
      if (said !== undefined) keys.set(u.id, said);
    }
    const groups = new Map<string, Group>();
    for (const u of uploads) {
      const k = keys.get(u.id);
      if (!k) continue;
      const g = groups.get(k) ?? groups.set(k, { key: k, single: k.startsWith('s|'), files: [], release: null, titles: [], artists: [], years: [], cat: { title: null, artist: null, year: null }, agree: { title: false, artist: false, year: false }, pictures: [], title: '', artist: '', year: '', cover: null }).get(k)!;
      g.files.push(u);
    }
    for (const g of groups.values()) {
      // the release is the album's own files' — not that of a file brought in from elsewhere by hand
      const taken = g.files.filter((u) => usesCatalog(u, row(u)) && (g.single || !!catAlbum(u)) && natural.get(u.id) === g.key);
      g.release = taken[0] ?? null;
      const m = g.release?.match;
      const own = g.single ? g.files : g.files.filter((u) => !u.single);
      g.titles = byCount([m ? catAlbum(g.release!) : null, ...own.map((u) => str(u.tags.album))]);
      // the release's main artist, everyone it is credited to as one line («Medoi The Maid, Lieath»), and what the files say
      g.artists = byCount([m?.album_artist ?? (g.single ? m?.artist : null) ?? null, (m?.album_artists?.length ?? 0) > 1 ? m!.album_artists!.join(', ') : null, ...own.map((u) => str(u.tags.album_artist) || u.artists?.[0])]);
      g.years = byCount([m?.year ? String(m.year) : null, ...own.map((u) => str(u.tags.year).slice(0, 4))]);
      g.title = (m ? catAlbum(g.release!) : null) ?? g.titles[0] ?? '';
      g.artist = m?.album_artist ?? (g.single ? m?.artist : null) ?? g.artists[0] ?? '';
      g.year = m?.year ? String(m.year) : (g.years[0] ?? '');
      g.cat = { title: m ? catAlbum(g.release!) : null, artist: m ? (m.album_artist ?? (g.single ? (m.artist ?? null) : null)) : null, year: m?.year ? String(m.year) : null };
      g.agree = {
        title: own.some((u) => same(str(u.tags.album), g.cat.title)),
        artist: own.some((u) => same(str(u.tags.album_artist) || u.artists?.[0], g.cat.artist)),
        year: own.some((u) => str(u.tags.year).slice(0, 4) === g.cat.year),
      };
      const pics = new Map<string, Picture>();
      const withCat = taken.find((u) => u.match?.cover_url);
      if (withCat) pics.set('catalog', { key: 'catalog', url: imageUrl(withCat.match!.cover_url!) as string, from: 'catalog', upload: withCat.id, count: 0 });
      for (const u of g.files) {
        const info = u.tags.cover_info;
        if (!u.tags.has_cover) continue;
        const key = `f:${info?.hash ?? u.id}`;
        const p = pics.get(key);
        if (p) p.count++;
        else pics.set(key, { key, url: uploadCoverUrl(u.id), from: 'file', upload: u.id, count: 1, info });
      }
      g.pictures = [...pics.values()].sort((a, b) => Number(b.from === 'catalog') - Number(a.from === 'catalog') || b.count - a.count);
      g.cover = g.pictures[0]?.key ?? null;
    }
    return { keys, groups };
  }, [uploads, rows]);

  const albumOf = (g: Group) => {
    const c = chosen[g.key] ?? {};
    // the catalog's release is called as the catalog calls it: a name of one's own is for an album taken off it
    if (g.release) return { title: g.title, artist: g.artist, year: g.year, cover: g.pictures.find((p) => p.key === (c.cover ?? g.cover)) ?? null };
    return { title: c.title ?? g.title, artist: c.artist ?? g.artist, year: c.year ?? g.year, cover: g.pictures.find((p) => p.key === (c.cover ?? g.cover)) ?? null };
  };
  const choose = (g: Group, patch: AlbumChoice) => setChosen((prev) => ({ ...prev, [g.key]: { ...prev[g.key], ...patch } }));
  const groupOf = (u: UploadOut): Group | null => {
    const k = all.keys.get(u.id);
    return k ? (all.groups.get(k) ?? null) : null;
  };

  const included = uploads.filter((u) => rowOf(u).include);
  const ready = included.filter((u) => valid(u, rowOf(u)));
  const toFill = included.filter((u) => !valid(u, rowOf(u)));
  const isNone = (u: UploadOut) => !u.match?.ref;
  const changes = (u: UploadOut) => usesCatalog(u, rowOf(u)) && changesOf(u, !groupOf(u)?.single).length > 0;
  const counts = {
    fill: toFill.length,
    doubt: uploads.filter((u) => unsure(u, rowOf(u))).length,
    changes: uploads.filter(changes).length,
    none: uploads.filter(isNone).length,
    twin: uploads.filter((u) => u.twin).length,
  };
  // the search of the page: by what a file is called here — its own names, the catalog's, its album, the file's name
  const needle = sf.q.trim().toLocaleLowerCase('ru');
  const matches = (u: UploadOut) => !needle || [u.title, u.artist, u.orig_name, str(u.tags.album), u.match?.title, u.match?.artist, ...(u.match?.artists ?? []), u.match?.album].filter(Boolean).join(' ').toLocaleLowerCase('ru').includes(needle);
  const passes = (u: UploadOut) =>
    matches(u) && (only === 'fill' ? rowOf(u).include && !valid(u, rowOf(u)) : only === 'doubt' ? unsure(u, rowOf(u)) : only === 'changes' ? changes(u) : only === 'none' ? isNone(u) : only === 'twin' ? !!u.twin : true);
  const shown = uploads.filter(passes);
  // the albums, then the singles — each in the order its files came
  /** A file's place in its album: the catalog's where its record is taken, else the file's own (none: not said). */
  const placeOf = (u: UploadOut): number | null => {
    const r = rowOf(u);
    const disc = toIntOrNull(str(u.tags.disc_no)) ?? 1;
    const no = (usesCatalog(u, r) && u.match?.track_no) || toIntOrNull(str(u.tags.track_no));
    return no ? disc * 1000 + no : null;
  };
  // an album's files stand as its tracks do: by their numbers, the ones without a number after them, as they came
  const inOrder = (files: UploadOut[]) => files.map((u, i) => ({ u, i, at: placeOf(u) })).sort((a, b) => (a.at ?? Infinity) - (b.at ?? Infinity) || a.i - b.i).map((x) => x.u);
  const shownGroups = [...all.groups.values()].map((g) => ({ g, files: inOrder(g.files.filter(passes)) })).filter((x) => x.files.length).sort((a, b) => Number(a.g.single) - Number(b.g.single));

  /** The picture a file is shown with — and filed with: its album's, or a single's own choice. */
  const pictureOf = (u: UploadOut): string | null => {
    const g = groupOf(u);
    if (g) return albumOf(g).cover?.url ?? null;
    const r = rowOf(u);
    const theirs = usesCatalog(u, r) && u.match?.cover_url ? (imageUrl(u.match.cover_url) as string) : null;
    const mine = u.tags.has_cover ? uploadCoverUrl(u.id) : null;
    return r.cover === 'catalog' ? (theirs ?? mine) : (mine ?? theirs);
  };
  /** The files as the tracks of the app's player: a tap on a cover plays from that file on, down the page. */
  const asTrack = (u: UploadOut): ListenTrack => {
    const r = rowOf(u);
    const by = usesCatalog(u, r);
    const g = groupOf(u);
    return {
      ref: `up:${u.id}`,
      title: by ? (same(u.title, u.match?.title) ? u.title : (u.match?.title ?? u.title)) : r.title || u.title,
      artist: by ? (u.match?.artists?.length ? u.match.artists.join(', ') : (u.match?.artist ?? '')) : r.artist,
      cover_url: pictureOf(u),
      cover_small_url: pictureOf(u),
      album: g ? albumOf(g).title : undefined,
      duration_s: u.duration_s ? Math.round(u.duration_s) : null,
    };
  };
  const order = shownGroups.flatMap((x) => x.files);
  const play = (u: UploadOut) => listen(asTrack(u), order.map(asTrack), 'upload');

  /** Put these files into one album: one of the page's, or a new one of this name. */
  const gather = (ids: number[], into: { key: string } | { title: string; artist: string }) => {
    const key = 'key' in into ? into.key : `m|${Date.now()}`;
    if (!('key' in into)) setChosen((prev) => ({ ...prev, [key]: { title: into.title, artist: into.artist } }));
    setRows((prev) => ({ ...prev, ...Object.fromEntries(ids.map((id) => [id, { ...(prev[id] ?? rowFromUpload(uploads.find((u) => u.id === id)!)), group: key }])) }));
    setToAlbum(null);
    setPicked(new Set());
  };
  /** An album's files off the catalog's records (it is an album of one's own then, written by hand) — or back on them. */
  const linkAlbum = (g: Group, on: boolean) => {
    const list = g.files.filter((u) => !!u.match?.ref && usesCatalog(u, rowOf(u)) !== on);
    setRows((prev) => ({ ...prev, ...Object.fromEntries(list.map((u) => [u.id, { ...(prev[u.id] ?? rowFromUpload(u)), catalog: on, cover: on && u.match?.cover_url ? 'catalog' as const : 'file' as const }])) }));
  };
  const chosenFiles = () => uploads.filter((u) => picked.has(u.id));
  /** The picked files as the files say, or — when none of them is the catalog's just now — as the catalog says. */
  const flipCatalog = () => {
    const list = chosenFiles().filter((u) => !!u.match?.ref);
    const off = list.some((u) => usesCatalog(u, rowOf(u)));
    setRows((prev) => ({ ...prev, ...Object.fromEntries(list.map((u) => [u.id, { ...(prev[u.id] ?? rowFromUpload(u)), catalog: !off, cover: !off && u.match?.cover_url ? 'catalog' as const : 'file' as const }])) }));
    toast(off ? `Как в файле: ${tracksWord(list.length)}` : `Данные каталога: ${tracksWord(list.length)}`, 'success');
  };
  const rematchPicked = async () => {
    const list = chosenFiles();
    toast(`Сверяю заново: ${tracksWord(list.length)}…`, 'info');
    for (const u of list) await rematch(u);
    toast('Сверка закончена', 'success');
  };
  /** A click on a row while rows are being picked, or with Ctrl / Shift: it picks, as in a playlist. */
  const rowClick = (u: UploadOut, e: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) => {
    if (e.shiftKey && anchor.current !== null) {
      const ids = order.map((x) => x.id);
      const a = ids.indexOf(anchor.current);
      const b = ids.indexOf(u.id);
      if (a >= 0 && b >= 0) setPicked((prev) => new Set([...prev, ...ids.slice(Math.min(a, b), Math.max(a, b) + 1)]));
      return true;
    }
    if (e.ctrlKey || e.metaKey || picked.size > 0) {
      anchor.current = u.id;
      togglePick(u.id);
      return true;
    }
    return false;
  };
  const togglePick = (id: number) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  /** The rows from the one the drag began at to this one, as they stand on the page. */
  const dragOver = (id: number) => {
    const d = drag.current;
    if (!d || (id === d.from && !d.moved)) return;
    d.moved = true;
    const ids = order.map((u) => u.id);
    const a = ids.indexOf(d.from);
    const b = ids.indexOf(id);
    if (a < 0 || b < 0) return;
    setPicked((prev) => new Set([...prev, ...ids.slice(Math.min(a, b), Math.max(a, b) + 1)]));
  };
  // the page's own keys: Ctrl+A picks every file shown, Esc drops the pick; a drag ends wherever the button is let go
  const shownIds = order.map((u) => u.id).join(',');
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement | null)?.closest('input, textarea, select, [contenteditable="true"]') || document.querySelector('[role="dialog"]');
      if (typing) return;
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.code === 'KeyA' && shownIds) {
        e.preventDefault();
        setPicked(new Set(shownIds.split(',').map(Number)));
      } else if (e.key === 'Escape') setPicked(new Set());
    };
    const up = () => window.setTimeout(() => (drag.current = null), 0); // after the click that ends the drag
    window.addEventListener('keydown', key);
    window.addEventListener('pointerup', up);
    return () => {
      window.removeEventListener('keydown', key);
      window.removeEventListener('pointerup', up);
    };
  }, [shownIds]);

  const uploadingNow = transfers.some((t) => !t.error);
  const canCommit = ready.length > 0 && !uploadingNow && !moving;

  const trackIn = (u: UploadOut): UploadTrackIn => {
    const r = rowOf(u);
    return {
      upload_id: u.id, catalog: usesCatalog(u, r), also_title: r.also && usesCatalog(u, r) && !same(u.title, u.match?.title) ? u.title : null, version_of_match: isVersion(u, r), no_check: !!r.skip && !usesCatalog(u, r), title: r.title.trim(), artist: r.artist.trim(), album: r.album.trim() || null, year: toIntOrNull(r.year),
      album_key: all.keys.get(u.id) ?? null, cover: r.cover, replace_track_id: r.replace && u.twin ? u.twin.track_id : null,
    };
  };
  /** What a file is called once it is in: the catalog's name where its record is taken (in the file's own case). */
  const titleOf = (u: UploadOut) => {
    const r = rowOf(u);
    return (usesCatalog(u, r) ? (same(u.title, u.match?.title) ? u.title : (u.match?.title ?? u.title)) : r.title.trim() || u.title) || u.orig_name;
  };
  const albumIn = (g: Group): UploadAlbumIn => {
    const a = albumOf(g);
    return {
      key: g.key, single: g.single, title: g.single ? titleOf(g.files[0]) : a.title.trim() || g.title, album_artist: a.artist.trim() || g.artist || 'Various Artists', year: toIntOrNull(a.year), release_of: g.release?.id ?? null,
      cover_of_match: a.cover?.from === 'catalog' ? a.cover.upload : null, cover_of_file: a.cover?.from === 'file' ? a.cover.upload : null,
    };
  };

  /** The files go in by parts, so that the page says how far it has got; what went in leaves the list. The ones
   * that lack a name stay on the page. Then the second step: the weak files are looked for anew there, and the
   * records that were only like their files are settled by the sound. */
  const commit = async () => {
    const list = [...order.filter((u) => ready.includes(u)), ...ready.filter((u) => !order.includes(u))]; // an album's files together
    const albums = new Map([...all.groups.values()].map((g) => [g.key, albumIn(g)]));
    let imported = 0;
    let weak = 0;
    let doubts = 0;
    const tracks: number[] = [];
    setMoving({ done: 0, of: list.length });
    for (let i = 0; i < list.length; i += CHUNK) {
      const part = list.slice(i, i + CHUNK).map(trackIn);
      const res = await attempt(() =>
        api.commitUploads({ tracks: part, albums: [...new Set(part.map((t) => t.album_key).filter((k): k is string => !!k))].map((k) => albums.get(k)!), playlist_ids: playlists.map((p) => p.id), star: like, tag_ids: tagIds, keep_in: keep ? keepName : null }));
      if (!res) break;
      imported += res.imported;
      weak += res.weak;
      doubts += res.unsure;
      tracks.push(...res.track_ids);
      const done = new Set(part.map((t) => t.upload_id));
      setUploads((prev) => prev.filter((u) => !done.has(u.id)));
      setMoving({ done: Math.min(i + CHUNK, list.length), of: list.length });
    }
    setMoving(null);
    if (!imported) return;
    const whole = imported === list.length && !toFill.length;
    if (weak || doubts) {
      if (weak) await attempt(() => api.qualityStart(tracks));
      toast(`Перенесено: ${tracksWord(imported)}. Дальше — качество звука: ${[weak ? `слабых файлов ${weak}` : null, doubts ? `уточнить по звуку ${doubts}` : null].filter(Boolean).join(', ')}`, 'success');
      if (whole) navigate('/quality', { replace: true });
      return;
    }
    toast(`Перенесено: ${tracksWord(imported)}`, 'success');
    if (whole) navigate('/files', { replace: true });
  };

  const totalSize = uploads.reduce((n, u) => n + u.size, 0);
  const chips: [Only, string, number, boolean?][] = [
    ['all', 'Все', uploads.length],
    ['fill', 'Нужно заполнить', counts.fill, true],
    ['changes', 'Изменится', counts.changes],
    ['doubt', 'Уточню на следующем шаге', counts.doubt],
    ['none', 'Не найдено', counts.none],
    ['twin', 'Уже есть', counts.twin],
  ];

  const importButton = uploads.length > 0 && (
    <span className="flex min-w-0 items-center gap-1.5">
      {/* the page's own search, in the browser's find's place (Ctrl+F): that one sees only the rows drawn */}
      {sf.open ? <span className="flex w-40 min-w-0 md:w-80">{sf.field(needle ? shown.length : null)}</span> : <IconButton icon="search" label="Поиск по файлам (Ctrl+F)" onClick={sf.show} />}
      <Button variant="primary" small icon="check" busy={!!moving} disabled={!canCommit} onClick={commit}>
        {moving ? `Переношу ${moving.done} из ${moving.of}` : `Перенести ${tracksWord(ready.length)}`}
      </Button>
    </span>
  );

  const pick = (label: string, ref: React.RefObject<HTMLInputElement | null>) => (
    <Button small onClick={() => ref.current?.click()}>
      {label}
    </Button>
  );

  const row = (u: UploadOut, g: Group | null, at: number) => (
    <FileRow
      key={u.id}
      u={u}
      r={rowOf(u)}
      desktop={desktop}
      cols={cols}
      inAlbum={!!g && !g.single}
      number={g && !g.single ? ((usesCatalog(u, rowOf(u)) && u.match?.track_no) || toIntOrNull(str(u.tags.track_no))) : at + 1}
      picture={pictureOf(u)}
      picked={picked.has(u.id)}
      menu={menu === u.id}
      onMenu={(on) => setMenu(on ? u.id : null)}
      onPick={() => {
        anchor.current = u.id;
        togglePick(u.id);
      }}
      onRowClick={(e) => rowClick(u, e)}
      onDragStart={() => (drag.current = { from: u.id, moved: false })}
      onDragOver={() => dragOver(u.id)}
      dragged={() => !!drag.current?.moved}
      onPlay={() => play(u)}
      onChange={(patch) => setRow(u.id, patch)}
      onEdit={() => {
        const r = rowOf(u);
        // the names are written by hand from what the row shows now: the catalog's, where its record was taken
        if (usesCatalog(u, r)) setRow(u.id, { catalog: false, cover: 'file', title: same(u.title, u.match?.title) ? u.title : (u.match?.title ?? r.title), artist: (u.match?.artists?.length ? u.match.artists : [u.match?.artist ?? r.artist]).join(', ') });
        setEditing(u.id);
      }}
      onAlbum={() => setToAlbum([u.id])}
      onFind={() => setFinding(u)}
      onAgain={() => void rematch(u)}
      onRemove={() => void removeUpload(u)}
    />
  );

  return (
    <div className="pb-10">
      <PageHeader title="Загрузить файлы" back="/files" sub={uploads.length ? `${uploads.length} ${plural(uploads.length, ['файл', 'файла', 'файлов'])} · ${fmtSize(totalSize)}` : undefined} right={importButton || undefined} />

      {dragging && (
        <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" aria-hidden="true">
          <div className="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-accent bg-surface px-10 py-8 text-accent">
            <Icon name="upload" size={32} />
            <span className="font-semibold">Отпустите, чтобы загрузить</span>
          </div>
        </div>
      )}

      {uploads.length > 0 && <Steps at={1} />}

      <div className="px-4 lg:px-6">
        {[fileRef, folderRef].map((ref, i) => (
          <input
            key={i}
            ref={ref}
            type="file"
            multiple
            accept={i === 0 ? AUDIO_ACCEPT : undefined}
            className="hidden"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
              e.target.value = '';
              if (files.length) void startUpload(files);
            }}
          />
        ))}
        <div className={cx('flex items-center gap-3 rounded-2xl border-2 border-dashed border-line bg-surface', uploads.length ? 'px-4 py-3' : 'flex-col px-4 py-8 text-center')}>
          <span className={cx('flex shrink-0 items-center justify-center rounded-full bg-accent text-on-accent', uploads.length ? 'h-9 w-9' : 'h-12 w-12')}>
            <Icon name="upload" size={uploads.length ? 18 : 24} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block font-semibold">{uploads.length ? 'Добавить ещё' : 'Перетащите сюда аудиофайлы или папку'}</span>
            <span className="block text-xs text-muted">{uploads.length ? 'Перетащите файлы или папку в любое место страницы' : 'MP3, M4A, FLAC, OGG/Opus, WAV · до 500 МБ на файл · папка читается вместе с подпапками'}</span>
          </span>
          <span className="flex shrink-0 flex-wrap justify-center gap-2">
            {pick('Выбрать файлы', fileRef)}
            {desktop && pick('Выбрать папку', folderRef)}
          </span>
        </div>
      </div>

      {transfers.length > 0 && (
        <div className="mx-4 mt-3 rounded-2xl bg-surface p-3 lg:mx-6" aria-label="Передача файлов">
          {(() => {
            const going = transfers.find((t) => !t.error);
            const failed = transfers.filter((t) => t.error);
            return (
              <>
                {going && (
                  <>
                    <div className="flex items-center gap-2 text-sm">
                      <span className="min-w-0 flex-1 truncate">{going.file.name}</span>
                      <span className="shrink-0 text-xs tabular-nums text-muted">осталось {transfers.length - failed.length}</span>
                    </div>
                    <ProgressBar value={going.progress} className="mt-2" />
                    <p className="pt-1.5 text-xs text-dim">Каждый файл сверяется с каталогом — это секунда-другая на файл. Вкладку не закрывайте.</p>
                  </>
                )}
                {failed.map((t) => (
                  <div key={t.key} className="flex items-center gap-2 pt-1 text-sm">
                    <span className="min-w-0 flex-1 truncate">
                      {t.file.name} <span className="text-xs text-danger">— {t.error}</span>
                    </span>
                    <IconButton icon="x" size={16} label="Убрать" onClick={() => setTransfers((prev) => prev.filter((x) => x.key !== t.key))} />
                  </div>
                ))}
              </>
            );
          })()}
        </div>
      )}

      {uploads.length > 0 && (
        <AfterImport
          className="mx-4 mt-3 lg:mx-6"
          count={ready.length}
          like={like}
          onLike={setLike}
          playlists={playlists}
          onPlaylists={setPlaylists}
          onPickPlaylists={() => setPlPicker(true)}
          tagIds={tagIds}
          onTags={setTagIds}
          keepName={keepName}
          keep={keep}
          onKeep={turnKeep}
        />
      )}

      {pendingRes.loading && !uploads.length ? (
        <Loading text="Проверяю незавершённые загрузки…" />
      ) : pendingRes.error && !uploads.length ? (
        <ErrorState text={pendingRes.error} onRetry={() => pendingRes.reload()} />
      ) : uploads.length === 0 ? null : (
        <>
          <div className="flex flex-wrap items-center gap-2 px-4 pt-4 lg:px-6">
            <div className="flex min-w-0 flex-1 gap-1.5 overflow-x-auto" role="group" aria-label="Какие файлы показывать" style={{ scrollbarWidth: 'none' }}>
              {chips.filter(([id, , n]) => id === 'all' || id === only || n > 0).map(([id, label, n, urgent]) => (
                <button key={id} type="button" aria-pressed={only === id} onClick={() => setOnly(only === id ? 'all' : id)} className={cx('shrink-0 rounded-full px-3 py-1.5 text-sm transition', only === id ? (urgent ? 'bg-danger font-semibold text-white' : 'bg-fg font-semibold text-bg') : urgent ? 'bg-danger/15 text-danger' : 'bg-fg/10 text-muted hover:text-fg')}>
                  {label}
                  <span className={cx('pl-1.5 tabular-nums', only === id || urgent ? 'opacity-80' : 'text-dim')}>{n}</span>
                </button>
              ))}
            </div>
          </div>
          {toFill.length > 0 && (
            <p className="px-4 pt-2 text-xs text-muted lg:px-6">
              {tracksWord(toFill.length)} без названия или исполнителя не {plural(toFill.length, ['перенесётся', 'перенесутся', 'перенесутся'])}, пока вы {plural(toFill.length, ['его', 'их', 'их'])} не заполните — метка «Нужно заполнить». Остальные {ready.length} можно переносить.
            </p>
          )}
          {only === 'doubt' && <p className="px-4 pt-2 text-xs text-muted lg:px-6">В каталоге есть похожая запись, но не точно эта: другая длина или версия. Такие треки переносятся со своими тегами, а на следующем шаге запись каталога скачивается и сравнивается с вашим файлом по звуку.</p>}

          {desktop && shown.length > 0 && <TableHead cols={cols} onCols={setCols} />}
          {shown.length === 0 && <div className="mx-4 mt-3 rounded-2xl bg-surface lg:mx-6"><Empty icon="search" title={needle ? 'Ничего не найдено' : 'Под эту метку ничего не подходит'} /></div>}
          {shownGroups.map(({ g, files }) => (
            <section key={g.key} className="mx-4 mt-3 rounded-2xl bg-surface lg:mx-6" aria-label={g.single ? `Сингл «${titleOf(g.files[0])}»` : `Альбом «${albumOf(g).title}»`}>
              <AlbumHead g={g} now={albumOf(g)} name={g.single ? titleOf(g.files[0]) : albumOf(g).title} versionTrack={g.single && usesCatalog(g.files[0], rowOf(g.files[0])) ? g.files[0].match : null} onChoose={(patch) => choose(g, patch)} shown={files.length} canLink={g.files.some((u) => !!u.match?.ref && !usesCatalog(u, rowOf(u)))} onUnlink={() => linkAlbum(g, false)} onLink={() => linkAlbum(g, true)} />
              {files.map((u, i) => row(u, g, i))}
            </section>
          ))}
        </>
      )}

      {picked.size > 0 && (
        <div className="pointer-events-none fixed bottom-[calc(var(--tabbar-h)+var(--player-h,0px)+12px+env(safe-area-inset-bottom))] left-(--side-w) right-0 z-40 flex justify-center px-3" role="region" aria-label="Выбранные файлы">
          <div className="anim-up pointer-events-auto flex max-w-full items-center gap-1 rounded-2xl border border-line bg-raised py-1.5 pl-4 pr-1.5 shadow-2xl">
            <span className="mr-2 whitespace-nowrap text-sm font-semibold">{picked.size} {plural(picked.size, ['файл', 'файла', 'файлов'])}</span>
            <IconButton icon="folder" size={20} label="В один альбом…" onClick={() => setToAlbum([...picked])} className="!h-10 !w-10 text-muted hover:text-fg" />
            <IconButton icon="note" size={20} label={chosenFiles().some((u) => usesCatalog(u, rowOf(u))) ? 'Оставить как в файле' : 'Взять данные каталога'} onClick={flipCatalog} className="!h-10 !w-10 text-muted hover:text-fg" />
            <IconButton icon="refresh" size={20} label="Сверить с каталогом заново" onClick={() => void rematchPicked()} className="!h-10 !w-10 text-muted hover:text-fg" />
            <IconButton icon="trash" size={20} label="Убрать из списка" onClick={() => void removePicked()} className="!h-10 !w-10 text-muted hover:text-danger" />
            <span className="mx-1 h-6 w-px bg-line" aria-hidden="true" />
            <IconButton icon="x" size={18} label="Снять выделение" onClick={() => setPicked(new Set())} className="!h-10 !w-10 text-muted hover:text-fg" />
          </div>
        </div>
      )}
      {editing !== null && uploads.some((u) => u.id === editing) && (
        <EditNames u={uploads.find((u) => u.id === editing)!} r={rowOf(uploads.find((u) => u.id === editing)!)} onChange={(patch) => setRow(editing, patch)} onClose={() => setEditing(null)} />
      )}
      {toAlbum && (
        <AlbumPick
          count={toAlbum.length}
          albums={[...all.groups.values()].filter((g) => !g.single).map((g) => ({ key: g.key, title: albumOf(g).title, artist: albumOf(g).artist, files: g.files.length }))}
          onClose={() => setToAlbum(null)}
          onPick={(into) => gather(toAlbum, into)}
        />
      )}
      {plPicker && (
        <PlaylistsPick
          title={`${tracksWord(ready.length)} — в плейлисты`}
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
      {finding && (
        <FindRecord
          u={finding}
          onClose={() => setFinding(null)}
          onPick={(ref) => {
            const u = finding;
            setFinding(null);
            void rematch(u, ref);
          }}
        />
      )}
    </div>
  );
}

/** The two steps of bringing music in, over both of their pages: where one is. */
export function Steps({ at }: { at: 1 | 2 }) {
  const step = (n: 1 | 2, label: string) => (
    <span className={cx('flex items-center gap-2', at === n ? 'font-semibold text-fg' : 'text-muted')}>
      <span className={cx('flex h-6 w-6 items-center justify-center rounded-full text-xs', at === n ? 'bg-accent text-on-accent' : 'bg-fg/10')}>{at > n ? <Icon name="check" size={13} /> : n}</span>
      {label}
    </span>
  );
  return (
    <div className="flex flex-wrap items-center gap-3 px-4 pb-3 text-sm lg:px-6" aria-label="Шаги">
      {step(1, 'Данные и обложки')}
      <span className="h-px w-8 bg-line" aria-hidden="true" />
      {step(2, 'Качество звука')}
    </div>
  );
}

/** The head of an album, as an album's page has it: the cover, over the name the word «Альбом» or «Сингл», under
 * it whose it is and the year.
 *
 * An album the catalog knows is the catalog's release: its name, artist and year are the catalog's and are not
 * rewritten here — the broken chain by the name takes the files off the release, and then it is an album of one's
 * own, with the three written by hand (the files' words to start with). The cover is chosen either way: one is
 * shown, with how many there are; a tap lays the others out to the right. */
function AlbumHead({ g, now, name, versionTrack, onChoose, shown, canLink, onUnlink, onLink }: { g: Group; now: { title: string; artist: string; year: string; cover: Picture | null }; /** what the head is called: the album, or a single's track */ name: string; versionTrack?: VersionIdentity | null; onChoose: (patch: AlbumChoice) => void; shown: number; /** the catalog has a record for a file that is not filed by it */ canLink: boolean; onUnlink: () => void; onLink: () => void }) {
  const [open, setOpen] = useState(false); // the other covers are laid out
  const linked = !!g.release;
  const kind = g.single ? 'Сингл' : 'Альбом';
  const input = (field: 'title' | 'artist' | 'year', label: string, value: string, variants: string[], className: string) => (
    <>
      <input
        key={`${field}:${value}`}
        className={cx('input !h-9 min-w-0 !py-1', className)}
        list={`${g.key}:${field}`}
        inputMode={field === 'year' ? 'numeric' : undefined}
        placeholder={label}
        aria-label={label}
        defaultValue={value}
        onBlur={(e) => {
          const v = field === 'year' ? e.target.value.replace(/\D/g, '').slice(0, 4) : e.target.value.trim();
          if (v !== value) onChoose({ [field]: v && v !== g[field] ? v : undefined });
        }}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />
      {/* what the files say otherwise: offered as the field is typed in */}
      <datalist id={`${g.key}:${field}`}>{variants.filter((v) => v !== value).map((v) => <option key={v} value={v} />)}</datalist>
    </>
  );
  const many = g.pictures.length > 1;
  return (
    <div className="flex flex-wrap items-start gap-x-4 gap-y-2 rounded-t-2xl border-b border-line/60 bg-fg/[0.04] px-4 py-3">
      {g.pictures.length > 0 && (
        <div className="flex items-start gap-2">
          {open && many ? (
            <>
              {g.pictures.map((p) => (
                <PictureOption key={p.key} p={p} on={now.cover?.key === p.key} onClick={() => { onChoose({ cover: p.key }); setOpen(false); }} />
              ))}
              <button type="button" aria-label="Свернуть обложки" title="Свернуть" onClick={() => setOpen(false)} className="mt-9 flex h-7 w-7 items-center justify-center rounded-full text-dim hover:bg-fg/10 hover:text-fg">
                <Icon name="chevron" size={14} className="rotate-180" />
              </button>
            </>
          ) : (
            now.cover && <PictureOption p={now.cover} on={false} badge={many ? `${g.pictures.indexOf(now.cover) + 1} из ${g.pictures.length}` : undefined} onClick={many ? () => setOpen(true) : undefined} />
          )}
        </div>
      )}
      <div className="min-w-0 flex-1 basis-64">
        {/* an album the catalog does not stand behind is made here: said so */}
        <div className="text-xs text-muted">{linked ? kind : g.single ? 'Новый сингл' : 'Новый альбом'}</div>
        {linked || g.single ? (
          <div className="flex min-w-0 items-center gap-1">
            <h3 className="min-w-0 truncate text-xl font-bold"><TrackTitle title={name || 'без названия'} track={versionTrack} /></h3>
            {/* the release the catalog took it for: its page, in a tab of its own — the choices made here stay */}
            {linked && g.release?.match?.album_ref && (
              <a href={`#/album/${encodeURIComponent(g.release.match.album_ref)}`} target="_blank" rel="noreferrer" aria-label={`${kind} в каталоге`} title={`${kind} в каталоге`} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-info hover:bg-fg/10">
                <Icon name="external" size={16} />
              </a>
            )}
            {linked && (
              <button type="button" aria-label="Открепить от каталога" title="Открепить от каталога: это другая запись — название, исполнителя и год впишу сам" onClick={onUnlink} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-dim hover:bg-fg/10 hover:text-fg">
                <Icon name="unlink" size={16} />
              </button>
            )}
          </div>
        ) : (
          <div className="pt-0.5">{input('title', 'Название альбома', now.title, g.titles, 'w-full max-w-md text-[15px] font-semibold')}</div>
        )}
        {linked ? (
          <p className="truncate pt-0.5 text-sm text-muted">{[now.artist || 'исполнитель не указан', now.year].filter(Boolean).join(' · ')}</p>
        ) : (
          <div className="flex max-w-md gap-2 pt-1.5">
            {input('artist', 'Исполнитель альбома', now.artist, g.artists, 'flex-1 text-sm')}
            {input('year', 'Год', now.year, g.years, 'w-20 text-sm')}
          </div>
        )}
        <p className="flex flex-wrap items-center gap-x-1.5 pt-1.5 text-[11px] text-dim">
          {linked ? (
            <><Icon name="link" size={12} /> данные каталога</>
          ) : canLink ? (
            <>
              <Icon name="unlink" size={12} /> не из каталога ·
              <button type="button" className="text-info hover:underline" onClick={onLink}>привязать обратно</button>
            </>
          ) : (
            'в каталоге не найден'
          )}
          {shown < g.files.length ? ` · показано ${shown} из ${g.files.length}` : ''}
        </p>
      </div>
    </div>
  );
}

/** A picture an album may take, with what it is under it: its size in pixels, its kind, its weight. `badge`: the
 * one shown of several — which of how many; a tap lays the others out. */
function PictureOption({ p, on, badge, onClick }: { p: Picture; on: boolean; badge?: string; onClick?: () => void }) {
  // the catalog's picture is fetched to be measured only once its head is on the screen: a page has hundreds
  const box = useRef<HTMLButtonElement>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (seen || p.from !== 'catalog' || !box.current) return;
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && setSeen(true), { rootMargin: '200px' });
    io.observe(box.current);
    return () => io.disconnect();
  }, [seen, p.from]);
  const fetched = useCatalogCover(p.from === 'catalog' && seen ? p.upload : null);
  const [px, rest] = pictureWords(p.from === 'catalog' ? fetched : p.info);
  const whence = p.from === 'catalog' ? 'из каталога' : `из ${p.count} ${plural(p.count, ['файла', 'файлов', 'файлов'])}`;
  return (
    <button ref={box} type="button" aria-pressed={badge ? undefined : on} disabled={!onClick} onClick={onClick} title={badge ? 'Показать другие обложки' : `Обложка ${whence}`} className="flex w-24 flex-col items-center gap-1 text-center">
      <span className={cx('relative rounded-lg transition', on ? 'ring-2 ring-accent' : !badge && onClick && 'opacity-60 hover:opacity-100')}>
        <Cover src={p.url} size={96} alt="" />
        {badge && (
          <span className="absolute bottom-1 right-1 flex items-center gap-0.5 rounded-full bg-black/70 px-1.5 py-0.5 text-[11px] text-white">
            {badge}
            <Icon name="chevron" size={10} />
          </span>
        )}
      </span>
      <span className="text-[11px] leading-tight text-dim">
        {px}
        {rest ? ` · ${rest.replace('JPEG · ', '')}` : ''}
        <br />
        {whence}
      </span>
    </button>
  );
}

/** The head of the table (a computer): the names of the columns, and — its ⋯ — which of the file's own are shown. */
function TableHead({ cols, onCols }: { cols: Columns; onCols: (next: Columns) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', close);
    };
  }, [open]);
  return (
    <div ref={ref} className="relative mx-4 mt-3 grid h-8 items-center gap-2.5 pr-1 text-xs text-dim lg:mx-6" style={grid(cols)}>
      <span className="text-center">#</span>
      <span />
      <span>Название</span>
      {cols.file && <span>Файл</span>}
      {cols.size && <span>Размер</span>}
      {cols.quality && <span>Качество</span>}
      <span className="text-right">Время</span>
      <IconButton icon="more" size={16} label="Колонки таблицы" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="!h-8 !w-8 text-dim hover:text-fg" />
      {open && (
        <div role="menu" aria-label="Колонки таблицы" className="anim-up absolute right-0 top-full z-30 w-52 rounded-2xl bg-raised p-1.5 text-sm text-fg shadow-2xl">
          {COLUMNS.map(([id, label]) => (
            <button key={id} type="button" role="menuitemcheckbox" aria-checked={cols[id]} onClick={() => onCols({ ...cols, [id]: !cols[id] })} className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left hover:bg-fg/10">
              <span className="flex w-4 justify-center">{cols[id] && <Icon name="check" size={15} />}</span>
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** A file as a row of a playlist: its number (a click on it picks the row, a drag from it the rows passed), the
 * cover that plays it in the app's player, its name with everyone credited, what the catalog changes in it, what
 * the file itself is, its length, and the ⋯ of its menu. On a phone the file's own is said in the menu. */
function FileRow({ u, r, desktop, cols, inAlbum, number, picture, picked, menu, onMenu, onPick, onRowClick, onDragStart, onDragOver, dragged, onPlay, onChange, onEdit, onAlbum, onFind, onAgain, onRemove }: { u: UploadOut; r: RowState; desktop: boolean; cols: Columns; inAlbum: boolean; number: number | null | false | 0 | undefined; picture: string | null; picked: boolean; menu: boolean; onMenu: (on: boolean) => void; onPick: () => void; /** true: the click was a pick, not the row's own */ onRowClick: (e: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) => boolean; onDragStart: () => void; onDragOver: () => void; dragged: () => boolean; onPlay: () => void; onChange: (patch: Partial<RowState>) => void; onEdit: () => void; onAlbum: () => void; onFind: () => void; onAgain: () => void; onRemove: () => void }) {
  const m = u.match;
  const has = !!m?.ref;
  const by = usesCatalog(u, r);
  const lacks = !by && (!r.title.trim() || !r.artist.trim());
  const phase = useListen((s) => (s.track?.ref === `up:${u.id}` ? s.phase : null));
  const playing = phase === 'playing' || phase === 'resolving' || phase === 'loading';
  const title = by ? (same(u.title, m?.title) ? u.title : m?.title) : r.title;
  const artists = by ? (m?.artists?.length ? m.artists : [m?.artist ?? '']).join(' · ') : r.artist;
  // what the catalog says is in a pill, what the file said in its place is struck out beside it
  const pill = (text: ReactNode, key?: string | number) => <span key={key} className="rounded-full bg-info/20 px-1.5 text-info" title="Из каталога">{text}</span>;
  const was = (text: ReactNode) => <span className="text-dim line-through" title="Было в файле">{text}</span>;
  // what stands with the row — said by a mark of its own beside the name, which the name is cut before: a sign and
  // one word (the sign alone where the row is narrow). A tap lays out under the row what it means, and what to do
  const [tip, setTip] = useState(false);
  const mark: { icon: 'disc' | 'help'; word: string; tone: string } | null = isVersion(u, r)
    ? { icon: 'disc', word: 'версия', tone: 'bg-info/20 text-info' }
    : unsure(u, r)
      ? { icon: 'help', word: 'уточню', tone: 'bg-warn/15 text-warn' }
      : null;
  const status = mark && (
    <button
      type="button"
      data-own
      aria-expanded={tip}
      title={mark.word === 'версия' ? 'Другая версия песни из каталога — нажмите, чтобы узнать больше' : 'В каталоге есть похожая запись — нажмите, чтобы узнать больше'}
      onClick={(e) => {
        e.stopPropagation();
        setTip((v) => !v);
      }}
      className={cx('flex h-5 shrink-0 items-center gap-1 rounded-full px-1 text-[11px] @[15rem]:px-2', mark.tone, tip && 'ring-1 ring-current')}
    >
      <Icon name={mark.icon} size={13} />
      <span className="hidden @[15rem]:inline">{mark.word}</span>
    </button>
  );
  const recordDetails = [m?.artist, m?.album, m?.duration_s ? fmtDuration(m.duration_s) : null].filter(Boolean).join(' · ');
  const record = <>{m?.title && <>«<TrackTitle title={m.title} track={m} />»</>}{m?.title && recordDetails && ' · '}{recordDetails}</>;
  const say = (patch: Partial<RowState>) => {
    setTip(false);
    onChange(patch);
  };
  const act = 'text-info hover:underline';
  const tipBox = tip && mark && (
    <div className="pb-2 pl-12 pr-4 md:pl-[5.75rem]" data-own>
      <div className="max-w-xl rounded-xl bg-fg/[0.06] px-3 py-2 text-xs text-muted">
        {mark.word === 'версия' ? (
          <>
            <p className="font-semibold text-fg">Другая версия песни из каталога</p>
            <p>{record}</p>
            <p>Ваш файл ляжет отдельным треком под своими данными и будет связан с ней как версия.</p>
            <p className="flex flex-wrap gap-x-3 pt-1">
              <button type="button" className={act} onClick={() => say({ version: false })}>Не версия</button>
              <button type="button" className={act} onClick={() => say({ catalog: true, cover: m?.cover_url ? 'catalog' : 'file', version: false })}>Это она — взять данные каталога</button>
            </p>
          </>
        ) : (
          <>
            <p className="font-semibold text-fg">В каталоге есть похожая запись</p>
            <p>{record}{u.duration_s ? `, а ваш файл — ${fmtDuration(u.duration_s)}` : ''}</p>
            {m?.why && <p>{m.why}</p>}
            <p>После импорта хаб сравнит их по звуку и спросит в «Качестве звука».</p>
            <p className="flex flex-wrap gap-x-3 pt-1">
              <button type="button" className={act} onClick={() => say({ catalog: true, cover: m?.cover_url ? 'catalog' : 'file' })}>Это она — взять данные каталога</button>
              <button type="button" className={act} onClick={() => say({ catalog: false, cover: 'file', version: true })}>Другая версия этой песни</button>
              <button type="button" className={act} onClick={() => say({ skip: true })}>Не сравнивать</button>
            </p>
          </>
        )}
      </div>
    </div>
  );
  const theirs = (m?.artists?.length ? m.artists : [m?.artist ?? '']).filter(Boolean);
  const mine = (u.artists ?? []).map(low);
  const kept = mine.length > 0 && mine.every((a) => theirs.map(low).includes(a)); // the catalog only adds to the file's artists
  const sameArtists = mine.join('|') === theirs.map(low).join('|');
  const fileNo = toIntOrNull(str(u.tags.track_no));
  const catNo = by && inAlbum && m?.track_no ? m.track_no : null;
  const quality = fileWords(u.codec, u.bitrate_kbps);
  const cover = (
    <button type="button" data-own aria-label={playing ? 'Пауза' : `Слушать «${title}»`} onClick={onPlay} className="group/c relative flex h-10 w-10 shrink-0 items-center justify-center">
      <Cover src={picture} size={40} alt="" />
      <span aria-hidden="true" className={cx('absolute inset-0 flex items-center justify-center rounded-md bg-black/60 text-white', !phase && 'opacity-0 group-hover/c:opacity-100')}>
        {phase === 'resolving' || phase === 'loading' ? <Spinner size={16} /> : <Icon name={playing ? 'pause' : 'play'} size={16} filled />}
      </span>
    </button>
  );
  const names = lacks ? (
    <span className="flex min-w-0 flex-col gap-1.5 py-0.5 md:flex-row" data-own>
      <input className={cx('input !h-8 !py-1 text-sm', !r.title.trim() && '!border-danger')} placeholder="Название" aria-label="Название" value={r.title} onChange={(e) => onChange({ title: e.target.value })} />
      <input className={cx('input !h-8 !py-1 text-sm', !r.artist.trim() && '!border-danger')} placeholder="Исполнитель" aria-label="Исполнитель" value={r.artist} onChange={(e) => onChange({ artist: e.target.value })} />
    </span>
  ) : (
    <span className="@container min-w-0">
      <span className="flex min-w-0 items-center gap-1.5">
        <span className={cx('min-w-0 truncate text-[15px]', phase && 'text-accent')}>
          {by && !same(u.title, m?.title) ? (
            <>
              {/* the name the file had: a tap keeps it as a second name of the song — the search finds it by that too */}
              <button
                type="button"
                data-own
                aria-pressed={!!r.also}
                title={r.also ? 'Останется вторым названием песни — нажмите, чтобы не сохранять' : 'Было в файле. Нажмите, чтобы оставить вторым названием песни'}
                onClick={(e) => {
                  e.stopPropagation();
                  onChange({ also: !r.also });
                }}
                className={cx('transition hover:text-fg', r.also ? 'rounded-full bg-fg/10 px-1.5 text-muted' : 'text-dim line-through')}
              >
                {r.also && <span className="text-dim">также: </span>}
                {u.title}
              </button>{' '}
              {pill(<TrackTitle title={m?.title} track={m} />)}
            </>
          ) : (
            <TrackTitle title={title} track={by ? m : undefined} />
          )}
        </span>
        {status}
      </span>
      <span className="block truncate text-[13px] text-muted">
        {!by || sameArtists ? artists : kept ? [...theirs].sort((a, b) => Number(!mine.includes(low(a))) - Number(!mine.includes(low(b))) || mine.indexOf(low(a)) - mine.indexOf(low(b))).map((a, i) => <span key={a}>{i > 0 && ' '}{mine.includes(low(a)) ? a : pill(a)}</span>) : <>{was(u.artist)} {theirs.map((a, i) => <span key={a}>{i > 0 && ' '}{pill(a)}</span>)}</>}
      </span>
    </span>
  );
  const item = (icon: Parameters<typeof Icon>[0]['name'], label: string, run: () => void, danger = false) => (
    <button key={label} type="button" role="menuitem" onClick={() => { onMenu(false); run(); }} className={cx('flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm hover:bg-fg/10', danger && 'text-danger')}>
      <Icon name={icon} size={18} />
      {label}
    </button>
  );
  const items = (
    <>
      {item('edit', 'Изменить название и исполнителей', onEdit)}
      {has && item('note', by ? 'Не та запись — оставить как в файле' : m?.state === 'doubt' ? 'Это она — взять данные каталога' : 'Взять данные каталога', () => onChange({ catalog: !by, cover: !by && m?.cover_url ? 'catalog' : 'file', version: false, skip: false }))}
      {/* neither that record nor another song: another recording of the same song — its own names, tied as a version */}
      {has && item('disc', isVersion(u, r) ? 'Не версия этой песни' : 'Это другая версия этой песни', () => onChange(isVersion(u, r) ? { version: false } : { catalog: false, cover: 'file', version: true }))}
      {item('search', has ? 'Другая запись каталога…' : 'Найти в каталоге вручную', onFind)}
      {!m?.by_hand && item('refresh', 'Сверить заново', onAgain)}
      {item('folder', 'В другой альбом…', onAlbum)}
      {item(picked ? 'x' : 'check', picked ? 'Снять выделение' : 'Выбрать', onPick)}
      {item('trash', 'Убрать из списка', onRemove, true)}
    </>
  );
  const about = (
    <span className="block text-xs text-dim">
      <span className="block truncate" title={u.orig_name}>{u.orig_name}</span>
      {[fmtSize(u.size), quality, fmtDuration(u.duration_s)].filter(Boolean).join(' · ')}
      {has && (
        <span className="block truncate pt-1">
          В каталоге{m?.state === 'doubt' ? ' похожая' : ''}: «<TrackTitle title={m?.title} track={m} />» — {(m?.artists?.length ? m.artists : [m?.artist]).join(', ')}
          {m?.album ? ` · «${m.album}»` : ''}
          {m?.why ? ` · ${m.why}` : ''}
        </span>
      )}
    </span>
  );
  const dots = (
    <span
      data-own
      className="contents"
      onMouseDown={(e) => {
        if (e.button !== 0 || e.shiftKey || e.ctrlKey || e.metaKey) return;
        e.preventDefault(); // no text is selected while the mouse drags
        onDragStart();
      }}
    >
      {picked ? (
        <IconButton icon="check" size={18} label="Снять выделение" onClick={() => !dragged() && onPick()} className="!text-accent" />
      ) : (
        <IconButton icon="more" size={18} label="Меню файла · потяните по строкам, чтобы выбрать несколько" aria-expanded={menu} onClick={() => !dragged() && onMenu(!menu)} className="text-dim hover:text-fg" />
      )}
    </span>
  );
  const ref = useRef<HTMLDivElement>(null);
  // the menu hangs under its ⋯ — or over it, where the foot of the window (the player's bar) would cut it
  const [up, setUp] = useState(false);
  useLayoutEffect(() => {
    if (!menu || !desktop) return setUp(false);
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    // cut by the window's edge, or under something that stands over the page there (the player's bar)
    const over = document.elementFromPoint(r.left + r.width / 2, Math.min(r.bottom, window.innerHeight) - 4);
    const cut = r.bottom > window.innerHeight - 8 || (!!over && !el.contains(over));
    if (cut && r.top - r.height - 48 > 0) setUp(true);
  }, [menu, desktop]);
  useEffect(() => {
    if (!menu || !desktop) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) onMenu(false);
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', close);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [menu, desktop]);
  return (
    <div
      role="row"
      aria-selected={picked}
      onPointerEnter={onDragOver}
      onClick={(e) => {
        // the row's own buttons do their own job; the rest of the row picks while rows are being picked
        if ((e.target as HTMLElement).closest('[data-own], [role="menu"], input')) return;
        if (onRowClick(e)) e.preventDefault();
      }}
      className={cx('relative border-b border-line/60 last:rounded-b-2xl last:border-0', picked && 'bg-accent/10', !r.include && 'opacity-60')}
    >
      <div className="grid min-h-14 items-center gap-2.5 py-1.5 pr-1" style={desktop ? grid(cols) : { gridTemplateColumns: '0.25rem 2.5rem minmax(0,1fr) 2.5rem' }}>
        {desktop ? (
          <span className="flex items-center justify-center gap-1 text-sm tabular-nums text-dim">
            {catNo && catNo !== fileNo ? <>{fileNo ? was(fileNo) : null}{pill(catNo)}</> : fileNo || (inAlbum ? '—' : number || '—')}
          </span>
        ) : (
          <span />
        )}
        {cover}
        {desktop ? (
          names
        ) : (
          <span className="min-w-0">
            {names}
            <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-1">
              <span className="text-[11px] tabular-nums text-dim">{fmtDuration(u.duration_s)}</span>
              {u.weak && <span className="text-[11px] text-warn">{quality}</span>}
            </span>
          </span>
        )}
        {desktop && cols.file && <span className="truncate text-[13px] text-muted" title={u.orig_name}>{u.orig_name}</span>}
        {desktop && cols.size && <span className="text-[13px] tabular-nums text-muted">{fmtSize(u.size)}</span>}
        {desktop && cols.quality && <span><span className={cx('whitespace-nowrap text-[13px]', u.weak ? 'rounded-full bg-warn/15 px-2 py-0.5 text-warn' : 'text-muted')} title={u.weak ? 'Слабый файл: на следующем шаге поищу лучше' : undefined}>{quality || '—'}</span></span>}
        {desktop && <span className="text-right text-xs tabular-nums text-dim">{fmtDuration(u.duration_s)}</span>}
        {dots}
      </div>
      {tipBox}
      {u.twin && <div className="pb-2 pl-12 pr-4 md:pl-[5.75rem]" data-own><TwinNote u={u} r={r} onChange={onChange} /></div>}
      {menu && desktop && (
        <div ref={ref} role="menu" aria-label={`Меню: ${title}`} className={cx('anim-up absolute right-2 z-30 w-80 rounded-2xl bg-raised p-1.5 shadow-2xl', up ? 'bottom-12' : 'top-12')}>
          <div className="px-3 pb-2 pt-1.5">{about}</div>
          {items}
        </div>
      )}
      {!desktop && (
        <Sheet open={menu} onClose={() => onMenu(false)} title={title || u.orig_name}>
          <div className="pb-2">
            <span className="block truncate pb-2 text-sm text-muted">{artists}</span>
            {about}
          </div>
          <div role="menu">{items}</div>
        </Sheet>
      )}
    </div>
  );
}

/** The names of a file written by hand: its own, not the catalog's. What its album is — and a single's artist, year
 * and picture — is said in the head over it; a file is put into another album by its menu. */
function EditNames({ u, r, onChange, onClose }: { u: UploadOut; r: RowState; onChange: (patch: Partial<RowState>) => void; onClose: () => void }) {
  const label = useVersionLabel(usesCatalog(u, r) ? u.match : null);
  return (
    <Sheet
      open
      onClose={onClose}
      title="Название и исполнители"
      footer={
        <Button variant="primary" block disabled={!r.title.trim() || !r.artist.trim()} onClick={onClose}>
          Готово
        </Button>
      }
    >
      <p className="truncate pb-3 text-xs text-muted" title={u.orig_name}>{u.orig_name}</p>
      <div className="space-y-3">
        <Field label="Название">
          <span className="flex min-w-0 items-center gap-2">
            <input className="input min-w-0 flex-1" autoFocus value={r.title} onChange={(e) => onChange({ title: e.target.value })} />
            <VersionBadge label={label} />
          </span>
        </Field>
        <Field label="Исполнители (через запятую)">
          <input className="input" value={r.artist} onChange={(e) => onChange({ artist: e.target.value })} />
        </Field>
      </div>
      {u.match?.ref && <p className="pt-3 text-xs text-dim">Данные каталога для этого файла отключены. Вернуть — «Взять данные каталога» в меню строки.</p>}
    </Sheet>
  );
}

/** The album files are put into by hand: one of the page's, or a new one. */
function AlbumPick({ count, albums, onClose, onPick }: { count: number; albums: { key: string; title: string; artist: string; files: number }[]; onClose: () => void; onPick: (into: { key: string } | { title: string; artist: string }) => void }) {
  const [title, setTitle] = useState('');
  const [artist, setArtist] = useState('');
  const [q, setQ] = useState('');
  const shown = albums.filter((a) => !q.trim() || `${a.title} ${a.artist}`.toLocaleLowerCase('ru').includes(q.trim().toLocaleLowerCase('ru')));
  return (
    <Sheet open onClose={onClose} title={`${tracksWord(count)} — в альбом`}>
      <div className="rounded-xl bg-fg/5 p-3">
        <p className="pb-2 text-sm font-semibold">Новый альбом</p>
        <div className="grid gap-2 md:grid-cols-2">
          <input className="input" placeholder="Название альбома" aria-label="Название альбома" value={title} onChange={(e) => setTitle(e.target.value)} />
          <input className="input" placeholder="Исполнитель альбома" aria-label="Исполнитель альбома" value={artist} onChange={(e) => setArtist(e.target.value)} />
        </div>
        <Button small variant="primary" className="mt-2" disabled={!title.trim() || !artist.trim()} onClick={() => onPick({ title: title.trim(), artist: artist.trim() })}>
          Создать и перенести
        </Button>
      </div>
      {albums.length > 0 && (
        <>
          <p className="pb-2 pt-4 text-sm font-semibold">Альбом из этой загрузки</p>
          {albums.length > 8 && <input className="input mb-2" type="search" placeholder="Найти альбом" aria-label="Найти альбом" value={q} onChange={(e) => setQ(e.target.value)} />}
          <ul>
            {shown.map((a) => (
              <li key={a.key}>
                <button type="button" onClick={() => onPick({ key: a.key })} className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-fg/10">
                  <Icon name="disc" size={18} className="shrink-0 text-muted" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[15px]">{a.title || 'без названия'}</span>
                    <span className="block truncate text-[13px] text-muted">{a.artist}</span>
                  </span>
                  <span className="shrink-0 text-xs tabular-nums text-dim">{a.files}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </Sheet>
  );
}

/** The record of the catalog a file is, picked by hand: the catalog is searched by words, a record is taken by a tap. */
function FindRecord({ u, onClose, onPick }: { u: UploadOut; onClose: () => void; onPick: (ref: string) => void }) {
  const [q, setQ] = useState(`${u.artists?.[0] ?? u.artist} ${u.title}`.trim());
  const dq = useDebounced(q.trim(), 350);
  const res = useAsync((s) => (dq.length > 1 ? api.searchTracks(dq, 0, s).then((r) => r.tracks) : Promise.resolve([] as TrackOut[])), [dq]);
  const found = (res.data ?? []).filter((t) => t.ref.startsWith('dz:'));
  const on = useListen((s) => (s.track && s.phase !== 'paused' && s.phase !== 'error' ? s.track.ref : null));
  return (
    <Sheet open onClose={onClose} title="Какая это запись">
      <p className="pb-3 text-xs text-muted">
        Файл: {u.title} — {u.artist || 'исполнитель не указан'} · {fmtDuration(u.duration_s)}
      </p>
      <input className="input" type="search" value={q} autoFocus placeholder="Исполнитель и название" aria-label="Поиск по каталогу" onChange={(e) => setQ(e.target.value)} />
      <p className="pt-2 text-[11px] text-dim">Обложка — послушать запись, строка — выбрать её.</p>
      <div className="pt-3">
        {res.loading ? (
          <div className="flex justify-center py-6"><Spinner /></div>
        ) : !found.length ? (
          <p className="py-6 text-center text-sm text-muted">{dq.length > 1 ? 'Каталог ничего не нашёл — попробуйте другие слова.' : 'Введите исполнителя и название.'}</p>
        ) : (
          <ul>
            {found.map((t) => (
              <li key={t.ref} className="flex items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-fg/10">
                {/* the cover plays the record, as a cover does everywhere; the row beside it takes the record */}
                <button type="button" aria-label={on === t.ref ? 'Пауза' : `Слушать: ${t.title}`} onClick={() => listen(listenTrack(t), found.map(listenTrack), 'upload')} className="group/c relative shrink-0">
                  <Cover src={t.cover_small_url || t.cover_url} size={40} alt="" />
                  <span aria-hidden="true" className={cx('absolute inset-0 flex items-center justify-center rounded-md bg-black/60 text-white', on !== t.ref && 'opacity-0 group-hover/c:opacity-100')}>
                    <Icon name={on === t.ref ? 'pause' : 'play'} size={16} filled />
                  </span>
                </button>
                <button type="button" title="Это она — взять эту запись" onClick={() => onPick(t.ref)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[15px]"><TrackTitle title={t.title} track={t} /></span>
                    <span className="block truncate text-[13px] text-muted">{[t.artists?.length ? t.artists.join(', ') : t.artist, t.album_title].filter(Boolean).join(' · ')}</span>
                  </span>
                  <span className="shrink-0 text-xs tabular-nums text-dim">{fmtDuration(t.duration_s)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Sheet>
  );
}

const KEEP_OFF = 'hm-upload-keep-off';

/** «После загрузки»: where the imported tracks go besides the library — «Любимое», playlists, tags. The same icons
 * as the selection bar's and the same windows; what is chosen stands beside them and is taken off by its ✕. An
 * upload also has the playlist that keeps it together (`keepName`): on unless taken off, and put back by its chip.
 * `made`: the playlists created from the selected source collections. */
export function AfterImport({ made = [], count, like, onLike, playlists, onPlaylists, onPickPlaylists, tagIds, onTags, keepName, keep = false, onKeep, label = 'После загрузки', className = 'mx-4 mt-5' }: { made?: { id: number; name: string }[]; keepName?: string; keep?: boolean; onKeep?: (on: boolean) => void; label?: string; className?: string; count: number; like: boolean; onLike: (v: boolean) => void; playlists: { id: string; name: string }[]; onPlaylists: (list: { id: string; name: string }[]) => void; onPickPlaylists: () => void; tagIds: number[]; onTags: (ids: number[]) => void }) {
  const tags = useTags();
  const picked = tagIds.map((id) => tags.byId.get(id)).filter((t) => !!t);
  const chip = 'flex min-w-0 items-center gap-1.5 rounded-full bg-fg/10 py-1 pl-3 pr-1 text-sm';
  const off = (label: string, onClick: () => void) => (
    <button type="button" aria-label={label} title={label} onClick={onClick} className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-muted hover:bg-fg/10 hover:text-fg">
      <Icon name="x" size={13} stroke={2.5} />
    </button>
  );
  const kept = !!keepName && keep;
  const nothing = !like && !playlists.length && !picked.length && !kept && !made.length;
  return (
    <div className={cx(className, 'flex flex-wrap items-center gap-x-1 gap-y-2 rounded-2xl bg-surface px-3 py-2')}>
      <span className="pr-2 text-sm text-muted">{label}</span>
      <IconButton icon="playlist" label="В «Любимое» и плейлисты" onClick={onPickPlaylists} className={like || playlists.length ? 'text-accent' : 'text-muted hover:text-fg'} />
      <IconButton icon="tag" label="Теги" onClick={() => openTagsPick(`${tracksWord(count)} — теги`, tagIds, onTags)} className={picked.length ? 'text-accent' : 'text-muted hover:text-fg'} />
      <span className="mx-2 h-5 w-px bg-line" aria-hidden="true" />
      {nothing && <span className="text-xs text-dim">Только в библиотеку. Можно сразу добавить в «Любимое», плейлисты и поставить теги.</span>}
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        {made.map((m) => (
          <span key={m.id} className={cx(chip, 'pr-3')} title={`Плейлист «${m.name}» создастся сам — в нём будут все треки этого списка`}>
            <Icon name="playlist" size={14} className="shrink-0 text-accent" />
            <span className="truncate">Плейлист «{m.name}»</span>
          </span>
        ))}
        {like && (
          <span className={chip}>
            <Icon name="heart" size={14} filled className="text-accent" />
            Любимое
            {off('Не добавлять в «Любимое»', () => onLike(false))}
          </span>
        )}
        {keepName && onKeep && (kept ? (
          <span className={chip} title="Плейлист создастся сам: в нём останется всё, что загружено за день">
            <Icon name="playlist" size={14} className="shrink-0 text-accent" />
            <span className="truncate">{keepName}</span>
            {off('Не собирать загруженное в плейлист', () => onKeep(false))}
          </span>
        ) : (
          <button type="button" onClick={() => onKeep(true)} className="rounded-full border border-dashed border-line px-3 py-1 text-sm text-muted hover:text-fg">
            + плейлист «{keepName}»
          </button>
        ))}
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
