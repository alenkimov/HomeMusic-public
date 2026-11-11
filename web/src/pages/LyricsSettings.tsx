import { useState } from 'react';
import { api } from '../api/client';
import { Button, ErrorState, IconButton, Loading, PageHeader, cx } from '../components/ui';
import { useAsync, useInterval } from '../lib/hooks';
import type { LyricsSettings } from '../lib/lyrics';
import { attempt, toast } from '../lib/ui';

/** The admin's «Тексты песен»: which sources the Hub asks for lyrics and in what order (the first that has a text
 * wins), how many of the library's tracks have a text in the database, when a miss is looked for again, and the
 * search for the whole library. The sources are the server's: every user gets the texts they find. */

const HINTS: Record<string, { text: string; tag: string; warn?: boolean }> = {
  file: { text: 'Текст, вшитый в сам файл или лежащий рядом (.lrc)', tag: 'всегда доступен' },
  lrclib: { text: 'Построчные и частично пословные тексты, без ключа', tag: 'открытая база' },
  netease: { text: 'Сильная база азиатской музыки, есть пословные «караоке»', tag: 'неофициальный API', warn: true },
  ovh: { text: 'Только обычный текст без таймингов — последний запасной', tag: 'открытый' },
};

export function LyricsSettingsPage() {
  const res = useAsync((s) => api.lyricsSettings(s), []);
  const [busy, setBusy] = useState(false);
  const [scanning, setScanning] = useState(false);
  const v = res.data;
  // while the search runs the counters move
  useInterval(() => res.reload(true), scanning ? 4000 : null);
  if (scanning && v && v.stats.unchecked === 0) setScanning(false);

  const save = async (next: LyricsSettings) => {
    res.setData(next);
    setBusy(true);
    const r = await attempt(() => api.lyricsSaveSettings({ sources: next.sources.map(({ id, on }) => ({ id, on })), retry_days: next.retry_days, on_add: next.on_add }));
    setBusy(false);
    if (r) res.setData(r);
    else res.reload(true);
  };
  const move = (i: number, by: number) => {
    if (!v) return;
    const sources = [...v.sources];
    const j = i + by;
    if (j < 0 || j >= sources.length) return;
    [sources[i], sources[j]] = [sources[j], sources[i]];
    void save({ ...v, sources });
  };
  const scan = async () => {
    const r = await attempt(() => api.lyricsScan());
    if (r) {
      res.setData(r);
      setScanning(r.stats.unchecked > 0);
      toast(r.stats.unchecked ? `Ищу тексты для ${r.stats.unchecked} треков в фоне` : 'Все треки уже проверены', 'success');
    }
  };

  return (
    <div className="mx-auto max-w-3xl pb-8">
      <PageHeader title="Тексты песен" back="/more" />
      {res.loading ? (
        <Loading />
      ) : res.error || !v ? (
        <ErrorState text={res.error ?? 'Не удалось загрузить'} onRetry={() => res.reload()} />
      ) : (
        <>
          <p className="px-4 pb-3 text-sm text-muted">Хаб спрашивает источники сверху вниз и берёт первый найденный текст с таймингами. Найденное хранится в Хабе и открывается сразу, без интернета.</p>
          <ul className="space-y-2 px-4" aria-label="Источники текстов">
            {v.sources.map((s, i) => {
              const h = HINTS[s.id];
              return (
                <li key={s.id} className={cx('card flex items-center gap-3 p-3', !s.on && 'opacity-55')}>
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-raised text-xs text-muted">{i + 1}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 font-semibold">
                      {s.title}
                      {h && <span className={cx('rounded-full px-2 py-0.5 text-[11px] font-normal', h.warn ? 'bg-warn/15 text-warn' : 'bg-accent/15 text-accent')}>{h.tag}</span>}
                    </div>
                    {h && <div className="text-xs text-muted">{h.text}</div>}
                  </div>
                  <IconButton icon="back" size={18} label={`Выше: ${s.title}`} disabled={busy || i === 0} onClick={() => move(i, -1)} className="!h-9 !w-9 rotate-90 text-muted" />
                  <IconButton icon="back" size={18} label={`Ниже: ${s.title}`} disabled={busy || i === v.sources.length - 1} onClick={() => move(i, 1)} className="!h-9 !w-9 -rotate-90 text-muted" />
                  <Toggle on={s.on} label={`Источник ${s.title}`} disabled={busy} onChange={(on) => void save({ ...v, sources: v.sources.map((x) => (x.id === s.id ? { ...x, on } : x)) })} />
                </li>
              );
            })}
          </ul>

          <div className="grid gap-2 px-4 pt-4 md:grid-cols-3">
            <div className="card p-3">
              <div className="text-xs text-muted">В базе Хаба</div>
              <div className="text-xl font-bold">
                {v.stats.word + v.stats.synced + v.stats.plain} из {v.stats.total}
              </div>
              <div className="text-xs text-dim">
                по словам {v.stats.word} · по строкам {v.stats.synced} · без таймингов {v.stats.plain}
                <br />
                не найдено {v.stats.none} · не проверено {v.stats.unchecked}
              </div>
            </div>
            <div className="card p-3">
              <div className="text-xs text-muted">Повторять поиск ненайденных</div>
              <select className="input mt-2" value={v.retry_days} disabled={busy} onChange={(e) => void save({ ...v, retry_days: Number(e.target.value) })} aria-label="Как часто повторять поиск">
                <option value={7}>раз в неделю</option>
                <option value={30}>раз в месяц</option>
                <option value={0}>никогда</option>
              </select>
            </div>
            <div className="card flex items-center justify-between gap-3 p-3">
              <div>
                <div className="text-xs text-muted">Искать при добавлении трека</div>
                <div className="text-sm">{v.on_add ? 'включено' : 'выключено'}</div>
              </div>
              <Toggle on={v.on_add} label="Искать текст при добавлении трека" disabled={busy} onChange={(on_add) => void save({ ...v, on_add })} />
            </div>
          </div>

          <div className="px-4 pt-4">
            <Button block icon="refresh" busy={scanning} disabled={!v.stats.unchecked && !scanning} onClick={scan}>
              {v.stats.unchecked ? `Найти тексты для непроверенных треков (${v.stats.unchecked})` : 'Все треки проверены'}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

function Toggle({ on, label, disabled, onChange }: { on: boolean; label: string; disabled?: boolean; onChange: (on: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={cx('relative h-6 w-10 shrink-0 rounded-full transition-colors disabled:opacity-60', on ? 'bg-accent' : 'bg-fg/20')}
    >
      <span className={cx('absolute top-0.5 h-5 w-5 rounded-full bg-bg transition-[left]', on ? 'left-[18px]' : 'left-0.5')} />
    </button>
  );
}
