import { useSyncExternalStore } from 'react';
import { api, errorMessage } from '../api/client';
import { tracksWord } from './format';
import { attempt, toast } from './ui';

/** Sending library tracks with the user's own Telegram bot (the Hub sends the files, `app/telegram.py`): to the
 * private chat with them or to a group / channel they added the bot to — picked in «Поделиться»
 * (`components/ShareSheet.tsx`). Without a bot that window opens the one where the bot's key is pasted
 * (`components/TelegramSetup.tsx`), and what was asked for goes to the private chat as soon as the bot is ready.
 * Several tracks (a selection, `lib/selection.ts`) go one after another, in their order. */

export interface TelegramStatus {
  connected: boolean;
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

export interface ShareTrack {
  title: string;
  artist?: string | null;
  nd_song_id?: string | null;
  ref?: string | null;
}

interface SetupState {
  open: boolean;
  /** the tracks to send once the bot is ready */
  pending: ShareTrack[];
  /** ... with their texts (null: as the user's setting says) */
  withLyrics: boolean | null;
}

let state: SetupState = { open: false, pending: [], withLyrics: null };
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
export const openTelegramSetup = (pending: ShareTrack[] = [], withLyrics: boolean | null = null) => set({ open: true, pending, withLyrics });
export const closeTelegramSetup = () => set({ open: false, pending: [], withLyrics: null });

let sending = false;

/** Sends the files (the bot is ready): one by one, so a big selection neither times out nor trips Telegram's limits.
 * `chat`: one of the bot's chats; none — the private chat with the user. */
const LINK_KEY = 'hm-share-link';

/** «С кнопкой „Открыть в HomeMusic“»: the track's link under the audio the bot sends (on until switched off). */
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

export async function deliverToTelegram(tracks: readonly ShareTrack[], withLyrics: boolean | null = null, chat: TelegramChat | null = null): Promise<void> {
  if (!tracks.length) return;
  const to = chat && chat.kind !== 'private' ? `в «${chat.title}»` : 'в Telegram';
  const chatId = chat?.id ?? null;
  if (sending) return void toast('Предыдущая отправка в Telegram ещё идёт', 'info');
  sending = true;
  try {
    if (tracks.length === 1) {
      toast(`Отправляю ${to}: «${tracks[0].title}»…`);
      const r = await attempt(() => api.telegramSend({ nd_song_id: tracks[0].nd_song_id ?? null, ref: tracks[0].ref ?? null, with_lyrics: withLyrics, chat: chatId, with_link: shareWithLink() }));
      if (r) toast(`Отправлено ${to}: «${r.title}»${r.lyrics ? ' с текстом' : withLyrics ? ' (текста у трека нет)' : ''}${r.light ? ' — сжатая копия: файл больше 50 МБ' : ''}`, 'success');
      return;
    }
    toast(`Отправляю ${to} ${tracksWord(tracks.length)} — это займёт время…`);
    let ok = 0;
    let firstError = '';
    for (const t of tracks) {
      try {
        await api.telegramSend({ nd_song_id: t.nd_song_id ?? null, ref: t.ref ?? null, with_lyrics: withLyrics, chat: chatId, with_link: shareWithLink() });
        ok++;
      } catch (e) {
        firstError ||= `«${t.title}»: ${errorMessage(e)}`;
      }
    }
    if (ok === tracks.length) toast(`Отправлено ${to}: ${tracksWord(ok)}`, 'success');
    else toast(`Отправлено ${ok} из ${tracks.length}. ${firstError}`, ok ? 'info' : 'error');
  } finally {
    sending = false;
  }
}

/** `withLyrics`: true / false — with or without the texts this time; null — as the user's setting says. */
export async function sendToTelegram(tracks: ShareTrack | readonly ShareTrack[], withLyrics: boolean | null = null): Promise<void> {
  const list = (Array.isArray(tracks) ? tracks : [tracks as ShareTrack]).filter((t) => t.nd_song_id || t.ref);
  if (!list.length) return;
  const st = await attempt(() => api.telegram());
  if (!st) return;
  if (!st.ready) openTelegramSetup(list, withLyrics);
  else await deliverToTelegram(list, withLyrics);
}
