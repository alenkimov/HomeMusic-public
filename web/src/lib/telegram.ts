import { useSyncExternalStore } from 'react';
import { api } from '../api/client';
import { attempt, toast } from './ui';

/** «Отправить в Telegram»: a library track goes to the user's own bot (the Hub sends the file, `app/telegram.py`).
 * The action is always offered; the first time it opens the window where the bot's key is pasted
 * (`components/TelegramSetup.tsx`), and the track that was asked for is sent as soon as the bot is ready. */

export interface TelegramStatus {
  connected: boolean;
  ready: boolean;
  bot_username?: string | null;
  chat_name?: string | null;
}

export interface ShareTrack {
  title: string;
  nd_song_id?: string | null;
  ref?: string | null;
}

interface SetupState {
  open: boolean;
  /** the track to send once the bot is ready */
  pending: ShareTrack | null;
}

let state: SetupState = { open: false, pending: null };
const subs = new Set<() => void>();
const set = (next: SetupState) => {
  state = next;
  subs.forEach((f) => f());
};
const subscribe = (cb: () => void) => {
  subs.add(cb);
  return () => void subs.delete(cb);
};

export const useTelegramSetup = (): SetupState => useSyncExternalStore(subscribe, () => state, () => state);
export const openTelegramSetup = (pending: ShareTrack | null = null) => set({ open: true, pending });
export const closeTelegramSetup = () => set({ open: false, pending: null });

/** Sends the file (the bot is ready). */
export async function deliverToTelegram(track: ShareTrack): Promise<boolean> {
  toast(`Отправляю в Telegram: «${track.title}»…`);
  const r = await attempt(() => api.telegramSend({ nd_song_id: track.nd_song_id ?? null, ref: track.ref ?? null }));
  if (r) toast(`Отправлено в Telegram: «${r.title}»${r.light ? ' (сжатая копия: файл больше 50 МБ)' : ''}`, 'success');
  return !!r;
}

export async function sendToTelegram(track: ShareTrack): Promise<void> {
  if (!track.nd_song_id && !track.ref) return;
  const st = await attempt(() => api.telegram());
  if (!st) return;
  if (!st.ready) openTelegramSetup(track);
  else await deliverToTelegram(track);
}
