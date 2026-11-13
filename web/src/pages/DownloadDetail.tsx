import { useState, type FormEvent } from 'react';
import { api, withQuery } from '../api/client';
import type { DownloadItemOut, DownloadStep } from '../api/types';
import { CandidateRow } from '../components/candidate';
import { Icon } from '../components/Icon';
import { Button, ErrorState, Field, Loading, Notice, PageHeader, ProgressBar, SectionTitle, StatusChip } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { DOWNLOAD_ACTIVE, fmtWhen, providerLabel, warningText } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { canPlay, playSongs } from '../lib/player';
import { Link } from '../lib/router';
import { attempt } from '../lib/ui';

export function DownloadDetailPage({ id }: { id: number }) {
  const res = useAsync((s) => api.downloadItem(id, s), [id]);
  const [busy, setBusy] = useState<string | null>(null);
  const [url, setUrl] = useState('');

  useHubEvent('download', (d) => {
    if (d.id !== id) return;
    // Status changed → candidates may have changed too; refetch the full detail.
    // an event carries neither the candidates nor the journal: the detail is asked for again when the status moves
    const moved = d.status !== res.data?.status;
    res.setData((prev) => (prev ? { ...prev, ...d, candidates: prev.candidates, log: prev.log } : prev));
    if (moved || d.status === 'needs_review' || d.status === 'failed' || d.status === 'done') res.reload(true);
  });
  useHubEvent('reconnect', () => res.reload(true));

  if (res.loading)
    return (
      <>
        <PageHeader title="Загрузка" back="/downloads" />
        <Loading />
      </>
    );
  if (res.error || !res.data)
    return (
      <>
        <PageHeader title="Загрузка" back="/downloads" />
        <ErrorState text={res.error ?? 'Не найдено'} onRetry={() => res.reload()} />
      </>
    );

  const item = res.data;
  const active = DOWNLOAD_ACTIVE.includes(item.status);
  const apply = (r: DownloadItemOut | undefined) => r && res.setData((prev) => ({ ...prev, ...r, candidates: r.candidates ?? prev?.candidates, log: r.log ?? prev?.log }));

  const run = async (key: string, fn: () => Promise<DownloadItemOut>) => {
    setBusy(key);
    apply(await attempt(fn));
    setBusy(null);
  };

  const submitUrl = (e: FormEvent) => {
    e.preventDefault();
    const u = url.trim();
    if (!u) return;
    void run('url', () => api.retry(item.id, { url: u })).then(() => setUrl(''));
  };

  const candidates = item.candidates ?? [];
  const chosenKey = item.chosen ? `${item.chosen.provider}:${item.chosen.provider_id}` : null;

  return (
    <div>
      <PageHeader title={item.title} sub={item.artist} back="/downloads" />
      <div className="space-y-3 px-4 pb-2">
        <div className="card space-y-2 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusChip status={item.status} progress={item.progress} />
            {item.provider && <span className="text-sm text-muted">{providerLabel(item.provider)}</span>}
            <span className="text-sm text-dim">{fmtWhen(item.updated_at)}</span>
          </div>
          {item.status === 'downloading' && <ProgressBar value={item.progress} />}
          <div className="text-sm text-muted">
            {[item.artist, item.album].filter(Boolean).join(' · ')}
          </div>
          {item.error && <Notice kind="error">{item.error}</Notice>}
          {(item.warnings ?? []).map((w, i) => (
            <Notice key={i}>{warningText(w)}</Notice>
          ))}
          {item.status === 'needs_review' && (
            <Notice kind="info">Подходящий файл не найден автоматически. Выберите вариант ниже или вставьте ссылку на страницу трека.</Notice>
          )}
          <div className="flex flex-wrap gap-2 pt-1">
            {active ? (
              <Button small variant="danger" icon="x" busy={busy === 'cancel'} onClick={() => run('cancel', () => api.cancel(item.id))}>
                Отменить
              </Button>
            ) : (
              item.status !== 'done' && (
                <Button small icon="refresh" busy={busy === 'retry'} onClick={() => run('retry', () => api.retry(item.id))}>
                  Повторить поиск
                </Button>
              )
            )}
            {item.status === 'done' && item.nd_song_id && canPlay() && (
              <Button small variant="primary" icon="play" onClick={() => playSongs([item.nd_song_id as string])}>
                Слушать
              </Button>
            )}
            {item.status === 'done' && item.track_id && (
              <Link to={withQuery('/search', { q: `${item.artist} ${item.title}` })} className="inline-flex min-h-9 items-center gap-1 rounded-full bg-raised px-3.5 text-sm">
                <Icon name="check" size={16} /> В библиотеке
              </Link>
            )}
          </div>
        </div>

        {item.chosen && (
          <div className="card overflow-hidden">
            <div className="px-4 pt-3 text-xs font-medium uppercase tracking-wide text-muted">Выбранный файл</div>
            <CandidateRow c={item.chosen} />
          </div>
        )}

        {item.status !== 'done' && (
          <form onSubmit={submitUrl} className="card space-y-2 p-4">
            <Field label="Своя ссылка">
              <input
                className="input"
                type="url"
                inputMode="url"
                placeholder="hitmoz, hitmos.fm, YouTube или SoundCloud"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </Field>
            <Button type="submit" small variant="primary" icon="link" busy={busy === 'url'} disabled={!url.trim()}>
              Скачать по ссылке
            </Button>
          </form>
        )}
      </div>

      <Journal steps={item.log ?? []} />

      {candidates.length > 0 && (
        <section aria-label="Кандидаты">
          <SectionTitle>Найденные файлы</SectionTitle>
          <ul className="divide-y divide-line/60">
            {candidates.map((c, i) => {
              const key = `${c.provider}:${c.provider_id}`;
              const isChosen = key === chosenKey;
              return (
                <li key={key + i}>
                  <CandidateRow
                    c={c}
                    action={
                      isChosen ? (
                        <span className="text-sm text-accent">Выбран</span>
                      ) : (
                        <Button small icon="download" busy={busy === `c${i}`} disabled={busy !== null} onClick={() => run(`c${i}`, () => api.retry(item.id, { candidate_index: i }))}>
                          Скачать этот
                        </Button>
                      )
                    }
                  />
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}

const STEP_DOT: Record<DownloadStep['kind'], string> = { info: 'bg-fg/25', ok: 'bg-accent', warn: 'bg-warn', bad: 'bg-danger' };

/** «Журнал»: the steps of the download in order — where the file was looked for, which ones were tried and why each
 * was dropped or taken. The first word of a verdict («Выбран:», «Не подошёл:») is in its colour. */
function Journal({ steps }: { steps: DownloadStep[] }) {
  if (!steps.length) return null;
  const clock = (iso: string) => new Date(iso + (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? '' : 'Z')).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  return (
    <section aria-label="Журнал загрузки">
      <SectionTitle>Журнал</SectionTitle>
      <ol className="px-4 pb-2">
        {steps.map((s, i) => {
          const cut = s.kind === 'info' ? -1 : s.text.indexOf(':');
          return (
            <li key={i} className="relative flex gap-3 pb-3.5 text-sm last:pb-1">
              {i < steps.length - 1 && <span aria-hidden="true" className="absolute bottom-0 left-[5px] top-4 w-px bg-line" />}
              <span aria-hidden="true" className={`mt-1 h-[11px] w-[11px] shrink-0 rounded-full ${STEP_DOT[s.kind] ?? STEP_DOT.info}`} />
              <div className="min-w-0 flex-1">
                <p className="break-words">
                  {cut > 0 ? (
                    <>
                      <span className={s.kind === 'ok' ? 'text-accent' : s.kind === 'warn' ? 'text-warn' : 'text-danger'}>{s.text.slice(0, cut + 1)}</span>
                      {s.text.slice(cut + 1)}
                    </>
                  ) : (
                    s.text
                  )}
                </p>
                {s.cand && (
                  <a href={s.cand.page_url} target="_blank" rel="noopener noreferrer" className="mt-1.5 flex items-center gap-2 rounded-xl bg-surface px-3 py-2 text-[13px] hover:bg-raised">
                    <span className="min-w-0 flex-1 truncate">
                      {s.cand.artist} — {s.cand.title}
                    </span>
                    <span className="shrink-0 rounded-full bg-fg/10 px-2 py-0.5 text-[11px] text-muted">{providerLabel(s.cand.provider)}</span>
                  </a>
                )}
              </div>
              <time className="shrink-0 pt-0.5 text-[11px] tabular-nums text-dim">{clock(s.t)}</time>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
