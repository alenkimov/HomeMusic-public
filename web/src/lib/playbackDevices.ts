import { useSyncExternalStore } from 'react';
import { request } from '../api/client';
import { hubEvents } from './events';
import {
  finishOutgoingPlayback, getListenState, listenAudio, refreshManualQueue, snapshotForTransfer,
  startTransferredPlayback, stopListening, subscribeListening, type PlaybackSnapshot,
} from './listen';
import { newEventId } from './plays';
import { toast, toastError } from './ui';
import { restorePlayerWave } from './wave';
import { onHubBack } from './reach';

interface Device {
  id: string;
  name: string;
  mobile: boolean;
}

interface Transfer {
  id: string;
  incoming: boolean;
  status: 'offered' | 'completed' | 'cancelled';
  sourceName: string;
  age: number;
  expiresIn: number;
  snapshot?: PlaybackSnapshot;
}

interface Arrival extends Transfer {
  snapshot: PlaybackSnapshot;
  receivedAt: number;
}

const base = '/api/playback/devices';
const agent = navigator.userAgent;
const mobile = /Android|iPhone|iPad|Mobile/.test(agent) || (/Macintosh/.test(agent) && navigator.maxTouchPoints > 1);
const platform = /iPhone/.test(agent) ? 'iPhone' : /iPad/.test(agent) || (mobile && /Macintosh/.test(agent)) ? 'iPad'
  : /Android/.test(agent) ? 'Android' : /Windows/.test(agent) ? 'Windows' : /Macintosh/.test(agent) ? 'Mac' : 'Linux';
const browser = /Edg/.test(agent) ? 'Edge' : /Firefox|FxiOS/.test(agent) ? 'Firefox' : /Chrome|CriOS/.test(agent) ? 'Chrome'
  : /Safari/.test(agent) ? 'Safari' : 'Браузер';
const device = { id: newEventId(), mobile, name: `${browser} · ${platform}` };
const initial = { devices: [] as Device[], incoming: null as Arrival | null, sending: false, receiving: false, connected: false };
let data = initial;
const subscribers = new Set<() => void>();
let session: ReturnType<typeof connect> | null = null;

function update(patch: Partial<typeof initial>) {
  data = { ...data, ...patch };
  subscribers.forEach((fn) => fn());
}

function subscribe(fn: () => void) {
  subscribers.add(fn);
  return () => { subscribers.delete(fn); };
}

export function usePlaybackDevices() {
  return useSyncExternalStore(subscribe, () => data);
}

export function watchPlaybackDevices() {
  const current = connect();
  session = current;
  return () => {
    session = null;
    current.close();
    update(initial);
  };
}

export function transferPlayback(target: string) {
  return session?.send(target);
}

export function answerPlaybackTransfer(accept: boolean) {
  session?.answer(accept);
}

function connect() {
  const controller = new AbortController();
  const { signal } = controller;
  let refreshing = false;
  let refreshAgain = false;
  let outgoing: { id: string; isCurrent: () => boolean; sent: boolean; until: number } | null = null;
  let receiving: { id: string; cancel: () => void } | null = null;
  // Ignore an offered snapshot already acted on until the next authoritative presence response.
  let answered: string | null = null;
  const answer = (id: string, status: 'completed' | 'cancelled') =>
    request<void>('POST', `${base}/transfers/${id}`, { signal, json: { device: device.id, status } });

  const releaseOutgoing = () => {
    const offer = outgoing;
    if (!offer) return;
    outgoing = null;
    update({ sending: false });
    if (offer.sent) void answer(offer.id, 'cancelled').catch(() => undefined);
  };

  async function refresh() {
    if (signal.aborted) return;
    if (refreshing) { refreshAgain = true; return; }
    refreshing = true;
    try {
      const result = await request<{ devices: Device[]; transfers: Transfer[] }>('POST', `${base}/presence`, { signal, json: device });
      if (signal.aborted) return;
      const sent = result.transfers.find((row) => row.id === outgoing?.id);
      if (outgoing && (sent?.status === 'completed' || sent?.status === 'cancelled' || performance.now() >= outgoing.until)) {
        const transfer = outgoing;
        outgoing = null;
        if (sent?.status === 'completed' && transfer.isCurrent()) {
          finishOutgoingPlayback();
          toast('Воспроизведение передано', 'success');
        } else {
          toast('Передача не состоялась. Плеер остался здесь', 'info');
        }
      }
      const incoming = result.transfers.find((row) => row.incoming && row.status === 'offered' && row.id !== answered);
      if (receiving) {
        const own = result.transfers.find((row) => row.id === receiving?.id);
        if (!own || own.status === 'cancelled') receiving.cancel();
      }
      update({ devices: result.devices, sending: !!outgoing, connected: true,
        incoming: incoming?.snapshot ? { ...incoming, snapshot: incoming.snapshot, receivedAt: performance.now() } : null });
    } catch {
      if (!signal.aborted) update({ connected: false });
    } finally {
      refreshing = false;
      if (refreshAgain) {
        refreshAgain = false;
        void refresh();
      }
    }
  }

  const offEvents = hubEvents.on('playback_devices', () => void refresh());
  const offReconnect = hubEvents.on('reconnect', () => void refresh());
  const offListen = subscribeListening(() => {
    if (outgoing && !outgoing.isCurrent()) releaseOutgoing();
  });
  const wake = () => { if (!document.hidden) void refresh(); };
  document.addEventListener('visibilitychange', wake);
  const offBack = onHubBack(wake);
  const heartbeat = window.setInterval(refresh, 12000);
  void refresh();

  return {
    async send(target: string) {
      if (outgoing) return;
      const current = snapshotForTransfer();
      if (!current) {
        toast('Дождитесь начала воспроизведения', 'info');
        return;
      }
      const offer = { id: newEventId(), isCurrent: current.isCurrent, sent: false, until: performance.now() + 90000 };
      outgoing = offer;
      update({ sending: true });
      try {
        await request<void>('POST', `${base}/transfers`, {
          signal, json: { id: offer.id, source: device.id, target, snapshot: current.snapshot },
        });
        offer.sent = true;
        if (outgoing !== offer) {
          await answer(offer.id, 'cancelled').catch(() => undefined);
          return;
        }
        toast('Подтвердите передачу на другом устройстве', 'info');
      } catch (e) {
        if (outgoing === offer) releaseOutgoing();
        if (!signal.aborted) toastError(e);
      }
    },

    answer(accept: boolean) {
      const incoming = data.incoming;
      if (receiving) {
        if (!accept) receiving.cancel();
        return;
      }
      if (!incoming) return;
      const { id, snapshot, receivedAt } = incoming;
      answered = id;
      update({ incoming: null });
      if (!accept) {
        void answer(id, 'cancelled').catch(() => undefined);
        return;
      }

      const remaining = incoming.expiresIn * 1000 - (performance.now() - receivedAt);
      if (remaining <= 0) { toast('Передача больше недоступна', 'info'); return; }
      const track = snapshot.queue[snapshot.index];
      const position = () => snapshot.position + (snapshot.autoplay ? incoming.age + (performance.now() - receivedAt) / 1000 : 0);
      // Run within the receiving device's tap to keep Safari's media activation.
      startTransferredPlayback(snapshot, position);
      restorePlayerWave();
      void refreshManualQueue();
      const audio = listenAudio()!;
      let settling = false;
      const finish = async (success: boolean) => {
        if (settling || receiving?.id !== id) return;
        settling = true;
        audio.removeEventListener('seeked', check);
        audio.removeEventListener('canplay', check);
        audio.removeEventListener('playing', check);
        off();
        window.clearTimeout(timeout);
        try {
          if (!signal.aborted) await answer(id, success ? 'completed' : 'cancelled');
        } catch {
          success = false;
        } finally {
          receiving = null;
          if (!signal.aborted) {
            if (!success && getListenState().track === track) stopListening();
            update({ receiving: false });
            toast(success ? 'Воспроизведение передано' : 'Не удалось принять воспроизведение', success ? 'success' : 'info');
          }
        }
      };
      const check = () => {
        const state = getListenState();
        if (state.track !== track || state.phase === 'error' || (snapshot.autoplay && state.phase === 'paused')) {
          void finish(false);
          return;
        }
        if (audio.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && !audio.seeking
          && (!snapshot.autoplay || (state.phase === 'playing' && !audio.paused))) void finish(true);
      };
      const off = subscribeListening(check);
      const timeout = window.setTimeout(() => void finish(false), remaining);
      audio.addEventListener('seeked', check);
      audio.addEventListener('canplay', check);
      audio.addEventListener('playing', check);
      receiving = { id, cancel: () => void finish(false) };
      update({ receiving: true });
      check();
    },

    close() {
      controller.abort();
      receiving?.cancel();
      offEvents();
      offReconnect();
      offListen();
      window.clearInterval(heartbeat);
      document.removeEventListener('visibilitychange', wake);
      offBack();
    },
  };
}
