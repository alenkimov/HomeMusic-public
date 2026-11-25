import { useState } from 'react';
import { STANDALONE } from '../lib/embedded';
import { isUpload, useListen } from '../lib/listen';
import { OFFLINE_WAVE } from '../lib/offlineWave';
import { answerPlaybackTransfer, transferPlayback, usePlaybackDevices } from '../lib/playbackDevices';
import { Icon } from './Icon';
import { TrackCell } from './Track';
import { IconButton, Sheet, Spinner } from './ui';

export function PlaybackDevicesButton() {
  const [open, setOpen] = useState(false);
  const available = useListen((state) => !!state.track && state.context !== OFFLINE_WAVE && !isUpload(state.track)
    && (state.phase === 'playing' || state.phase === 'paused'));
  const { devices, connected, sending } = usePlaybackDevices();
  if (!STANDALONE) return null;
  return (
    <>
      <IconButton icon="devices" size={19} label="Устройства воспроизведения" disabled={!available || sending}
        busy={sending} className="!h-9 !w-9 text-muted hover:text-fg" onClick={() => setOpen(true)} />
      <Sheet open={open} onClose={() => setOpen(false)} title="Устройства воспроизведения">
        <div className="space-y-1 px-3 py-3">
          <div className="flex items-center gap-3 rounded-xl px-3 py-3 text-accent">
            <Icon name="devices" size={20} />
            <span>Это устройство</span>
            <Icon name="check" size={17} className="ml-auto" />
          </div>
          {devices.map((device) => (
            <button key={device.id} type="button" disabled={!available || !connected || sending}
              className="flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left hover:bg-raised disabled:opacity-50"
              onClick={() => { setOpen(false); void transferPlayback(device.id); }}>
              <Icon name={device.mobile ? 'phone' : 'devices'} size={20} className="shrink-0 text-muted" />
              <span className="min-w-0 flex-1 truncate">{device.name}</span>
              <span className="text-sm text-muted">Передать</span>
            </button>
          ))}
          {!devices.length && (
            <p className="px-3 py-3 text-sm text-muted">
              {connected ? 'Откройте Хаб на другом устройстве под этим же аккаунтом.' : 'Связываемся с Хабом'}
            </p>
          )}
        </div>
      </Sheet>
    </>
  );
}

export function PlaybackTransferOffer() {
  const { incoming, receiving } = usePlaybackDevices();
  if (!STANDALONE) return null;
  const track = incoming?.snapshot.queue[incoming.snapshot.index];
  return (
    <Sheet open={!!incoming || receiving} onClose={() => answerPlaybackTransfer(false)} title="Продолжить здесь?"
      footer={incoming && (
        <div className="flex justify-end gap-2 px-4 py-3">
          <button type="button" className="rounded-full bg-raised px-4 py-2.5" onClick={() => answerPlaybackTransfer(false)}>Отмена</button>
          <button type="button" className="rounded-full bg-accent px-4 py-2.5 font-semibold text-bg" onClick={() => answerPlaybackTransfer(true)}>
            {incoming.snapshot.autoplay ? 'Продолжить' : 'Принять'}
          </button>
        </div>
      )}>
      {track && (
        <div className="flex items-center px-5 py-4">
          <TrackCell track={{ title: track.title, cover: track.cover_small_url || track.cover_url, identity: track }} sub={`${track.artist} · ${incoming?.sourceName ?? ''}`} />
        </div>
      )}
      {receiving && <div className="flex items-center gap-2 px-5 py-4 text-muted"><Spinner size={17} /> Подготавливаем воспроизведение</div>}
    </Sheet>
  );
}
