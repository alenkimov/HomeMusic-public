import { useEffect } from 'react';
import { api } from '../api/client';
import { ErrorState, PageHeader, SectionTitle, SkeletonRows } from '../components/ui';
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
          <ul className="mx-4 overflow-hidden rounded-2xl bg-surface lg:mx-6">
            {rows.map(([name, value]) => (
              <li key={name} className="flex min-h-11 items-center justify-between gap-3 border-b border-line/60 px-4 last:border-0">
                <span className="text-muted">{name}</span>
                <span className="truncate font-mono text-sm">{value ?? '—'}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
