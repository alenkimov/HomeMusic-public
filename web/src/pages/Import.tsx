import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { ImportCollectionOut, ImportMode, ImportOut, ImportSource } from '../api/types';
import { ServiceBadge, ServiceIcon, ServiceText } from '../components/ServiceBadge';
import { PlaylistsPick } from '../components/PlaylistsButton';
import { AfterImport } from './Upload';
import { Icon, type IconName } from '../components/Icon';
import { Button, Checkbox, ErrorState, Loading, Notice, PageHeader, ProgressBar, Radio, SectionTitle, SkeletonRows, Spinner, cx, Badge } from '../components/ui';
import { LIKED_MARK, PlaylistRow, playlistLook } from '../components/Playlist';
import { useSession } from '../lib/session';
import { EMBEDDED } from '../lib/embedded';
import { useHubEvent } from '../lib/events';
import { fmtDuration, fmtSize, fmtWhen, plural, tracksWord } from '../lib/format';
import { useAsync, useInterval, useWindowEvent } from '../lib/hooks';
import { enc, Link, navigate } from '../lib/router';
import { copyText } from '../lib/share';
import { attempt, confirmDialog, toast } from '../lib/ui';

/** «Перенести музыку»: a user's collection from Yandex Music, VK («Моя музыка» and the playlists: a sign-in with the
 * login and the password, the code VK sends is typed here) or Spotify (a saved account or a playlist link) — ♥, playlists in
 * their order, liked albums and artists. The Hub reads the lists only (no audio): sign-in by a code on ya.ru/device,
 * a token, or an open profile link. Economy (the default): playlists and ♥ at once from what the library has, the
 * rest plays from the sources and is downloaded once listened to; «Скачать всё» downloads everything in the
 * background, below the users' own downloads. */

const KIND_ICON: Record<ImportCollectionOut['kind'], IconName> = {
  likes: 'heart',
  playlist: 'playlist',
  albums: 'disc',
  artists: 'user',
  dislikes: 'x',
};

const ACTIVE = ['auth', 'fetching', 'running'];

function statusLine(imp: ImportOut): string {
  const st = imp.stats;
  switch (imp.status) {
    case 'auth':
      return SOURCES[imp.source]?.waits ?? 'Ждём входа';
    case 'fetching':
      return 'Читаем коллекцию…';
    case 'preview':
      return `Найдено ${tracksWord(st.tracks)} — выберите, что перенести`;
    case 'running':
      return st.pending ? `Сопоставляем: осталось ${st.pending}` : st.downloading ? `Качается ${st.downloading}, скачано ${st.done}` : 'Переносим…';
    case 'paused':
      return 'На паузе';
    case 'done':
      return `Перенесено${imp.finished_at ? ' ' + fmtWhen(imp.finished_at) : ''}`;
    case 'cancelled':
      return 'Остановлен';
    default:
      return imp.error || 'Не получилось';
  }
}

export const SOURCES: Record<ImportSource, { name: string; page: string; waits: string }> = {
  yandex: { name: 'Яндекс Музыка', page: 'Перенос из Яндекс Музыки', waits: 'Ждём входа в Яндекс' },
  vk: { name: 'ВКонтакте', page: 'Перенос из ВКонтакте', waits: 'Ждём код для входа в ВК' },
  spotify: { name: 'Spotify', page: 'Перенос из Spotify', waits: 'Ждём входа в Spotify' },
};

const sourceName = (source: ImportSource): string => SOURCES[source]?.name ?? source;

function sourceTitle(imp: ImportOut): string {
  return `${sourceName(imp.source)}${imp.title ? ` · ${imp.title}` : ''}`;
}

/** The page of the service a transfer is from: where «Назад» leads, and where one is started anew. */
const sourcePage = (source: ImportSource): string => `/import?from=${source}`;

function SavedAccounts({ source, disabled }: { source: ImportSource; disabled: boolean }) {
  const response = useAsync((signal) => api.importAccounts(source, signal), [source]);
  const [chosen, setChosen] = useState<number>();
  const [link, setLink] = useState('');
  const [busy, setBusy] = useState(false);
  const accounts = response.data ?? [];
  const selected = accounts.find((row) => row.id === chosen) ?? accounts[0];
  if (!selected) return null;

  async function start() {
    setBusy(true);
    const result = await attempt(() => api.createImport({
      source, method: 'saved', connection_id: selected.id, url: link.trim() || undefined,
    }));
    setBusy(false);
    if (result) navigate(`/import/${result.id}`);
  }

  async function disconnect() {
    if (!await confirmDialog({ title: 'Удалить сохранённый вход ВКонтакте?', okLabel: 'Удалить', danger: true })) return;
    setBusy(true);
    const result = await attempt(() => api.disconnectVk(selected.id), 'Сохранённый вход удалён');
    setBusy(false);
    if (result) response.reload();
  }

  return <form className="card mx-4 mt-3 space-y-3 p-4 lg:mx-6" onSubmit={(e) => { e.preventDefault(); void start(); }}>
    <label className="block text-sm font-semibold">
      <span className="mb-1 block">Сохранённый аккаунт</span>
      <select className="input w-full" value={selected.id} onChange={(e) => setChosen(Number(e.target.value))}>
        {accounts.map((row) => <option key={row.id} value={row.id}>{row.account || 'Сохранённый вход'}</option>)}
      </select>
    </label>
    {source === 'spotify' && <label className="block">
      <span className="mb-1 block text-xs text-muted">Ссылка на плейлист. Оставьте пустым для «Любимых треков».</span>
      <input className="input w-full" value={link} onChange={(e) => setLink(e.target.value)} inputMode="url" placeholder="https://open.spotify.com/playlist/…" />
    </label>}
    <div className="flex flex-wrap gap-2">
      <Button type="submit" variant="primary" icon="transfer" busy={busy} disabled={disabled}>Открыть предпросмотр</Button>
      {source === 'vk' && <Button type="button" variant="ghost" disabled={busy || disabled} onClick={() => void disconnect()}>Удалить вход</Button>}
    </div>
  </form>;
}

// --- the list of imports + a new one ---------------------------------------------------------------

/** `from`: the page of one service — its past transfers and its way in («Ещё» has an entry for each); none: all. */
export function ImportsPage({ from = null }: { from?: ImportSource | null }) {
  const res = useAsync((s) => api.imports(s), []);
  const past = (res.data ?? []).filter((imp) => !from || imp.source === from);
  useHubEvent('import', () => res.reload(true));
  const [busy, setBusy] = useState<'device' | 'profile' | 'token' | 'vk' | 'vk-token' | 'spotify-link' | null>(null);
  const [vkLogin, setVkLogin] = useState('');
  const [vkPassword, setVkPassword] = useState('');
  const [vkToken, setVkToken] = useState('');
  const [profile, setProfile] = useState('');
  const [token, setToken] = useState('');
  const [spotifyLink, setSpotifyLink] = useState('');

  const start = async (method: 'device' | 'profile' | 'token') => {
    setBusy(method);
    const r = await attempt(() =>
      api.createImport(method === 'profile' ? { method, url: profile.trim() } : method === 'token' ? { method, token: token.trim() } : { method }),
    );
    setBusy(null);
    if (r) navigate(`/import/${r.id}`);
  };

  const startVk = async (how: 'vk' | 'vk-token') => {
    setBusy(how);
    const r = await attempt(() => api.createImport(how === 'vk' ? { source: 'vk', method: 'password', login: vkLogin.trim(), password: vkPassword } : { source: 'vk', method: 'token', token: vkToken.trim() }));
    setBusy(null);
    if (!r) return;
    setVkPassword('');
    navigate(`/import/${r.id}`);
  };

  const startSpotify = async () => {
    setBusy('spotify-link');
    const r = await attempt(() => api.createImport({ source: 'spotify', method: 'link', url: spotifyLink.trim() }));
    setBusy(null);
    if (r) navigate(`/import/${r.id}`);
  };

  return (
    <div>
      <PageHeader title={from ? <ServiceBadge service={from} label={SOURCES[from].page} size={18} /> : 'Перенести музыку'} back={EMBEDDED ? '/library' : '/more'} />
      {res.loading ? (
        <SkeletonRows n={2} />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : past.length ? (
        <ul className="mx-4 lg:mx-6 mb-2 overflow-hidden rounded-2xl bg-surface">
          {past.map((imp) => (
            <li key={imp.id} className="border-b border-line/60 last:border-0">
              <Link to={`/import/${imp.id}`} className="flex min-h-16 items-center gap-3 px-4 py-2 hover:bg-fg/5">
                <ServiceIcon service={imp.source} size={22} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{sourceTitle(imp)}</span>
                  <span className={cx('block truncate text-xs', imp.status === 'failed' ? 'text-danger' : 'text-muted')}><ServiceText text={statusLine(imp)} /></span>
                </span>
                {ACTIVE.includes(imp.status) ? <Spinner size={16} className="text-accent" /> : <Icon name="chevron" size={18} className="text-dim" />}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}

      {(!from || from === 'yandex') && (
      <>
      {!from && <SectionTitle><ServiceBadge service="yandex" label="Из Яндекс Музыки" size={18} /></SectionTitle>}
      <SavedAccounts source="yandex" disabled={!!busy} />
      <div className={cx('card mx-4 lg:mx-6 space-y-3 p-4', from && 'mt-3')}>
        <p className="text-sm text-muted">«Мне нравится», плейлисты (и закрытые), любимые альбомы и исполнители. Подписка Плюс не нужна.</p>
        <Button variant="primary" block icon="lock" busy={busy === 'device'} disabled={!!busy} onClick={() => start('device')}>
          <ServiceText text="Войти через Яндекс" />
        </Button>
        <p className="text-xs text-dim">Хаб покажет код — введите его на ya.ru/device. Хаб читает только списки треков, пароль он не видит. Вход можно удалить в любой момент.</p>
      </div>

      <div className="card mx-4 lg:mx-6 mt-3 space-y-2 p-4">
        <div className="font-semibold">Без входа — по ссылке на профиль</div>
        <p className="text-xs text-muted">Если медиатека открыта всем. Закрытые плейлисты и «Не нравится» так не перенести.</p>
        <input className="input" value={profile} onChange={(e) => setProfile(e.target.value)} placeholder="music.yandex.ru/users/логин/playlists/3" inputMode="url" autoCapitalize="off" autoCorrect="off" />
        <Button block busy={busy === 'profile'} disabled={!!busy || !profile.trim()} onClick={() => start('profile')}>
          Прочитать профиль
        </Button>
      </div>

      <details className="card mx-4 lg:mx-6 mt-3 p-4">
        <summary className="cursor-pointer text-sm text-muted"><ServiceText text="Вставить токен Яндекс Музыки вручную" /></summary>
        <div className="mt-3 space-y-2">
          <input className="input" type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Токен" autoComplete="off" />
          <Button block busy={busy === 'token'} disabled={!!busy || !token.trim()} onClick={() => start('token')}>
            Продолжить с токеном
          </Button>
        </div>
      </details>
      </>
      )}

      {(!from || from === 'vk') && (
      <>
      {!from && <SectionTitle><ServiceBadge service="vk" label="Из ВКонтакте" size={18} /></SectionTitle>}
      <SavedAccounts source="vk" disabled={!!busy} />
      <form
        className={cx('card mx-4 lg:mx-6 space-y-3 p-4', from && 'mt-3')}
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy && vkLogin.trim() && vkPassword) void startVk('vk');
        }}
      >
        <p className="text-sm text-muted">«Моя музыка» станет плейлистом «ВКонтакте», ваши плейлисты — плейлистами. Перед переносом вы выберете, что брать.</p>
        <input className="input" value={vkLogin} onChange={(e) => setVkLogin(e.target.value)} placeholder="Телефон или почта" aria-label="Логин ВКонтакте" autoComplete="username" autoCapitalize="off" autoCorrect="off" />
        <input className="input" type="password" value={vkPassword} onChange={(e) => setVkPassword(e.target.value)} placeholder="Пароль" aria-label="Пароль ВКонтакте" autoComplete="current-password" />
        <Button type="submit" variant="primary" block icon="lock" busy={busy === 'vk'} disabled={!!busy || !vkLogin.trim() || !vkPassword}>
          <ServiceText text="Войти во ВКонтакте" />
        </Button>
        <p className="text-xs text-dim">
          У ВК нет открытого доступа к музыке, поэтому Хаб входит как приложение ВК для Android: пароль уходит во ВК один раз и не сохраняется. Если ВК попросит код из SMS, Хаб спросит его у вас.
          Придёт уведомление о новом входе — это он. Хаб читает только списки треков.
        </p>
      </form>

      <details className="card mx-4 lg:mx-6 mt-3 p-4">
        <summary className="cursor-pointer text-sm text-muted"><ServiceText text="Вставить токен ВКонтакте вручную" /></summary>
        <div className="mt-3 space-y-2">
          <p className="text-xs text-dim">Подойдёт токен приложения ВК для Android с доступом к аудио (его выдаёт, например, vkpymusic).</p>
          <input className="input" type="password" value={vkToken} onChange={(e) => setVkToken(e.target.value)} placeholder="Токен" autoComplete="off" />
          <Button block busy={busy === 'vk-token'} disabled={!!busy || !vkToken.trim()} onClick={() => startVk('vk-token')}>
            Продолжить с токеном
          </Button>
        </div>
      </details>
      </>
      )}

      {(!from || from === 'spotify') && (
      <>
      {!from && <SectionTitle><ServiceBadge service="spotify" label="Из Spotify" size={18} /></SectionTitle>}
      <SavedAccounts source="spotify" disabled={!!busy} />
      <form className="card mx-4 mt-3 space-y-3 p-4 lg:mx-6" onSubmit={(e) => { e.preventDefault(); void startSpotify(); }}>
        <label htmlFor="spotify-playlist-link" className="block text-sm font-semibold">Публичный плейлист по ссылке</label>
        <input id="spotify-playlist-link" className="input w-full" inputMode="url" placeholder="https://open.spotify.com/playlist/…" value={spotifyLink} onChange={(e) => setSpotifyLink(e.target.value)} />
        <Button type="submit" variant="primary" icon="transfer" busy={busy === 'spotify-link'} disabled={!!busy || !spotifyLink.trim()}>
          Открыть предпросмотр
        </Button>
      </form>
      {!EMBEDDED && (
        <Link to="/stats" className="card mx-4 lg:mx-6 mt-3 flex items-center gap-3 p-4 hover:bg-hover">
          <ServiceIcon service="spotify" size={22} />
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold">История прослушиваний</span>
            <span className="block text-xs text-muted">Что вы слушали в Spotify за все годы — импортируется на странице «Статистика»</span>
          </span>
          <Icon name="chevron" size={18} className="text-dim" />
        </Link>
      )}
      </>
      )}

    </div>
  );
}

// --- one import ---------------------------------------------------------------------------------------

export function ImportPage({ id }: { id: number }) {
  const res = useAsync((s) => api.importGet(id, s), [id]);
  useHubEvent('import', (d) => d.id === id && res.reload(true));
  useHubEvent('reconnect', () => res.reload(true));
  const imp = res.data;
  // SSE carries status changes; counters (matching, downloads) are refreshed now and then
  useInterval(() => res.reload(true), imp && ACTIVE.includes(imp.status) ? (imp.status === 'running' ? 10_000 : 4_000) : null);

  return (
    <div>
      <PageHeader title="Перенос музыки" sub={imp ? <ServiceBadge service={imp.source} label={sourceTitle(imp)} /> : undefined} back={imp ? sourcePage(imp.source) : '/import'} />
      {res.loading ? (
        <Loading />
      ) : res.error || !imp ? (
        <ErrorState text={res.error ?? 'Не найдено'} onRetry={() => res.reload()} />
      ) : imp.status === 'auth' ? (
        imp.auth && imp.auth.kind !== 'device' ? <VkAuthStep imp={imp} onChange={(r) => res.setData(r)} /> : <AuthStep imp={imp} />
      ) : imp.status === 'fetching' ? (
        <Loading text={<>Читаем коллекцию <ServiceBadge service={imp.source} /></>} />
      ) : imp.status === 'preview' ? (
        <Preview imp={imp} onStarted={(r) => res.setData(r)} />
      ) : imp.status === 'failed' && !imp.started_at ? (
        <div className="space-y-4 px-4 lg:px-6 pt-2">
          <Notice kind="error">{imp.error || 'Не получилось'}</Notice>
          <Button block variant="primary" onClick={() => navigate(sourcePage(imp.source), { replace: true })}>
            Начать заново
          </Button>
        </div>
      ) : (
        <Progress imp={imp} onChange={(r) => res.setData(r)} />
      )}
    </div>
  );
}

function AuthStep({ imp }: { imp: ImportOut }) {
  const auth = imp.auth;
  const [left, setLeft] = useState(0);
  useEffect(() => {
    if (!auth) return;
    const tick = () => setLeft(Math.max(0, Math.round((Date.parse(auth.expires_at + (auth.expires_at.endsWith('Z') ? '' : 'Z')) - Date.now()) / 1000)));
    tick();
    const t = window.setInterval(tick, 1000);
    return () => window.clearInterval(t);
  }, [auth]);
  if (!auth) return <Loading />;
  const copy = async () => {
    if (await copyText(auth.user_code)) toast('Код скопирован', 'success');
    else toast('Не удалось скопировать — введите код вручную', 'info');
  };
  return (
    <div className="space-y-4 px-4 lg:px-6 pt-2">
      <ol className="list-decimal space-y-1 pl-5 text-sm text-muted">
        <li>Откройте ya.ru/device и войдите в свой Яндекс ID.</li>
        <li><ServiceText text="Введите код ниже и подтвердите доступ для «Яндекс Музыки»." /></li>
        <li>Вернитесь сюда — Хаб сам продолжит.</li>
      </ol>
      <div className="card flex flex-col items-center gap-3 p-5">
        <div className="select-all break-all text-center font-mono text-3xl font-bold tracking-[0.2em]">{auth.user_code}</div>
        <div className="flex w-full flex-wrap justify-center gap-2">
          <Button small icon="paste" onClick={copy}>
            Скопировать
          </Button>
          <a href={auth.verification_url} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-9 items-center gap-2 rounded-full bg-accent px-3.5 text-sm font-semibold text-on-accent">
            <Icon name="external" size={18} />
            Открыть ya.ru/device
          </a>
        </div>
      </div>
      <div className="flex items-center justify-center gap-2 text-sm text-muted">
        <Spinner size={16} />
        {left > 0 ? `Ждём подтверждения · код действует ${fmtDuration(left)}` : 'Код устарел — начните заново'}
      </div>
    </div>
  );
}

/** VK asked for more at the sign-in: the code it sent (an SMS, a call, the authenticator), or its captcha — a page
 * of VK's to pass, whose answer (`success_token`) is given here. The Hub signs in again with what is typed. */
function VkAuthStep({ imp, onChange }: { imp: ImportOut; onChange: (r: ImportOut) => void }) {
  const auth = imp.auth!;
  const captcha = auth.kind === 'captcha';
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const send = async () => {
    if (!value.trim() || busy) return;
    setBusy(true);
    const r = await attempt(() => api.importAuth(imp.id, captcha ? { captcha: value.trim() } : { code: value.trim() }));
    setBusy(false);
    if (!r) return;
    setValue('');
    onChange(r);
  };
  return (
    <form
      className="space-y-4 px-4 lg:px-6 pt-2"
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      <Notice kind={auth.wrong ? 'error' : 'info'}><ServiceText text={auth.hint || (captcha ? 'ВК просит подтвердить, что вы не робот' : 'ВК ждёт код подтверждения')} /></Notice>
      {captcha ? (
        <div className="card space-y-3 p-4">
          <ol className="list-decimal space-y-1 pl-5 text-sm text-muted">
            <li>Откройте страницу проверки ВК и пройдите её.</li>
            <li>
              Проверка отвечает ключом <span className="font-mono text-fg">success_token</span>: он виден в инструментах разработчика браузера (вкладка «Сеть», ответ запроса <span className="font-mono text-fg">captchaNotRobot.check</span>).
            </li>
            <li>Вставьте ключ сюда.</li>
          </ol>
          <a href={auth.verification_url} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-9 items-center gap-2 rounded-full bg-accent px-3.5 text-sm font-semibold text-on-accent">
            <Icon name="external" size={18} />
            Открыть проверку ВК
          </a>
          <input className="input" value={value} onChange={(e) => setValue(e.target.value)} placeholder="success_token" aria-label="Ключ проверки" autoComplete="off" autoCapitalize="off" autoCorrect="off" />
          <p className="text-xs text-dim">Это неудобно, и проще подождать: капчу ВК обычно показывает после нескольких входов подряд. Через час-другой её может не быть — тогда начните перенос заново.</p>
        </div>
      ) : (
        <input className="input text-center font-mono text-2xl tracking-[0.3em]" value={value} onChange={(e) => setValue(e.target.value)} placeholder="Код" aria-label="Код подтверждения" inputMode="numeric" autoComplete="one-time-code" autoFocus />
      )}
      <Button type="submit" variant="primary" block busy={busy} disabled={!value.trim()}>
        Продолжить
      </Button>
      <p className="text-center text-xs text-dim">Вход ждёт 15 минут. Пароль хранится в Хабе зашифрованным только на это время и стирается, как только вход завершён.</p>
    </form>
  );
}

async function deleteTransfer(imp: ImportOut, setBusy: (action: 'delete' | null) => void) {
  setBusy('delete');
  const draft = !imp.started_at;
  const confirmed = await confirmDialog({
    title: draft ? 'Удалить черновик переноса?' : 'Удалить перенос?',
    body: draft ? 'Предварительный список и настройки этого переноса будут удалены.' : 'Запись о переносе будет удалена из истории.',
    okLabel: 'Удалить перенос', danger: 'solid',
  });
  const removed = confirmed && await attempt(() => api.importDelete(imp.id));
  setBusy(null);
  if (removed) navigate(sourcePage(imp.source), { replace: true });
}

function Preview({ imp, onStarted }: { imp: ImportOut; onStarted: (r: ImportOut) => void }) {
  const [picked, setPicked] = useState<Set<number>>(() => new Set(imp.collections.filter((c) => c.selected && c.kind !== 'dislikes').map((c) => c.id)));
  const [mode, setMode] = useState<ImportMode>('economy');
  const [albums, setAlbums] = useState(false);
  const [keep, setKeep] = useState(false);
  const [busy, setBusy] = useState<'start' | 'delete' | null>(null);
  const toggle = (cid: number, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(cid);
      else next.delete(cid);
      return next;
    });

  const st = imp.stats;
  // the Hub counts distinct tracks of all collections; the share of the picked ones scales it
  const est = useMemo(() => {
    const tracks = imp.collections.filter((c) => c.kind === 'likes' || c.kind === 'playlist');
    const all = tracks.reduce((n, c) => n + c.count - c.owned, 0);
    const sel = tracks.filter((c) => picked.has(c.id)).reduce((n, c) => n + c.count - c.owned, 0);
    const k = all ? Math.min(1, sel / all) : 0;
    return { tracks: Math.round(st.to_download * k), bytes: Math.round(st.est_bytes * k) };
  }, [imp, picked, st]);
  const lowSpace = st.free_bytes != null && est.bytes > st.free_bytes * 0.9;
  const albumsPicked = imp.collections.some((c) => c.kind === 'albums' && picked.has(c.id));

  // «После переноса»: what every transferred track also gets once the library has it
  const [favoriteChoice, setFavoriteChoice] = useState<boolean | null>(null);
  const [toPlaylists, setToPlaylists] = useState<{ id: string; name: string }[]>([]);
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [plPicker, setPlPicker] = useState(false);
  const trackLists = imp.collections.filter((c) => (c.kind === 'likes' || c.kind === 'playlist') && picked.has(c.id));
  const moved = trackLists.reduce((n, c) => n + c.count, 0);
  const like = favoriteChoice ?? trackLists.some((c) => c.kind === 'likes');
  const star = favoriteChoice === true && trackLists.some((c) => c.kind === 'playlist');
  const setLike = (value: boolean) => { if (value !== like) setFavoriteChoice(value); };
  const made = trackLists.filter((c) => c.kind === 'playlist' || !like).map((c) => ({ id: c.id, name: c.title }));

  const go = async () => {
    setBusy('start');
    const r = await attempt(() =>
      api.importStart(imp.id, {
        collection_ids: [...picked], mode, download_albums: mode === 'full' && albums, keep_token: keep,
        copy_likes: like, star, playlist_ids: toPlaylists.map((p) => p.id), tag_ids: tagIds,
      }),
    );
    setBusy(null);
    if (r) {
      onStarted(r);
      toast(mode === 'economy' ? 'Треки появятся по мере сопоставления' : 'Переносим: загрузка пойдёт в фоне', 'success');
    }
  };

  return (
    <div className="pb-10">
      <p className="px-4 lg:px-6 pb-1 text-sm text-muted">
        Найдено {tracksWord(st.tracks)}, из них уже в библиотеке {st.owned}. Отметьте, что перенести.
      </p>
      <ul className="mx-4 lg:mx-6 overflow-hidden rounded-2xl bg-surface">
        {imp.collections.map((c) => (
          <li key={c.id} className="flex items-center gap-1 border-b border-line/60 pr-2 last:border-0">
            <Checkbox checked={picked.has(c.id)} onChange={(v) => toggle(c.id, v)} label={c.title} />
            <Icon name={KIND_ICON[c.kind]} size={18} className="shrink-0 text-muted" />
            <span className="min-w-0 flex-1 py-2 pl-2">
              <span className="block truncate">{c.title}</span>
              <span className="block truncate text-xs text-muted">{collectionSub(c)}</span>
            </span>
          </li>
        ))}
      </ul>

      <SectionTitle>Как переносить</SectionTitle>
      <div className="mx-4 lg:mx-6 space-y-2">
        <ModeCard on={mode === 'economy'} onClick={() => setMode('economy')} title="Экономно" badge="рекомендуем">
          Треки появятся в выбранных списках по мере сопоставления. Файл скачивается после прослушивания.
        </ModeCard>
        <ModeCard on={mode === 'full'} onClick={() => setMode('full')} title="Скачать всё">
          Около {tracksWord(est.tracks)}, ~{fmtSize(est.bytes)}
          {st.free_bytes != null ? ` (свободно ${fmtSize(st.free_bytes)})` : ''}. Качается в фоне несколько часов, обычные загрузки идут первыми.
        </ModeCard>
        {mode === 'full' && lowSpace && <Notice kind="error">На диске не хватит места — выберите меньше или режим «Экономно».</Notice>}
        {mode === 'full' && albumsPicked && (
          <label className="flex items-center gap-1 text-sm">
            <Checkbox checked={albums} onChange={setAlbums} label="Скачивать любимые альбомы целиком" />
            Скачивать любимые альбомы целиком
          </label>
        )}
        {imp.has_token && !imp.keep_token && (
          <label className="flex items-center gap-1 text-sm">
            <Checkbox checked={keep} onChange={setKeep} label="Запомнить вход" />
            <span className="min-w-0">Запомнить вход, чтобы потом подтянуть новое</span>
          </label>
        )}
      </div>
      {moved > 0 && (
        <>
          <AfterImport label="После переноса" className="mx-4 lg:mx-6 mt-4" made={made} count={moved} like={like} onLike={setLike} playlists={toPlaylists} onPlaylists={setToPlaylists} onPickPlaylists={() => setPlPicker(true)} tagIds={tagIds} onTags={setTagIds} />
          {(star || toPlaylists.length > 0 || tagIds.length > 0) && (
            <p className="px-5 pt-1.5 text-xs text-dim lg:px-7">Трек получает это, когда попадает в библиотеку{mode === 'economy' ? ' — в режиме «Экономно» после того, как вы его послушали' : ''}.</p>
          )}
        </>
      )}
      {plPicker && (
        <PlaylistsPick
          title={`${tracksWord(moved)} — в плейлисты`}
          picked={toPlaylists}
          liked={like}
          onClose={() => setPlPicker(false)}
          onDone={(list, liked) => {
            setToPlaylists(list);
            setLike(liked);
            setPlPicker(false);
          }}
        />
      )}
      <div className="flex gap-2 px-4 pt-4 lg:px-6">
        <Button variant="danger-solid" icon="trash" className="min-w-0 text-balance max-sm:px-3" busy={busy === 'delete'} disabled={!!busy} onClick={() => void deleteTransfer(imp, setBusy)}>
          Удалить перенос
        </Button>
        <Button variant="primary" className="min-w-0 flex-1 max-sm:px-3" busy={busy === 'start'} disabled={!!busy || !picked.size || (mode === 'full' && lowSpace)} onClick={go}>
          Перенести
        </Button>
      </div>
    </div>
  );
}

function collectionSub(c: ImportCollectionOut): string {
  if (c.kind === 'albums') return `${c.count} ${plural(c.count, ['альбом', 'альбома', 'альбомов'])} · ★ и по желанию загрузка`;
  if (c.kind === 'artists') return `${c.count} ${plural(c.count, ['исполнитель', 'исполнителя', 'исполнителей'])} · ★ в библиотеке`;
  if (c.kind === 'dislikes') return `${tracksWord(c.count)} · только запомнить`;
  return `${tracksWord(c.count)}${c.owned ? ` · ${c.owned} уже есть` : ''}`;
}

function ModeCard({ on, onClick, title, badge, children }: { on: boolean; onClick: () => void; title: string; badge?: string; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={on} className={cx('card flex w-full items-start gap-3 p-4 text-left ring-1 transition', on ? 'ring-accent' : 'ring-transparent')}>
      <span className="pt-0.5">
        <Radio checked={on} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2 font-semibold">
          {title}
          {badge && <Badge>{badge}</Badge>}
        </span>
        <span className="mt-1 block text-sm text-muted">{children}</span>
      </span>
    </button>
  );
}

function Progress({ imp, onChange }: { imp: ImportOut; onChange: (r: ImportOut) => void }) {
  const st = imp.stats;
  const [busy, setBusy] = useState<string | null>(null);
  const running = imp.status === 'running';
  const paused = imp.status === 'paused';
  const showProgress = running || paused;
  const matched = st.tracks - st.pending;
  const downloadTotal = st.done + st.downloading + st.failed;
  const downloadCount = downloadTotal > 0 ? `${st.done} из ${downloadTotal}`
    : imp.mode === 'economy' ? 'По мере прослушивания' : 'Очередь пока пуста';
  const run = async (key: string, fn: () => Promise<ImportOut | void>, ok?: string) => {
    setBusy(key);
    const r = await attempt(fn, ok);
    setBusy(null);
    if (r) onChange(r);
  };

  return (
    <div className="pb-10">
      <div className="card mx-4 lg:mx-6 space-y-3 p-4">
        <div className="flex items-center gap-2 font-semibold">
          {running ? <Spinner size={16} className="text-accent" /> : <Icon name={paused ? 'pause' : imp.status === 'done' ? 'check' : 'alert'} size={18} className={paused ? 'text-muted' : imp.status === 'done' ? 'text-accent' : 'text-warn'} />}
          {running ? 'Переносим' : paused ? 'На паузе' : imp.status === 'done' ? 'Перенесено' : 'Остановлено'}
        </div>
        {st.tracks > 0 && (showProgress || st.pending > 0) && (
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm text-muted">
              <span>Сопоставление</span>
              <span className="tabular-nums">{matched} из {st.tracks}</span>
            </div>
            <ProgressBar label="Сопоставление" value={matched / st.tracks} />
          </div>
        )}
        {showProgress && (
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm text-muted">
              <span>Скачивание</span>
              <span className="tabular-nums">{downloadCount}</span>
            </div>
            <ProgressBar label="Скачивание" value={downloadTotal > 0 ? st.done / downloadTotal : 0} />
            {st.downloading > 0 && (
              <Link to={`/downloads?import=${imp.id}&tab=now`} className="inline-flex items-center gap-1 text-xs text-muted hover:text-fg">
                Что качается сейчас — в «Загрузках»
                <Icon name="chevron" size={12} />
              </Link>
            )}
          </div>
        )}
        {st.failed > 0 && (
          <div className="flex flex-wrap items-center gap-3 rounded-xl bg-warn/10 p-3">
            <Icon name="alert" size={20} className="shrink-0 text-warn" />
            <span className="min-w-0 flex-1 basis-56 text-sm">
              {st.failed.toLocaleString('ru')} {plural(st.failed, ['трек', 'трека', 'треков'])} без вас не скачается: хаб не нашёл подходящий файл.
              {running ? ' Перенос закончится и без них, а разобрать их можно в любой момент.' : ' Разобрать их можно в любой момент.'}
            </span>
            <Link to={`/downloads?import=${imp.id}&tab=review`} className="flex min-h-10 items-center rounded-full bg-fg px-4 text-sm font-semibold text-bg max-sm:w-full max-sm:justify-center">
              Разобрать в «Загрузках»
            </Link>
          </div>
        )}
        {imp.error && <Notice kind="error"><ServiceText text={imp.error} /></Notice>}
      </div>

      {imp.destinations.length > 0 && <Destinations destinations={imp.destinations} />}

      <div className="space-y-2 px-4 lg:px-6 pt-4">
        {imp.mode === 'economy' && st.waiting > 0 && (running || imp.status === 'done') && (
          <Button block icon="download" busy={busy === 'all'} onClick={() => run('all', () => api.importDownloadAll(imp.id), 'Остальное скачается в фоне')}>
            Скачать всё сейчас ({st.waiting})
          </Button>
        )}
        {running ? (
          <Button block icon="pause" busy={busy === 'pause'} disabled={!!busy} onClick={() => run('pause', () => api.importSetPaused(imp.id, true))}>
            Пауза
          </Button>
        ) : (
          <div className="flex justify-center gap-2">
            {paused ? (
              <Button variant="primary" icon="play" busy={busy === 'resume'} disabled={!!busy} className="min-w-0 text-balance max-sm:px-3"
                onClick={() => run('resume', () => api.importSetPaused(imp.id, false))}>
                Продолжить
              </Button>
            ) : imp.can_sync && (
              <Button icon="refresh" busy={busy === 'sync'} disabled={!!busy} className="min-w-0 text-balance max-sm:px-3"
                onClick={() => run('sync', () => api.importSync(imp.id), 'Повторное сканирование запущено')}>
                Сканировать повторно
              </Button>
            )}
            <Button variant="danger-solid" icon="trash" busy={busy === 'delete'} disabled={!!busy} className="min-w-0 text-balance max-sm:px-3" onClick={() => void deleteTransfer(imp, setBusy)}>
              Удалить перенос
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

function Destinations({ destinations }: { destinations: ImportOut['destinations'] }) {
  const { me } = useSession();
  const hasPlaylists = destinations.some(({ playlist_id }) => playlist_id != null);
  const hasLiked = destinations.some(({ playlist_id }) => playlist_id == null);
  const lists = useAsync(api.playlists, [], hasPlaylists, 'playlists');
  const liked = useAsync(api.ndLiked, [], hasLiked, 'liked');
  const refresh = () => {
    if (hasPlaylists) lists.reload(true);
    if (hasLiked) liked.reload(true);
  };
  useHubEvent('library', refresh);
  useHubEvent('import', refresh);
  useHubEvent('reconnect', refresh);
  useWindowEvent('hm:hub-favorite', () => { if (hasLiked) liked.reload(true); });

  return (
    <ul aria-label="Плейлисты" className="mx-4 mt-3 overflow-hidden rounded-2xl bg-surface lg:mx-6">
      {destinations.map(({ playlist_id: id, name }) => {
        const playlist = id == null ? undefined : lists.data?.find((p) => p.id === id);
        const count = id == null ? liked.data?.songs.length : playlist && playlist.song_count + (playlist.pending_count ?? 0);
        return (
          <li key={id ?? 'liked'} className="border-b border-line/60 last:border-0">
            <PlaylistRow
              playlist={id == null
                ? { title: 'Любимое', to: '/liked', mark: LIKED_MARK, owners: [me.username], count }
                : { ...(playlist ? playlistLook(playlist) : { title: name, owners: [me.username] }), to: `/playlist/${enc(id)}`, count }}
              className="hover:bg-fg/5"
            />
          </li>
        );
      })}
    </ul>
  );
}
