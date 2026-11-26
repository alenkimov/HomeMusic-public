import { TrackTitle } from '../components/TrackTitle';
import { TrackCell } from '../components/Track';
import { titleWithVersion } from '../lib/versionLabels';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import type { QualityRow } from '../api/types';
import { fileReplaced } from '../lib/heard';
import { Icon } from '../components/Icon';
import { Button, Empty, ErrorState, PageHeader, ProgressBar, SkeletonRows, cx , Segmented } from '../components/ui';
import { fmtDuration, tracksWord } from '../lib/format';
import { ServiceBadge, ServiceText, serviceName } from '../components/ServiceBadge';
import { SourceStatuses } from '../components/SourceStatuses';
import { useHubEvent } from '../lib/events';
import { useAsync, useInterval } from '../lib/hooks';
import { EDITED_EVENT } from '../lib/trackEdit';
import { attempt, confirmDialog, toast } from '../lib/ui';
import { Steps } from './Upload';

/** «Качество звука», the second, slow step of bringing music in — and a page of its own for any weak file of the
 * library. The Hub looks for each weak file anew through the sources and weighs what it finds against the file the
 * track has (the Hub: `library/upgrade.py`): the same recording in a better file takes the old one's place by
 * itself («Заменено»); one that is only alike — a version, a remaster — is kept aside («Ждут решения») and the
 * listener says: both files play whole here, each with the picture of its loudness to move through it by. Nothing
 * better — the row says why. The search goes on in the Hub: the page may be closed and come back to.
 *
 * A file replaced by itself can be heard too («Заменено» → «Сравнить»): the one the track had and the one it has
 * now, side by side, with the new one marked as taken — and the old one brought back while the Hub keeps it. The
 * sound tells one recording from another, not a censored copy from the one with the words in it: that is for the
 * listener to hear.
 *
 * The same page settles what the first step could not (`kind` "identify"): a file the catalog only had something
 * like went in under its own names; here the record's file is downloaded and the two are compared by the sound —
 * the same recording takes the record's names and cover by itself, one only alike waits for the listener's word. */

type Tab = 'found' | 'replaced' | 'none' | 'queue';
const TABS: [Tab, string][] = [
  ['found', 'Ждут решения'],
  ['replaced', 'Заменено'],
  ['none', 'Лучше не нашлось'],
  ['queue', 'В очереди'],
];
const tabOf = (r: QualityRow): Tab => (r.state === 'found' ? 'found' : r.state === 'replaced' ? 'replaced' : r.state === 'queued' || r.state === 'searching' ? 'queue' : 'none');
const fileWords = (codec?: string | null, kbps?: number | null) => [codec ? codec.toUpperCase() : null, kbps ? String(kbps) : null].filter(Boolean).join(' ') || '—';

export function QualityPage() {
  const res = useAsync((s) => api.quality(s), []);
  const rows = useMemo(() => res.data?.rows ?? [], [res.data]);
  const [tab, setTab] = useState<Tab | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null); // the replaced track whose two files are being heard
  const byTab = useMemo(() => {
    const out: Record<Tab, QualityRow[]> = { found: [], replaced: [], none: [], queue: [] };
    for (const r of rows) out[tabOf(r)].push(r);
    out.queue.sort((a, b) => Number(b.state === 'searching') - Number(a.state === 'searching') || a.id - b.id);
    return out;
  }, [rows]);
  const active = byTab.queue.length > 0;
  useInterval(() => res.reload(true), active ? 4000 : null);
  const updates = useRef<number | undefined>(undefined);
  useHubEvent('download', () => {
    if (!active || updates.current !== undefined) return;
    updates.current = window.setTimeout(() => {
      updates.current = undefined;
      res.reload(true);
    }, 500);
  });
  useEffect(() => () => window.clearTimeout(updates.current), []);
  // the first look: what waits for a word, else what is going on, else what was done
  const first: Tab = byTab.found.length ? 'found' : byTab.queue.length ? 'queue' : byTab.replaced.length ? 'replaced' : 'none';
  const at = tab ?? first;
  const shown = byTab[at];
  const done = rows.length - byTab.queue.length;
  const now = byTab.queue.find((r) => r.state === 'searching');
  const listening = useTwoFiles();

  const start = async () => {
    setBusy('start');
    const r = await attempt(() => api.qualityStart());
    setBusy(null);
    if (!r) return;
    toast(r.queued ? `В очереди: ${tracksWord(r.queued)}` : 'Слабых файлов нет', r.queued ? 'success' : 'info');
    res.reload(true);
  };
  /** `file`: with the yes to «is it that record?» the file found is taken too (said apart: yes by itself keeps one's own). */
  const say = async (r: QualityRow, yes: boolean, file = false) => {
    setBusy(`${r.id}`);
    listening.stop();
    const ok = await attempt(() => (yes ? api.qualityAccept(r.id, file) : api.qualityDecline(r.id)), yes ? (r.kind === 'identify' ? (file ? 'Данные каталога применены, файл заменён' : 'Данные каталога применены, файл остался ваш') : 'Файл заменён') : undefined);
    setBusy(null);
    if (!ok) return;
    if (yes) window.dispatchEvent(new Event(EDITED_EVENT));
    if (yes && (r.kind !== 'identify' || file)) fileReplaced({ library_track_id: r.track_id });
    res.reload(true);
  };
  /** «Другая версия той же песни»: the track stays as it is, tied to the catalog's record as a version. */
  const version = async (r: QualityRow) => {
    setBusy(`${r.id}`);
    listening.stop();
    const ok = await attempt(() => api.qualityVersion(r.id), 'Связаны как версии одной песни');
    setBusy(null);
    if (!ok) return;
    window.dispatchEvent(new Event(EDITED_EVENT));
    res.reload(true);
  };
  const back = async (r: QualityRow) => {
    if (!(await confirmDialog({ title: `Вернуть прежний файл: «${titleWithVersion(r.title, r)}»?`, body: 'Найденный файл уйдёт в корзину, трек снова будет играть ваш.', okLabel: 'Вернуть' }))) return;
    setBusy(`${r.id}`);
    listening.stop();
    const ok = await attempt(() => api.qualityRevert(r.id), 'Прежний файл возвращён');
    setBusy(null);
    if (!ok) return;
    setOpen(null);
    window.dispatchEvent(new Event(EDITED_EVENT));
    fileReplaced({ library_track_id: r.track_id });
    res.reload(true);
  };
  const takeAll = async () => {
    const list = byTab.found;
    if (!(await confirmDialog({ title: `Заменить ${tracksWord(list.length)}?`, body: 'Это записи, которые звучат похоже, но не одинаково — возможно, другие версии. Старые файлы уйдут в корзину на 30 дней.', okLabel: 'Заменить' }))) return;
    setBusy('all');
    listening.stop();
    let n = 0;
    for (const r of list) if (await attempt(() => api.qualityAccept(r.id))) n++;
    setBusy(null);
    toast(`Заменено: ${tracksWord(n)}`, 'success');
    window.dispatchEvent(new Event(EDITED_EVENT));
    res.reload(true);
  };

  const weak = res.data?.weak ?? 0;
  return (
    <div className="pb-10">
      <PageHeader title="Качество звука" back="/files" sub={rows.length ? `Проверено ${done} из ${rows.length}` : undefined} toolbar={<Steps at={2} />} />

      {res.loading ? (
        <SkeletonRows n={6} />
      ) : res.error && !res.data ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : (
        <>
          {weak > 0 && (
            <div className="mx-4 mb-3 flex flex-wrap items-center gap-3 rounded-2xl bg-surface px-4 py-3 lg:mx-6">
              <span className="min-w-0 flex-1 text-sm">
                Слабых файлов в библиотеке: {weak}
                <span className="block text-xs text-muted">Для каждого поищу ту же запись в лучшем качестве. Это небыстро: скачивание идёт в фоне.</span>
              </span>
              <Button small variant="primary" busy={busy === 'start'} onClick={start}>
                Искать для {weak}
              </Button>
            </div>
          )}
          {active && (
            <div className="mx-4 mb-3 rounded-2xl bg-surface px-4 py-3 lg:mx-6">
              <div className="flex items-center gap-2 text-sm">
                <span className="min-w-0 flex-1 truncate">
                  Проверено {done} из {rows.length}
                  {now && <> · сейчас: «<TrackTitle title={now.title} track={now} />»</>}
                </span>
                <span className="shrink-0 text-xs tabular-nums text-muted">осталось {byTab.queue.length}</span>
              </div>
              <ProgressBar value={rows.length ? done / rows.length : 0} className="mt-2" />
              <p className="pt-1.5 text-xs text-dim">Поиск идёт на сервере — страницу можно закрыть и вернуться позже. Сначала песни, которые знает каталог: они находятся быстрее.</p>
            </div>
          )}
          {rows.length === 0 ? (
            weak === 0 && <Empty icon="check" title="Слабых файлов нет" text="Здесь появляются треки, файл которых слышно: MP3 ниже 192 кбит/с и подобные." />
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 px-4 pb-3 lg:px-6">
                <div className="min-w-0 flex-1">
                  <Segmented<Tab> label="Раздел" value={at} onChange={setTab} options={TABS.map(([id, label]) => ({ value: id, label, count: byTab[id].length }))} />
                </div>
                {at === 'found' && shown.length > 1 && (
                  <Button small busy={busy === 'all'} onClick={takeAll}>
                    Заменить все {shown.length}
                  </Button>
                )}
              </div>
              {at === 'found' && shown.length > 0 && (
                <p className="px-4 pb-2 text-xs text-muted lg:px-6">Найденный файл звучит похоже, но не точь-в-точь — так бывает у ремастера или другой версии. Послушайте оба и решите. Без ответа файл через неделю удалится, трек останется как был.</p>
              )}
              {shown.length === 0 ? (
                <Empty icon="note" title={at === 'found' ? 'Решать пока нечего' : at === 'queue' ? 'Очередь пуста' : 'Пока пусто'} text={at === 'found' ? 'Одинаковые записи заменяются сами — сюда попадают только спорные.' : undefined} />
              ) : (
                <ul className="mx-4 rounded-2xl bg-surface lg:mx-6">
                  {shown.map((r) => (
                    <li key={r.id} className="border-b border-line/60 px-4 py-3 last:border-0">
                      <div className="relative flex items-center gap-3">
                        <TrackCell
                          track={{ title: r.title, artist: r.artist, cover: r.cover_url, identity: r }}
                          sub={<>{r.artist}{r.kind === 'identify' && r.record && r.state !== 'replaced' && <span className="block truncate text-[11px] text-dim">та ли это запись: <RecordTitle row={r} /></span>}</>}
                        />
                        <RowState r={r} />
                        {r.can_revert && (
                          <Button small onClick={() => { listening.stop(); setOpen(open === r.id ? null : r.id); }}>
                            {open === r.id ? 'Свернуть' : 'Сравнить'}
                          </Button>
                        )}
                      </div>
                      {r.state === 'found' && (
                        <Compare
                          r={r}
                          listening={listening}
                          busy={busy === `${r.id}` || busy === 'all'}
                          onYes={() => void say(r, true)}
                          onNo={() => void say(r, false)}
                          onVersion={r.kind === 'identify' ? () => void version(r) : undefined}
                          onTake={r.kind === 'identify' && r.better ? () => void say(r, true, true) : undefined}
                        />
                      )}
                      {r.can_revert && open === r.id && <Compare r={r} listening={listening} busy={busy === `${r.id}`} onYes={() => setOpen(null)} onNo={() => void back(r)} />}
                      {r.phase === 'downloading' && <ProgressBar value={r.progress} className="mt-2 max-w-md" />}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

/** What became of a track, at the right of its row. */
function RowState({ r }: { r: QualityRow }) {
  const pill = (text: string, cls: string, title?: string) => (
    <span title={title} className={cx('shrink-0 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px]', cls)}>
      {text}
    </span>
  );
  const was = fileWords(r.was_codec, r.was_kbps);
  const got = fileWords(r.codec, r.bitrate_kbps);
  if (r.state === 'replaced')
    return (
      <span className="flex shrink-0 items-center gap-1.5">
        {pill(was, 'bg-fg/5 text-dim line-through')}
        <Icon name="chevron" size={12} className="text-dim" />
        {pill(got, 'bg-accent/15 text-accent', r.note ?? undefined)}
      </span>
    );
  if (r.state === 'found') return pill(`сходство ${(r.likeness ?? 0).toFixed(2)}`, 'bg-warn/15 text-warn', r.note ?? undefined);
  if (r.state === 'searching') {
    const sources = Object.entries(r.search_sources ?? {}).map(([service, state]) => ({ service, state }));
    if (r.phase === 'searching' && sources.length) {
      return <SourceStatuses sources={sources} label="Источники поиска файла" />;
    }
    if (r.provider && (r.phase === 'downloading' || r.phase === 'checking')) {
      const name = serviceName(r.provider);
      const checking = r.phase === 'checking';
      return (
        <span title={checking ? `Проверка файла из ${name}` : `Скачивание из ${name}`}
          className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full bg-info/15 px-2 py-0.5 text-[11px] text-info">
          <ServiceBadge service={r.provider} iconOnly />
          {checking ? 'проверка' : `${Math.round(r.progress * 100)}%`}
        </span>
      );
    }
    return <span role="status" aria-label="Подготовка поиска" className="hm-spin h-4 w-4 shrink-0 rounded-full border-2 border-info border-r-transparent motion-reduce:animate-none" />;
  }
  if (r.state === 'queued' && r.kind === 'identify') return pill('в очереди · уточняю запись', 'bg-fg/5 text-muted', 'Скачаю запись, которую предлагает каталог, и сравню с вашим файлом по звуку');
  if (r.state === 'queued') return pill(r.known ? 'в очереди' : 'в очереди · нет в каталоге', 'bg-fg/5 text-muted', r.known ? undefined : 'Каталог не знает эту песню: ищу по названию из файла, это дольше и реже удаётся');
  return (
    <span className="flex min-w-0 shrink items-center gap-2">
      <span className="hidden min-w-0 truncate text-[11px] text-dim sm:block" title={r.note ?? undefined}>
        {r.state === 'kept' ? 'вы оставили свой файл' : r.note && <ServiceText text={r.note} />}
      </span>
      {pill(was, 'bg-fg/5 text-muted')}
    </span>
  );
}

/** The two files of a track — the one it has and the one found — heard one at a time, each from where its picture
 * is tapped. */
function useTwoFiles() {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [on, setOn] = useState<{ key: string; at: number } | null>(null);
  useEffect(() => () => audio.current?.pause(), []);
  const stop = () => {
    audio.current?.pause();
    setOn(null);
  };
  /** `from`: where to start, 0…1 of the file (none: from where it stopped, or the start) */
  const play = (key: string, src: string, from?: number) => {
    const a = (audio.current ??= new Audio());
    if (on?.key === key && from === undefined) return stop();
    if (on?.key !== key) {
      a.src = src;
      a.ontimeupdate = () => setOn((cur) => (cur ? { ...cur, at: a.duration ? a.currentTime / a.duration : 0 } : cur));
      a.onended = () => setOn(null);
      a.onerror = () => {
        setOn(null);
        toast('Не удалось воспроизвести', 'error');
      };
    }
    const seek = () => {
      if (from !== undefined && a.duration) a.currentTime = from * a.duration;
    };
    if (a.readyState >= 1) seek();
    else a.onloadedmetadata = seek;
    setOn({ key, at: from ?? 0 });
    void a.play().catch(() => setOn(null));
  };
  return { on, play, stop };
}

function RecordTitle({ row }: { row: QualityRow }) {
  if (!row.record_title) return row.record;
  return <><TrackTitle title={row.record_title} track={{ ref: row.record_ref }} />{row.record?.slice(row.record_title.length)}</>;
}

function Compare({ r, listening, busy, onYes, onNo, onVersion, onTake }: { r: QualityRow; listening: ReturnType<typeof useTwoFiles>; busy: boolean; onYes: () => void; onNo: () => void; /** «is it that record?» has a third answer: another version of the same song */ onVersion?: () => void; /** …and yes with the file found taken too, where it is the better one */ onTake?: () => void }) {
  const peaks = useAsync((s) => api.qualityPeaks(r.id, s), [r.id]);
  const done = r.state === 'replaced'; // replaced already: the file it had beside the one it has, the new one taken
  const [more, setMore] = useState(false); // the answers that are hardly the case, where the sound says «the same recording»
  // the second file: one that may take the place of the track's own — or, where it is no better and the question
  // is of the record, one fetched only to compare the sound by
  const foundWord = r.kind === 'identify' && !r.better ? 'Для сверки' : 'Найден';
  // where the two files part though they are one recording
  const gaps = peaks.data?.gaps ?? [];
  type Which = 'current' | 'found' | 'was';
  const [upper, lower]: [Which, Which] = done ? ['was', 'current'] : ['current', 'found'];
  const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  const lengthOf = (which: Which) => peaks.data?.length?.[which] ?? r.duration_s ?? 0;
  const hear = (which: Which, at: number) => {
    const len = lengthOf(which);
    if (len) listening.play(`${r.id}:${which}`, `/api/quality/${r.id}/audio/${which}`, Math.min(0.999, Math.max(0, (at - 2) / len)));
  };
  const lane = (which: Which, label: string, words: string, good: boolean, taken = false) => {
    const key = `${r.id}:${which}`;
    const src = `/api/quality/${r.id}/audio/${which}`;
    const playing = listening.on?.key === key;
    const bars = peaks.data?.[which] ?? [];
    const len = lengthOf(which);
    const marked = (i: number) => {
      const t = ((i + 0.5) / bars.length) * len;
      return !!len && gaps.some((g) => (which === upper ? t >= g.at && t <= g.to : t >= g.other_at && t <= g.other_to));
    };
    return (
      <div className="flex items-center gap-3 py-1">
        <span className="w-[4.5rem] shrink-0 text-[11px] text-dim">{label}</span>
        <button type="button" aria-label={playing ? 'Пауза' : `Слушать: ${label.toLowerCase()}`} onClick={() => listening.play(key, src)} className={cx('flex h-8 w-8 shrink-0 items-center justify-center rounded-full border transition hover:scale-105', playing ? 'border-accent text-accent' : 'border-line text-fg')}>
          <Icon name={playing ? 'pause' : 'play'} size={14} filled className={playing ? '' : 'translate-x-px'} />
        </button>
        <button
          type="button"
          aria-label={`Перемотать: ${label.toLowerCase()}`}
          title="Нажмите, чтобы слушать с этого места"
          className="flex h-9 min-w-0 flex-1 cursor-pointer items-center gap-px"
          onClick={(e) => {
            const box = e.currentTarget.getBoundingClientRect();
            listening.play(key, src, Math.min(0.999, Math.max(0, (e.clientX - box.left) / box.width)));
          }}
        >
          {bars.length ? (
            bars.map((v, i) => <span key={i} className={cx('min-w-px flex-1 rounded-sm', marked(i) ? 'bg-danger' : playing && i / bars.length <= (listening.on?.at ?? 0) ? 'bg-accent' : 'bg-fg/30')} style={{ height: `${Math.max(6, v)}%` }} />)
          ) : (
            <span className="h-1 w-full rounded bg-fg/10" />
          )}
        </button>
        <span className={cx('w-20 shrink-0 whitespace-nowrap rounded-full px-2 py-0.5 text-center text-[11px]', good ? 'bg-accent/15 text-accent' : 'bg-fg/10 text-muted')}>{words}</span>
        <span className="w-16 shrink-0 text-[11px] text-accent">{taken ? '✓ выбран' : ''}</span>
      </div>
    );
  };
  return (
    <div className="pt-2 md:pl-[3.25rem]">
      {r.kind === 'identify' && !done && (
        <p className="pb-1 text-xs text-muted">
          Каталог считает, что это «<RecordTitle row={r} />».{' '}
          {r.better
            ? <>Найденный файл{r.provider && <> (<ServiceBadge service={r.provider} />)</>} лучше вашего по качеству — взять его можно отдельной кнопкой.</>
            : <>Файл для сверки скачан{r.provider && <> с <ServiceBadge service={r.provider} /></>}: он нужен только для этого сравнения, ваш файл останется.</>}
        </p>
      )}
      {done ? lane('was', 'Был', fileWords(r.was_codec, r.was_kbps), false) : lane('current', 'Сейчас', fileWords(r.was_codec, r.was_kbps), false)}
      {done ? lane('current', 'Стал', fileWords(r.codec, r.bitrate_kbps), true, true) : lane('found', foundWord, fileWords(r.codec, r.bitrate_kbps), !!r.better)}
      {gaps.length > 0 && (
        <div className="pt-1 text-xs">
          <p className="pb-1 text-danger">Файлы расходятся в {gaps.length === 1 ? 'одном месте' : `${gaps.length} местах`}. Что там, слышно только на слух — послушайте оба:</p>
          <ul className="space-y-1">
            {gaps.map((g) => (
              <li key={g.at} className="flex flex-wrap items-center gap-2">
                <span className="w-24 tabular-nums text-fg">{clock(g.at)}–{clock(g.to)}</span>
                <button type="button" onClick={() => hear(upper, g.at)} className="rounded-full bg-fg/10 px-2 py-0.5 hover:bg-fg/20">▶ {done ? 'был' : 'сейчас'}</button>
                <button type="button" onClick={() => hear(lower, g.other_at)} className="rounded-full bg-fg/10 px-2 py-0.5 hover:bg-fg/20">▶ {done ? 'стал' : foundWord.toLowerCase()}</button>
                {g.quieter && <span className="text-muted">здесь тише файл «{g.quieter === 'upper' ? (done ? 'был' : 'сейчас') : done ? 'стал' : foundWord.toLowerCase()}»</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {done && gaps.length === 0 && !peaks.loading && <p className="pt-1 text-xs text-muted">Отличающихся мест не нашлось. Очень короткое расхождение сверка может не заметить — если сомневаетесь, послушайте. Прежний файл хранится 30 дней.</p>}
      <div className="flex flex-wrap items-center gap-2 pt-2">
        {done ? (
          <>
            <Button small variant="primary" onClick={onYes}>
              Оставить новый
            </Button>
            <Button small busy={busy} onClick={onNo}>
              Вернуть мой файл
            </Button>
          </>
        ) : r.kind === 'identify' ? (
          <>
            {/* yes is of the record: the track keeps its file unless the listener says the other thing — and that is
                offered only where the file found is the better one. Where the sound says «the same recording» the
                yes stands out, and the other answers are put away: they are hardly the case */}
            <Button small variant={r.sure ? 'primary' : undefined} busy={busy} onClick={onYes}>
              Это она · файл оставить мой
            </Button>
            {onTake && (
              <Button small disabled={busy} onClick={onTake} className="!border !border-danger/60 !text-danger" title="Трек получит данные каталога, а его файл заменится найденным. Прежний хранится 30 дней">
                Это она · взять найденный файл
              </Button>
            )}
            {r.sure && !more ? (
              <button type="button" className="px-2 text-sm text-muted hover:text-fg" onClick={() => setMore(true)}>
                ещё…
              </button>
            ) : (
              <>
                {onVersion && (
                  <Button small disabled={busy} onClick={onVersion} title="Трек останется как есть и будет связан с записью каталога: это разные записи одной песни">
                    Другая версия той же песни
                  </Button>
                )}
                <Button small disabled={busy} onClick={onNo}>
                  Нет, другая песня
                </Button>
              </>
            )}
          </>
        ) : (
          <>
            {/* a better file of a weak one that the Hub did not take by itself: the two differ, or are only alike —
                keeping one's own is the safe word, replacing is said on purpose */}
            <Button small variant={r.sure ? 'primary' : undefined} busy={busy} onClick={onNo}>
              Оставить мой
            </Button>
            <Button small disabled={busy} onClick={onYes} className="!border !border-danger/60 !text-danger" title="Файл трека заменится найденным. Прежний хранится 30 дней">
              Заменить всё равно
            </Button>
          </>
        )}
        <span className="min-w-0 truncate text-[11px] text-dim">
          {r.provider && <ServiceBadge service={r.provider} />}
          {r.provider && (r.duration_s || r.note) ? ' · ' : ''}
          <ServiceText text={[r.duration_s ? fmtDuration(r.duration_s) : null, r.note].filter(Boolean).join(' · ')} />
        </span>
      </div>
    </div>
  );
}

