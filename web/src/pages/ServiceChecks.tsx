import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { CheckState, ServiceCheckOut, ServiceStateOut } from '../api/types';
import { Icon } from '../components/Icon';
import { Button, ErrorState, PageHeader, SkeletonRows, cx } from '../components/ui';
import { fmtWhen } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { attempt } from '../lib/ui';

/** «Проверка сервисов» (admins, from «Меню»): is each outside service the Hub depends on working, and answering as it
 * used to (`hub/app/checks`). A check is known by the real work (a download, a catalog page, a text found) or, where
 * the day did not show it, by the nightly check. The broken and the changed on top, open; the rest closed, a click
 * opens them. Each with its last 14 days. */
export function ServiceChecksPage() {
  const res = useAsync((s) => api.serviceChecks(s), [], true, 'admin:checks');
  const d = res.data;
  const [busy, setBusy] = useState(false);
  const running = !!d?.running || busy;
  // a run goes in the worker: asked again until it is over
  useEffect(() => {
    if (!d?.running) return;
    const t = window.setInterval(() => res.reload(true), 5000);
    return () => window.clearInterval(t);
  }, [d?.running, res]);
  const run = async () => {
    setBusy(true);
    const r = await attempt(() => api.runServiceChecks());
    setBusy(false);
    if (r) res.setData(r);
  };
  const count = (states: CheckState[]) => d?.services.filter((x) => states.includes(x.state)).length ?? 0;
  return (
    <div className="pb-10">
      <PageHeader
        title="Проверка сервисов"
        back="/more"
        sub={d ? `Каждую ночь в 4:00${d.checked_at ? ` · последняя ${fmtWhen(d.checked_at)}${d.took_s != null ? `, шла ${fmtTook(d.took_s)}` : ''}` : ''}` : undefined}
        right={<Button small icon="refresh" busy={running} onClick={() => void run()}>{running ? 'Проверяю…' : 'Проверить сейчас'}</Button>}
      />
      <div className="max-w-4xl px-4 lg:px-6">
        {!d ? (
          res.error ? <ErrorState text={res.error} onRetry={() => res.reload()} /> : <SkeletonRows n={8} />
        ) : (
          <>
            <div className="flex flex-wrap gap-2 pb-4">
              <Chip state="ok" n={count(['ok'])} word="работают" />
              {count(['changed', 'flaky']) > 0 && <Chip state="changed" n={count(['changed', 'flaky'])} word="отвечают иначе или сбоят" />}
              {count(['broken']) > 0 && <Chip state="broken" n={count(['broken'])} word={count(['broken']) === 1 ? 'сломан' : 'сломаны'} />}
              {count(['off']) > 0 && <Chip state="off" n={count(['off'])} word="не подключены" />}
              {count(['none']) > 0 && <Chip state="none" n={count(['none'])} word="ещё без данных" />}
            </div>
            <div className="space-y-3">
              {d.services.map((x) => <Service key={x.key} s={x} />)}
            </div>
            <p className="pt-4 text-xs text-dim">
              Сервис считается рабочим по реальной работе Хаба: скачанным трекам, открытым страницам, найденным текстам. Ночью проверяется только то,
              что за сутки не подтвердилось. Результаты хранятся 90 дней. Запросы проверки видны в «Прокси и запросы» с пометкой «ночная проверка».
            </p>
          </>
        )}
      </div>
    </div>
  );
}

const fmtTook = (s: number) => (s < 60 ? `${Math.round(s)} с` : `${Math.floor(s / 60)} мин ${Math.round(s % 60)} с`);

const STATE_WORD: Record<CheckState, string> = {
  ok: 'работает', flaky: 'сбоит', changed: 'отвечает иначе', broken: 'не работает', off: 'не подключён', none: 'ещё без данных',
};

/** The mark of a state: a green circle, a yellow square, a red diamond, an empty ring — told apart by the shape too. */
function Mark({ state, size = 12 }: { state: CheckState | 'ok' | 'flaky' | 'broken' | 'none'; size?: number }) {
  const look =
    state === 'ok' ? 'rounded-full bg-accent'
      : state === 'broken' ? 'rotate-45 bg-danger'
        : state === 'changed' || state === 'flaky' ? 'rounded-[3px] bg-warn'
          : 'rounded-full border-2 border-fg/30';
  return <span aria-hidden="true" className={cx('inline-block shrink-0', look)} style={{ width: size, height: size }} />;
}

function Chip({ state, n, word }: { state: CheckState; n: number; word: string }) {
  return (
    <span className={cx('flex items-center gap-2 rounded-xl bg-surface px-3.5 py-2 text-sm', (state === 'off' || state === 'none') && 'text-muted')}>
      <Mark state={state} size={10} />
      {n} {word}
    </span>
  );
}

function Nights({ nights }: { nights: ServiceStateOut['nights'] }) {
  const tone = { ok: 'bg-accent', flaky: 'bg-warn', broken: 'bg-danger', none: 'bg-fg/10' } as const;
  return (
    <span className="hidden shrink-0 gap-[3px] sm:flex" aria-label={`Последние ${nights.length} дней`} role="img">
      {nights.map((n, i) => <span key={i} className={cx('h-5 w-2.5 rounded-[3px]', tone[n])} title={STATE_WORD[n]} />)}
    </span>
  );
}

function summary(s: ServiceStateOut): string {
  const on = s.checks.filter((c) => c.state !== 'off');
  if (!on.length) return s.checks[0]?.off ?? 'не подключён';
  const fine = on.filter((c) => c.state === 'ok' || c.state === 'changed').length;
  const bad = on.find((c) => c.state === 'broken') ?? on.find((c) => c.state === 'flaky');
  const changed = on.find((c) => c.state === 'changed');
  const head = `${fine} из ${on.length}`;
  if (bad) return `${head} · ${bad.title.toLowerCase()}: ${STATE_WORD[bad.state]}`;
  if (changed) return `${head} · ${changed.title.toLowerCase()}: ответ изменился`;
  if (on.every((c) => c.state === 'none')) return 'ещё без данных: проверится этой ночью';
  return `${head} · ${on.map((c) => c.title.toLowerCase()).join(', ')}`;
}

function Service({ s }: { s: ServiceStateOut }) {
  const trouble = s.state === 'broken' || s.state === 'changed' || s.state === 'flaky';
  const [open, setOpen] = useState(trouble);
  const ring = s.state === 'broken' ? 'ring-1 ring-inset ring-danger/45' : trouble ? 'ring-1 ring-inset ring-warn/40' : '';
  return (
    <section aria-label={s.name} className={cx('rounded-2xl bg-surface', ring)}>
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="flex min-h-14 w-full items-center gap-3.5 rounded-2xl px-4 text-left hover:bg-fg/[0.03]">
        <Mark state={s.state} />
        <span className="w-36 shrink-0 truncate text-[15px] font-semibold sm:w-44">{s.name}</span>
        <span className={cx('min-w-0 flex-1 truncate text-[13px]', s.state === 'broken' ? 'text-danger' : trouble ? 'text-warn' : 'text-muted')}>{summary(s)}</span>
        <Nights nights={s.nights} />
        <Icon name="chevron" size={16} className={cx('shrink-0 text-dim transition', open && 'rotate-90')} />
      </button>
      {open && (
        <ul className="space-y-0.5 px-4 pb-3.5 sm:pl-11">
          {s.checks.map((c) => <CheckRow key={c.key} c={c} />)}
        </ul>
      )}
    </section>
  );
}

function CheckRow({ c }: { c: ServiceCheckOut }) {
  const sign = c.state === 'ok' ? '✓' : c.state === 'broken' ? '✕' : c.state === 'changed' || c.state === 'flaky' ? '!' : '—';
  const color = c.state === 'ok' ? 'text-accent' : c.state === 'broken' ? 'text-danger' : c.state === 'changed' || c.state === 'flaky' ? 'text-warn' : 'text-dim';
  const how = c.at ? `${c.source === 'probe' ? 'ночная проверка' : 'по реальной работе'}, ${fmtWhen(c.at)}` : null;
  const bad = c.state === 'broken' || c.state === 'flaky';
  return (
    <li className="py-1.5 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <span className={cx('w-4 shrink-0 text-center font-bold', color)} aria-label={STATE_WORD[c.state]}>{sign}</span>
        <span className="min-w-0 sm:w-64">{c.title}</span>
        <span className={cx('min-w-0 flex-1 text-[13px]', bad ? 'font-mono text-[12.5px] text-danger' : 'text-muted')}>
          {c.state === 'off' ? c.off : c.state === 'none' ? 'ещё без данных' : [c.detail, how].filter(Boolean).join(' · ')}
        </span>
      </div>
      {c.missing.length > 0 && (
        <div className="ml-7 mt-1.5 rounded-lg bg-bg/60 px-3 py-2">
          <div className="pb-1 text-[13px] text-warn">Ответ больше не приходит с полями:</div>
          {c.missing.map((p) => <div key={p} className="font-mono text-[12.5px] text-fg/80">− {p}</div>)}
        </div>
      )}
    </li>
  );
}
