import { useCallback, useEffect, useId, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { ApiError, api, isAbort, withQuery } from '../api/client';
import type { Candidate, FileComparisonOut, PickOut } from '../api/types';
import { useDesktop } from '../lib/desktop';
import { closeFindFile, filePickerTarget, openFindFile, useFindFile, type FileSought } from '../lib/findFile';
import { fmtDuration } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { isUpload, pause, useListen } from '../lib/listen';
import { fileReplaced } from '../lib/heard';
import { pauseNative } from '../lib/player';
import { EDITED_EVENT } from '../lib/trackEdit';
import { confirmDialog, toast, toastError } from '../lib/ui';
import { fileRef } from './candidate';
import { FileInfo, fileWords } from './FileInfo';
import { Icon } from './Icon';
import { AudioServiceList, ServiceBadge } from './ServiceBadge';
import { TrackTitle } from './TrackTitle';
import { Button, Cover, ErrorState, IconButton, ProgressBar, Sheet, Spinner, cx } from './ui';

const CURRENT = 'current';
const clock = (seconds: number) => fmtDuration(Math.floor(seconds)) || '0:00';
type Interval = [number, number];

interface FileRow {
  ref: string;
  title: string;
  artist: string;
  source?: string | null;
  cover_url?: string | null;
  page_url?: string | null;
  codec?: string | null;
  bitrate_kbps?: number | null;
  duration_s?: number | null;
}

export function FindFile() {
  const track = useFindFile();
  return track ? <PickerTarget key={track.library_track_id ?? track.ref} track={track} /> : null;
}

export function ChoosePlaybackFile({ compact = false }: { compact?: boolean }) {
  const track = useListen((state) => state.track);
  const info = useListen((state) => state.info);
  const failed = useListen((state) => state.phase === 'error');
  if (!failed || !track || isUpload(track) || track.nd_song_id || info?.kind === 'library') return null;
  const target = filePickerTarget({ ...track, library_track_id: info?.track_id ?? track.library_track_id });
  if (!target) return null;
  const choose = () => openFindFile(target);
  return compact
    ? <IconButton icon="search" size={18} label="Выбрать файл" onClick={choose} className="shrink-0 text-muted" />
    : <Button small icon="search" onClick={choose}>Выбрать файл</Button>;
}

function PickerTarget({ track }: { track: FileSought }) {
  const ref = track.ref ?? '';
  const target = useAsync((signal) => api.resolveFilePickerTrack(ref, signal), [ref], track.library_track_id == null);
  const id = track.library_track_id ?? target.data?.track_id;
  if (id != null) return <FilePicker track={{ ...track, library_track_id: id }} />;
  return (
    <Sheet open onClose={closeFindFile} title="Выбрать файл">
      {target.error ? <ErrorState text={target.error} onRetry={() => target.reload()} /> :
        <div role="status" aria-label="Открываем трек" className="flex justify-center py-4"><Spinner /></div>}
    </Sheet>
  );
}

function candidateRow(c: Candidate): FileRow {
  const audio = c.extra?.acodec;
  const codec = c.provider === 'hitmoz' || c.provider === 'hitmos_fm' ? 'mp3'
    : typeof audio === 'string' && audio !== 'none' ? audio.startsWith('mp4a.') ? 'aac' : audio : null;
  return { ...c, ref: fileRef(c), source: c.provider, codec };
}

function preparedRow(pick: PickOut): FileRow {
  return { ...pick, source: pick.provider };
}

interface Preview {
  ref: string;
  url: string;
  at: number;
  excerpt?: Interval;
}

function usePreview() {
  const audio = useRef<HTMLAudioElement | null>(null);
  const active = useRef<Preview | null>(null);
  const [on, setOn] = useState<Preview | null>(null);
  const reset = useCallback(() => {
    active.current = null;
    const previous = audio.current;
    audio.current = null;
    if (previous) {
      previous.pause();
      previous.onloadedmetadata = previous.ontimeupdate = previous.onended = previous.onerror = null;
      previous.removeAttribute('src');
      previous.load();
    }
  }, []);
  useEffect(() => reset, [reset]);
  const stop = useCallback(() => {
    reset();
    setOn(null);
  }, [reset]);

  const play = (ref: string, url: string, seconds?: number, excerpt?: Interval) => {
    if (active.current?.url === url && seconds == null) return stop();
    reset();
    pause();
    pauseNative();
    const next = { ref, url, at: excerpt?.[0] ?? seconds ?? 0, excerpt };
    const element = new Audio(url);
    audio.current = element;
    active.current = next;
    setOn(next);
    element.onloadedmetadata = () => {
      if (seconds != null && !excerpt) element.currentTime = Math.min(seconds, element.duration || seconds);
    };
    element.ontimeupdate = () => {
      setOn({ ...next, at: (excerpt?.[0] ?? 0) + element.currentTime });
    };
    element.onended = stop;
    const fail = () => {
      if (audio.current !== element) return;
      stop();
      toast('Не удалось воспроизвести файл', 'error');
    };
    element.onerror = fail;
    void element.play().catch(fail);
  };
  return { on, play, stop };
}

/** The same loudness bars as the quality view. The buttons over the red bars play server-cut excerpts. */
function WaveLane({ label, main, bars, length, marks, preview, onSeek, onExcerpt }: {
  label: string;
  main: boolean;
  bars: number[];
  length: number;
  marks: Interval[];
  preview: Preview | null;
  onSeek: (at: number) => void;
  onExcerpt: (mark: Interval) => void;
}) {
  const n = Math.min(112, bars.length);
  const spans = marks.flatMap(([start, end]): Interval[] => {
    const from = Math.max(0, start), to = Math.min(length, end);
    return to > from ? [[from, to]] : [];
  });
  return (
    <div className="grid grid-cols-[1.15rem_minmax(0,1fr)_2.5rem] items-center gap-2">
      <span role="img" aria-label={label} className={main ? 'text-warn' : 'text-muted'}>
        <Icon name={main ? 'star' : 'search'} size={17} filled={main} />
      </span>
      <div className="relative min-w-0">
        <button type="button" className="flex h-11 w-full items-center gap-px" aria-label={'Перемотать · ' + label}
          onClick={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const at = e.detail ? Math.min(.999, Math.max(0, (e.clientX - rect.left) / rect.width)) * length : 0;
            onSeek(at);
          }}>
          {n ? Array.from({ length: n }, (_, i) => {
            const start = i / n * length, end = (i + 1) / n * length;
            const red = spans.some(([from, to]) => start < to && end > from);
            const sourceStart = Math.floor(i * bars.length / n);
            const sourceEnd = Math.floor((i + 1) * bars.length / n);
            const height = Math.max(6, ...bars.slice(sourceStart, sourceEnd));
            return <span key={i} className={cx('min-w-0 flex-1 rounded-sm', red ? 'bg-danger' : preview && start <= preview.at ? 'bg-accent' : 'bg-fg/30')}
              style={{ height: height + '%' }} />;
          }) : <span className="h-1 w-full rounded bg-fg/10" />}
        </button>
        {spans.map(([from, to]) => {
          const active = preview?.excerpt?.[0] === from && preview.excerpt[1] === to;
          const range = clock(from) + '–' + clock(to);
          return <button type="button" key={from + ':' + to}
            className={cx('absolute inset-y-0 min-w-3 -translate-x-1/2 rounded border border-transparent hover:border-danger/70 focus-visible:border-danger', active && 'border-danger bg-danger/15')}
            style={{ left: (from + to) / 2 / length * 100 + '%', width: (to - from) / length * 100 + '%' }}
            aria-label={(active ? 'Остановить отрывок ' : 'Слушать отрывок ') + range + ' · ' + label}
            title={range} aria-pressed={active} onClick={() => onExcerpt([from, to])} />;
        })}
      </div>
      <span className="text-right text-xs tabular-nums text-dim">{clock(length)}</span>
    </div>
  );
}

function FilePicker({ track }: { track: FileSought & { library_track_id: number } }) {
  const id = track.library_track_id;
  const headingId = useId();
  const desktop = useDesktop();
  const player = usePreview();
  const mine = useAsync((signal) => api.trackFile(id, signal), [id]);
  const sought = track.import_review ?? track;
  const query = [(sought.artist.split(',')[0] || sought.artist).trim(), sought.title].filter(Boolean).join(' - ');
  const sources = useAsync((signal) => api.searchProviders(query, signal, 'sources'), [query]);
  const reuploads = useAsync((signal) => api.searchProviders(query, signal, 'reuploads'), [query]);
  const [prepared, setPrepared] = useState<Record<string, PickOut>>({});
  const [extra, setExtra] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [comparisonRef, setComparisonRef] = useState<string | null>(null);
  const [showLink, setShowLink] = useState(false);
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [saving, setSaving] = useState(false);
  const request = useRef<AbortController | null>(null);
  const saveLock = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  const current = mine.data?.file_uid != null ? { ...mine.data, ref: CURRENT } : null;
  const rows = new Map<string, FileRow>();
  if (current) rows.set(CURRENT, current);
  for (const ref of extra) {
    if (prepared[ref]) rows.set(ref, preparedRow(prepared[ref]));
  }
  for (const c of [...(sources.data?.candidates ?? []), ...(reuploads.data?.candidates ?? [])]) {
    const row = candidateRow(c);
    if (!rows.has(row.ref)) rows.set(row.ref, prepared[row.ref] ? { ...row, ...preparedRow(prepared[row.ref]) } : row);
  }
  for (const file of Object.values(prepared)) {
    if (!rows.has(file.ref)) rows.set(file.ref, preparedRow(file));
  }
  const main = rows.get(selected ?? CURRENT);
  const other = comparisonRef ? rows.get(comparisonRef) : undefined;
  const reversed = other?.ref === CURRENT;
  const left = reversed ? other : main;
  const right = reversed ? main : other;
  const pairKey = left && right ? JSON.stringify([left.ref, right.ref, mine.data?.file_uid]) : '';
  const comparison = useAsync(async (signal) => {
    if (!left || !right) throw new Error('Выберите два файла');
    try {
      const result = await api.fileCompare(id, {
        ref: right.ref,
        baseline_ref: left.ref === CURRENT ? undefined : left.ref,
        expected_file_uid: left.ref === CURRENT ? mine.data?.file_uid ?? undefined : undefined,
      }, signal);
      return { pairKey, result };
    } catch (error) {
      if (!signal.aborted && error instanceof ApiError && error.status === 410) {
        discardPrepared([left.ref, right.ref]);
        toastError(error);
      }
      throw error;
    }
  }, [pairKey], !!pairKey);
  const result = comparison.data?.pairKey === pairKey && !comparison.loading ? comparison.data.result : null;
  const ready = (ref: string) => ref === CURRENT || !!prepared[ref];

  const close = useCallback(() => {
    if (saveLock.current) return;
    request.current?.abort();
    closeFindFile();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    window.addEventListener('keydown', onKey);
    const old = document.body.style.overflow;
    const opener = document.activeElement;
    document.body.style.overflow = 'hidden';
    closeButton.current?.focus();
    return () => {
      request.current?.abort();
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = old;
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus({ preventScroll: true });
    };
  }, [close]);

  function audioUrl(ref: string, excerpt?: Interval) {
    const interval = { start: excerpt?.[0], end: excerpt?.[1] };
    const trackUrl = '/api/quality/track/' + id;
    return ref === CURRENT
      ? withQuery(trackUrl + '/audio', { file_uid: mine.data?.file_uid ?? undefined, ...interval })
      : withQuery(trackUrl + '/picked/audio', { ref, ...interval });
  }

  function discardPrepared(refs: string[]) {
    player.stop();
    setPrepared((previous) => Object.fromEntries(Object.entries(previous).filter(([ref]) => !refs.includes(ref))));
    setSelected((ref) => ref && refs.includes(ref) ? null : ref);
    setComparisonRef(null);
  }

  async function prepare(key: string, get: (signal: AbortSignal) => Promise<PickOut>, add = false) {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(key);
    try {
      const file = await get(controller.signal);
      if (controller.signal.aborted) return;
      setPrepared((previous) => ({ ...previous, [file.ref]: file }));
      if (add) setExtra((previous) => [file.ref, ...previous.filter((ref) => ref !== file.ref)]);
      return file;
    } catch (error) {
      if (!isAbort(error)) toastError(error);
    } finally {
      request.current = null;
      if (!controller.signal.aborted) setBusy(null);
    }
  }

  async function download(row: FileRow, play = false) {
    const file = await prepare(row.ref, (signal) => api.filePrepare(id, { ref: row.ref }, signal));
    if (!file) return;
    if (main && file.ref !== main.ref) setComparisonRef(file.ref);
    if (play) player.play(file.ref, audioUrl(file.ref));
  }

  function hear(row: FileRow) {
    if (!ready(row.ref)) {
      void download(row, true);
      return;
    }
    if (player.on?.ref === row.ref) {
      player.stop();
      return;
    }
    if (main && main.ref !== row.ref) setComparisonRef(row.ref);
    player.play(row.ref, audioUrl(row.ref));
  }

  function choose(row: FileRow) {
    player.stop();
    setSelected(row.ref);
    setComparisonRef(main?.ref ?? null);
  }

  async function addLink() {
    const link = url.trim();
    if (!link) return;
    const file = await prepare('link', (signal) => api.filePrepare(id, { url: link }, signal), true);
    if (!file) return;
    setShowLink(false);
    setUrl('');
    if (main && main.ref !== file.ref) setComparisonRef(file.ref);
  }

  async function upload(file: File | undefined) {
    if (!file) return;
    setProgress(0);
    const picked = await prepare('upload', (signal) => api.fileUpload(id, file, setProgress, signal), true);
    if (picked && main) setComparisonRef(picked.ref);
  }

  async function commit() {
    if (saveLock.current || !mine.data || !selected || selected === CURRENT || !ready(selected)) return;
    saveLock.current = true;
    setSaving(true);
    player.stop();
    const existing = mine.data.file_uid;
    try {
      if (existing != null && !(other?.ref === CURRENT && result?.verdict === 'same' && !result.gaps.length)) {
        const confirmed = await confirmDialog({
          title: 'Заменить файл трека?',
          body: 'Выбранный файл будет воспроизводиться вместо текущего. Прежний можно вернуть в разделе «Качество звука».',
          okLabel: 'Заменить',
        });
        if (!confirmed) return;
      }
      try {
        await api.fileTake(id, { ref: selected, expected_file_uid: existing, sure: existing != null, import_review_id: track.import_review?.id });
      } catch (error) {
        toastError(error);
        if (error instanceof ApiError && error.status === 410) discardPrepared([selected]);
        return;
      }
      window.dispatchEvent(new Event(EDITED_EVENT));
      fileReplaced(track); // a copy kept on this device for offline is fetched anew
      closeFindFile();
      if (existing == null) toast('Файл привязан к треку', 'success');
      else toast('Файл заменён. Прежний хранится 30 дней', 'success', { label: 'Сравнить', to: '/quality' });
    } finally {
      saveLock.current = false;
      setSaving(false);
    }
  }

  function renderWave(file: FileRow, index: 'mine' | 'other', data: FileComparisonOut, mainLane: boolean) {
    const label = mainLane ? 'Основной файл' : 'Файл для сравнения';
    return (
      <WaveLane label={label} main={mainLane} bars={data.peaks[index]} length={data.length[index]}
        marks={data.gaps.map((gap) => index === 'mine' ? [gap.at, gap.to] : [gap.other_at, gap.other_to])}
        preview={player.on?.ref === file.ref ? player.on : null}
        onSeek={(at) => player.play(file.ref, audioUrl(file.ref), at)}
        onExcerpt={(part) => player.play(file.ref, audioUrl(file.ref, part), undefined, part)} />
    );
  }

  const title = (mine.data ? current != null : track.has_file) ? 'Найти другой файл' : 'Выбрать файл';
  const grid: CSSProperties = { gridTemplateColumns: desktop
    ? '1.75rem minmax(0,1fr) minmax(7rem,.5fr) 3rem 8rem'
    : '1rem minmax(0,1fr) auto' };
  const searching = sources.loading || reuploads.loading;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center" role="dialog" aria-modal="true" aria-labelledby={headingId}>
      <button type="button" aria-label="Закрыть окно" tabIndex={-1} className="absolute inset-0 bg-black/70" onClick={close} />
      <div className="anim-up pb-safe relative flex max-h-[94dvh] w-full max-w-4xl flex-col rounded-t-2xl bg-surface sm:max-h-[90dvh] sm:rounded-2xl">
        <header className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-2">
          <div className="min-w-0 flex-1">
            <h2 id={headingId} className="text-base font-bold">{title}</h2>
            <p className="truncate text-xs text-muted"><TrackTitle title={track.title} track={track} /> · {track.artist}</p>
          </div>
          <IconButton icon="refresh" label="Искать заново" onClick={() => { sources.reload(); reuploads.reload(); }} />
          <button ref={closeButton} type="button" aria-label="Закрыть" disabled={saving} onClick={close}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full hover:bg-fg/10"><Icon name="x" size={20} /></button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="flex flex-wrap gap-1 border-b border-line px-3 py-1 lg:justify-end">
            <Button variant="ghost" small icon="link" aria-expanded={showLink} onClick={() => setShowLink(!showLink)}>Скачать по ссылке</Button>
            <Button variant="ghost" small icon="upload" disabled={busy !== null} onClick={() => input.current?.click()}>Загрузить файл</Button>
            <input ref={input} type="file" accept="audio/*,.flac,.opus,.ogg,.oga,.m4a,.mp3,.wav,.aac,.aiff,.aif" hidden
              onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ''; }} />
          </div>
          {busy === 'upload' && <div className="border-b border-line px-4 py-2" aria-label="Загрузка файла"><ProgressBar value={progress} /></div>}
          {showLink && (
            <form className="space-y-1.5 border-b border-line px-4 py-2" onSubmit={(e) => { e.preventDefault(); void addLink(); }}>
              <div className="flex items-center gap-2">
                <input className="input min-w-0 flex-1" type="url" autoFocus required value={url} aria-label="Ссылка на страницу трека"
                  placeholder="https://…" onChange={(e) => setUrl(e.target.value)} />
                <Button type="submit" small busy={busy === 'link'} disabled={busy !== null || !url.trim()}>Скачать</Button>
              </div>
              <div className="text-xs text-muted"><AudioServiceList /></div>
            </form>
          )}

          {!mine.data && mine.error && <p role="alert" className="px-4 py-4 text-sm text-danger">{mine.error}</p>}
          {desktop && !!rows.size && (
            <div aria-hidden="true" className="grid items-center gap-3 border-b border-line px-4 py-2 text-xs text-dim" style={grid}>
              <span className="text-center">#</span><span>Название</span><span>Файл</span><span className="text-right">Время</span>
            </div>
          )}
          <ul aria-label="Файлы для трека" className="px-2 py-1">
            {[...rows.values()].map((row, index) => {
              const isMain = main?.ref === row.ref;
              const isOther = other?.ref === row.ref;
              const playing = player.on?.ref === row.ref;
              const wait = busy === row.ref;
              return (
                <li key={row.ref} style={grid} className={cx('grid min-h-14 items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-fg/5 lg:gap-3', isOther && 'bg-fg/[0.07]')}>
                  {isMain || isOther ? (
                    <span role="img" aria-label={isMain ? 'Основной файл' : 'Файл для сравнения'}
                      className={cx('flex justify-center', isMain ? 'text-warn' : 'text-muted')}>
                      <Icon name={isMain ? 'star' : 'search'} filled={isMain} size={17} />
                    </span>
                  ) : <span className="text-center text-xs tabular-nums text-dim">{index + 1}</span>}
                  <div className="flex min-w-0 items-center gap-2.5">
                    <button type="button" disabled={wait || (!!busy && !ready(row.ref))}
                      aria-label={(playing ? 'Пауза · ' : 'Слушать · ') + row.title}
                      className="group/cover relative h-10 w-10 shrink-0 rounded-md" aria-busy={wait || undefined}
                      onClick={() => hear(row)}>
                      <Cover src={row.cover_url || track.cover_url || mine.data?.cover_url} size={40} />
                      <span className={cx('absolute inset-0 flex items-center justify-center rounded-md bg-black/60 text-white',
                        desktop && !playing && !wait && 'opacity-0 group-hover/cover:opacity-100 group-focus-visible/cover:opacity-100')}>
                        <Icon name={playing || wait ? 'pause' : 'play'} size={16} filled />
                      </span>
                    </button>
                    <div className="min-w-0">
                      <div className={cx('truncate text-[15px]', playing && 'text-accent')}>
                        <TrackTitle title={row.title} track={row.ref === CURRENT ? track : row} />
                      </div>
                      <div className="truncate text-[13px] text-muted">{row.artist}</div>
                      {!desktop && <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
                        <FileCell row={row} />{row.duration_s ? <span>{fmtDuration(row.duration_s)}</span> : null}
                      </div>}
                    </div>
                  </div>
                  {desktop && <div className="min-w-0 truncate text-xs text-muted"><FileCell row={row} /></div>}
                  {desktop && <span className="text-right text-xs tabular-nums text-dim">{fmtDuration(row.duration_s)}</span>}
                  <div className="justify-self-end">
                    {isMain ? null : !ready(row.ref) ? (
                      <Button variant="secondary" small busy={wait} disabled={busy !== null}
                        onClick={() => void download(row)}>Скачать</Button>
                    ) : !main || isOther ? (
                      <Button variant="secondary" small disabled={!mine.data || saving} onClick={() => choose(row)}>
                        <Icon name="star" size={15} filled className="text-warn" />Выбрать
                      </Button>
                    ) : (
                      <Button variant="secondary" small icon="search" onClick={() => { player.stop(); setComparisonRef(row.ref); }}>Сравнить</Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          {searching && (
            <div role="status" aria-label="Поиск файлов" className="flex justify-center py-3"><Spinner size={18} /></div>
          )}
          {!searching && rows.size === (current ? 1 : 0) && (
            <p className="px-4 py-4 text-sm text-muted">Варианты не найдены.</p>
          )}
          {mine.data && !main && Object.keys(prepared).length > 0 && (
            <p className="px-4 pb-3 text-xs text-muted">Выберите основной файл, чтобы сравнить его с другим.</p>
          )}
          {main && other && pairKey && (
            <section aria-label="Сравнение по звуку" className="space-y-1 border-t border-line bg-bg/35 px-4 py-3">
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="text-sm font-medium">Сравнение по звуку</h3>
                {player.on?.excerpt && <span className="text-xs tabular-nums text-muted">{clock(player.on.at)}</span>}
              </div>
              {result ? (
                <>
                  <p className="text-xs text-muted">
                    {{ same: 'Та же запись', alike: 'Похожая запись', other: 'Другая запись', unknown: 'Не удалось определить сходство' }[result.verdict]}
                  </p>
                  {renderWave(main, reversed ? 'other' : 'mine', result, true)}
                  {renderWave(other, reversed ? 'mine' : 'other', result, false)}
                </>
              ) : comparison.error && !comparison.loading ? (
                <div className="flex flex-wrap items-center gap-2 text-xs text-danger" role="alert">
                  {comparison.error}<Button small onClick={() => comparison.reload()}>Повторить</Button>
                </div>
              ) : (
                <div role="status" aria-label="Сравниваем файлы" className="py-2"><Spinner size={16} /></div>
              )}
            </section>
          )}
        </div>

        <footer className="flex shrink-0 justify-end gap-2 border-t border-line px-4 py-2">
          <Button variant="ghost" disabled={saving} onClick={close}>Отмена</Button>
          <Button variant="primary" busy={saving} disabled={!mine.data || !selected || selected === CURRENT || !ready(selected)}
            onClick={() => void commit()}>Готово</Button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

function FileCell({ row }: { row: FileRow }) {
  return row.source === 'upload'
    ? <span className="inline-flex flex-wrap items-center gap-x-1.5"><ServiceBadge service="upload" label="Свой файл" />{fileWords(row, true)}</span>
    : <FileInfo info={row} />;
}
