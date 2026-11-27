import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { Button, ErrorState, PageHeader, SectionTitle, SkeletonRows } from '../components/ui';
import { Icon } from '../components/Icon';
import { toast } from '../lib/ui';
import { fmtWhen } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { useSession } from '../lib/session';
import { WEB_VERSION, markSeen, useVersion } from '../lib/version';

const dateOf = (iso: string | null) =>
  iso ? new Date(`${iso}T00:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }) : null;

/** «Что нового» («Меню»): the releases of HomeMusic, newest first, as `CHANGELOG.md` tells them. Opened, the newest
 * one is seen: «Меню» drops its dot. */
export function WhatsNewPage() {
  const { data } = useVersion();
  const me = useSession().me;
  useEffect(() => {
    if (data?.version) markSeen(data.version);
  }, [data?.version]);
  return (
    <div className="pb-10">
      <PageHeader title="Что нового" back="/more" sub={data ? `Версия ${data.version}` : undefined} />
      <div className="max-w-3xl">
        {!data ? (
          <SkeletonRows n={6} />
        ) : !data.releases.length ? (
          <p className="px-4 text-sm text-muted lg:px-6">История изменений пока пуста.</p>
        ) : (
          data.releases.map((r, i) => (
            <section key={r.version} className="pb-4">
              <SectionTitle page note={dateOf(r.date) ?? undefined}>
                {r.version}{i === 0 && <span className="ml-2 text-sm font-normal text-accent">сейчас</span>}
              </SectionTitle>
              {r.intro && <p className="px-4 pb-2 text-[15px] leading-relaxed text-muted lg:px-6">{r.intro}</p>}
              {r.sections.map((s) => (
                <div key={s.title} className="px-4 pb-3 lg:px-6">
                  {s.title && <h3 className="pb-1 text-sm font-semibold">{s.title}</h3>}
                  <ul className="list-disc space-y-1 pl-5 text-[15px] leading-relaxed">
                    {s.items.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
              ))}
            </section>
          ))
        )}
        {data && WEB_VERSION !== 'dev' && WEB_VERSION !== data.version && (
          <p className="px-4 text-xs text-dim lg:px-6">Приложение на этом устройстве — версии {WEB_VERSION}; оно обновится само.</p>
        )}
        {me.is_admin && <p className="px-4 pt-2 text-xs text-dim lg:px-6">Версии сервера и его компонентов — в «Меню» → «О сервере».</p>}
      </div>
    </div>
  );
}

/** «О сервере» (admins): the Hub's version, the commit and the time it was built, and the versions of what it
 * depends on — the first place a failure is looked for in (yt-dlp, when YouTube stops). */
export function AboutPage() {
  const res = useAsync((s) => api.about(s), [], true, 'admin:about');
  const a = res.data;
  const rows: [string, string | null][] = a ? [
    ['HomeMusic', a.version],
    ['Коммит', a.commit],
    ['Собран', a.built_at ? fmtWhen(a.built_at) : null],
    ['Процесс', a.role === 'api' ? 'api (рядом — worker)' : a.role],
    ['Веб-приложение', WEB_VERSION],
    ...a.components.map((c) => [c.name, c.version] as [string, string | null]),
  ] : [];
  return (
    <div className="pb-10">
      <PageHeader title="О сервере" back="/more" />
      <div className="max-w-2xl">
        {!a ? (
          res.error ? <ErrorState text={res.error} onRetry={() => res.reload()} /> : <SkeletonRows n={8} />
        ) : (
          <>
          <Updates />
          <ul className="mx-4 overflow-hidden rounded-2xl bg-surface lg:mx-6">
            {rows.map(([name, value]) => (
              <li key={name} className="flex min-h-11 items-center justify-between gap-3 border-b border-line/60 px-4 last:border-0">
                <span className="text-muted">{name}</span>
                <span className="truncate font-mono text-sm">{value ?? '—'}</span>
              </li>
            ))}
          </ul>
          </>
        )}
      </div>
    </div>
  );
}

/** What is behind (the worker asks once a day): each with the version it has, the newest and what is at stake;
 * the time of the check, and a check now. Nothing behind — one quiet line. */
function Updates() {
  const res = useAsync((s) => api.updates(s), [], true, 'admin:updates');
  const [busy, setBusy] = useState(false);
  const u = res.data;
  if (!u) return null;
  const check = async () => {
    setBusy(true);
    try {
      const next = await api.checkUpdates();
      res.setData(next);
      if (!next.outdated.length) toast('Всё актуально');
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Не удалось проверить');
    } finally {
      setBusy(false);
    }
  };
  const when = u.checked_at ? `Проверено ${fmtWhen(u.checked_at)}` : 'Ещё не проверялось';
  const hub = u.outdated.some((o) => o.name === 'HomeMusic');
  const libs = u.outdated.some((o) => o.name !== 'HomeMusic');
  return (
    <div className="mx-4 mb-5 lg:mx-6">
      {u.outdated.length > 0 && (
        <div className="overflow-hidden rounded-2xl bg-warn/10 ring-1 ring-inset ring-warn/35">
          <div className="flex items-center gap-2 px-4 pb-1 pt-3 font-semibold text-warn">
            <Icon name="download" size={18} />
            Есть новые версии
          </div>
          <ul>
            {u.outdated.map((o) => (
              <li key={o.name} className="border-b border-warn/15 px-4 py-2.5 last:border-0">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <span className="font-semibold">{o.name}</span>
                  <span className="font-mono text-sm"><span className="text-muted">{o.installed}</span> → <span className="text-warn">{o.latest}</span></span>
                </div>
                <p className="pt-0.5 text-[13px] text-muted">{o.why}</p>
              </li>
            ))}
          </ul>
          <p className="border-t border-warn/15 px-4 py-2.5 text-[13px] text-muted">
            {hub && <>Обновить сервер: <code className="font-mono text-fg">.\scripts\update-server.ps1</code>. </>}
            {libs && 'Новые версии библиотек попадают на сервер с выпуском HomeMusic.'}
          </p>
        </div>
      )}
      <div className="flex min-h-10 items-center justify-between gap-3 pt-2 text-[13px] text-muted">
        <span>{u.outdated.length ? when : `Всё актуально · ${when.toLowerCase()}`}</span>
        <Button small icon="refresh" busy={busy} onClick={check}>Проверить сейчас</Button>
      </div>
    </div>
  );
}
