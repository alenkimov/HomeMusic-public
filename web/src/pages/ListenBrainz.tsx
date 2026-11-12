import { useState } from 'react';
import { api } from '../api/client';
import type { ListenBrainzOut } from '../api/types';
import { Button, ErrorState, Field, Loading, Notice, PageHeader } from '../components/ui';
import { parseTime } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { attempt, confirmDialog, toast } from '../lib/ui';

/** ListenBrainz, for whoever wants it: paste the account's token and the Hub sends there what you listen to — and
 * takes from there the «Weekly Exploration» playlist as one more source of «Открытия недели». Nothing in the Hub
 * needs it. The token goes to the Hub and never comes back. */
export function ListenBrainzPage() {
  const res = useAsync((s) => api.listenbrainz(s), []);
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const st = res.data;
  const done = (r: ListenBrainzOut | undefined) => r && res.setData(r);

  const connect = async () => {
    setBusy(true);
    const r = await attempt(() => api.listenbrainzConnect(token.trim()));
    if (r) {
      setToken('');
      done(r);
      toast(`ListenBrainz подключён: ${r.user}. Отправляю историю…`, 'success');
      const sent = await attempt(() => api.listenbrainzSend(false));
      if (sent) {
        done(sent);
        toast(`Отправлено прослушиваний: ${sent.sent ?? 0}`, 'success');
      }
    }
    setBusy(false);
  };
  const send = async (again: boolean) => {
    setBusy(true);
    const r = await attempt(() => api.listenbrainzSend(again));
    setBusy(false);
    if (r) {
      done(r);
      toast(r.sent ? `Отправлено прослушиваний: ${r.sent}` : 'Новых прослушиваний нет', r.sent ? 'success' : 'info');
    }
  };
  const disconnect = async () => {
    if (!(await confirmDialog({ title: 'Отключить ListenBrainz?', body: 'Токен удалится из Хаба, прослушивания перестанут отправляться. То, что уже отправлено, останется в вашем профиле ListenBrainz.', okLabel: 'Отключить', danger: true }))) return;
    done(await attempt(() => api.listenbrainzDisconnect(), 'ListenBrainz отключён'));
  };

  return (
    <div>
      <PageHeader title="ListenBrainz" back="/more" sub="Необязательно" />
      {res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !st ? (
        <Loading />
      ) : (
        <div className="mx-auto max-w-xl space-y-4 px-4 pb-8">
          <p className="text-sm text-muted">
            ListenBrainz — бесплатный открытый сервис, который по истории прослушиваний подбирает музыку людей с похожим вкусом. Хаб работает и без него; с ним в «Открытиях недели» появляется ещё один источник — плейлист Weekly Exploration.
          </p>
          {st.connected ? (
            <>
              <div className="rounded-xl bg-surface p-4">
                <div className="text-sm text-muted">Подключён аккаунт</div>
                <div className="text-lg font-semibold">{st.user}</div>
                <div className="pt-1 text-xs text-dim">
                  {st.sent_until ? `Прослушивания отправлены по ${parseTime(st.sent_until).toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })}` : 'Прослушивания ещё не отправлялись'}
                </div>
              </div>
              <Notice kind="info">Новые прослушивания уходят сами, раз в 10 минут. Плейлист Weekly Exploration появится, когда у ListenBrainz накопится несколько недель истории; он обновляется по понедельникам.</Notice>
              <div className="flex flex-wrap gap-2">
                <Button busy={busy} onClick={() => void send(false)}>
                  Отправить сейчас
                </Button>
                <Button variant="ghost" busy={busy} onClick={() => void send(true)}>
                  Отправить всю историю заново
                </Button>
                <Button variant="ghost" onClick={() => void disconnect()}>
                  Отключить
                </Button>
              </div>
              <p className="text-xs text-dim">«Заново» пригодится после импорта истории из Spotify: ListenBrainz не задваивает то, что уже получил.</p>
            </>
          ) : (
            <>
              <Notice>История прослушиваний в профиле ListenBrainz по умолчанию видна всем. Отправится всё, что считает «Статистика», включая импортированное из Spotify.</Notice>
              <ol className="list-decimal space-y-1 pl-5 text-sm text-muted">
                <li>
                  Зарегистрируйтесь на{' '}
                  <a href="https://listenbrainz.org" target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                    listenbrainz.org
                  </a>{' '}
                  (вход через аккаунт MusicBrainz).
                </li>
                <li>
                  Откройте{' '}
                  <a href="https://listenbrainz.org/settings/" target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                    Settings
                  </a>{' '}
                  и скопируйте User token.
                </li>
                <li>Вставьте его сюда.</li>
              </ol>
              <form
                className="space-y-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (token.trim()) void connect();
                }}
              >
                <Field label="User token">
                  <input className="input" value={token} onChange={(e) => setToken(e.target.value)} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" autoComplete="off" spellCheck={false} />
                </Field>
                <Button type="submit" variant="primary" busy={busy} disabled={!token.trim()}>
                  Подключить
                </Button>
              </form>
            </>
          )}
        </div>
      )}
    </div>
  );
}
