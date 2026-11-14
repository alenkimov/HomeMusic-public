import { useEffect } from 'react';
import type { TrackOut } from '../api/types';
import { dropList, keepList, offlineSupported, syncList, useKept, useKeptList, useOnline } from '../lib/offline';
import { confirmDialog, toast } from '../lib/ui';
import { Icon } from './Icon';
import { IconButton, Spinner, cx } from './ui';

/** The mark of a list's bar that keeps the list on the device (`lib/offline.ts`): a tap fetches its tracks, so they
 * play without the network; a tap on a kept list lets it go. While it is being fetched the mark says how far it is.
 * A kept list opened again is brought up to date here. Nothing where a browser cannot keep files (no secure address). */
export function OfflineButton({ listKey, title, to, tracks }: { listKey: string; title: string; to: string; tracks: readonly TrackOut[] }) {
  const st = useKeptList(listKey);
  useEffect(() => {
    if (st.kept) void syncList(listKey, title, tracks);
  }, [st.kept, listKey, title, tracks]);
  if (!tracks.length && !st.kept) return null;

  const tap = async () => {
    if (!offlineSupported()) {
      // a browser keeps files only for a secure address: the mark is there, and says why it cannot
      toast('Скачать на устройство можно только по защищённому адресу (https). Откройте приложение по нему, а не по номеру в домашней сети.', 'info');
      return;
    }
    if (!st.kept) {
      await keepList(listKey, title, to, tracks);
      toast('Скачиваю на устройство — не закрывайте приложение, пока идёт загрузка', 'info');
      return;
    }
    if (await confirmDialog({ title: `Убрать «${title}» с устройства?`, body: 'Сам список останется, но без сети играть не будет.', okLabel: 'Убрать' })) await dropList(listKey);
  };
  const label = !offlineSupported()
    ? 'Скачать на устройство: нужен адрес с https'
    : !st.kept
    ? 'Скачать на устройство: слушать без сети'
    : st.busy
      ? `Скачивается: ${st.done} из ${st.total}`
      : st.done < st.total
        ? `На устройстве ${st.done} из ${st.total}${st.failed ? ` — ${st.failed} не скачалось` : ''}. Нажмите, чтобы убрать`
        : 'На устройстве, играет без сети. Нажмите, чтобы убрать';
  if (st.kept && st.busy)
    return (
      <button type="button" onClick={() => void tap()} title={label} aria-label={label} className="flex h-11 shrink-0 items-center gap-1.5 rounded-full px-2 text-xs tabular-nums text-accent">
        <Spinner size={16} />
        {st.done}/{st.total}
      </button>
    );
  return <IconButton icon={st.kept ? 'check' : 'download'} label={label} aria-pressed={st.kept} onClick={() => void tap()} className={st.kept ? (st.done < st.total ? '!text-warn' : '!text-accent') : offlineSupported() ? undefined : 'opacity-50'} />;
}

/** A small mark at a track kept on the device. */
export function KeptMark({ refId, className }: { refId: string; className?: string }) {
  const kept = useKept(refId);
  if (!kept) return null;
  return (
    <span title="На устройстве: играет без сети" aria-label="На устройстве" className={cx('flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent', className)}>
      <Icon name="download" size={9} stroke={3.4} />
    </span>
  );
}

/** A track cannot play now: there is no network and it is not kept on the device. */
export function useUnreachable(refId: string): boolean {
  const online = useOnline();
  const kept = useKept(refId);
  return !online && !kept;
}

/** «Нет сети»: a line at the top of the screen while the device has none — what is kept on it still plays. */
export function OfflineBanner() {
  const online = useOnline();
  if (online) return null;
  return (
    <div role="status" className="pt-safe pointer-events-none fixed inset-x-0 top-0 z-[55] flex justify-center">
      <span className="mt-2 flex items-center gap-1.5 rounded-full bg-raised px-3 py-1 text-xs text-muted shadow-lg">
        <span className="h-1.5 w-1.5 rounded-full bg-warn" />
        Нет сети — играет то, что на устройстве
      </span>
    </div>
  );
}
