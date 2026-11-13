import { EMBEDDED } from '../lib/embedded';
import { Link } from '../lib/router';
import { useActiveDownloads } from '../lib/notify';
import { Icon, type IconName } from './Icon';
import { cx } from './ui';

/** «Поиск» was tapped while it is open: `pages/Search.tsx` puts the cursor into its field. */
export const FOCUS_SEARCH = 'hm:focus-search';

export type Tab = 'home' | 'search' | 'downloads' | 'library' | 'assistant' | 'more';

/** Only a plain browser draws it (inside the app the app's tabs open the routes): what is listened to — «Главная»,
 * «Поиск», «Библиотека» — and «Меню» with everything else (the downloads and the assistant among it: the number of
 * downloads under way stands on «Меню»). */
const TABS: Array<{ id: Tab; to: string; label: string; icon: IconName }> = [
  { id: 'home', to: '/home', label: 'Главная', icon: 'home' },
  { id: 'search', to: '/search', label: 'Поиск', icon: 'search' },
  { id: 'library', to: '/library', label: 'Библиотека', icon: 'library' },
  { id: 'more', to: '/more', label: 'Меню', icon: 'menu' },
];

/** The tab a route's own tab shows under: the downloads and the assistant live in «Меню». */
const shownTab = (t: Tab | null): Tab | null => (t && !TABS.some((x) => x.id === t) ? 'more' : t);

export function TabBar({ active: routeTab, last }: { active: Tab | null; last: Partial<Record<Tab, string>> }) {
  const activeDownloads = useActiveDownloads();
  if (EMBEDDED) return null; // the Android app shows its own navigation
  const active = shownTab(routeTab);

  return (
    <nav className="pb-safe fixed inset-x-0 bottom-0 z-30" aria-label="Разделы">
      {/* no plate under the tabs: the page fades into the background behind them and the small player */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 bottom-0 -z-10 h-[calc(var(--tabbar-h)+var(--player-h,0px)+env(safe-area-inset-bottom)+56px)] bg-gradient-to-t from-bg from-55% to-transparent" />
      <ul className="mx-auto flex max-w-(--app-w)">
        {TABS.map((t) => {
          const on = active === t.id;
          return (
            <li key={t.id} className="flex-1">
              <Link
                to={on ? t.to : (last[t.id] ?? t.to)}
                aria-current={on ? 'page' : undefined}
                // «Поиск» tapped again (the second tap of a double one): the field takes the cursor and the keyboard
                // comes up. Said within the tap itself — a phone opens its keyboard only for a touch
                onClick={t.id === 'search' && on ? () => window.dispatchEvent(new Event(FOCUS_SEARCH)) : undefined}
                className={cx('relative flex h-16 flex-col items-center justify-center gap-1 text-[11px] transition-colors', on ? 'text-fg' : 'text-muted/70 hover:text-muted')}
              >
                <span className="relative">
                  <Icon name={t.icon} size={24} stroke={on ? 2.4 : 2} />
                  {t.id === 'more' && activeDownloads > 0 && (
                    <span className="absolute -right-2.5 -top-1.5 min-w-4 rounded-full bg-accent px-1 text-center text-[10px] font-bold leading-4 text-on-accent">
                      {activeDownloads > 99 ? '99+' : activeDownloads}
                    </span>
                  )}
                </span>
                <span className={on ? 'font-semibold' : ''}>{t.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
