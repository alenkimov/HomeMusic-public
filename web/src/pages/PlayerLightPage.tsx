import { useState } from 'react';
import { PlayerLight } from '../components/PlayerLight';
import { Button, Notice, PageHeader, SectionTitle, cx } from '../components/ui';
import { useListen } from '../lib/listen';
import { type PlayerLight as Look, setPlayerLight, usePlayerLight } from '../lib/playerLight';

const LOOKS: { value: Look; title: string; text: string }[] = [
  { value: 'blur', title: 'Размытая обложка', text: 'Три размытые копии обложки медленно плывут. Размытие видеокарта пересчитывает в каждом кадре.' },
  { value: 'light', title: 'Лёгкий', text: 'Три мягких пятна цветов обложки. Рисуются почти даром.' },
];
const SECONDS = 8;
const WARMUP_MS = 1000;

interface Result {
  look: Look;
  fps: number;
  late: number; // the share of frames late by more than half a frame
  worst: number; // ms
}

/** Frames counted while `look` is drawn over everything for `SECONDS`: how many a second, how many came late. A
 * browser says nothing of the battery; frames a GPU did not manage in time are what it can tell. */
function measure(): Promise<{ fps: number; late: number; worst: number }> {
  return new Promise((resolve) => {
    const times: number[] = [];
    const start = performance.now();
    const tick = (t: number) => {
      if (t - start > WARMUP_MS) times.push(t);
      if (t - start < WARMUP_MS + SECONDS * 1000) requestAnimationFrame(tick);
      else {
        const gaps = times.slice(1).map((x, i) => x - times[i]);
        const sorted = [...gaps].sort((a, b) => a - b);
        const frame = sorted[Math.floor(sorted.length / 2)] || 16.7; // the screen's own frame (60 or 120 Hz)
        resolve({
          fps: Math.round((gaps.length / ((times[times.length - 1] - times[0]) / 1000)) * 10) / 10,
          late: gaps.length ? gaps.filter((g) => g > frame * 1.5).length / gaps.length : 0,
          worst: Math.round(sorted[sorted.length - 1] ?? 0),
        });
      }
    };
    requestAnimationFrame(tick);
  });
}

/** «Фон плеера» («Меню» → «Это устройство»): which light the full player has on this device, and a measure of both. */
export function PlayerLightPage() {
  const look = usePlayerLight();
  const track = useListen((s) => s.track);
  const cover = track?.cover_small_url || track?.cover_url || '/icons/icon-192.png';
  const [running, setRunning] = useState<Look | null>(null);
  const [results, setResults] = useState<Result[]>([]);

  const run = async () => {
    const out: Result[] = [];
    for (const l of ['blur', 'light'] as const) {
      setRunning(l);
      await new Promise((r) => setTimeout(r, 300)); // drawn before the counting starts
      out.push({ look: l, ...(await measure()) });
    }
    setRunning(null);
    setResults(out);
  };
  const titleOf = (l: Look) => LOOKS.find((x) => x.value === l)?.title ?? l;

  return (
    <div className="pb-10">
      <PageHeader title="Фон плеера" back="/more" />
      <div className="max-w-2xl space-y-3 px-4 lg:px-6">
        <p className="text-sm text-muted">Как выглядит фон полноэкранного плеера на этом устройстве. На других устройствах — свой выбор.</p>
        <ul className="overflow-hidden rounded-2xl bg-surface">
          {LOOKS.map((l) => (
            <li key={l.value} className="border-b border-line/60 last:border-0">
              <button type="button" onClick={() => setPlayerLight(l.value)} className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-fg/5">
                <span className={cx('mt-1 size-4 shrink-0 rounded-full border-2', look === l.value ? 'border-accent bg-accent' : 'border-muted')} />
                <span>
                  <span className="block font-semibold">{l.title}</span>
                  <span className="block text-sm text-muted">{l.text}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <SectionTitle page>Замер</SectionTitle>
      <div className="max-w-2xl space-y-3 px-4 lg:px-6">
        <p className="text-sm text-muted">
          Каждый вариант по {SECONDS} секунд показывается на весь экран, и считаются кадры: сколько в секунду и сколько опоздало.
          Расход батареи браузер узнать не даёт; опоздавшие кадры — это то, что видеокарта не успела, и чем их больше, тем тяжелее ей.
          На время замера ничего не трогайте.
        </p>
        <Button variant="primary" icon="chart" busy={running !== null} disabled={running !== null} onClick={() => void run()}>Замерить</Button>
        {results.length > 0 && (
          <>
            <table className="w-full overflow-hidden rounded-2xl bg-surface text-sm tabular-nums">
              <thead className="text-left text-xs text-dim">
                <tr className="border-b border-line/60">
                  <th className="px-4 py-2 font-normal">Вариант</th>
                  <th className="px-4 py-2 text-right font-normal">Кадров в секунду</th>
                  <th className="px-4 py-2 text-right font-normal">Опоздали</th>
                  <th className="px-4 py-2 text-right font-normal">Худший кадр</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => (
                  <tr key={r.look} className="border-b border-line/40 last:border-0">
                    <td className="px-4 py-2">{titleOf(r.look)}</td>
                    <td className="px-4 py-2 text-right">{r.fps}</td>
                    <td className={cx('px-4 py-2 text-right', r.late > 0.05 && 'text-danger')}>{(r.late * 100).toFixed(1)} %</td>
                    <td className="px-4 py-2 text-right">{r.worst} мс</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Notice kind="info">{verdict(results)}</Notice>
          </>
        )}
      </div>

      {running && (
        // the look measured, over everything, as the player draws it
        <div className="fixed inset-0 isolate z-[100] bg-bg">
          <PlayerLight cover={cover} look={running} />
          <div className="absolute inset-x-0 bottom-[calc(2rem+env(safe-area-inset-bottom))] text-center text-sm text-muted">
            Замер: «{titleOf(running)}»…
          </div>
        </div>
      )}
    </div>
  );
}

function verdict(rs: Result[]): string {
  const blur = rs.find((r) => r.look === 'blur');
  const light = rs.find((r) => r.look === 'light');
  if (!blur || !light) return '';
  if (blur.late <= 0.02 && blur.fps >= light.fps * 0.95)
    return 'Размытая обложка здесь идёт так же плавно, как лёгкий фон: видеокарта справляется. Разница в батарее, если и есть, небольшая.';
  if (blur.late <= 0.05)
    return 'Размытая обложка изредка роняет кадры: видеокарта работает на пределе. Если батарея садится заметно, выберите лёгкий фон.';
  return 'Размытая обложка заметно тормозит на этом устройстве: видеокарта не успевает. Здесь лучше лёгкий фон.';
}
