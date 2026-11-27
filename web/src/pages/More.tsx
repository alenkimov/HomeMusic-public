import { openTelegramSetup, TELEGRAM_LINK_EVENT } from '../lib/telegram';
import { useEffect, useState, type ReactNode } from 'react';
import { STANDALONE } from '../lib/embedded';
import { useSession } from '../lib/session';
import { api } from '../api/client';
import { useDislikes } from '../lib/dislikes';
import { tracksWord } from '../lib/format';
import { useAsync, useWindowEvent } from '../lib/hooks';
import { useKeptLists } from '../lib/offline';
import { BrandIcon } from '../components/BrandIcon';
import { Icon, type IconName } from '../components/Icon';
import { ServiceBadge } from '../components/ServiceBadge';
import { PageHeader, cx, Badge } from '../components/ui';
import { notifyPermission, requestNotifyPermission, useActiveDownloads, type NotifyPermission } from '../lib/notify';
import { Link } from '../lib/router';
import { WEB_VERSION, useVersion } from '../lib/version';
import { confirmDialog, toast } from '../lib/ui';

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

// beforeinstallprompt fires early, possibly before this page mounts: keep it globally.
let deferredInstall: InstallPromptEvent | null = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredInstall = e as InstallPromptEvent;
});

export function MorePage() {
  const { me, logout } = useSession();
  const [perm, setPerm] = useState<NotifyPermission>(notifyPermission);
  const [canInstall, setCanInstall] = useState(!!deferredInstall);
  const downloads = useActiveDownloads();
  // what stands now behind the lines: asked once the menu opens, kept between visits — a line waits for none of it
  const quality = useAsync((s) => (STANDALONE ? api.quality(s) : Promise.resolve(null)), [], true, 'menu:quality');
  // what «Наведение порядка» found and waits for an answer: a count draws the eye to it
  const tidy = useAsync((s) => (STANDALONE ? api.tidy(s) : Promise.resolve(null)), [], true, 'menu:tidy');
  const tidyWaiting = tidy.data?.total ?? 0;
  // the backups, for an admin: a red word when they are not going off the server
  const version = useVersion(); // the number at the bottom, and a dot while a release is not opened yet
  const backup = useAsync((s) => (me.is_admin ? api.backup(s) : Promise.resolve(null)), [], true, 'menu:backup').data;
  // what the Hub is built of that is behind (yt-dlp first): a count by «О сервере»
  const behind = useAsync((s) => (me.is_admin ? api.updates(s) : Promise.resolve(null)), [], true, 'menu:updates').data?.outdated.length ?? 0;
  // the outside services broken or answering otherwise («Проверка сервисов»)
  const checksWrong = useAsync((s) => (me.is_admin ? api.serviceChecks(s) : Promise.resolve(null)), [], true, 'menu:checks').data?.attention ?? 0;
  const backupWord = !backup ? undefined : !backup.connected ? 'не настроены' : backup.stale || backup.error ? 'не отправляются' : undefined;
  // what waits for the user in «Загрузки»: files to choose, failed ones — a transfer's too
  const review = useAsync((s) => (STANDALONE ? api.downloadsOverview(s) : Promise.resolve(null)), [], true, 'menu:downloads')?.data?.review ?? 0;
  const telegram = useAsync(api.telegram, []);
  useWindowEvent(TELEGRAM_LINK_EVENT, () => telegram.reload(true));
  const kept = useKeptLists().tracks;
  const hiddenCount = useDislikes().length;
  const waiting = (quality.data?.rows ?? []).filter((r) => r.state === 'found').length;
  const weak = quality.data?.weak ?? 0;
  const telegramLabel = telegram.data?.ready ? 'подключён' : telegram.data?.configured ? 'не подключён' : telegram.data ? 'не настроен' : undefined;
  const standalone = window.matchMedia('(display-mode: standalone)').matches;

  useEffect(() => {
    const on = () => setCanInstall(!!deferredInstall);
    window.addEventListener('beforeinstallprompt', on);
    window.addEventListener('appinstalled', on);
    return () => {
      window.removeEventListener('beforeinstallprompt', on);
      window.removeEventListener('appinstalled', on);
    };
  }, []);

  const enableNotifications = async () => {
    const p = await requestNotifyPermission();
    setPerm(p);
    if (p === 'granted') toast('Уведомления включены', 'success');
    else if (p === 'denied') toast('Уведомления запрещены. Разрешите их в настройках сайта в браузере.', 'error');
  };

  const install = async () => {
    if (!deferredInstall) return;
    await deferredInstall.prompt();
    await deferredInstall.userChoice.catch(() => undefined);
    deferredInstall = null;
    setCanInstall(false);
  };

  const doLogout = async () => {
    if (await confirmDialog({ title: 'Выйти из аккаунта?', okLabel: 'Выйти' })) await logout();
  };

  // what stands now, in a word, at the right of the line
  const permText: Record<NotifyPermission, string> = { granted: 'включены', denied: 'запрещены', default: 'выключены', unsupported: 'недоступны' };
  const notifications = () => {
    if (perm === 'default') return void enableNotifications();
    toast(perm === 'granted' ? 'Уведомления включены. Выключить их можно в настройках сайта в браузере.' : perm === 'denied' ? 'Уведомления запрещены. Разрешите их в настройках сайта в браузере.' : 'Этот браузер не умеет показывать уведомления.', 'info');
  };

  return (
    <div className="pb-6">
      <PageHeader title="Меню" />
      {/* a line says one thing: what it is — and, at its right, what stands now. What a group is about is said by
          its heading; a computer has the groups in two columns */}
      <div className="grid items-start gap-5 px-4 lg:grid-cols-2 lg:px-6">
        <div className="space-y-5">
          <Group>
            <li className="flex min-h-[4.25rem] items-center gap-3.5 pl-3.5 pr-2">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-accent text-lg font-bold uppercase text-on-accent">{me.username.slice(0, 1)}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-semibold">{me.username}</span>
                <span className="block truncate text-[13px] text-muted">{[me.is_admin ? 'Администратор' : 'Пользователь', me.email].filter(Boolean).join(' · ')}{me.google_email && <> · <ServiceBadge service="google" />: {me.google_email}</>}</span>
              </span>
              <button type="button" onClick={doLogout} aria-label="Выйти из аккаунта" title="Выйти из аккаунта" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-danger hover:bg-danger/10">
                <Icon name="logout" size={20} />
              </button>
            </li>
          </Group>
          {STANDALONE && (
            <Group>
              <Item icon="download" tint="bg-[#3d8ef0]" to="/downloads" title="Загрузки" value={downloads > 0 ? `качается ${downloads > 99 ? '99+' : downloads}` : undefined} badge={review ? (review > 999 ? '999+' : String(review)) : undefined} badgeTone="warn" />
              <Item icon="folder" tint="bg-[#ef8b3a]" to="/files" title={me.is_admin ? 'Все файлы' : 'Мои файлы'} />
              <Item icon="sparkle" tint="bg-[#1f9d55]" to="/tidy" title="Наведение порядка" badge={tidyWaiting ? (tidyWaiting > 99 ? '99+' : String(tidyWaiting)) : undefined} />
              <Item icon="sliders" tint="bg-[#30b85c]" to="/quality" title="Качество звука" value={waiting ? `ждут решения: ${waiting}` : weak ? `слабых: ${weak}` : undefined} />
            </Group>
          )}
          {STANDALONE && (
            <Group>
              <Item icon="chart" tint="bg-[#2ba6bc]" to="/stats" title="Статистика" />
              <Item icon="history" tint="bg-[#7b61d9]" to="/history" title="История" />
              <Item icon="user" tint="bg-[#a45ad6]" to="/taste" title="Мои исполнители" />
              {me.artist_map && <Item icon="wave" tint="bg-[#d5483f]" to="/map" title="Карта похожих" />}
              <Item icon="download" tint="bg-[#1f9d55]" to="/offline" title="Офлайн" value={kept ? tracksWord(kept) : undefined} />
              <Item icon="ban" tint="bg-[#b0483f]" to="/hidden" title="Скрытое" value={hiddenCount ? String(hiddenCount) : undefined} />
            </Group>
          )}
        </div>
        <div className="space-y-5">
          <Group title="Администрирование">
            {me.is_admin && <Item icon="user" tint="bg-[#3d8ef0]" to="/admin" title="Пользователи и приглашения" />}
            {me.is_admin && <Item icon="install" tint="bg-[#6b7a8f]" to="/admin/backup" title="Резервные копии" badge={backupWord} badgeTone="warn" />}
            {me.is_admin && <Item icon="chart" tint="bg-[#7a5af0]" to="/admin/outbound" title="Прокси и запросы" />}
            {me.is_admin && <Item icon="check" tint="bg-[#1f9d8a]" to="/admin/checks" title="Проверка сервисов" badge={checksWrong ? String(checksWrong) : undefined} badgeTone="warn" />}
            {me.is_admin && <Item icon="help" tint="bg-[#6b7a8f]" to="/admin/about" title="О сервере" badge={behind ? String(behind) : undefined} badgeTone="warn" />}
            <Item icon="link" tint="bg-[#537fe2]" to="/integrations" title="Сторонние сервисы" />
            {me.is_admin && <Item icon="lyrics" tint="bg-[#c2528b]" to="/lyrics-sources" title="Тексты и перевод" />}
            <Item icon="send" brand="telegram" title="Telegram бот" value={telegramLabel} onClick={() => openTelegramSetup()} />
          </Group>
          <Group title="Импорт">
            <Item icon="transfer" brand="yandex" to="/import?from=yandex" title="Я.Музыка" />
            <Item icon="transfer" brand="vk" to="/import?from=vk" title="ВКонтакте" />
            <Item icon="transfer" brand="spotify" to="/import?from=spotify" title="Spotify" />
          </Group>
          {me.public_music_url && (
            <Group title="Сервер">
              <Item icon="external" brand="navidrome" href={me.public_music_url} title="Navidrome" />
            </Group>
          )}
          <Group title="Это устройство">
            <Item icon="bell" tint="bg-[#d9534f]" title="Уведомления" value={permText[perm]} onClick={notifications} />
            {canInstall && !standalone && <Item icon="install" tint="bg-[#30b85c]" title="Установить приложение" onClick={install} />}
            {/* the player is here: in the browser, and the app's own on Android */}
            <Item icon="install" tint="bg-[#1f9d55]" href="/app/latest.apk" title="Приложение для Android" />
          </Group>
        </div>
      </div>

      <p className="px-6 pt-6 text-center text-xs text-dim">
        <Link to="/whats-new" className="inline-flex items-center gap-1.5 hover:text-fg">
          HomeMusic {version.data?.version ?? WEB_VERSION} · <span className="text-accent">Что нового</span>
          {version.fresh && <span className="size-2 rounded-full bg-accent" aria-label="есть новое" />}
        </Link>
      </p>
    </div>
  );
}

/** Lines that belong together: one card, its heading over it in small. */
function Group({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section>
      {title && <h2 className="px-3.5 pb-1.5 text-[13px] text-muted">{title}</h2>}
      <ul className="overflow-hidden rounded-2xl bg-surface">{children}</ul>
    </section>
  );
}

/** A line of the menu: a mark, a name, then — at the right — what stands now (`value`: a count, a word) and the way
 * in. One line of text: what the place is for is said by the place itself. The rule between two lines starts where
 * the names do, not at the card's edge. */
function Item({
  icon,
  tint,
  title,
  value,
  badge,
  badgeTone = 'accent',
  to,
  href,
  onClick,
  brand,
}: {
  /** the mark of a music service instead of the icon (components/BrandIcon) */
  brand?: string;
  /** the colour of the line's mark (a class of the background; grey without one) */
  tint?: string;
  icon: IconName;
  title: string;
  value?: string;
  /** a count that asks to be looked at: a green pill (yellow: things that wait for the user's decision) */
  badge?: string;
  badgeTone?: 'accent' | 'warn';
  to?: string;
  href?: string;
  onClick?: () => void;
}) {
  const inner = (
    <>
      <span className={cx('flex h-[1.875rem] w-[1.875rem] shrink-0 items-center justify-center rounded-lg text-white', brand ? 'bg-fg/10' : (tint ?? 'bg-[#6b7684]'))}>
        {brand ? <BrandIcon service={brand} size={19} /> : <Icon name={icon} size={17} />}
      </span>
      <span className="flex min-h-12 min-w-0 flex-1 items-center gap-2 border-b border-fg/[0.07] pr-3.5 group-last/item:border-0">
        <span className="min-w-0 flex-1 truncate">{title}</span>
        {value && <span className="max-w-[55%] shrink-0 truncate text-sm text-muted">{value}</span>}
        {badge && <Badge solid size="md" tone={badgeTone}>{badge}</Badge>}
        <Icon name={href ? 'external' : 'chevron'} size={16} className="shrink-0 text-dim" />
      </span>
    </>
  );
  const cls = 'flex w-full items-center gap-3 pl-3.5 text-left hover:bg-fg/5';
  return (
    <li className="group/item">
      {to ? (
        <Link to={to} className={cls}>
          {inner}
        </Link>
      ) : href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className={cls}>
          {inner}
        </a>
      ) : (
        <button type="button" onClick={onClick} className={cls}>
          {inner}
        </button>
      )}
    </li>
  );
}
