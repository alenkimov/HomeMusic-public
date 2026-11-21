import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useAsync, useInterval } from '../lib/hooks';
import { closeTelegramSetup, deliverToTelegram, TELEGRAM_LINK_EVENT, useTelegramSetup } from '../lib/telegram';
import { attempt, confirmDialog, toast } from '../lib/ui';
import { Icon } from './Icon';
import { ServiceBadge } from './ServiceBadge';
import { Button, Checkbox, ErrorState, Loading, Notice, Sheet } from './ui';

export function TelegramSetup() {
  const { open } = useTelegramSetup();
  return open ? <Connection /> : null;
}

function Connection() {
  const { pending, options } = useTelegramSetup();
  const setup = useAsync(async (signal) => {
    const st = await api.telegram(signal);
    return st.configured && !st.ready ? api.telegramLink(signal) : { ...st, start_url: null };
  }, []);
  const st = setup.data;
  const [waiting, setWaiting] = useState(false);
  const [checking, setChecking] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (st?.ready && pending.length) {
      closeTelegramSetup();
      void deliverToTelegram(pending, options);
    }
  }, [st?.ready, pending, options]);

  const check = async (manual = false) => {
    if (request.current) return;
    const ctl = new AbortController();
    request.current = ctl;
    if (manual) setChecking(true);
    const fetch = () => api.telegramCheck(ctl.signal);
    const next = await (manual ? attempt(fetch) : fetch().catch(() => undefined));
    request.current = null;
    if (ctl.signal.aborted) return;
    setChecking(false);
    if (!next?.ready) {
      if (manual && next) setNotFound(true);
      return;
    }
    setup.setData({ ...next, start_url: null });
    window.dispatchEvent(new Event(TELEGRAM_LINK_EVENT));
    toast('Telegram подключён', 'success');
  };
  useInterval(() => void check(), waiting && st?.configured && !st.ready ? 2500 : null);

  const disconnect = async () => {
    if (!(await confirmDialog({ title: 'Отключить Telegram?', body: 'Чтобы снова получать треки в личку, потребуется подключить аккаунт заново.', okLabel: 'Отключить', danger: true }))) return;
    const next = await attempt(() => api.telegramDisconnect(), 'Telegram отключён');
    if (next) {
      window.dispatchEvent(new Event(TELEGRAM_LINK_EVENT));
      closeTelegramSetup();
    }
  };

  return (
    <Sheet open onClose={closeTelegramSetup} title={<ServiceBadge service="telegram" label="Отправка в Telegram" size={18} />}>
      {setup.loading ? (
        <Loading />
      ) : setup.error || !st ? (
        <ErrorState text={setup.error ?? 'Не удалось открыть настройки Telegram'} onRetry={() => setup.reload()} />
      ) : !st.configured ? (
        <p className="text-sm text-muted">Администратор Хаба ещё не настроил бота Telegram.</p>
      ) : !st.ready ? (
        <div className="space-y-3">
          <p className="text-sm">Откройте @{st.bot_username} и нажмите Start. После этого бот сможет отправлять треки вам в личку.</p>
          {pending.length > 0 && <p className="text-sm text-muted">После подключения бот пришлёт {pending.length === 1 ? 'выбранный трек' : 'выбранные треки'}.</p>}
          {notFound && <Notice kind="warn">Бот пока не получил команду Start. Если ссылка устарела, откройте новую.</Notice>}
          <div className="flex flex-wrap gap-2">
            {st.start_url && (
              <a href={st.start_url} target="_blank" rel="noopener noreferrer" onClick={() => setWaiting(true)}
                className="inline-flex min-h-12 flex-1 items-center justify-center gap-2 rounded-full bg-accent px-5 text-sm font-semibold text-on-accent">
                <Icon name="external" size={18} /> Открыть бота
              </a>
            )}
            <Button className="flex-1" busy={checking} onClick={() => void check(true)}>Проверить</Button>
          </div>
          {notFound && (
            <Button variant="ghost" onClick={() => { setWaiting(false); setNotFound(false); setup.reload(); }}>Обновить ссылку</Button>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="card flex items-center gap-3 p-3">
            <ServiceBadge service="telegram" label={`@${st.bot_username}`} size={22} />
            <span className="truncate text-xs text-muted">{st.chat_name}</span>
          </div>
          <p className="text-sm text-muted">Нажмите «Поделиться» у трека и выберите чат.</p>
          <p className="text-sm text-muted">Для отправки в группу бот должен быть её администратором. В канале право публикации требуется и вам, и боту.</p>
          <label className="flex cursor-pointer items-center justify-between gap-3 rounded-xl bg-raised px-3 py-2 text-sm">
            <span>
              Отправлять с текстом песни
              <span className="block text-xs text-muted">Свёрнутой цитатой под треком</span>
            </span>
            <Checkbox checked={!!st.with_lyrics} label="Отправлять с текстом песни"
              onChange={(v) => void attempt(() => api.telegramPrefs(v)).then((r) => r && setup.setData({ ...r, start_url: null }))} />
          </label>
          <Button variant="danger" block onClick={disconnect}>Отключить Telegram</Button>
        </div>
      )}
    </Sheet>
  );
}
