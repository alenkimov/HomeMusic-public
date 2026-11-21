import { useEffect, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { BuiltInIntegration, IntegrationConnection, IntegrationGrant, IntegrationProvider } from '../api/types';
import { Icon } from '../components/Icon';
import { ServiceBadge, ServiceIcon, serviceName } from '../components/ServiceBadge';
import { Button, ErrorState, Field, IconButton, Notice, PageHeader, Sheet, SkeletonRows, cx } from '../components/ui';
import { parseTime } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { useSession } from '../lib/session';
import { attempt, confirmDialog, toast } from '../lib/ui';

const GRID = 'lg:grid lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)_8rem_10rem_2.25rem] lg:items-center lg:gap-3';
const stateLabel: Record<IntegrationConnection['state'], string> = {
  connected: 'Подключён',
  reauth: 'Нужен вход',
  unavailable: 'Временно недоступен',
};

function date(value: string | null): string {
  return value ? parseTime(value).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
}

function plan(row: IntegrationConnection): string | null {
  if (row.provider !== 'yandex' && row.provider !== 'spotify') return null;
  if (row.plan === 'plus') return 'Яндекс Плюс';
  if (row.plan === 'premium') return 'Premium';
  if (row.plan === 'free') return row.provider === 'yandex' ? 'Без Плюса' : 'Free';
  return 'Тариф не определён';
}

function State({ state }: { state: IntegrationConnection['state'] }) {
  return <span className={cx('inline-flex items-center gap-1.5 text-xs', state === 'connected' ? 'text-accent' : 'text-amber-400')}>
    <Icon name={state === 'connected' ? 'check' : 'alert'} size={15} />{stateLabel[state]}
  </span>;
}

function Rows({ title, rows, username, onOpen }: {
  title: string;
  rows: IntegrationConnection[];
  username: string;
  onOpen: (row: IntegrationConnection) => void;
}) {
  if (!rows.length) return null;
  return (
    <section className="space-y-2">
      <h2 className="px-1 font-semibold">{title}</h2>
      <ul className="overflow-hidden rounded-2xl bg-surface">
        {rows.map((row) => {
          const tariff = plan(row);
          const owner = row.owner === username ? 'Вы' : row.owner || 'Хаб';
          const access = row.provider === 'lastfm' ? 'Для всего Хаба' : row.shared ? 'Общие запросы' : 'Личный аккаунт';
          return (
            <li key={row.id} className={cx(GRID, 'relative min-h-15 border-b border-line/60 py-2.5 pl-3 pr-12 last:border-0 lg:pr-3')}>
              <div className="flex min-w-0 items-center gap-2.5">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-fg/5">
                  <ServiceIcon service={row.provider} size={20} />
                </span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    <span className="font-semibold">{serviceName(row.provider)}</span>
                    {tariff && <span className="rounded bg-fg/8 px-1.5 text-[11px] text-muted">{tariff}</span>}
                  </div>
                  {(row.account || row.provider === 'lastfm') && <div className="truncate text-xs text-muted">{row.account || 'Ключ API'}</div>}
                </div>
              </div>
              <div className="min-w-0 pl-[2.875rem] text-xs lg:pl-0">
                <span className="break-words">{owner}</span>
                <span className="ml-2 text-muted lg:ml-0 lg:block">{access}</span>
              </div>
              <div className="hidden text-xs text-muted lg:block">{date(row.connected_at)}</div>
              <div className="flex flex-wrap items-center gap-x-2 pl-[2.875rem] lg:pl-0">
                <State state={row.state} /><span className="text-xs text-dim lg:hidden">{date(row.connected_at)}</span>
              </div>
              <IconButton icon={row.can_manage ? 'more' : 'chevron'} label={`${row.can_manage ? 'Настроить' : 'Сведения о'} ${serviceName(row.provider)}`}
                className="absolute right-1 top-1/2 -translate-y-1/2 lg:static lg:translate-y-0" size={18} onClick={() => onOpen(row)} />
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Sources({ title, sources }: { title: string; sources: BuiltInIntegration[] }) {
  return <section className="min-w-0 space-y-1">
    <h2 className="py-1 text-sm font-semibold">{title}</h2>
    <ul>{sources.map(({ service, available }) => (
      <li key={service} className="flex min-h-9 items-center justify-between gap-2 text-sm">
        <ServiceBadge service={service} size={16} />
        <span className="text-xs text-muted">{available ? 'Доступен' : 'Выключен'}</span>
      </li>
    ))}</ul>
  </section>;
}

function ShareScope({ shared, onChange }: { shared: boolean; onChange: (next: boolean) => void }) {
  return <fieldset className="space-y-2 rounded-xl border border-line px-3 pb-3 pt-1">
    <legend className="px-1 text-sm font-semibold">Кто может пользоваться</legend>
    <label className="flex min-h-9 items-center gap-2"><input type="radio" name="account-access" className="accent-accent" checked={!shared} onChange={() => onChange(false)} /> Только я</label>
    <label className="flex min-h-9 items-center gap-2"><input type="radio" name="account-access" className="accent-accent" checked={shared} onChange={() => onChange(true)} /> Все в Хабе</label>
    <p className="text-xs text-muted">Общий доступ действует для запросов Хаба. Личная коллекция доступна владельцу.</p>
  </fieldset>;
}

export function IntegrationsPage() {
  const response = useAsync(api.integrations, []);
  const { me, refresh } = useSession();
  const [panel, setPanel] = useState<IntegrationProvider | 'picker' | null>(null);
  const [selected, setSelected] = useState<IntegrationConnection | null>(null);
  const [editing, setEditing] = useState(false);
  const [shared, setShared] = useState(false);
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [grant, setGrant] = useState<IntegrationGrant | null>(null);
  const [stage, setStage] = useState('');
  const [playlist, setPlaylist] = useState('');
  const [checked, setChecked] = useState<string>();
  const { reload } = response;
  const brainz = useAsync((signal) => panel === 'listenbrainz' ? api.listenbrainz(signal) : Promise.resolve(null), [panel]);
  const accounts = response.data?.connections ?? [];

  function open(next: IntegrationProvider | 'picker' | null, row: IntegrationConnection | null = null) {
    setPanel(next);
    setSelected(row);
    setShared(row?.shared ?? false);
    setEditing(!row);
    setSecret('');
    setGrant(null);
    setStage('');
    setPlaylist('');
    setChecked(undefined);
  }

  useEffect(() => {
    if (!grant) return;
    const controller = new AbortController();
    let timer: number;
    async function poll() {
      try {
        const result = await api.integrationPoll(grant!.ticket, controller.signal);
        if (controller.signal.aborted) return;
        setStage(result.stage);
        if (result.stage === 'connected') {
          setGrant(null);
          open(null);
          reload(true);
          toast('Аккаунт подключён', 'success');
          return;
        }
        if (result.stage === 'failed') return;
        timer = window.setTimeout(() => void poll(), grant!.interval * 1000);
      } catch {
        if (!controller.signal.aborted) setStage('failed');
      }
    }
    timer = window.setTimeout(() => void poll(), grant.interval * 1000);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [grant, reload]);

  async function connectAccount(provider: 'yandex' | 'spotify') {
    const tab = provider === 'spotify' ? window.open('about:blank', '_blank') : null;
    if (provider === 'spotify' && !tab) {
      toast('Разрешите открыть окно входа Spotify', 'error');
      return;
    }
    if (tab) tab.opener = null;
    setBusy(true);
    const next = await attempt(() => api.integrationConnect(provider, shared));
    setBusy(false);
    if (!next) {
      tab?.close();
      return;
    }
    if (tab) {
      if (!next.authorization_url) {
        tab.close();
        toast('Spotify не открыл страницу входа', 'error');
        return;
      }
      tab.location.replace(next.authorization_url);
    }
    setGrant(next);
    setStage('waiting');
  }

  async function saveSecret(provider: 'lastfm' | 'listenbrainz') {
    setBusy(true);
    const value = secret.trim();
    const saved = provider === 'lastfm'
      ? await attempt(() => api.lastfmConnect(value), 'Last.fm подключён')
      : await attempt(() => api.listenbrainzConnect(value), 'ListenBrainz подключён');
    setBusy(false);
    if (!saved) return;
    open(null);
    reload(true);
    if (provider === 'lastfm') await refresh();
    else await attempt(() => api.listenbrainzSend(false));
  }

  async function disconnect(row: IntegrationConnection) {
    if (!await confirmDialog({ title: `Отключить ${serviceName(row.provider)}?`, okLabel: 'Отключить', danger: true })) return;
    setBusy(true);
    const done = await attempt(() => api.integrationDisconnect(row.id), 'Сервис отключён');
    setBusy(false);
    if (!done) return;
    open(null);
    reload(true);
    if (row.provider === 'lastfm') await refresh();
  }

  async function saveAccess(row: IntegrationConnection) {
    const next = await attempt(() => api.integrationAccess(row.id, shared), 'Доступ обновлён');
    if (!next) return;
    setSelected(next);
    reload(true);
  }

  async function check(row: IntegrationConnection) {
    setBusy(true);
    setChecked(undefined);
    const next = await attempt(() => api.integrationCheck(row.id, playlist.trim()));
    setBusy(false);
    if (!next) return;
    setSelected(next.connection);
    setChecked(next.playlist_error || (next.playlist ? `Плейлист «${next.playlist}» доступен`
      : next.liked_read !== undefined ? '«Любимые треки» доступны' : undefined));
    reload(true);
  }

  const choices: IntegrationProvider[] = me.is_admin ? ['spotify', 'yandex', 'listenbrainz', 'lastfm'] : ['spotify', 'yandex', 'listenbrainz'];
  const choose = (provider: IntegrationProvider) => {
    const old = provider === 'lastfm' || provider === 'listenbrainz'
      ? accounts.find((row) => row.provider === provider && row.can_manage) : undefined;
    open(provider, old ?? null);
  };
  let body: ReactNode;
  if (panel === 'picker') {
    body = <ul>{choices.map((provider) => <li key={provider}>
      <button type="button" className="flex min-h-12 w-full items-center justify-between gap-3 rounded-lg px-3 hover:bg-fg/5" onClick={() => choose(provider)}>
        <ServiceBadge service={provider} size={20} /><Icon name="chevron" size={16} />
      </button>
    </li>)}</ul>;
  } else if (grant && stage !== 'failed') {
    body = <div className="space-y-3 px-4 pb-4" aria-live="polite">
      {grant.user_code && grant.verification_url
        ? <><p className="text-sm text-muted">Введите код на странице Яндекс ID.</p>
          <p className="select-all text-2xl font-semibold tracking-widest">{grant.user_code}</p>
          <a className="inline-flex min-h-10 items-center gap-2 text-accent" href={grant.verification_url} target="_blank" rel="noreferrer">Открыть Яндекс ID <Icon name="external" size={16} /></a></>
        : <p className="text-sm text-muted">Подтвердите вход в открывшемся окне Spotify.</p>}
      <p className="text-xs text-muted">{stage === 'validating' ? 'Проверяем аккаунт' : 'Ждём подтверждения входа'}</p>
    </div>;
  } else if ((panel === 'spotify' || panel === 'yandex') && editing) {
    body = <div className="space-y-3 px-4 pb-4">
      {stage === 'failed' && <Notice kind="error">Вход не завершился. Попробуйте ещё раз.</Notice>}
      <ShareScope shared={shared} onChange={setShared} />
      {panel === 'spotify' && <p className="text-sm text-muted">Подтвердите вход на компьютере, где запущен Хаб.</p>}
      <Button variant="primary" busy={busy} onClick={() => void connectAccount(panel)}>Войти через {panel === 'spotify' ? 'Spotify' : 'Яндекс'}</Button>
    </div>;
  } else if ((panel === 'lastfm' || panel === 'listenbrainz') && editing) {
    body = <form className="space-y-3 px-4 pb-4" onSubmit={(event) => { event.preventDefault(); if (secret.trim()) void saveSecret(panel); }}>
      {panel === 'listenbrainz' && <Notice>Хаб будет отправлять историю прослушиваний в ваш профиль ListenBrainz.</Notice>}
      <Field label={panel === 'lastfm' ? 'Ключ API' : 'Токен пользователя'}>
        <input className="input" type="password" autoComplete="off" value={secret} onChange={(event) => setSecret(event.target.value)} />
      </Field>
      <a className="block text-sm text-accent" target="_blank" rel="noreferrer" href={panel === 'lastfm' ? 'https://www.last.fm/api/account/create' : 'https://listenbrainz.org/settings/'}>Получить {panel === 'lastfm' ? 'ключ' : 'токен'}</a>
      <Button type="submit" variant="primary" busy={busy} disabled={!secret.trim()}>{selected ? 'Заменить' : 'Подключить'}</Button>
    </form>;
  } else if (selected) {
    const row = selected;
    const checkedAt = row.checked_at ? date(row.checked_at) : null;
    body = <div className="space-y-3 px-4 pb-4">
      <div className="flex flex-wrap items-center gap-2 text-sm"><State state={row.state} />{row.account && <span>{row.account}</span>}
        {plan(row) && <span className="rounded bg-fg/8 px-1.5 text-xs text-muted">{plan(row)}</span>}
      </div>
      {checkedAt && <p className="text-xs text-muted">Последняя проверка · {checkedAt}</p>}
      {row.provider === 'spotify' && <p className="text-sm text-muted">Для чтения коллекции и переноса. Скачивание аудио недоступно.</p>}
      {row.can_manage && (row.provider === 'spotify' || row.provider === 'yandex') && <>
        <ShareScope shared={shared} onChange={setShared} />
        {shared !== row.shared && <Button variant="primary" onClick={() => void saveAccess(row)}>Сохранить доступ</Button>}
        {row.provider === 'spotify' && <Field label="Ссылка на приватный плейлист для проверки">
          <input className="input" value={playlist} onChange={(event) => setPlaylist(event.target.value)} placeholder="open.spotify.com/playlist/…" />
        </Field>}
        {checked && <p className="text-sm text-muted" aria-live="polite">{checked}</p>}
        <Button busy={busy} onClick={() => void check(row)}>Проверить подключение</Button>
      </>}
      {row.can_manage && row.provider === 'listenbrainz' && <>
        <p className="text-xs text-muted">{brainz.data?.sent_until ? `История отправлена по ${date(brainz.data.sent_until)}` : 'История ещё не отправлялась'}</p>
        {row.state !== 'reauth' && <div className="flex flex-wrap gap-2">
          {[false, true].map((again) => <Button key={String(again)} busy={busy} variant={again ? 'ghost' : 'secondary'} onClick={async () => {
            setBusy(true);
            const sent = await attempt(() => api.listenbrainzSend(again));
            setBusy(false);
            if (sent) { brainz.setData(sent); toast(`Отправлено прослушиваний: ${sent.sent ?? 0}`, 'success'); }
          }}>{again ? 'Отправить всю историю заново' : 'Отправить сейчас'}</Button>)}
        </div>}
      </>}
      {row.can_manage && <div className="flex flex-wrap gap-2">
        <Button onClick={() => setEditing(true)}>{row.provider === 'lastfm' || row.provider === 'listenbrainz' ? 'Заменить ключ или токен' : 'Войти заново'}</Button>
        <Button className="text-danger" busy={busy} onClick={() => void disconnect(row)}>Отключить</Button>
      </div>}
    </div>;
  }

  return <div className="pb-6">
    <PageHeader title="Сторонние сервисы" back="/more" right={<Button variant="primary" icon="plus" small onClick={() => open('picker')}>Подключить</Button>} />
    {response.error ? <ErrorState text={response.error} onRetry={() => reload()} /> : !response.data ? <SkeletonRows n={4} /> : (
      <div className="space-y-4 px-4 lg:px-6">
        {!!accounts.length && <div className={cx(GRID, 'hidden px-3 text-xs text-muted')}>
          <span>Сервис</span><span>Пользователь</span><span>Добавлен</span><span>Состояние</span><span />
        </div>}
        <Rows title="Мои подключения" username={me.username} rows={accounts.filter((row) => !row.shared)} onOpen={(row) => open(row.provider, row)} />
        <Rows title="Доступно всем в Хабе" username={me.username} rows={accounts.filter((row) => row.shared)} onOpen={(row) => open(row.provider, row)} />
        <details className="rounded-2xl bg-surface">
          <summary className="cursor-pointer px-4 py-3.5 font-semibold">Каталоги и источники файлов</summary>
          <div className="grid gap-3 border-t border-line px-4 py-3 sm:grid-cols-2">
            <Sources title="Каталоги" sources={response.data.catalogs} />
            <Sources title="Источники файлов" sources={response.data.sources} />
          </div>
        </details>
      </div>
    )}
    <Sheet open={panel !== null} onClose={() => open(null)}
      title={panel && panel !== 'picker' ? <ServiceBadge service={panel} size={20} /> : 'Подключить сервис'}>
      {body}
    </Sheet>
  </div>;
}
