import { api } from '../api/client';
import { FileInfo } from '../components/FileInfo';
import { Icon } from '../components/Icon';
import { Empty, ErrorState, Loading, PageHeader } from '../components/ui';
import { providerLabel } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link } from '../lib/router';
import { Journal } from './DownloadDetail';

/** «Как скачан файл» (`/file/<track>`): the journal of the download that brought a track's file — where it was
 * looked for, which files were tried, why this one was taken. It is opened from the file's own words: the column
 * «Файл» of a table, the card «Файл» of the player. «Назад» goes where one came from. */
export function FileJournalPage({ id }: { id: number }) {
  const res = useAsync((s) => api.fileJournal(id, s), [id]);
  const d = res.data;
  return (
    <div>
      <PageHeader title="Как скачан файл" sub={d ? `${d.artist} — ${d.title}` : undefined} back="/library" />
      {res.loading ? (
        <Loading />
      ) : !d ? (
        <ErrorState text={res.error ?? 'Трек не найден'} onRetry={() => res.reload()} />
      ) : (
        <>
          {d.file && (
            <div className="mx-4 mb-2 flex min-h-14 items-center gap-3 rounded-2xl bg-surface px-4 py-2">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[15px]">
                  <FileInfo info={d.file} />
                </span>
                <span className="block truncate text-[13px] text-muted">{providerLabel(d.file.source)}</span>
              </span>
              {d.download_id !== null && d.download_id !== undefined && (
                <Link to={`/downloads/${d.download_id}`} className="flex shrink-0 items-center gap-1 text-sm text-muted hover:text-fg" title="Найденные файлы, выбор другого">
                  Загрузка
                  <Icon name="chevron" size={16} />
                </Link>
              )}
            </div>
          )}
          {d.steps.length ? (
            <Journal steps={d.steps} />
          ) : (
            <Empty icon="history" title="Журнала нет" text="Файл попал в библиотеку до того, как Хаб начал записывать шаги загрузки, или был загружен вручную." />
          )}
        </>
      )}
    </div>
  );
}
