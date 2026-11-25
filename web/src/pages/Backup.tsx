import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { BackupOut } from '../api/types';
import { Icon } from '../components/Icon';
import { Button, ErrorState, Notice, PageHeader, SectionTitle, SkeletonRows } from '../components/ui';
import { fmtSize, fmtWhen } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { attempt, confirmDialog, toast } from '../lib/ui';

/** «Резервные копии» (admins, from «Меню»): every night the Hub sends the database and the files uploaded by hand to
 * Google Drive. Here: connecting the Drive (a code typed at google.com/device), the last copy, a copy now, the copies
 * kept, and what to keep oneself for a restore (`.env`). */
export function BackupPage() {
  const res = useAsync((s) => api.backup(s), [], true, 'backup');
  const b = res.data;
  const [busy, setBusy] = useState<'connect' | 'run' | 'off' | null>(null);

  // while a copy is on its way, its end is asked for now and then
  useEffect(() => {
    if (!b?.running) return;
    const t = window.setInterval(() => res.reload(true), 4000);
    return () => window.clearInterval(t);
  }, [b?.running, res]);

  const run = async () => {
    setBusy('run');
    const next = await attempt(() => api.backupRun(), 'Копия началась');
    setBusy(null);
    if (next) res.setData(next);
  };

  const off = async () => {
    const ok = await confirmDialog({
      title: 'Отключить Google Drive?',
      body: 'Копии перестанут отправляться. Уже сохранённые останутся на Drive.',
      okLabel: 'Отключить',
      danger: true,
    });
    if (!ok) return;
    setBusy('off');
    const next = await attempt(() => api.backupDisconnect(), 'Google Drive отключён');
    setBusy(null);
    if (next) res.setData(next);
  };

  return (
    <div className="pb-10">
      <PageHeader title="Резервные копии" back="/more" />
      <div className="max-w-3xl space-y-4 px-4 lg:px-6">
        <p className="text-[15px] leading-relaxed text-muted">
          Каждую ночь Хаб отправляет в Google Drive базу — историю прослушиваний, лайки, плейлисты, теги, правки — и файлы,
          загруженные вручную. Скачанную музыку после восстановления Хаб скачает заново.
        </p>
        {!b ? (
          res.error ? <ErrorState text={res.error} onRetry={() => res.reload()} /> : <SkeletonRows n={3} />
        ) : !b.configured ? (
          <Notice kind="info">
            Чтобы подключить Google Drive, добавьте в <code>.env</code> сервера <code>GOOGLE_DRIVE_CLIENT_ID</code> и{' '}
            <code>GOOGLE_DRIVE_CLIENT_SECRET</code> и перезапустите Хаб. Как их получить — в README, раздел «Резервные копии».
          </Notice>
        ) : !b.connected ? (
          <Connect pending={b.pending} busy={busy === 'connect'} setBusy={(on) => setBusy(on ? 'connect' : null)} onConnected={(next) => res.setData(next)} />
        ) : (
          <Connected b={b} busy={busy} onRun={run} onOff={off} />
        )}
      </div>
      {b?.connected && b.copies.length > 0 && (
        <div className="max-w-3xl">
          <SectionTitle page note={`${b.copies.length}`}>Сохранённые копии базы</SectionTitle>
          <ul className="mx-4 overflow-hidden rounded-2xl bg-surface lg:mx-6">
            {b.copies.map((c) => (
              <li key={c.day} className="flex min-h-12 items-center justify-between gap-3 border-b border-line/60 px-4 last:border-0">
                <span>{new Date(c.day + 'T00:00:00').toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', weekday: 'short' })}</span>
                <span className="text-sm text-muted">{fmtSize(c.size)}</span>
              </li>
            ))}
          </ul>
          <p className="px-4 pt-2 text-xs text-dim lg:px-6">Хранятся копии за последние 7 дней и по одной за каждую из 4 недель до них.</p>
        </div>
      )}
      <div className="max-w-3xl">
        <SectionTitle page>Если сервер умрёт</SectionTitle>
        <div className="space-y-3 px-4 text-[15px] leading-relaxed text-muted lg:px-6">
          <Notice kind="warn">
            Сохраните у себя копию файла <code>.env</code> с сервера — в менеджере паролей или на флешке. В нём ключ, которым
            зашифрованы пароли пользователей и подключения сервисов. В Google Drive он не отправляется: без него копия
            восстановится, но пользователям придётся заново задать пароли.
          </Notice>
          <p>
            На новом сервере установите HomeMusic, положите сохранённый <code>.env</code> и запустите{' '}
            <code>scripts\restore-server.ps1</code>: он попросит войти в тот же Google-аккаунт и вернёт базу и загруженные
            файлы. Остальную музыку Хаб скачает сам, а пользователи войдут под прежними логинами.
          </p>
        </div>
      </div>
    </div>
  );
}

function Connected({ b, busy, onRun, onOff }: { b: BackupOut; busy: string | null; onRun: () => void; onOff: () => void }) {
  const last = b.last_ok ? `Последняя копия: ${fmtWhen(b.last_ok)}` : 'Копий ещё не было';
  return (
    <div className="space-y-3 rounded-2xl bg-surface p-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent/15 text-accent">
          <Icon name="check" size={20} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-semibold">Google Drive подключён</span>
          <span className="block truncate text-[13px] text-muted">{b.email ?? 'аккаунт Google'}</span>
        </span>
        <Button small variant="outline" busy={busy === 'off'} onClick={onOff}>Отключить</Button>
      </div>
      <p className={b.stale ? 'font-semibold text-danger' : 'text-[15px]'}>
        {b.running ? 'Копия отправляется…' : b.stale && b.last_ok ? `${last} — больше двух суток назад` : last}
      </p>
      {b.error && <Notice kind="error">Последняя попытка не удалась: {b.error}</Notice>}
      <p className="text-[13px] text-muted">
        Загруженных файлов в копии: {b.files_count}{b.files_size ? ` (${fmtSize(b.files_size)})` : ''}
        {b.drive_used != null && ` · Занято на Drive: ${fmtSize(b.drive_used)}${b.drive_limit ? ` из ${fmtSize(b.drive_limit)}` : ''}`}
      </p>
      <Button small variant="primary" icon="upload" busy={busy === 'run' || b.running} disabled={b.running} onClick={onRun}>
        Сделать копию сейчас
      </Button>
    </div>
  );
}

/** «Подключить Google Drive»: the Hub gets a code, the admin types it at google.com/device and allows access to the
 * files the Hub makes (nothing else of the Drive); meanwhile the page asks whether it is done. */
function Connect({ pending, busy, setBusy, onConnected }: {
  pending: BackupOut['pending']; busy: boolean; setBusy: (on: boolean) => void; onConnected: (b: BackupOut) => void;
}) {
  const [code, setCode] = useState(pending);
  const [interval, setIntervalS] = useState(5);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  useEffect(() => {
    if (!code) return;
    let stop = false;
    const tick = async () => {
      if (stop || !alive.current) return;
      try {
        const r = await api.backupPoll();
        if (r.state === 'connected') {
          toast('Google Drive подключён — первая копия уже отправляется', 'success');
          onConnected(r.backup);
          return;
        }
        if (r.state === 'failed') {
          toast(r.error ?? 'Не получилось подключить', 'error');
          setCode(null);
          return;
        }
      } catch { /* the network blinked: the next tick asks again */ }
      window.setTimeout(tick, interval * 1000);
    };
    const t = window.setTimeout(tick, interval * 1000);
    return () => { stop = true; window.clearTimeout(t); };
  }, [code, interval, onConnected]);

  const start = async () => {
    setBusy(true);
    const r = await attempt(() => api.backupConnect());
    setBusy(false);
    if (r) {
      setIntervalS(r.interval);
      setCode({ user_code: r.user_code, url: r.url });
      window.open(r.url, '_blank', 'noopener');
    }
  };

  if (!code) {
    return (
      <div className="space-y-3 rounded-2xl bg-surface p-4">
        <p className="font-semibold">Google Drive не подключён</p>
        <p className="text-[13px] text-muted">
          Хаб получит доступ только к своей папке «HomeMusic backups» — остальное на вашем Drive он не видит. Пароль от
          аккаунта Хаб не узнаёт, а доступ можно отозвать в настройках аккаунта Google.
        </p>
        <Button variant="primary" busy={busy} onClick={start}>Подключить Google Drive</Button>
      </div>
    );
  }
  return (
    <div className="space-y-3 rounded-2xl bg-surface p-4">
      <p className="font-semibold">Введите код на странице Google</p>
      <p className="text-[13px] text-muted">
        Откройте <a href={code.url} target="_blank" rel="noopener noreferrer" className="text-accent underline">{code.url.replace(/^https?:\/\//, '')}</a>,
        войдите в аккаунт Google, куда сохранять копии, введите код и разрешите доступ.
      </p>
      <div className="flex items-center gap-3">
        <span className="select-all rounded-lg bg-raised px-4 py-2 font-mono text-2xl font-bold tracking-widest">{code.user_code}</span>
        <Button small icon="copy" onClick={() => void navigator.clipboard?.writeText(code.user_code).then(() => toast('Код скопирован', 'success'))}>
          Скопировать
        </Button>
      </div>
      <p className="flex items-center gap-2 text-[13px] text-muted"><Icon name="refresh" size={14} className="animate-spin" /> Жду подтверждения…</p>
    </div>
  );
}
