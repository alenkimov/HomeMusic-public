import { openTelegramSetup } from '../lib/telegram';
import { useEffect, useState, type ReactNode } from 'react';
import { STANDALONE } from '../lib/embedded';
import { useSession } from '../lib/session';
import { BrandIcon } from '../components/BrandIcon';
import { Icon, type IconName } from '../components/Icon';
import { PageHeader, cx } from '../components/ui';
import { notifyPermission, requestNotifyPermission, useActiveDownloads, type NotifyPermission } from '../lib/notify';
import { Link } from '../lib/router';
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

  const permText: Record<NotifyPermission, string> = {
    granted: 'Включены',
    denied: 'Запрещены в настройках браузера',
    default: 'Нажмите, чтобы включить',
    unsupported: 'Не поддерживаются этим браузером',
  };

  return (
    <div>
      <PageHeader title="Меню" />
      <div className="mx-4 mb-4 flex items-center gap-3 rounded-2xl bg-surface p-4">
        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-accent text-lg font-bold uppercase text-on-accent">{me.username.slice(0, 1)}</span>
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold">{me.username}</div>
          <div className="truncate text-xs text-muted">
            {me.is_admin ? 'Администратор' : 'Пользователь'}
            {me.email ? ` · ${me.email}` : ''}
            {me.google_email ? ` · Google: ${me.google_email}` : ''}
          </div>
        </div>
      </div>

      {/* what a line does is said by its group; a computer has the groups in two columns */}
      <div className="grid items-start gap-4 px-4 lg:grid-cols-2">
        <div className="space-y-4">
          <Group>
            {STANDALONE && <Item icon="sparkle" tint="bg-[#8b6cf6]" to="/assistant" title="Ассистент" sub="Скриншот, список или просьба — найдёт и скачает" />}
            {STANDALONE && <Item icon="download" tint="bg-[#3d8ef0]" to="/downloads" title="Загрузки" sub="Что скачивается и что скачано" right={downloads > 0 ? <span className="min-w-5 rounded-full bg-accent px-1.5 text-center text-[11px] font-bold leading-5 text-on-accent">{downloads > 99 ? '99+' : downloads}</span> : undefined} />}
            <Item icon="upload" tint="bg-[#30b85c]" to="/upload" title="Загрузить файлы" sub="Свои аудиофайлы с телефона" />
            {STANDALONE && <Item icon="folder" tint="bg-[#ef8b3a]" to="/files" title="Мои файлы" sub="Альбомы, чьи файлы хранит хаб: правка, свой альбом" />}
          </Group>
          {STANDALONE && (
            <Group>
              <Item icon="chart" tint="bg-[#2ba6bc]" to="/stats" title="Статистика" sub="Что вы слушали: треки, исполнители, жанры; история из Spotify" />
            </Group>
          )}
        </div>
        <div className="space-y-4">
          <Group title="Импорт и интеграции">
            <Item icon="transfer" brand="yandex" to="/import?from=yandex" title="Перенести из Яндекс Музыки" sub="«Мне нравится», плейлисты, альбомы и исполнители" />
            <Item icon="transfer" brand="spotify" to="/import?from=spotify" title="Перенести из Spotify" sub="Плейлисты и «Любимые треки» из файлов CSV" />
            {STANDALONE && <Item icon="wave" brand="listenbrainz" to="/listenbrainz" title="ListenBrainz" sub="Необязательно: ещё один источник «Открытий недели»" />}
            <Item icon="send" brand="telegram" title="Telegram" sub="Свой бот: отправка треков себе, в группы и каналы" onClick={() => openTelegramSetup()} />
          </Group>
          <Group>
            {me.is_admin && <Item icon="lyrics" to="/lyrics-sources" title="Тексты песен" sub="Источники текстов и их порядок, поиск для библиотеки" />}
            {me.is_admin && <Item icon="user" to="/admin" title="Пользователи и приглашения" sub="Расходы на ИИ, песни, коды для новых людей" />}
            {/* the player is here: in the browser, and the app's own on Android */}
            <Item icon="install" href="/app/latest.apk" title="Приложение для Android" sub="HomeMusic: офлайн, плеер в шторке, тот же аккаунт" />
            {me.public_music_url && <Item icon="external" href={me.public_music_url} title="Navidrome" sub="Веб-интерфейс библиотеки" />}
            <Item
              icon="bell"
              title="Уведомления"
              sub={permText[perm]}
              onClick={perm === 'default' ? enableNotifications : undefined}
              right={perm === 'granted' ? <Icon name="check" size={20} className="text-accent" /> : undefined}
            />
            {canInstall && !standalone && <Item icon="install" title="Установить приложение" sub="На домашний экран, с «Поделиться» → HomeMusic" onClick={install} />}
            <Item icon="logout" title="Выйти" onClick={doLogout} danger />
          </Group>
        </div>
      </div>

      <p className="px-6 py-6 text-center text-xs text-dim">HomeMusic · личный музыкальный сервис</p>
    </div>
  );
}

/** Lines that belong together: one card, a thin rule between two lines. */
function Group({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section>
      {title && <h2 className="px-1 pb-2 text-[13px] font-semibold uppercase tracking-wide text-muted">{title}</h2>}
      <ul className="overflow-hidden rounded-2xl bg-surface">{children}</ul>
    </section>
  );
}

function Item({
  icon,
  tint,
  title,
  sub,
  to,
  href,
  onClick,
  right,
  danger,
  brand,
}: {
  /** the mark of a music service instead of the icon (components/BrandIcon) */
  brand?: string;
  /** the colour of the line's mark (a class of the background; grey without one) */
  tint?: string;
  icon: IconName;
  title: string;
  sub?: string;
  to?: string;
  href?: string;
  onClick?: () => void;
  right?: ReactNode;
  danger?: boolean;
}) {
  const inner = (
    <>
      <span className={cx('flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] text-white', brand ? 'bg-fg/10' : danger ? 'bg-danger' : (tint ?? 'bg-[#6b7684]'))}>
        {brand ? <BrandIcon service={brand} size={20} /> : <Icon name={icon} size={18} />}
      </span>
      <span className="min-w-0 flex-1">
        <span className={cx('block', danger && 'text-danger')}>{title}</span>
        {sub && <span className="block truncate text-xs text-muted">{sub}</span>}
      </span>
      {right ?? ((to || href || onClick) && <Icon name={href ? 'external' : 'chevron'} size={18} className="text-dim" />)}
    </>
  );
  const cls = 'flex min-h-14 w-full items-center gap-3.5 px-3.5 py-2 text-left hover:bg-fg/5';
  return (
    <li className="border-b border-fg/5 last:border-0">
      {to ? (
        <Link to={to} className={cls}>
          {inner}
        </Link>
      ) : href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className={cls}>
          {inner}
        </a>
      ) : onClick ? (
        <button type="button" onClick={onClick} className={cls}>
          {inner}
        </button>
      ) : (
        <div className={cls}>{inner}</div>
      )}
    </li>
  );
}
