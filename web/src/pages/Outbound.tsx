import { useMemo, useState } from 'react';
import { api } from '../api/client';
import type { OutboundFilter, OutboundRequestOut, OutboundSummaryRow, ProxiesOut, ProxyOut } from '../api/types';
import { Badge, Button, Empty, ErrorState, Field, PageHeader, SectionTitle, Segmented, SkeletonRows, cx } from '../components/ui';
import { fmtSize, fmtWhen } from '../lib/format';
import { useAsync, type AsyncState } from '../lib/hooks';
import { attempt, confirmDialog, toast } from '../lib/ui';

/** «Прокси и запросы» (admins, from «Меню»): the proxies the Hub's requests to outside services go through — of an
 * account, of a whole service — and the journal of those requests (`hub/app/outbound.py`): how many, how many failed
 * or hit a limit (429), how long they took, by service, proxy and what they were for; the requests themselves. */
export function OutboundPage() {
  const [tab, setTab] = useState<'requests' | 'proxies'>('requests');
  const proxies = useAsync((s) => api.proxies(s), [], true, 'admin:proxies');
  return (
    <div className="pb-10">
      <PageHeader title="Прокси и запросы" back="/more" />
      <div className="px-4 pb-3 lg:px-6">
        <Segmented
          label="Раздел"
          value={tab}
          onChange={setTab}
          options={[{ value: 'requests', label: 'Запросы' }, { value: 'proxies', label: 'Прокси', count: proxies.data?.proxies.length }]}
        />
      </div>
      {tab === 'requests' ? <Requests proxies={proxies.data} /> : <Proxies res={proxies} />}
    </div>
  );
}

// --- the proxies ----------------------------------------------------------------------------------------------

const proxyLabel = (p: ProxyOut) => p.name || `${p.host}:${p.port}`;
const proxyAddress = (p: ProxyOut) => `${p.protocol}://${p.login ? `${p.login}${p.has_password ? ':••••' : ''}@` : ''}${p.host}:${p.port}`;

function Proxies({ res }: { res: AsyncState<ProxiesOut> }) {
  const d = res.data;
  const [url, setUrl] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);

  if (!d) return res.error ? <ErrorState text={res.error} onRetry={() => res.reload()} /> : <SkeletonRows n={4} />;

  const add = async () => {
    setBusy('add');
    const p = await attempt(() => (editing != null ? api.changeProxy(editing, url.trim(), name.trim() || null) : api.addProxy(url.trim(), name.trim() || null)),
      editing != null ? 'Прокси изменён' : 'Прокси добавлен');
    setBusy(null);
    if (!p) return;
    setUrl('');
    setName('');
    setEditing(null);
    res.reload(true);
    void check(p.id);
  };
  const check = async (id: number) => {
    setBusy(`check:${id}`);
    const p = await attempt(() => api.checkProxy(id));
    setBusy(null);
    if (!p) return;
    toast(p.check_ok ? `Работает: ${p.ip}${p.country_code ? ` · ${p.country_code}` : ''}` : `Не отвечает: ${p.check_error ?? ''}`, p.check_ok ? 'success' : 'error');
    res.reload(true);
  };
  const drop = async (p: ProxyOut) => {
    const ok = await confirmDialog({ title: `Удалить «${proxyLabel(p)}»?`, body: 'Сервисы и аккаунты, которые шли через него, пойдут напрямую.', okLabel: 'Удалить', danger: true });
    if (!ok) return;
    await attempt(() => api.dropProxy(p.id), 'Прокси удалён');
    res.reload(true);
  };
  const bind = async (what: 'service' | 'account', key: string | number, proxyId: number | null) => {
    await attempt(() => (what === 'service' ? api.serviceProxy(String(key), proxyId) : api.accountProxy(Number(key), proxyId)));
    res.reload(true);
  };
  const choose = (value: number | null, onChange: (id: number | null) => void, label: string) => (
    <select className="input min-w-0 max-w-56 text-sm" aria-label={label} value={value ?? ''} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">Напрямую</option>
      {d.proxies.map((p) => <option key={p.id} value={p.id}>{proxyLabel(p)}{p.country_code ? ` · ${p.country_code}` : ''}</option>)}
    </select>
  );
  const usedBy = (id: number) => d.services.filter((s) => s.proxy_id === id).length + d.accounts.filter((a) => a.proxy_id === id).length;

  return (
    <div className="max-w-4xl space-y-2">
      <p className="px-4 text-sm leading-relaxed text-muted lg:px-6">
        Запрос с аккаунтом идёт через прокси аккаунта, остальные запросы к сервису — через прокси сервиса, иначе напрямую.
      </p>
      <form className="card mx-4 space-y-3 p-4 lg:mx-6" onSubmit={(e) => { e.preventDefault(); if (url.trim()) void add(); }}>
        <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
          <Field label="Адрес">
            <input className="input font-mono text-sm" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="socks5://логин:пароль@хост:порт" autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false} />
          </Field>
          <Field label="Название">
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Например, RU" maxLength={100} />
          </Field>
        </div>
        <p className="text-xs text-dim">Протоколы: http, https, socks5, socks5h. Без протокола — http. Пароль хранится зашифрованным и обратно не показывается.</p>
        <div className="flex gap-2">
          <Button type="submit" variant="primary" busy={busy === 'add'} disabled={!url.trim() || !!busy}>{editing != null ? 'Сохранить' : 'Добавить и проверить'}</Button>
          {editing != null && <Button type="button" onClick={() => { setEditing(null); setUrl(''); setName(''); }}>Отмена</Button>}
        </div>
      </form>

      {d.proxies.length > 0 && (
        <>
          <SectionTitle page note={`${d.proxies.length}`}>Прокси</SectionTitle>
          <ul className="mx-4 overflow-hidden rounded-2xl bg-surface lg:mx-6">
            {d.proxies.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-3 border-b border-line/60 px-4 py-3 last:border-0">
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-semibold">{proxyLabel(p)}</span>
                  <span className="block truncate font-mono text-xs text-muted">{proxyAddress(p)}</span>
                  <span className={cx('block text-xs', p.check_ok === false ? 'text-danger' : 'text-dim')}>
                    {p.checked_at == null ? 'Не проверен' : p.check_ok ? `Работает · ${p.ip}${p.country_code ? ` · ${p.country_code}` : ''} · ${fmtWhen(p.checked_at)}` : `Не отвечает · ${p.check_error ?? ''}`}
                    {' · '}{usedBy(p.id) ? `используется: ${usedBy(p.id)}` : 'не используется'}
                  </span>
                </span>
                <Button small busy={busy === `check:${p.id}`} onClick={() => void check(p.id)}>Проверить</Button>
                <Button small onClick={() => { setEditing(p.id); setUrl(proxyAddress(p).replace(':••••@', '@')); setName(p.name ?? ''); window.scrollTo({ top: 0, behavior: 'smooth' }); }}>Изменить</Button>
                <Button small variant="ghost" onClick={() => void drop(p)}>Удалить</Button>
              </li>
            ))}
          </ul>
          {editing != null && <p className="px-4 text-xs text-dim lg:px-6">Пароль оставьте пустым, чтобы не менять его; без логина пароль стирается.</p>}
        </>
      )}

      <SectionTitle page>Сервисы</SectionTitle>
      <ul className="mx-4 overflow-hidden rounded-2xl bg-surface lg:mx-6">
        {d.services.map((s) => (
          <li key={s.service} className="flex min-h-12 items-center justify-between gap-3 border-b border-line/60 px-4 py-1.5 last:border-0">
            <span className="min-w-0 truncate">{s.name}</span>
            {choose(s.proxy_id, (id) => void bind('service', s.service, id), `Прокси для ${s.name}`)}
          </li>
        ))}
      </ul>

      <SectionTitle page note={d.accounts.length ? `${d.accounts.length}` : undefined}>Аккаунты</SectionTitle>
      {d.accounts.length ? (
        <ul className="mx-4 overflow-hidden rounded-2xl bg-surface lg:mx-6">
          {d.accounts.map((a) => (
            <li key={a.id} className="flex min-h-12 items-center justify-between gap-3 border-b border-line/60 px-4 py-1.5 last:border-0">
              <span className="min-w-0">
                <span className="block truncate">{a.account || `#${a.id}`}{a.plan === 'plus' && <Badge className="ml-2">Плюс</Badge>}</span>
                <span className="block truncate text-xs text-muted">{a.provider}{a.owner ? ` · ${a.owner}` : ''}</span>
              </span>
              {choose(a.proxy_id, (id) => void bind('account', a.id, id), `Прокси для аккаунта ${a.account ?? a.id}`)}
            </li>
          ))}
        </ul>
      ) : <p className="px-4 text-sm text-muted lg:px-6">Подключённых аккаунтов нет.</p>}
    </div>
  );
}

// --- the journal ----------------------------------------------------------------------------------------------

const PERIODS = [{ value: '1', label: 'Час' }, { value: '24', label: 'Сутки' }, { value: '168', label: 'Неделя' }] as const;
const ms = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)} с` : `${n} мс`);

function Requests({ proxies }: { proxies: ProxiesOut | undefined }) {
  const [hours, setHours] = useState<'1' | '24' | '168'>('24');
  const [filter, setFilter] = useState<OutboundFilter>({});
  const summary = useAsync((s) => api.outboundSummary(Number(hours), s), [hours], true, `outbound:summary:${hours}`);
  const proxyName = useMemo(() => new Map((proxies?.proxies ?? []).map((p) => [String(p.id), proxyLabel(p)])), [proxies]);
  const accountName = useMemo(() => new Map((proxies?.accounts ?? []).map((a) => [a.id, `${a.provider}: ${a.account ?? a.id}`])), [proxies]);
  const serviceName = useMemo(() => new Map((proxies?.services ?? []).map((s) => [s.service, s.name])), [proxies]);
  const pick = (f: OutboundFilter) => setFilter((was) => {
    const next = { ...was, ...f };
    for (const k of Object.keys(f) as (keyof OutboundFilter)[]) if (was[k] === f[k]) delete next[k]; // a second tap takes it off
    return next;
  });

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3 px-4 lg:px-6">
        <Segmented label="Период" value={hours} onChange={setHours} options={PERIODS.map((p) => ({ ...p }))} />
        <Button small icon="refresh" onClick={() => summary.reload(true)}>Обновить</Button>
      </div>
      {!summary.data ? (
        summary.error ? <ErrorState text={summary.error} onRetry={() => summary.reload()} /> : <SkeletonRows n={5} />
      ) : !summary.data.services.length ? (
        <Empty icon="chart" title="Запросов не было" text="За этот период Хаб не обращался к сторонним сервисам." />
      ) : (
        <div className="grid gap-x-4 xl:grid-cols-2">
          <Summary title="По сервисам" rows={summary.data.services} name={(k) => serviceName.get(k) ?? k} chosen={filter.service} onPick={(k) => pick({ service: k })} />
          <Summary title="По прокси" rows={summary.data.proxies} name={(k) => (k ? proxyName.get(k) ?? `#${k}` : 'Напрямую')} chosen={filter.proxy_id != null ? String(filter.proxy_id || '') : undefined} onPick={(k) => pick({ proxy_id: k ? Number(k) : 0 })} />
          <div className="xl:col-span-2">
            <Summary title="Для чего" rows={summary.data.purposes} name={(k) => k || 'Без пометки'} chosen={filter.purpose} onPick={(k) => k && pick({ purpose: k })} />
          </div>
        </div>
      )}
      <Journal filter={filter} pick={pick} serviceName={serviceName} proxyName={proxyName} accountName={accountName} />
    </div>
  );
}

function Summary({ title, rows, name, chosen, onPick }: { title: string; rows: OutboundSummaryRow[]; name: (key: string) => string; chosen?: string; onPick: (key: string) => void }) {
  if (!rows.length) return null;
  return (
    <div className="min-w-0">
      <SectionTitle page>{title}</SectionTitle>
      <div className="mx-4 overflow-x-auto rounded-2xl bg-surface lg:mx-6">
        <table className="w-full text-sm tabular-nums">
          <thead className="text-left text-xs text-dim">
            <tr className="border-b border-line/60">
              <th className="px-3 py-2 font-normal">Кто</th>
              <th className="px-3 py-2 text-right font-normal">Запросов</th>
              <th className="px-3 py-2 text-right font-normal">Ошибок</th>
              <th className="px-3 py-2 text-right font-normal" title="Ответ 429: сервис просит подождать">Лимит</th>
              <th className="px-3 py-2 text-right font-normal">Среднее</th>
              <th className="px-3 py-2 text-right font-normal" title="95 % запросов быстрее этого">95 %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} onClick={() => onPick(r.key)} className={cx('cursor-pointer border-b border-line/40 last:border-0 hover:bg-fg/5', chosen === r.key && 'bg-accent/10')}>
                <td className="max-w-64 truncate px-3 py-2">{name(r.key)}</td>
                <td className="px-3 py-2 text-right">{r.requests.toLocaleString('ru-RU')}</td>
                <td className={cx('px-3 py-2 text-right', r.errors > 0 && 'text-danger')}>{r.errors ? `${r.errors.toLocaleString('ru-RU')} · ${Math.round((r.errors / r.requests) * 100)} %` : '—'}</td>
                <td className={cx('px-3 py-2 text-right', r.limited > 0 && 'font-semibold text-danger')}>{r.limited || '—'}</td>
                <td className="px-3 py-2 text-right">{ms(r.avg_ms)}</td>
                <td className="px-3 py-2 text-right">{ms(r.p95_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const OUTCOMES = [{ value: 'error', label: 'Ошибки' }, { value: 'limited', label: 'Лимит (429)' }, { value: 'ok', label: 'Успешные' }] as const;

function Journal({ filter, pick, serviceName, proxyName, accountName }: {
  filter: OutboundFilter;
  pick: (f: OutboundFilter) => void;
  serviceName: Map<string, string>;
  proxyName: Map<string, string>;
  accountName: Map<number, string>;
}) {
  const key = JSON.stringify(filter);
  const [older, setOlder] = useState<{ key: string; items: OutboundRequestOut[]; more: boolean } | null>(null);
  const page = useAsync((s) => api.outboundRequests(filter, s), [key], true, `outbound:requests:${key}`);
  const items = [...(page.data?.items ?? []), ...(older?.key === key ? older.items : [])];
  const more = older?.key === key ? older.more : page.data?.more;
  const [loading, setLoading] = useState(false);
  const loadMore = async () => {
    const last = items[items.length - 1];
    if (!last) return;
    setLoading(true);
    const next = await attempt(() => api.outboundRequests({ ...filter, before: last.id }));
    setLoading(false);
    if (next) setOlder({ key, items: [...(older?.key === key ? older.items : []), ...next.items], more: next.more });
  };
  const chips = [
    filter.service && `сервис: ${serviceName.get(filter.service) ?? filter.service}`,
    filter.proxy_id != null && `прокси: ${filter.proxy_id ? proxyName.get(String(filter.proxy_id)) ?? filter.proxy_id : 'напрямую'}`,
    filter.connection_id != null && `аккаунт: ${accountName.get(filter.connection_id) ?? filter.connection_id}`,
    filter.purpose && `для: ${filter.purpose}`,
  ].filter(Boolean);

  return (
    <div>
      <SectionTitle page right={<Segmented label="Исход" value={(filter.outcome ?? '') as 'ok'} onChange={(v) => pick({ outcome: v })} options={OUTCOMES.map((o) => ({ ...o }))} />}>
        Последние запросы
      </SectionTitle>
      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 px-4 pb-2 text-xs text-muted lg:px-6">
          {chips.join(' · ')}
          <button type="button" className="text-accent" onClick={() => pick({ service: filter.service, proxy_id: filter.proxy_id, connection_id: filter.connection_id, purpose: filter.purpose })}>Сбросить</button>
        </div>
      )}
      {!page.data ? (
        page.error ? <ErrorState text={page.error} onRetry={() => page.reload()} /> : <SkeletonRows n={6} />
      ) : !items.length ? (
        <p className="px-4 text-sm text-muted lg:px-6">Ничего не подходит.</p>
      ) : (
        <div className="mx-4 overflow-x-auto rounded-2xl bg-surface lg:mx-6">
          <table className="w-full text-xs tabular-nums">
            <thead className="text-left text-dim">
              <tr className="border-b border-line/60">
                <th className="px-3 py-2 font-normal">Когда</th>
                <th className="px-3 py-2 font-normal">Сервис</th>
                <th className="px-3 py-2 font-normal">Запрос</th>
                <th className="px-3 py-2 font-normal">Для чего</th>
                <th className="px-3 py-2 font-normal">Через</th>
                <th className="px-3 py-2 text-right font-normal">Ответ</th>
                <th className="px-3 py-2 text-right font-normal">Время</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id} className="border-b border-line/40 align-top last:border-0">
                  <td className="whitespace-nowrap px-3 py-1.5 text-muted" title={r.started_at}>{new Date(r.started_at.endsWith('Z') ? r.started_at : `${r.started_at}Z`).toLocaleTimeString('ru-RU')}</td>
                  <td className="whitespace-nowrap px-3 py-1.5"><button type="button" className="hover:underline" onClick={() => pick({ service: r.service })}>{serviceName.get(r.service) ?? r.service}</button></td>
                  <td className="max-w-md px-3 py-1.5"><span className="break-all font-mono"><span className="text-dim">{r.method} {r.host}</span>{r.path}</span>{r.error && <span className="block text-danger">{r.error}</span>}</td>
                  <td className="max-w-48 truncate px-3 py-1.5 text-muted">{r.purpose && <button type="button" className="hover:underline" onClick={() => pick({ purpose: r.purpose ?? undefined })}>{r.purpose}</button>}</td>
                  <td className="whitespace-nowrap px-3 py-1.5 text-muted">
                    <button type="button" className="hover:underline" onClick={() => pick({ proxy_id: r.proxy_id ?? 0 })}>{r.proxy_id ? proxyName.get(String(r.proxy_id)) ?? `#${r.proxy_id}` : 'напрямую'}</button>
                    {r.connection_id != null && <button type="button" className="block hover:underline" onClick={() => pick({ connection_id: r.connection_id ?? undefined })}>{accountName.get(r.connection_id) ?? `аккаунт #${r.connection_id}`}</button>}
                    {r.auth === 'key' && <span className="block">ключ API</span>}
                  </td>
                  <td className={cx('whitespace-nowrap px-3 py-1.5 text-right', (r.error || (r.status ?? 0) >= 400) && 'text-danger')}>{r.status ?? '—'}{r.bytes != null && <span className="block text-dim">{fmtSize(r.bytes)}</span>}</td>
                  <td className="whitespace-nowrap px-3 py-1.5 text-right">{ms(r.elapsed_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {more && <div className="px-4 pt-3 lg:px-6"><Button small busy={loading} onClick={() => void loadMore()}>Ещё</Button></div>}
      <p className="px-4 pt-2 text-xs text-dim lg:px-6">Журнал хранится 14 дней. Значения параметров запросов не сохраняются — только их имена: в них бывают токены и ключи.</p>
    </div>
  );
}
