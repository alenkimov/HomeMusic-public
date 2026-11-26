import { useEffect, useState } from 'react';
import { Icon } from '../components/Icon';
import { Button, Empty, Notice, PageHeader, Spinner } from '../components/ui';
import { fmtSize, tracksWord } from '../lib/format';
import { api } from '../api/client';
import { clearHeard, HEARD_LIMITS_GB, setHeardLimit, useHeardUsage } from '../lib/heard';
import { useAsync } from '../lib/hooks';
import { clearOffline, dropList, keepLiked, offlineSupported, useKeptLists, useLikedKept } from '../lib/offline';
import { attempt } from '../lib/ui';
import { useHubReachable } from '../lib/reach';
import { refreshWaveReserve, setWaveReserveSize, useWaveReserve, waveBudget, WAVE_SIZES } from '../lib/offlineWave';
import { Link, navigate } from '../lib/router';
import { confirmDialog } from '../lib/ui';
import { startOfflineWave } from '../lib/wave';
import { isDesktop } from '../lib/desktop';

/** «Офлайн» (from «Меню»): what is on this device and what the Hub keeps. The cache of what was heard (`lib/heard.ts`)
 * and its limit; «Любимое» kept by itself; the lists kept on this device and the room they take — a list is let go
 * here, or all of them at once (a list is kept from its own page — the mark in its bar); the Hub's copies of what was
 * listened to. */
export function OfflinePage() {
  const kept = useKeptLists();
  const heard = useHeardUsage();
  const likedKept = useLikedKept();
  const [likedBusy, setLikedBusy] = useState(false);
  const server = useAsync((s) => api.listenStorage(s), []);
  const reserve = useWaveReserve();
  const online = useHubReachable();
  const [room, setRoom] = useState<{ usage?: number; quota?: number } | null>(null);
  useEffect(() => {
    void navigator.storage?.estimate?.().then(setRoom).catch(() => undefined);
  }, [kept.bytes, reserve.bytes, heard.bytes]);

  const toggleLiked = async () => {
    setLikedBusy(true);
    await attempt(() => keepLiked(!likedKept));
    setLikedBusy(false);
  };
  const clearCache = async () => {
    if (await confirmDialog({ title: 'Очистить кеш прослушанного?', body: 'Треки, которые вы слушали, будут удалены с этого устройства. Скачанные плейлисты останутся.', okLabel: 'Очистить' }))
      await clearHeard();
  };

  const clear = async () => {
    if (await confirmDialog({ title: 'Убрать всё с устройства?', body: 'Скачанные плейлисты и запас волны будут удалены с этого устройства.', okLabel: 'Убрать всё', danger: true })) {
      await Promise.all([clearOffline(), setWaveReserveSize(0)]);
    }
  };

  return (
    <div>
      <PageHeader title="Офлайн" back="/more" sub={kept.tracks ? `${tracksWord(kept.tracks)} · ${fmtSize(kept.bytes)}` : undefined} />
      <div className="space-y-3 px-4 pb-4">
        {offlineSupported() && (
          <section aria-label="Кеш прослушанного" className="space-y-3 rounded-2xl bg-surface px-4 py-4">
            <div className="flex items-center gap-2">
              <Icon name="history" size={20} className="text-accent" />
              <h2 className="font-semibold">Кеш прослушанного</h2>
            </div>
            <p className="text-sm text-muted">
              Что вы слушаете, остаётся на устройстве: снова включается сразу и играет без сети. Следующие треки очереди скачиваются заранее.
              Когда место кончается, первыми уходят давно не игравшие — сначала те, что не в «Любимом» и не в плейлистах.
            </p>
            <div role="group" aria-label="Размер кеша" className="flex flex-wrap gap-2">
              {HEARD_LIMITS_GB.map((gb) => (
                <button key={gb} type="button" aria-pressed={heard.limit === gb * 2 ** 30}
                  className={`rounded-full px-3 py-2 text-sm ${heard.limit === gb * 2 ** 30 ? 'bg-fg font-semibold text-bg' : 'bg-raised hover:bg-hover'}`}
                  onClick={() => setHeardLimit(gb)}>
                  {gb ? `${gb} ГБ` : 'Выключено'}
                </button>
              ))}
            </div>
            <p className="text-sm text-muted" role="status">
              {heard.count ? `${tracksWord(heard.count)} · ${fmtSize(heard.bytes)}${heard.limit ? ` из ${fmtSize(heard.limit)}` : ''}` : 'Пока пусто'}
            </p>
            {heard.count > 0 && <Button small icon="trash" onClick={() => void clearCache()}>Очистить кеш</Button>}
          </section>
        )}
        {offlineSupported() && (
          <section aria-label="Любимое на устройстве" className="flex items-center gap-3 rounded-2xl bg-surface px-4 py-4">
            <Icon name="heart" size={20} className="shrink-0 text-accent" />
            <div className="min-w-0 flex-1">
              <h2 className="font-semibold">Скачивать «Любимое»</h2>
              <p className="text-sm text-muted">Все треки с ♥ на устройстве, новые докачиваются сами.</p>
            </div>
            <button type="button" role="switch" aria-checked={likedKept} aria-label="Скачивать «Любимое»" disabled={likedBusy || !online}
              onClick={() => void toggleLiked()}
              className={`relative h-7 w-12 shrink-0 rounded-full transition disabled:opacity-50 ${likedKept ? 'bg-accent' : 'bg-raised'}`}>
              <span className={`absolute top-1 h-5 w-5 rounded-full bg-fg transition-all ${likedKept ? 'left-6' : 'left-1'}`} />
            </button>
          </section>
        )}
        {offlineSupported() && (
          <section aria-label="Офлайн-волна" className="space-y-3 rounded-2xl bg-surface px-4 py-4">
            <div className="flex items-center gap-2">
              <Icon name="wave" size={20} className="text-accent" />
              <h2 className="font-semibold">Офлайн-волна</h2>
            </div>
            <div role="group" aria-label="Размер запаса" className="flex flex-wrap gap-2">
              {WAVE_SIZES.map((size) => (
                <button key={size} type="button" aria-pressed={reserve.size === size}
                  className={`rounded-full px-3 py-2 text-sm ${reserve.size === size ? 'bg-fg font-semibold text-bg' : 'bg-raised hover:bg-hover'}`}
                  onClick={() => void setWaveReserveSize(size)}>
                  {size ? tracksWord(size) : 'Выключено'}
                </button>
              ))}
            </div>
            {reserve.size === 0 ? (
              <p className="text-sm text-muted">Запас «Моей волны» для прослушивания без сети.</p>
            ) : (
              <>
                <p className="text-sm text-muted" role="status">
                  Готово {reserve.ready} из {reserve.size} · {fmtSize(reserve.bytes)} из {fmtSize(waveBudget(reserve.size))}
                  {reserve.busy && ' · скачивается'}
                </p>
                {reserve.problem && <p className="text-sm text-muted">{reserve.problem}</p>}
                <div className="flex flex-wrap gap-2">
                  {reserve.ready > 0 && <Button small variant="primary" icon="play" onClick={() => {
                    if (startOfflineWave()) navigate('/player', { overlay: !isDesktop() });
                  }}>Слушать офлайн</Button>}
                  {online && reserve.ready < reserve.size && <Button small icon="download" busy={reserve.busy}
                    onClick={() => void refreshWaveReserve(true)}>Пополнить запас</Button>}
                </div>
              </>
            )}
          </section>
        )}
        {!offlineSupported() ? (
          <Notice kind="warn">Здесь браузер не даёт хранить музыку: нужен защищённый адрес (https). Откройте приложение по его обычному адресу, а не по номеру в домашней сети.</Notice>
        ) : !kept.lists.length ? (
          <Empty icon="download" title="Нет скачанных плейлистов" text="Откройте плейлист, «Любимое» или список «Для вас» и нажмите значок загрузки справа сверху — список будет играть без сети и сам докачивать новые треки." />
        ) : (
          <>
            {!online && <Notice kind="info">Сети нет: играет то, что скачано. Недостающее докачается, когда сеть вернётся.</Notice>}
            <ul className="overflow-hidden rounded-2xl bg-surface">
              {kept.lists.map((l) => {
                const st = kept.stateOf(l);
                return (
                  <li key={l.key} className="flex min-h-16 items-center gap-3 border-b border-fg/5 pl-4 pr-2 last:border-0">
                    <Link to={l.to} className="min-w-0 flex-1 py-2">
                      <span className="block truncate font-semibold">{l.title}</span>
                      <span className={`block truncate text-[13px] ${st.failed && !st.busy ? 'text-warn' : 'text-muted'}`}>
                        {st.done === st.total ? tracksWord(st.total) : `${st.done} из ${st.total}`}
                        {' · '}
                        {fmtSize(kept.sizeOf(l))}
                        {st.busy ? ' · скачивается' : st.failed ? ` · ${st.failed} не скачалось` : ''}
                      </span>
                    </Link>
                    {st.busy && <Spinner size={16} className="text-accent" />}
                    <button type="button" onClick={() => void dropList(l.key)} aria-label={`Убрать «${l.title}» с устройства`} title="Убрать с устройства" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-muted hover:text-danger">
                      <Icon name="trash" size={18} />
                    </button>
                  </li>
                );
              })}
            </ul>
            {room?.quota ? (
              <p className="px-1 text-xs text-dim">
                Приложение занимает {fmtSize(room.usage ?? 0)} из {fmtSize(room.quota)}, которые даёт браузер.
              </p>
            ) : null}
          </>
        )}
        {server.data && (
          <section aria-label="На сервере" className="space-y-1 rounded-2xl bg-surface px-4 py-4">
            <div className="flex items-center gap-2">
              <Icon name="disc" size={20} className="text-accent" />
              <h2 className="font-semibold">На сервере</h2>
            </div>
            <p className="text-sm text-muted">
              Сервер хранит копии прослушанных треков {server.data.keep_days} дней с последнего прослушивания: пауза, повтор и перемотка идут с его диска, а не из источника.
            </p>
            <p className="text-sm text-muted" role="status">
              {tracksWord(server.data.count)} · {fmtSize(server.data.bytes)} из {fmtSize(server.data.limit_bytes)}
            </p>
          </section>
        )}
        {(kept.lists.length > 0 || reserve.size > 0) && <Button icon="trash" onClick={() => void clear()}>
          Убрать всё с устройства
        </Button>}
        <p className="px-1 text-xs text-dim">
          Загрузка идёт, пока приложение открыто. Свёрнутое приложение телефон может остановить — тогда она продолжится, когда вы вернётесь и откроете список.
        </p>
      </div>
    </div>
  );
}
