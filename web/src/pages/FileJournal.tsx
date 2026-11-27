import { useState } from 'react';
import { api } from '../api/client';
import type { FileEventOut, TrackFileOut, TrackFilesOut } from '../api/types';
import { FileInfo, fileWords } from '../components/FileInfo';
import { Icon } from '../components/Icon';
import { ServiceIcon } from '../components/ServiceBadge';
import { TrackTitle } from '../components/TrackTitle';
import { Button, Empty, ErrorState, IconButton, Loading, PageHeader, SectionTitle } from '../components/ui';
import { fmtWhen, providerLabel } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link } from '../lib/router';
import { attempt } from '../lib/ui';
import { Journal } from './DownloadDetail';

/** «Файлы трека» (`/file/<track>`): the files a track has — the main one plays, the others are kept beside it — and
 * the journal of them: how each came (a download's own journal of its steps, where it kept one), which was made the
 * main one, which went to the trash folder and can still be brought back from there. Opened from the file's own
 * words: the column «Файл» of a table, the file's row of the player. «Назад» goes where one came from. */
export function FileJournalPage({ id }: { id: number }) {
  const res = useAsync((s) => api.trackFiles(id, s), [id]);
  const [data, setData] = useState<TrackFilesOut | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const d = data?.track_id === id ? data : res.data;

  const act = async (key: string, fn: () => Promise<TrackFilesOut>, done: string) => {
    setBusy(key);
    const got = await attempt(fn, done);
    setBusy(null);
    if (got) setData(got);
  };

  return (
    <div>
      <PageHeader title="Файлы трека" sub={d ? <TrackTitle title={`${d.artist} — ${d.title}`} track={d} /> : undefined} back="/library" />
      {res.loading && !d ? (
        <Loading />
      ) : !d ? (
        <ErrorState text={res.error ?? 'Трек не найден'} onRetry={() => res.reload()} />
      ) : (
        <>
          {d.files.length ? (
            <section aria-label="Файлы">
              {d.files.map((f) => (
                <FileCard
                  key={f.id}
                  trackId={d.track_id}
                  file={f}
                  busy={busy}
                  onMain={() => act(`main:${f.id}`, () => api.makeFileMain(d.track_id, f.id), 'Теперь играет этот файл')}
                  onTrash={() => act(`trash:${f.id}`, () => api.trashFile(d.track_id, f.id), `Файл в корзине: вернуть его можно ${d.trash_days} дн.`)}
                />
              ))}
            </section>
          ) : (
            <Empty icon="note" title="У трека нет файла" />
          )}
          <History
            events={d.events}
            trashDays={d.trash_days}
            busy={busy}
            onRestore={(e) => act(`restore:${e.id}`, () => api.restoreFile(d.track_id, e.id), 'Файл возвращён и играет')}
          />
        </>
      )}
    </div>
  );
}

function FileCard({ trackId, file, busy, onMain, onTrash }: { trackId: number; file: TrackFileOut; busy: string | null; onMain: () => void; onTrash: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mx-4 mb-2 rounded-2xl bg-surface">
      <div className="flex min-h-16 items-center gap-3 px-4 py-2">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-fg/10">
          {file.source ? <ServiceIcon service={file.source} size={20} /> : <Icon name="note" size={20} />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[15px] font-medium">
            {fileWords(file) || providerLabel(file.source)}
            {file.main && <span className="ml-2 rounded-full bg-accent/15 px-2 py-0.5 align-[0.1em] text-[11px] font-medium text-accent">Играет</span>}
          </span>
          <span className="block truncate text-[13px] text-muted" title={file.name}>
            {[fileWords(file) && providerLabel(file.source), fmtWhen(file.acquired_at), file.name].filter(Boolean).join(' · ')}
          </span>
          {file.sounds_like_kbps ? <span className="block text-[13px] text-warn">По звуку около {file.sounds_like_kbps} кбит/с: похоже, файл сделан из худшего</span> : null}
        </span>
        {!file.main && (
          <>
            <Button small busy={busy === `main:${file.id}`} disabled={!!busy} onClick={onMain}>
              Сделать главным
            </Button>
            <IconButton icon="trash" label="В корзину" size={18} busy={busy === `trash:${file.id}`} disabled={!!busy} onClick={onTrash} />
          </>
        )}
      </div>
      {file.download_id ? (
        <>
          <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-1 border-t border-line px-4 py-2.5 text-left text-sm text-muted hover:text-fg" aria-expanded={open}>
            Как скачан
            <Icon name="chevron" size={16} className={open ? '-rotate-90' : 'rotate-90'} />
          </button>
          {open && <Steps trackId={trackId} fileId={file.id} />}
        </>
      ) : null}
    </div>
  );
}

function Steps({ trackId, fileId }: { trackId: number; fileId: number }) {
  const res = useAsync((s) => api.fileSteps(trackId, fileId, s), [trackId, fileId]);
  if (res.loading) return <Loading />;
  if (!res.data) return <ErrorState text={res.error ?? 'Журнала загрузки нет'} onRetry={() => res.reload()} />;
  return (
    <div className="pb-2">
      <Journal steps={res.data.steps} />
      {res.data.download_id ? (
        <Link to={`/downloads/${res.data.download_id}`} className="mx-4 flex items-center gap-1 text-sm text-muted hover:text-fg" title="Найденные файлы, выбор другого">
          Загрузка
          <Icon name="chevron" size={16} />
        </Link>
      ) : null}
    </div>
  );
}

const CAME: Record<string, string> = {
  download: 'Скачан',
  upload: 'Загружен с компьютера',
  picked: 'Выбран вручную',
  upgrade: 'Найден файл лучше',
};

/** A line of the journal in words: what happened to which file, and why. */
function said(e: FileEventOut, first: boolean): string {
  switch (e.kind) {
    case 'added': // the track's first file is its main one by itself; a later one takes the place of the one before
      return first ? (CAME[e.why] ?? 'Добавлен') : `${CAME[e.why] ?? 'Добавлен'}, стал главным`;
    case 'main':
      return e.why === 'listener' ? 'Сделан главным' : 'Стал главным';
    case 'trashed':
      return e.why === 'picked' || e.why === 'upgrade' ? 'Заменён, убран в корзину' : e.why === 'revert' ? 'Отменена замена: убран в корзину' : 'Убран в корзину';
    case 'restored':
      return 'Возвращён из корзины';
    case 'lost':
      return 'Пропал с диска';
  }
}

function History({ events, trashDays, busy, onRestore }: { events: FileEventOut[]; trashDays: number; busy: string | null; onRestore: (e: FileEventOut) => void }) {
  if (!events.length) return null;
  return (
    <section aria-label="История файлов">
      <SectionTitle note={`файл из корзины можно вернуть ${trashDays} дн.`}>История</SectionTitle>
      <ol className="px-4 pb-4">
        {events.map((e, i) => (
          <li key={e.id} className="relative flex items-center gap-3 pb-3 text-sm last:pb-0">
            {i < events.length - 1 && <span aria-hidden="true" className="absolute bottom-0 left-[5px] top-4 w-px bg-line" />}
            <span aria-hidden="true" className={`h-[11px] w-[11px] shrink-0 self-start mt-1 rounded-full ${e.kind === 'trashed' || e.kind === 'lost' ? 'bg-dim' : e.kind === 'added' ? 'bg-accent' : 'bg-info'}`} />
            <div className="min-w-0 flex-1">
              <p className="break-words">
                {said(e, i === events.length - 1)}
                {(e.file.codec || e.file.source) && (
                  <>
                    {' · '}
                    <FileInfo info={e.file} className="text-muted" />
                  </>
                )}
              </p>
              <p className="truncate text-[12px] text-dim" title={e.name ?? undefined}>
                {[fmtWhen(e.at), e.who, e.name].filter(Boolean).join(' · ')}
              </p>
            </div>
            {e.can_restore && (
              <Button small busy={busy === `restore:${e.id}`} disabled={!!busy} onClick={() => onRestore(e)}>
                Вернуть
              </Button>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
