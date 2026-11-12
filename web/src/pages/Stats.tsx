import { useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { NdSongOut } from '../api/types';
import { playAll } from '../components/catalog';
import { Icon, type IconName } from '../components/Icon';
import { Button, Cover, Empty, ErrorState, Loading, Notice, PageHeader, ProgressBar, Segmented, Sheet, cx } from '../components/ui';
import { WEEKDAYS_SHORT, fmtListened, fmtNum, fmtPct, plural } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { enc, navigate } from '../lib/router';
import { refreshPlayCounts, type StatsBy, type StatsImportOut, type StatsItem, type StatsKind, type StatsOut, type StatsPeriod } from '../lib/stats';
import { useTags } from '../lib/tags';
import { attempt, confirmDialog } from '../lib/ui';

const PERIODS: Array<{ value: StatsPeriod; label: string }> = [
  { value: 'week', label: 'Неделя' },
  { value: 'month', label: 'Месяц' },
  { value: 'halfyear', label: 'Полгода' },
  { value: 'year', label: 'Год' },
  { value: 'all', label: 'Всё время' },
];
const BY: Array<{ value: StatsBy; label: string }> = [
  { value: 'plays', label: 'По прослушиваниям' },
  { value: 'time', label: 'По времени' },
];
const KINDS: Array<{ kind: StatsKind; title: string; icon: IconName; round?: boolean }> = [
  { kind: 'tracks', title: 'Треки', icon: 'note' },
  { kind: 'artists', title: 'Исполнители', icon: 'user', round: true },
  { kind: 'genres', title: 'Жанры', icon: 'tag' },
  { kind: 'albums', title: 'Альбомы', icon: 'disc' },
];
const TOP = 5;
const ALL = 500;

const playsWord = (n: number) => `${fmtNum(n)} ${plural(n, ['прослушивание', 'прослушивания', 'прослушиваний'])}`;
const daysWord = (n: number) => `${n} ${plural(n, ['день', 'дня', 'дней'])}`;

/** «Статистика»: what was listened to in a period — the sums, the tops, when. Both switches act on the whole page. */
export function StatsPage() {
  const [period, setPeriod] = useState<StatsPeriod>('month');
  const [by, setBy] = useState<StatsBy>('plays');
  const [all, setAll] = useState<StatsKind | null>(null);
  const [importing, setImporting] = useState(false);
  const stats = useAsync((s) => api.stats({ period, by, limit: TOP }, s), [period, by]);
  const songs = useAsync((s) => api.ndSongs(s), []);
  const bySong = useMemo(() => new Map((songs.data ?? []).map((t) => [t.nd_song_id, t] as const)), [songs.data]);
  const d = stats.data;

  const forget = async () => {
    if (!(await confirmDialog({ title: 'Удалить импортированную историю?', body: `${playsWord(d?.imported ?? 0)} из Spotify исчезнут из статистики. То, что вы слушали здесь, останется.`, okLabel: 'Удалить', danger: true }))) return;
    if (await attempt(() => api.statsForget(), 'Импортированная история удалена')) {
      stats.reload(true);
      void refreshPlayCounts();
    }
  };

  return (
    <div className="pb-8">
      <PageHeader title="Статистика" back="/more" sub={d?.first_at ? `Данные с ${new Date(d.first_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })}` : undefined} />
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 pb-4">
        <Segmented value={period} options={PERIODS} onChange={setPeriod} label="Период" />
        <Segmented value={by} options={BY} onChange={setBy} label="Как считать" />
      </div>
      {stats.error && !d ? (
        <ErrorState text={stats.error} onRetry={() => stats.reload()} />
      ) : !d ? (
        <Loading />
      ) : (
        <div className={cx('space-y-4 px-4 transition-opacity', stats.loading && 'opacity-60')}>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Tile label="Прослушано" value={fmtListened(d.summary.ms)} />
            <Tile label="Прослушиваний" value={fmtNum(d.summary.plays)} />
            <Tile label="Разных треков" value={fmtNum(d.summary.tracks)} />
            <Tile label="Дней подряд" value={String(d.summary.streak)} hint="С прослушиванием, считая сегодня" />
            <Tile label="Пропуски" value={fmtPct(d.summary.skips, d.summary.attempts)} hint="Треки, выключенные раньше 30 секунд" />
          </div>
          {!d.summary.attempts ? (
            <Empty icon="history" title="За этот период прослушиваний нет" text="Выберите период подольше или импортируйте историю из Spotify." />
          ) : (
            <>
              <div className="grid gap-4 lg:grid-cols-2">
                {KINDS.map((k) => (
                  <TopCard key={k.kind} {...k} top={d[k.kind]} by={by} bySong={bySong} onAll={() => setAll(k.kind)} />
                ))}
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <Card title="По часам суток">
                  <Bars values={d.hours} labels={d.hours.map((_, h) => (h % 3 ? '' : String(h)))} by={by} name={(h) => `${h}:00–${h}:59`} />
                </Card>
                <Card title="По дням недели">
                  <Bars values={d.weekdays} labels={WEEKDAYS_SHORT} by={by} name={(i) => WEEKDAYS_SHORT[i]} />
                </Card>
              </div>
            </>
          )}
          <Card title="Последние 26 недель">
            <Weeks heat={d.heatmap} by={by} />
          </Card>
          <div className="flex flex-wrap items-center gap-3 rounded-xl bg-surface p-4">
            <Icon name="transfer" size={22} className="text-muted" />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold">История из Spotify</div>
              <div className="text-xs text-muted">
                {d.imported ? `Импортировано: ${playsWord(d.imported)}` : 'Что вы слушали в Spotify за все годы — в эту же статистику'}
              </div>
            </div>
            {d.imported > 0 && (
              <Button small onClick={() => void forget()}>
                Удалить импортированное
              </Button>
            )}
            <Button small variant="primary" icon="upload" onClick={() => setImporting(true)}>
              Импортировать историю
            </Button>
          </div>
        </div>
      )}
      {all && <AllSheet kind={all} period={period} by={by} bySong={bySong} onClose={() => setAll(null)} />}
      {importing && (
        <ImportSheet
          onClose={() => setImporting(false)}
          onDone={() => {
            stats.reload(true);
            void refreshPlayCounts();
          }}
        />
      )}
    </div>
  );
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl bg-surface p-4" title={hint}>
      <div className="text-xs text-muted">{label}</div>
      <div className="pt-1 text-2xl font-bold tabular-nums">{value}</div>
    </div>
  );
}

function Card({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-xl bg-surface p-4">
      <div className="flex items-center pb-3">
        <h2 className="flex-1 text-sm font-bold">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

const amount = (x: StatsItem, by: StatsBy) => (by === 'time' ? x.ms : x.plays);
const shown = (v: number, by: StatsBy) => (by === 'time' ? fmtListened(v) : fmtNum(v));

/** The rows of a top: the place, the picture, the name over a bar as long as its share of the first one. A track is
 * played by a click, an artist, an album and a genre open their pages; what the library has not got is grey. */
function Rows({ kind, items, by, bySong, icon, round }: { kind: StatsKind; items: StatsItem[]; by: StatsBy; bySong: Map<string | null | undefined, NdSongOut>; icon: IconName; round?: boolean }) {
  const tags = useTags();
  const max = Math.max(1, ...items.map((x) => amount(x, by)));
  const act = (x: StatsItem): (() => void) | null => {
    const song = x.nd_song_id ? bySong.get(x.nd_song_id) : undefined;
    if (kind === 'genres') {
      const tag = tags.groups.find((g) => g.id === 'genre')?.tags.find((t) => t.name === x.name);
      return tag ? () => navigate(`/selection/library?yes=${tag.id}`) : null;
    }
    if (!song) return null;
    if (kind === 'tracks') return () => playAll([song], { context: 'stats', page: '/stats' });
    if (kind === 'artists') return song.nd_artist_id ? () => navigate(`/lib/artist/${enc(song.nd_artist_id!)}`) : null;
    return song.nd_album_id ? () => navigate(`/lib/album/${enc(song.nd_album_id!)}`) : null;
  };
  return (
    <ol>
      {items.map((x, i) => {
        const go = act(x);
        const song = x.nd_song_id ? bySong.get(x.nd_song_id) : undefined;
        const outside = kind !== 'genres' && !x.nd_song_id;
        const body = (
          <>
            <span className="w-5 shrink-0 text-right text-xs tabular-nums text-dim">{i + 1}</span>
            {kind !== 'genres' && (
              <span className="relative shrink-0">
                <Cover src={song?.cover_url} size={36} round={round} icon={icon} className={cx(outside && 'opacity-50')} />
                {kind === 'tracks' && go && (
                  <span className="absolute inset-0 flex items-center justify-center rounded-md bg-black/55 text-white opacity-0 transition group-hover:opacity-100">
                    <Icon name="play" size={16} filled />
                  </span>
                )}
              </span>
            )}
            <span className="min-w-0 flex-1">
              <span className="flex items-baseline gap-2">
                <span className={cx('truncate text-sm', outside ? 'text-muted' : 'text-fg')}>{x.name}</span>
                {x.sub && <span className="truncate text-xs text-dim">{x.sub}</span>}
              </span>
              <span className="mt-1 block h-1 overflow-hidden rounded-full bg-fg/10">
                <span className={cx('block h-full rounded-full', outside ? 'bg-fg/30' : 'bg-accent')} style={{ width: `${Math.max(2, (amount(x, by) / max) * 100)}%` }} />
              </span>
            </span>
            <span className="w-16 shrink-0 text-right text-sm tabular-nums text-muted">{shown(amount(x, by), by)}</span>
          </>
        );
        const title = `${playsWord(x.plays)} · ${fmtListened(x.ms)}${outside ? ' · нет в библиотеке' : ''}`;
        const cls = 'group flex w-full items-center gap-3 rounded-lg px-1 py-1.5 text-left';
        return (
          <li key={`${x.name}|${x.sub ?? ''}|${i}`}>
            {go ? (
              <button type="button" onClick={go} title={title} className={cx(cls, 'hover:bg-fg/5')}>
                {body}
              </button>
            ) : (
              <div title={title} className={cls}>
                {body}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function TopCard({ kind, title, icon, round, top, by, bySong, onAll }: { kind: StatsKind; title: string; icon: IconName; round?: boolean; top: StatsOut[StatsKind]; by: StatsBy; bySong: Map<string | null | undefined, NdSongOut>; onAll: () => void }) {
  return (
    <Card
      title={title}
      right={
        top.total > top.items.length && (
          <button type="button" onClick={onAll} className="text-xs text-muted hover:text-fg hover:underline">
            Показать все ({top.total})
          </button>
        )
      }
    >
      {top.items.length ? (
        <Rows kind={kind} items={top.items} by={by} bySong={bySong} icon={icon} round={round} />
      ) : (
        <p className="py-4 text-center text-xs text-dim">{kind === 'genres' ? 'У прослушанных треков нет жанра' : 'Пока пусто'}</p>
      )}
    </Card>
  );
}

function AllSheet({ kind, period, by, bySong, onClose }: { kind: StatsKind; period: StatsPeriod; by: StatsBy; bySong: Map<string | null | undefined, NdSongOut>; onClose: () => void }) {
  const full = useAsync((s) => api.stats({ period, by, limit: ALL }, s), [period, by]);
  const k = KINDS.find((x) => x.kind === kind)!;
  const top = full.data?.[kind];
  return (
    <Sheet open onClose={onClose} title={`${k.title} — ${PERIODS.find((p) => p.value === period)!.label.toLowerCase()}`}>
      {!top ? full.error ? <ErrorState text={full.error} /> : <Loading /> : <Rows kind={kind} items={top.items} by={by} bySong={bySong} icon={k.icon} round={k.round} />}
      {top && top.total > top.items.length && <p className="pt-2 text-center text-xs text-dim">Показаны первые {top.items.length} из {top.total}</p>}
    </Sheet>
  );
}

function Bars({ values, labels, by, name }: { values: number[]; labels: string[]; by: StatsBy; name: (i: number) => string }) {
  const max = Math.max(1, ...values);
  return (
    <div>
      <div className="flex h-28 items-end gap-1">
        {values.map((v, i) => (
          <div key={i} className="flex h-full flex-1 items-end" title={`${name(i)}: ${by === 'time' ? fmtListened(v) : playsWord(v)}`}>
            <div className={cx('w-full rounded-t', v ? 'bg-accent' : 'bg-fg/10')} style={{ height: v ? `${Math.max(4, (v / max) * 100)}%` : 2 }} />
          </div>
        ))}
      </div>
      <div className="flex gap-1 pt-1">
        {labels.map((l, i) => (
          <span key={i} className="flex-1 text-center text-[10px] text-dim">
            {l}
          </span>
        ))}
      </div>
    </div>
  );
}

const dayKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/** Day by day, a column a week (Monday on top): the brighter, the more was listened to. */
function Weeks({ heat, by }: { heat: StatsOut['heatmap']; by: StatsBy }) {
  const weeks = useMemo(() => {
    const [y, m, d] = heat.from.split('-').map(Number);
    const out: Date[][] = [];
    const day = new Date(y, m - 1, d);
    while (dayKey(day) <= heat.to) {
      if (!out.length || out[out.length - 1].length === 7) out.push([]);
      out[out.length - 1].push(new Date(day));
      day.setDate(day.getDate() + 1);
    }
    return out;
  }, [heat.from, heat.to]);
  const max = Math.max(1, ...Object.values(heat.days));
  const active = Object.keys(heat.days).length;
  return (
    <div>
      <div className="flex gap-[3px] overflow-x-auto no-scrollbar">
        {weeks.map((w, i) => (
          <div key={i} className="flex flex-1 flex-col gap-[3px]" style={{ minWidth: 10, maxWidth: 22 }}>
            {w.map((day) => {
              const v = heat.days[dayKey(day)] ?? 0;
              return (
                <div
                  key={day.getTime()}
                  className="aspect-square rounded-[3px]"
                  style={{ background: v ? 'var(--color-accent)' : 'color-mix(in srgb, var(--color-fg) 8%, transparent)', opacity: v ? 0.25 + 0.75 * (v / max) : 1 }}
                  title={`${day.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}: ${v ? (by === 'time' ? fmtListened(v) : playsWord(v)) : 'ничего'}`}
                />
              );
            })}
          </div>
        ))}
      </div>
      <p className="pt-2 text-xs text-dim">{active ? `Слушали в ${daysWord(active)} из ${weeks.flat().length}` : 'За эти недели прослушиваний нет'}</p>
    </div>
  );
}

/** «Импортировать историю»: the archive Spotify sends on request (or the .json files of it) goes to the Hub, which
 * says what became of it. */
function ImportSheet({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [progress, setProgress] = useState<number | null>(null);
  const [result, setResult] = useState<StatsImportOut | null>(null);
  const busy = progress !== null;
  const send = async () => {
    setProgress(0);
    const r = await attempt(() => api.statsImport(files, setProgress));
    setProgress(null);
    if (!r) return;
    setResult(r);
    onDone();
  };
  return (
    <Sheet
      open
      onClose={onClose}
      title="История из Spotify"
      footer={
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{result ? 'Готово' : 'Отмена'}</Button>
          {!result && (
            <Button variant="primary" busy={busy} disabled={!files.length} onClick={() => void send()}>
              Импортировать
            </Button>
          )}
        </div>
      }
    >
      {result ? (
        <div className="space-y-3">
          <Notice kind="info">{result.added ? `Добавлено: ${playsWord(result.added)}` : 'Новых прослушиваний нет — всё это уже было импортировано.'}</Notice>
          <ul className="space-y-1.5 text-sm">
            <Line label="Найдено в файлах" value={result.found} />
            <Line label="Уже было" value={result.duplicates} />
            <Line label="Привязано к трекам библиотеки" value={result.linked} />
            <Line label="Только в статистике (трека нет в библиотеке)" value={result.outside} />
            <Line label="Из добавленных — дольше 30 секунд" value={result.listens} />
          </ul>
        </div>
      ) : (
        <div className="space-y-3 text-sm">
          <p className="text-muted">
            Запросите у Spotify «Расширенную историю стриминга»: аккаунт → «Конфиденциальность» → «Загрузка ваших данных». Архив приходит на почту — обычно за несколько дней, иногда до 30.
          </p>
          <p className="text-muted">Сюда можно отдать архив целиком (.zip) или файлы Streaming_History_Audio_….json из него. Повторный импорт дубликатов не создаёт.</p>
          <input ref={input} type="file" multiple accept=".zip,.json,application/zip,application/json" className="hidden" onChange={(e) => setFiles([...(e.target.files ?? [])])} />
          <Button icon="upload" block disabled={busy} onClick={() => input.current?.click()}>
            {files.length ? 'Выбрать другие файлы' : 'Выбрать файлы'}
          </Button>
          {files.length > 0 && <p className="truncate text-xs text-dim">{files.map((f) => f.name).join(', ')}</p>}
          {busy && (
            <div className="space-y-1">
              <ProgressBar value={progress ?? 0} />
              <p className="text-xs text-dim">{(progress ?? 0) < 1 ? 'Загрузка…' : 'Хаб разбирает историю…'}</p>
            </div>
          )}
        </div>
      )}
    </Sheet>
  );
}

function Line({ label, value }: { label: string; value: number }) {
  return (
    <li className="flex items-baseline gap-3">
      <span className="flex-1 text-muted">{label}</span>
      <span className="tabular-nums">{fmtNum(value)}</span>
    </li>
  );
}
