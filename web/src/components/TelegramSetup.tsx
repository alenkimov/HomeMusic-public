import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { closeTelegramSetup, deliverToTelegram, useTelegramSetup, type TelegramStatus } from '../lib/telegram';
import { attempt, confirmDialog, toast } from '../lib/ui';
import { Icon } from './Icon';
import { Button, Field, Loading, Notice, Sheet } from './ui';

/** The window of «Отправить в Telegram»: paste the key of your own bot, press Start in it, and the Hub knows where
 * to send. Opened by a share that found no bot (the track asked for is sent once the bot is ready) and from «Ещё»,
 * where a connected bot can be turned off. The key goes to the Hub and never comes back. */
export function TelegramSetup() {
  const { open, pending } = useTelegramSetup();
  const [st, setSt] = useState<TelegramStatus | null>(null);
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false); // «Проверить» found no chat yet

  useEffect(() => {
    if (!open) return;
    setSt(null);
    setToken('');
    setWaiting(false);
    void attempt(() => api.telegram()).then((r) => (r ? setSt(r) : closeTelegramSetup()));
  }, [open]);

  if (!open) return null;

  const done = (r: TelegramStatus) => {
    setSt(r);
    if (!r.ready) return;
    toast(`Telegram подключён: @${r.bot_username}`, 'success');
    if (pending) {
      closeTelegramSetup();
      void deliverToTelegram(pending);
    }
  };
  const connect = async () => {
    setBusy(true);
    const r = await attempt(() => api.telegramConnect(token));
    setBusy(false);
    if (r) {
      setToken('');
      setWaiting(false);
      done(r);
    }
  };
  const check = async () => {
    setBusy(true);
    const r = await attempt(() => api.telegramCheck());
    setBusy(false);
    if (r) {
      setWaiting(!r.ready);
      done(r);
    }
  };
  const disconnect = async () => {
    if (!(await confirmDialog({ title: 'Отключить бота?', body: 'Ключ бота удалится из Хаба. Сам бот и переписка в Telegram останутся.', okLabel: 'Отключить', danger: true }))) return;
    const r = await attempt(() => api.telegramDisconnect(), 'Бот отключён');
    if (r) setSt(r);
  };
  const botLink = st?.bot_username ? `https://t.me/${st.bot_username}` : null;
  const link = 'font-semibold text-accent underline';

  return (
    <Sheet open onClose={closeTelegramSetup} title="Отправка в Telegram">
      {!st ? (
        <Loading />
      ) : !st.connected ? (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (token.trim()) void connect();
          }}
        >
          <p className="text-sm text-muted">
            Треки отправляет ваш собственный бот — в чат с вами.{pending ? ` Подключите его, и «${pending.title}» уйдёт сразу.` : ''}
          </p>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-muted">
            <li>
              Откройте{' '}
              <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer" className={link}>
                @BotFather
              </a>{' '}
              и отправьте ему /newbot.
            </li>
            <li>Придумайте боту имя — BotFather пришлёт ключ вида 123456:ABC…</li>
            <li>Вставьте ключ сюда.</li>
          </ol>
          <Field label="Ключ бота">
            <input className="input font-mono" value={token} onChange={(e) => setToken(e.target.value)} placeholder="123456789:AA…" autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false} autoFocus />
          </Field>
          <Button type="submit" variant="primary" block busy={busy} disabled={!token.trim()}>
            Подключить бота
          </Button>
          <p className="text-xs text-dim">Ключ хранится в Хабе зашифрованным и никому не показывается. Отключить бота можно в «Ещё» → «Telegram».</p>
        </form>
      ) : !st.ready ? (
        <div className="space-y-3">
          <p className="text-sm">
            Бот <span className="font-semibold">@{st.bot_username}</span> подключён. Остался один шаг: бот не может написать первым.
          </p>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-muted">
            <li>
              Откройте{' '}
              {botLink ? (
                <a href={botLink} target="_blank" rel="noopener noreferrer" className={link}>
                  @{st.bot_username}
                </a>
              ) : (
                'бота'
              )}{' '}
              в Telegram и нажмите Start.
            </li>
            <li>Вернитесь сюда и нажмите «Проверить».</li>
          </ol>
          {waiting && <Notice kind="warn">Бот пока не получил сообщение. Нажмите в нём Start (или напишите что-нибудь) и проверьте ещё раз.</Notice>}
          <div className="flex flex-wrap gap-2">
            {botLink && (
              <a href={botLink} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-full bg-fg/10 px-4 text-sm font-semibold">
                <Icon name="external" size={18} />
                Открыть бота
              </a>
            )}
            <Button variant="primary" className="flex-1" busy={busy} onClick={check}>
              Проверить
            </Button>
          </div>
          <Button variant="ghost" block onClick={disconnect}>
            Подключить другого бота
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="card flex items-center gap-3 p-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent/20 text-accent">
              <Icon name="send" size={20} />
            </span>
            <div className="min-w-0">
              <div className="truncate font-semibold">@{st.bot_username}</div>
              <div className="truncate text-xs text-muted">Отправляет в чат: {st.chat_name}</div>
            </div>
          </div>
          <p className="text-sm text-muted">Чтобы отправить трек, откройте его меню «⋯» → «Отправить в Telegram».</p>
          <Button variant="danger" block onClick={disconnect}>
            Отключить бота
          </Button>
        </div>
      )}
    </Sheet>
  );
}
