import { useEffect, useState } from 'react';
import { Icon } from '../components/Icon';
import { Button, Empty, Notice, PageHeader, Spinner } from '../components/ui';
import { fmtSize, tracksWord } from '../lib/format';
import { clearOffline, dropList, offlineSupported, useKeptLists, useOnline } from '../lib/offline';
import { refreshWaveReserve, setWaveReserveSize, useWaveReserve, waveBudget, WAVE_SIZES } from '../lib/offlineWave';
import { Link, navigate } from '../lib/router';
import { confirmDialog } from '../lib/ui';
import { startOfflineWave } from '../lib/wave';
import { isDesktop } from '../lib/desktop';

/** «Офлайн» (from «Меню»): the lists kept on this device and the room they take; a list is let go here, or all of
 * them at once. A list is kept from its own page — the mark in its bar. */
export function OfflinePage() {
  const kept = useKeptLists();
  const reserve = useWaveReserve();
  const online = useOnline();
  const [room, setRoom] = useState<{ usage?: number; quota?: number } | null>(null);
  useEffect(() => {
    void navigator.storage?.estimate?.().then(setRoom).catch(() => undefined);
  }, [kept.bytes, reserve.bytes]);

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
          <Empty icon="download" title="Нет скачанных плейлистов" text="Откройте плейлист, «Любимое» или список «Для вас» и нажмите значок загрузки справа сверху — список будет играть без сети." />
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
