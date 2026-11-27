import { useState, type FormEvent } from 'react';
import { api, withQuery } from '../api/client';
import type { Candidate, DownloadItemOut, DownloadStep, FileOut, TrackOut } from '../api/types';
import { fileRef } from '../components/candidate';
import { TrackRow, TrackTableHeader } from '../components/catalog';
import { FileInfo } from '../components/FileInfo';
import { Icon } from '../components/Icon';
import { TrackTitle } from '../components/TrackTitle';
import { AudioServiceList, ServiceBadge, ServiceText } from '../components/ServiceBadge';
import { Button, cx, ErrorState, Loading, Notice, PageHeader, ProgressBar, SectionTitle } from '../components/ui';
import { useDesktop } from '../lib/desktop';
import { useHubEvent } from '../lib/events';
import { DOWNLOAD_ACTIVE, downloadStatusLabel, fmtDuration, warningText } from '../lib/format';
import { useAsync, useInterval } from '../lib/hooks';
import { listen } from '../lib/listen';
import { Link } from '../lib/router';
import { attempt } from '../lib/ui';

export function DownloadDetailPage({ id }: { id: number }) {
  const res = useAsync((s) => api.downloadItem(id, s), [id]);
  const [busy, setBusy] = useState<string | null>(null);
  const [url, setUrl] = useState('');
  const [showLink, setShowLink] = useState(false);
  const [checkedLink, setCheckedLink] = useState<{ url: string; candidate: Candidate } | null>(null);
  const checkingFiles = !!res.data?.attach_to_track_id && !DOWNLOAD_ACTIVE.includes(res.data.status)
    && res.data.status !== 'done' && !!(checkedLink || res.data.candidates?.length);
  const checkedRef = checkedLink && fileRef(checkedLink.candidate);
  const ready = useAsync((s) => api.preparedDownloadFiles(id, checkedRef ?? undefined, s), [id, checkedRef], checkingFiles);
  useInterval(() => ready.reload(true), checkingFiles ? 2000 : null);

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
  const done = item.status === 'done';
  const checked = !active && !done ? checkedLink : null;
  const candidates = checked ? [checked.candidate] : item.candidates ?? [];
  const choosing = !active && !done && candidates.length > 0;
  const noFile = item.status === 'needs_review' && !choosing;
  const heading = done
    ? (item.attach_to_track_id ? 'Файл привязан' : 'Файл скачан')
    : noFile ? 'Файл не найден'
    : item.status === 'failed' ? 'Не удалось скачать файл'
    : item.status === 'searching' ? 'Ищем файл…'
    : downloadStatusLabel(item.status, item.progress);
  const showError = !checked && item.error && !(noFile && item.error === 'Не найдено ни в одном источнике')
    && !(choosing && item.error.startsWith('Нет записи в каталоге'));
  const apply = (r: DownloadItemOut | undefined) => r && res.setData((prev) => ({ ...prev, ...r, candidates: r.candidates ?? prev?.candidates, log: r.log ?? prev?.log }));

  const run = async (key: string, fn: () => Promise<DownloadItemOut>) => {
    setBusy(key);
    const result = await attempt(fn);
    apply(result);
    setBusy(null);
    return result;
  };

  const submitUrl = async (e: FormEvent) => {
    e.preventDefault();
    const u = url.trim();
    if (!u || busy || active || done) return;
    setBusy('url');
    const candidate = await attempt(() => api.checkDownloadLink(item.id, u));
    if (candidate) {
      setCheckedLink({ url: u, candidate });
      setShowLink(false);
    }
    setBusy(null);
  };

  const pickFile = async (index: number) => {
    const candidate = candidates[index];
    if (!candidate || busy) return;
    const ref = fileRef(candidate);
    if (item.attach_to_track_id && !ready.data?.includes(ref)) {
      setBusy(`c${index}`);
      const refReady = await attempt(() => api.prepareDownloadFile(id, ref));
      if (refReady) ready.setData((prev) => [...new Set([...(prev ?? []), refReady])]);
      ready.reload(true);
      setBusy(null);
      return;
    }
    const result = await run(`c${index}`, () => api.retry(item.id,
      item.attach_to_track_id ? { candidate_ref: ref } : checked ? { url: checked.url } : { candidate_index: index }));
    if (!result && item.attach_to_track_id) ready.reload(true);
    if (result) {
      setCheckedLink(null);
      setUrl('');
      setShowLink(false);
    }
  };

  const songId = item.nd_song_id;
  const playDownloaded = () => {
    if (!songId) return;
    listen({ ref: `nd:${songId}`, nd_song_id: songId, title: item.title, artist: item.artist, album: item.album ?? undefined, duration_s: item.duration_s, cover_url: item.chosen?.cover_url });
  };

  return (
    <div>
      <PageHeader
        title={<TrackTitle title={item.title} track={{ ...item, page_url: item.chosen?.page_url }} />}
        sub={[item.artist, fmtDuration(item.duration_s)].filter(Boolean).join(' · ')}
        back="/downloads"
      />
      {choosing && (
        <DownloadCandidates
          candidates={candidates}
          attach={!!item.attach_to_track_id}
          ready={ready.data ?? []}
          busy={busy}
          onPick={(i) => void pickFile(i)}
        />
      )}
      <div className="px-4 py-2">
        <section className={choosing ? 'space-y-3' : 'card space-y-4 p-4'}>
          {!choosing && (
            <div className="flex items-center gap-3" role="status">
              <span className={cx('flex h-10 w-10 shrink-0 items-center justify-center rounded-xl', done ? 'bg-accent/15 text-accent' : 'bg-raised text-muted')}>
                <Icon name={done ? 'check' : item.status === 'downloading' ? 'download' : 'search'} size={19} />
              </span>
              <div className="min-w-0">
                <h2 className="font-semibold">{heading}</h2>
                {done && item.chosen && <FileInfo info={candidateFile(item.chosen)} className="text-sm text-muted" />}
              </div>
            </div>
          )}
          {item.status === 'downloading' && <ProgressBar value={item.progress} />}
          {showError && item.error && <p className="text-sm text-muted"><ServiceText text={item.error} /></p>}
          {(item.warnings ?? []).map((w, i) => (
            <Notice key={i}><ServiceText text={warningText(w)} /></Notice>
          ))}
          {!active && !done && !checked && (!choosing || showLink) && (
            <form onSubmit={submitUrl} className="space-y-2">
              <label htmlFor="download-file-url" className="text-sm font-medium">Ссылка на запись</label>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <input
                  id="download-file-url"
                  aria-describedby="download-file-sources"
                  className="input min-w-0 flex-1"
                  type="url"
                  inputMode="url"
                  placeholder="https://…"
                  value={url}
                  readOnly={busy === 'url'}
                  onChange={(e) => setUrl(e.target.value)}
                />
                <Button type="submit" variant="primary" busy={busy === 'url'} disabled={!url.trim() || busy !== null} className="shrink-0">Проверить ссылку</Button>
              </div>
              <div id="download-file-sources" className="text-xs text-muted"><AudioServiceList /></div>
            </form>
          )}
          <div className="flex flex-wrap gap-2">
            {active ? (
              <Button small variant="danger" icon="x" busy={busy === 'cancel'} disabled={busy !== null} onClick={() => run('cancel', () => api.cancel(item.id))}>
                Отменить
              </Button>
            ) : !done && (
              <>
                {checked && (
                  <Button small variant="ghost" icon="link" disabled={busy !== null} onClick={() => { setCheckedLink(null); setShowLink(true); }}>Изменить ссылку</Button>
                )}
                {choosing && !checked && !showLink && (
                  <Button small variant="ghost" icon="link" disabled={busy !== null} onClick={() => setShowLink(true)}>Добавить ссылку</Button>
                )}
                {!checked && (
                  <Button small variant="ghost" icon="refresh" busy={busy === 'retry'} disabled={busy !== null} onClick={() => run('retry', () => api.retry(item.id))}>
                    Повторить поиск
                  </Button>
                )}
              </>
            )}
            {done && songId && (
              <Button small variant="primary" icon="play" onClick={playDownloaded}>
                Слушать
              </Button>
            )}
            {done && item.track_id && (
              <Link to={withQuery('/search', { q: `${item.artist} ${item.title}` })} className="inline-flex min-h-9 items-center gap-1 rounded-full bg-raised px-3.5 text-sm">
                <Icon name="check" size={16} /> В библиотеке
              </Link>
            )}
          </div>
        </section>
      </div>

      <Journal steps={item.log ?? []} />
    </div>
  );
}

function candidateFile(c: Candidate): FileOut {
  const audio = c.extra?.acodec;
  const codec = typeof audio === 'string' && audio !== 'none' ? audio : null;
  return {
    source: c.provider,
    bitrate_kbps: c.bitrate_kbps,
    codec: c.provider === 'hitmoz' || c.provider === 'hitmos_fm' ? 'mp3' : codec?.startsWith('mp4a.') ? 'aac' : codec,
  };
}

function DownloadCandidates({ candidates, attach, ready, busy, onPick }: { candidates: Candidate[]; attach: boolean; ready: string[]; busy: string | null; onPick: (index: number) => void }) {
  const desktop = useDesktop();
  const tracks = candidates.map((c): TrackOut & { page_url: string } => ({
    source: 'file',
    id: c.provider_id,
    ref: fileRef(c),
    title: c.title,
    artist: c.artist,
    album_title: c.album,
    cover_url: c.cover_url,
    duration_s: c.duration_s,
    page_url: c.page_url,
    file: candidateFile(c),
    owned: false,
    downloading: false,
  }));
  return (
    <section aria-labelledby="download-file-selection">
      <SectionTitle id="download-file-selection">Выберите файл</SectionTitle>
      <TrackTableHeader numbered album />
      <ul>
        {tracks.map((track, i) => (
          <li key={`${track.ref}:${i}`}>
            <TrackRow
              track={track}
              queue={tracks}
              index={i + 1}
              showAlbum
              link={false}
              note={!desktop && <FileInfo info={track.file} className="block truncate text-xs text-muted" />}
              menuExtra={track.page_url ? [{
                icon: 'external',
                label: 'Открыть источник',
                onClick: () => window.open(track.page_url, '_blank', 'noopener,noreferrer'),
              }] : undefined}
              primaryAction={
                <Button small variant={attach && ready.includes(track.ref) ? 'primary' : 'secondary'} busy={busy === `c${i}`} disabled={busy !== null} onClick={() => onPick(i)} className="shrink-0">
                  {attach && ready.includes(track.ref) ? 'Привязать' : 'Скачать'}
                </Button>
              }
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

const STEP_DOT: Record<DownloadStep['kind'], string> = { info: 'bg-fg/25', ok: 'bg-accent', warn: 'bg-warn', bad: 'bg-danger' };

function JournalMessage({ step }: { step: DownloadStep }) {
  // Earlier journal entries stored the provider before the underlying client's diagnostic.
  const provider = step.failed_provider ?? /^Источник не ответил — ([^:\r\n]+):/.exec(step.text)?.[1];
  if (provider) {
    return <><ServiceBadge service={provider} /> <span className="text-danger">— ошибка запроса</span></>;
  }

  if (/^(Запрос|Название в латинице)/.test(step.text)) return step.text;
  const text = step.text.replace(/^(Итог: загрузка не удалась — все источники недоступны):[\s\S]*/i, '$1');
  const cut = step.kind === 'info' ? -1 : text.indexOf(':');
  if (cut < 1) return <ServiceText text={text} />;
  return (
    <>
      <span className={step.kind === 'ok' ? 'text-accent' : step.kind === 'warn' ? 'text-warn' : 'text-danger'}>
        <ServiceText text={text.slice(0, cut + 1)} />
      </span>
      <ServiceText text={text.slice(cut + 1)} />
    </>
  );
}

/** «Журнал»: the steps of the download in order — where the file was looked for, which ones were tried and why each
 * was dropped or taken. The first word of a verdict («Выбран:», «Не подошёл:») is in its colour. */
export function Journal({ steps }: { steps: DownloadStep[] }) {
  if (!steps.length) return null;
  const clock = (iso: string) => new Date(iso + (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? '' : 'Z')).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  return (
    <section aria-label="Журнал загрузки">
      <SectionTitle>Журнал</SectionTitle>
      <ol className="px-4 pb-2">
        {steps.map((s, i) => (
          <li key={i} className="relative flex gap-3 pb-3.5 text-sm last:pb-1">
            {i < steps.length - 1 && <span aria-hidden="true" className="absolute bottom-0 left-[5px] top-4 w-px bg-line" />}
            <span aria-hidden="true" className={`mt-1 h-[11px] w-[11px] shrink-0 rounded-full ${STEP_DOT[s.kind] ?? STEP_DOT.info}`} />
            <div className="min-w-0 flex-1">
              <p className="break-words">
                <JournalMessage step={s} />
              </p>
              {s.cand && (
                <a href={s.cand.page_url} target="_blank" rel="noopener noreferrer" className="mt-1.5 flex items-center gap-2 rounded-xl bg-surface px-3 py-2 text-[13px] hover:bg-raised">
                  <span className="min-w-0 flex-1 truncate">
                    {s.cand.artist} — <TrackTitle title={s.cand.title} track={s.cand} />
                  </span>
                  <ServiceBadge service={s.cand.provider} className="max-w-[55%]! shrink-0 rounded-full bg-fg/10 px-2 py-1 text-[11px] text-muted" />
                </a>
              )}
            </div>
            <time className="shrink-0 pt-0.5 text-[11px] tabular-nums text-dim">{clock(s.t)}</time>
          </li>
        ))}
      </ol>
    </section>
  );
}
