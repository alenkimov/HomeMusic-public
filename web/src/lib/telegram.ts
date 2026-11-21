import { titleWithVersion } from './versionLabels';
import { useSyncExternalStore } from 'react';
import { api, errorMessage } from '../api/client';
import { tracksWord } from './format';
import { attempt, toast } from './ui';

export interface TelegramStatus {
  configured: boolean;
  ready: boolean;
  bot_username?: string | null;
  chat_name?: string | null;
  /** «Отправить в Telegram» sends the track's text with it */
  with_lyrics?: boolean;
}

/** A chat the bot can send to. */
export interface TelegramChat {
  id: number;
  kind: string; // private / group / supergroup / channel
  title: string;
  /** the key of its picture (`/api/telegram/chats/{id}/photo`); none — no picture */
  photo?: string | null;
}

export const TELEGRAM_LINK_EVENT = 'hm:telegram-link';

export interface ShareTrack {
  title: string;
  artist?: string | null;
  nd_song_id?: string | null;
  ref?: string | null;
}

interface DeliveryOptions {
  withLyrics?: boolean | null;
  anonymous?: boolean;
  chat?: TelegramChat | null;
}

interface SetupState {
  open: boolean;
  /** the tracks to send once the bot is ready */
  pending: ShareTrack[];
  options: Omit<DeliveryOptions, 'chat'>;
}

let state: SetupState = { open: false, pending: [], options: {} };
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
export const openTelegramSetup = (pending: ShareTrack[] = [], options: SetupState['options'] = {}) => set({ open: true, pending, options });
export const closeTelegramSetup = () => set({ open: false, pending: [], options: {} });

let sending = false;

/** Sends the files (the bot is ready): one by one, so a big selection neither times out nor trips Telegram's limits.
 * `chat`: one of the bot's chats; none — the private chat with the user. */
const LINK_KEY = 'hm-share-link';

/** «С кнопкой „Открыть“»: the track's link under the audio the bot sends (on until switched off). */
export function shareWithLink(): boolean {
  try {
    return localStorage.getItem(LINK_KEY) !== '0';
  } catch {
    return true;
  }
}

export function setShareWithLink(on: boolean) {
  try {
    localStorage.setItem(LINK_KEY, on ? '1' : '0');
  } catch {
    /* storage unavailable: the choice lives until the page is closed */
  }
}

export async function deliverToTelegram(tracks: readonly ShareTrack[], { withLyrics = null, anonymous = false, chat = null }: DeliveryOptions = {}): Promise<void> {
  if (!tracks.length) return;
  const to = chat && chat.kind !== 'private' ? `в «${chat.title}»` : 'в Telegram';
  const chatId = chat?.kind === 'private' ? null : chat?.id ?? null;
  if (sending) return void toast('Предыдущая отправка в Telegram ещё идёт', 'info');
  const settings = { with_lyrics: withLyrics, chat: chatId, with_link: shareWithLink(), anonymous };
  const send = (track: ShareTrack) => api.telegramSend({ nd_song_id: track.nd_song_id ?? null, ref: track.ref ?? null, ...settings });
  sending = true;
  try {
    if (tracks.length === 1) {
      toast(`Отправляю ${to}: «${titleWithVersion(tracks[0].title, tracks[0])}»…`);
      const r = await attempt(() => send(tracks[0]));
      if (r) toast(`Отправлено ${to}: «${titleWithVersion(r.title, tracks[0], r.version_label ?? undefined)}»${r.lyrics ? ' с текстом' : withLyrics ? ' (текста у трека нет)' : ''}${r.light ? ' — сжатая копия: файл больше 50 МБ' : ''}`, 'success');
      return;
    }
    toast(`Отправляю ${to} ${tracksWord(tracks.length)} — это займёт время…`);
    let ok = 0;
    let firstError = '';
    for (const t of tracks) {
      try {
        await send(t);
        ok++;
      } catch (e) {
        firstError ||= `«${titleWithVersion(t.title, t)}»: ${errorMessage(e)}`;
      }
    }
    if (ok === tracks.length) toast(`Отправлено ${to}: ${tracksWord(ok)}`, 'success');
    else toast(`Отправлено ${ok} из ${tracks.length}. ${firstError}`, ok ? 'info' : 'error');
  } finally {
    sending = false;
  }
}

