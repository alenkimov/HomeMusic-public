import { useEffect, useState } from 'react';
import { Icon } from '../components/Icon';
import { Button, Empty, Notice, PageHeader, Spinner } from '../components/ui';
import { fmtSize, tracksWord } from '../lib/format';
import { clearOffline, dropList, offlineSupported, useKeptLists, useOnline } from '../lib/offline';
import { Link } from '../lib/router';
import { confirmDialog } from '../lib/ui';

/** «Офлайн» (from «Меню»): the lists kept on this device and the room they take; a list is let go here, or all of
 * them at once. A list is kept from its own page — the mark in its bar. */
export function OfflinePage() {
  const kept = useKeptLists();
  const online = useOnline();
  const [room, setRoom] = useState<{ usage?: number; quota?: number } | null>(null);
  useEffect(() => {
    void navigator.storage?.estimate?.().then(setRoom).catch(() => undefined);
  }, [kept.bytes]);

  const clear = async () => {
    if (await confirmDialog({ title: 'Убрать всё с устройства?', body: 'Плейлисты останутся, но без сети играть не будут.', okLabel: 'Убрать всё', danger: true })) await clearOffline();
  };

  return (
    <div>
      <PageHeader title="Офлайн" back="/more" sub={kept.tracks ? `${tracksWord(kept.tracks)} · ${fmtSize(kept.bytes)}` : undefined} />
      <div className="space-y-3 px-4 pb-4">
        {!offlineSupported() ? (
          <Notice kind="warn">Здесь браузер не даёт хранить музыку: нужен защищённый адрес (https). Откройте приложение по его обычному адресу, а не по номеру в домашней сети.</Notice>
        ) : !kept.lists.length ? (
          <Empty icon="download" title="На устройстве пока ничего нет" text="Откройте плейлист, «Любимое» или список «Для вас» и нажмите значок загрузки справа сверху — список будет играть без сети." />
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
            <Button icon="trash" onClick={() => void clear()}>
              Убрать всё с устройства
            </Button>
          </>
        )}
        <p className="px-1 text-xs text-dim">
          Загрузка идёт, пока приложение открыто. Свёрнутое приложение телефон может остановить — тогда она продолжится, когда вы вернётесь и откроете список.
        </p>
      </div>
    </div>
  );
}
