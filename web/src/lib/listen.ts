import { useSyncExternalStore } from 'react';
import { subscribeVersionLabels, titleWithVersion } from './versionLabels';
import { imageUrl } from './img';
import { ofHiddenArtist } from './dislikes';
import { keptInfo, keptPlays } from './offline';
import { OFFLINE_WAVE, waveAudio } from './offlineWave';
import { ApiError, api, errorMessage, isAbort, listenStreamUrl } from '../api/client';
import { CANT_ANY } from './formats';
import type { LaunchOrigin, ListenOut } from '../api/types';
import { startDownload } from './actions';
import { hubEvents } from './events';
import { NATIVE_PLAYING_EVENT, canPlayAll, pauseNative, webPlayerShown } from './player';
import { listeningAccount, newEventId, newLaunch, playEvent, routeContext, type Launch } from './plays';
import { toast } from './ui';
import { hubReachable, onHubBack } from './reach';
import { dropQueue, loadQueue, saveQueue } from './listenStore';

/** Listening to catalog tracks before downloading them, in the page itself (one `<audio>`): the Hub picks the
 * file a download would take and streams it (GET /api/listen). Works in a plain browser too. Inside the Android
 * app only one player plays: starting here pauses the app's player, and its start pauses this one.
 *
 * A tap in a list (album, search results) plays that list from the tapped track on: when a track ends the next
 * one starts, and while one plays the Hub already picks the file for the next (the pick is what takes seconds).
 * The tracks on screen are named to the Hub as they appear (`watchOnScreen`): it picks their files in the
 * background and remembers them, so most taps start at once.
 *
 * `<audio src>` is set and `play()` is called within the tap itself: the app's WebView (and Safari) allow sound
 * only from a user gesture, and the Hub picking the file may take seconds. The stream request waits for the
 * same pick that `api.listen` reports on.
 *
 * What plays here is reported to the Hub for mixes (`lib/plays.ts`, MIXES.md 3.2): `start` once sound plays, then
 * `finish` (played to its end, or left after ≥ 90 %) or `skip` (left earlier: another track, the player closed),
 * with the screen the list was started from. */

export interface ListenTrack {
  ref: string;
  /** the library song, when the track is one: the Hub knows a playlist's song by this id (`idOf`) */
  nd_song_id?: string | null;
  /** the Hub's track, if the Hub knows the song: what tags and playlists go by (`lib/tags.ts` `tagKey`) */
  library_track_id?: number | null;
  /** a recommendation of the smart shuffle (`lib/smartShuffle.ts`): ✨ in the player and the queue; `label` — «Рекомендация
   * на основе …», `session` — the Hub's session (its events carry the context `smart:<session>`) */
  rec?: { label: string; session: string } | null;
  /** The current track came from the manual queue. Pending manual entries are kept separately. */
  queued?: boolean;
  /** an owned track: which file it has (`FileOut.uid`) — a kept copy of another file is not played (`keptPlays`) */
  file?: { uid?: string | null } | null;
  title: string;
  artist: string;
  cover_url?: string | null;
  page_url?: string | null;
  /** the small cover for the mini player (the system notification keeps the big one) */
  cover_small_url?: string | null;
  album_ref?: string | null;
  release_id?: number | null;
  artist_ref?: string | null;
  /** the library's album and artist of the song (Navidrome's): where «Перейти к альбому» goes when the catalog has none */
  nd_album_id?: string | null;
  nd_artist_id?: string | null;
  hub_artist_id?: number | null;
  /** every credited artist, with the page each opens: the catalog's (`ref`) when the Hub knows it, else the library's
   * (`nd`: Navidrome's artist) — `artistTo` */
  artists?: { name: string; ref?: string; nd?: string; id?: number }[];
  album?: string;
  isrc?: string;
  year?: number;
  duration_s?: number | null;
  spotify_plays?: number | null;
}

export interface ManualQueueEntry {
  id: number;
  track: ListenTrack;
}

/** A file on «Загрузить файлы» that is played before it is brought in (`up:<upload>`): it is no track of the library
 * or of the catalog yet, so nothing is done with it but listening — the players show none of a track's buttons (keep,
 * tags, share, the menu). */
export const isUpload = (t: { ref: string } | null | undefined): boolean => !!t?.ref.startsWith('up:');

/** The page of a credited artist of a playing track: the catalog's when the track says which (`ref`), the library's
 * (`nd`), else the one found by the name (`pages/Artist.tsx`, `ArtistNamedPage`) — a wave, a recommendation or a
 * line of the history names its artists and nothing more. Null: there is not even a name. */
export const artistTo = (a: { name?: string | null; ref?: string | null; nd?: string | null; id?: number }): string | null =>
  a.id || a.ref ? `/artist/${encodeURIComponent(a.id ?? a.ref ?? '')}` : a.nd ? `/lib/artist/${encodeURIComponent(a.nd)}` : a.name?.trim() ? `/artist-named/${encodeURIComponent(a.name.trim())}` : null;

/** Everyone credited on a playing track, each with what opens their page (`artistTo`). A track that names its
 * artists one by one says so itself; one that has only a line of them («Eliminate, fussy» — a wave's, a line of the
 * history) is read by its commas, the first being the one its `artist_ref` is of. */
export function creditedOf(t: Pick<ListenTrack, 'artist' | 'artist_ref' | 'artists' | 'hub_artist_id'>): NonNullable<ListenTrack['artists']> {
  if (t.artists?.length) return t.artists;
  const names = t.artist.split(',').map((x) => x.trim()).filter(Boolean);
  return (names.length ? names : [t.artist]).map((name, i) => ({ name, ...(i === 0 && t.artist_ref ? { ref: t.artist_ref } : {}),
    ...(i === 0 && t.hub_artist_id ? { id: t.hub_artist_id } : {}) }));
}

/** off; all: the queue starts over after its end; one: the track plays again. */
export type RepeatMode = 'off' | 'all' | 'one';

/** resolving: the Hub is picking/opening the file; loading: buffering after a seek or a stall. */
export type ListenPhase = 'resolving' | 'loading' | 'playing' | 'paused' | 'ended' | 'error';

export interface ListenState {
  track: ListenTrack | null;
  /** the list the track was started from (album, search results); `index` is the track's place in it */
  queue: readonly ListenTrack[];
  manualQueue: readonly ManualQueueEntry[];
  index: number;
  /** where the list was started from (`routeContext()`, `wave:<id>`) */
  context: string;
  /** the page the list was started on (`/playlist/<id>`): the player says what it plays and links there */
  page: string;
  phase: ListenPhase;
  info: ListenOut | null;
  error: string | null;
  position: number;
  duration: number;
  volume: number;
  /** Catalog tracks downloaded from the player or a track row this session: rows show «качается», then «есть». */
  downloads: ReadonlyMap<string, 'queued' | 'done'>;
  /** The queue after the current track is in random order (not for «Моя волна»: the Hub picks its order). */
  shuffle: boolean;
  /** ... and the Hub puts recommendations between the playlist's songs (the smart shuffle, `lib/smartShuffle.ts`):
   * for the list that plays now; `shuffle` is on too */
  smart: boolean;
  repeat: RepeatMode;
}

/** The id the Hub knows a track of a playlist by: its Navidrome song, else the catalog ref. */
export const idOf = (t: Pick<ListenTrack, 'ref' | 'nd_song_id'>) => t.nd_song_id || t.ref;

function playbackRef(t: ListenTrack) {
  return t.library_track_id && !t.ref.startsWith('file:') && !isUpload(t) && !keptPlays(t)
    ? `lib:${t.library_track_id}` : t.ref;
}

const VOLUME_KEY = 'hm-listen-volume';
const savedVolume = (() => {
  try {
    const value = localStorage.getItem(VOLUME_KEY);
    const volume = value === null ? 1 : Number(value);
    return Number.isFinite(volume) && volume >= 0 && volume <= 1 ? volume : 1;
  } catch { return 1; }
})();

const MODES_KEY = 'hm-listen-modes';
const savedModes = ((): { shuffle: boolean; smart: boolean; repeat: RepeatMode } => {
  try {
    const m = JSON.parse(localStorage.getItem(MODES_KEY) ?? 'null') as { shuffle?: unknown; smart?: unknown; repeat?: unknown } | null;
    return { shuffle: m?.shuffle === true, smart: m?.shuffle === true && m?.smart === true,
      repeat: m?.repeat === 'all' || m?.repeat === 'one' ? m.repeat : 'off' };
  } catch {
    return { shuffle: false, smart: false, repeat: 'off' };
  }
})();
/** The shuffle button's choice (off, on, smart) lives on past lists that cannot be smart: an album plays in the plain
 * shuffle, the next playlist in the smart one again. `state.smart` is whether the playing list has the smart one. */
let smartChosen = savedModes.smart;

let state: ListenState = {
  track: null,
  queue: [],
  manualQueue: [],
  index: 0,
  context: 'other',
  page: '',
  phase: 'paused',
  info: null,
  error: null,
  position: 0,
  duration: 0,
  volume: savedVolume,
  downloads: new Map(),
  shuffle: savedModes.shuffle,
  smart: false,
  repeat: savedModes.repeat,
};
const subscribers = new Set<() => void>();

function set(patch: Partial<ListenState>) {
  const shown = !!state.track;
  state = { ...state, ...patch };
  // Time updates are frequent; queue edits and controls must survive an immediate reload.
  const playerKeys = Object.keys(patch).filter((key) => key !== 'manualQueue');
  if (playerKeys.length) persistListening(playerKeys.some((key) => key !== 'position' && key !== 'duration'));
  if (!!state.track !== shown) webPlayerShown(!!state.track);
  for (const fn of subscribers) fn();
}

export function subscribeListening(fn: () => void) {
  subscribers.add(fn);
  return () => {
    subscribers.delete(fn);
  };
}

/** The page's own player is playing or about to (a reload would cut the sound). */
export const listenBusy = () => !!state.track && (state.phase === 'playing' || state.phase === 'resolving' || state.phase === 'loading');

export const getListenState = (): ListenState => state;

/** A slice of the listening state; `select` must return a primitive or a stored object (not a new one). */
export function useListen<T>(select: (s: ListenState) => T): T {
  return useSyncExternalStore(subscribeListening, () => select(state));
}

let audio: HTMLAudioElement | null = null;

/** The page player's `<audio>` (null until the first track): `lib/waveAudio.ts` reads its loudness. */
export const listenAudio = (): HTMLAudioElement | null => audio;
let pendingSeek = 0;
let pendingPosition: (() => number) | null = null;
let seekRevision = 0;
let resolving: { controller: AbortController; pending: boolean; retryMedia: boolean; anyFormat: boolean } | null = null;
let autoAdvanceOnFailure = false;
/** How many tracks of the queue are loaded ahead (`prefetchNext`): the next one whole, the rest only their start. */
const AHEAD = 3;
/** What the Hub was asked to load ahead: ref -> how much of it, and the request. */
const prefetching = new Map<string, { how: 'whole' | 'head'; ctl: AbortController }>();
/** Seconds into a track after which ⏮ restarts it instead of going to the previous one. */
const RESTART_AFTER_S = 3;
/** Left after this share of a track: it counts as played to the end (`finish`), earlier — `skip`. */
const FINISH_SHARE = 0.9;

/** The track being played, as reported to the Hub: `started` once its `start` was sent. */
type OpenPlay = { ref: string; context: string; page?: string; launch: Launch; started: boolean; playbackId: string; listenedMs: number; anchor: number | null; account: number | null; ndSongId?: string | null; trackId?: number | null; manual?: ManualClaim };
let played: OpenPlay | null = null;
let OPEN_PLAY_KEY = (() => {
  try {
    let tabId = sessionStorage.getItem('hm-listening-tab');
    if (!tabId) { tabId = newEventId(); sessionStorage.setItem('hm-listening-tab', tabId); }
    return `hm-open-play:${tabId}`;
  } catch { return `hm-open-play:${newEventId()}`; }
})();
const DEVICE_KEY = 'hm-listening-device';
const deviceId = (() => {
  try {
    const existing = localStorage.getItem(DEVICE_KEY);
    if (existing) return existing;
    const id = newEventId();
    localStorage.setItem(DEVICE_KEY, id);
    return id;
  } catch { return newEventId(); }
})();

function accrue(p: OpenPlay) {
  if (p.anchor !== null) {
    const now = performance.now();
    p.listenedMs += Math.max(0, now - p.anchor);
    p.anchor = now;
  }
}

function checkpoint() {
  const p = played;
  if (!p?.started) return;
  accrue(p);
  const snapshot = { ref: p.ref, context: p.context, page: p.page, playback_id: p.playbackId, device_id: deviceId,
    listened_ms: Math.round(p.listenedMs), nd_song_id: p.ndSongId, track_metadata: metadata(), account: p.account, updated_ms: Date.now(),
    launch_id: p.launch.id, launch_origin: p.launch.origin };
  try { localStorage.setItem(OPEN_PLAY_KEY, JSON.stringify(snapshot)); } catch (e) { console.error('Could not persist listening checkpoint', e); }
  playEvent('progress', { ref: p.ref, nd_song_id: p.ndSongId, track_id: p.trackId }, snapshot);
}

type SavedPlay = { ref: string; context: string; playback_id: string; device_id: string; listened_ms: number;
  nd_song_id?: string | null; account?: number | null; updated_ms?: number; launch_id?: string | null; launch_origin?: LaunchOrigin | null };

function recover(key: string) {
  try {
    const old = JSON.parse(localStorage.getItem(key) ?? 'null') as SavedPlay | null;
    if (old?.ref && old.playback_id && old.device_id && old.account != null && Number.isFinite(old.listened_ms)) {
      if (playEvent('interrupted', { ref: old.ref, nd_song_id: old.nd_song_id }, old)) localStorage.removeItem(key);
    }
  } catch (e) { console.error('Could not recover listening checkpoint', e); }
}

let releaseTabLock: (() => void) | null = null;
function scanAbandoned() {
  try {
    const keys = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i))
      .filter((key): key is string => !!key && key.startsWith('hm-open-play:') && key !== OPEN_PLAY_KEY);
    for (const key of keys) {
      if (navigator.locks) {
        void navigator.locks.request(key, { ifAvailable: true }, (lock) => { if (lock) recover(key); });
      } else {
        const old = JSON.parse(localStorage.getItem(key) ?? 'null') as SavedPlay | null;
        if (old?.updated_ms && Date.now() - old.updated_ms > 300_000) recover(key);
      }
    }
  } catch (e) { console.error('Could not inspect listening checkpoints', e); }
}

function lockTab() {
  if (!navigator.locks) { recover(OPEN_PLAY_KEY); scanAbandoned(); return; }
  void navigator.locks.request(OPEN_PLAY_KEY, { ifAvailable: true }, async (lock) => {
    if (!lock) {
      OPEN_PLAY_KEY = `hm-open-play:${newEventId()}`;
      try { sessionStorage.setItem('hm-listening-tab', OPEN_PLAY_KEY.slice('hm-open-play:'.length)); } catch {}
      lockTab();
      return;
    }
    recover(OPEN_PLAY_KEY);
    scanAbandoned();
    await new Promise<void>((resolve) => { releaseTabLock = resolve; });
  });
}
lockTab();
setInterval(() => { checkpoint(); scanAbandoned(); }, 15_000);
window.addEventListener('pagehide', () => {
  persistListening(true);
  checkpoint();
  reportEnd(false, false, true);
  releaseTabLock?.();
  releaseTabLock = null;
});
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') persistListening(true);
});
window.addEventListener('pageshow', (event) => {
  if (!event.persisted) return;
  lockTab();
  const t = state.track;
  if (t) {
    played = { ref: t.ref, context: state.context, page: state.page, launch: listLaunch, started: false, playbackId: newEventId(), listenedMs: 0, anchor: null, account: listeningAccount() };
    reportStart();
  }
});

/** The screen the playing list was started from. */
let listPage = '';
let listContext = 'other';
/** The launch of the playing list: its tracks' events carry it (INSIGHTS.md 6.5). */
let listLaunch: Launch = newLaunch(listContext);

function metadata() {
  const t = state.track;
  return t ? { title: t.title, artist_credit: t.artist, duration_s: t.duration_s, album: t.album,
    isrc: t.isrc, year: t.year, ...(t.artists?.length ? { artists: t.artists } : {}) } : undefined;
}

/** Sends `start` once the track sounds and the Hub has said what plays. */
function reportStart() {
  const p = played;
  if (!p || p.started || state.track?.ref !== p.ref || !state.info) return;
  if (state.phase !== 'playing') return;
  p.started = true;
  p.ndSongId = state.info.nd_song_id;
  p.trackId = state.info.track_id ?? state.track?.library_track_id ?? null;
  p.anchor = performance.now();
  playEvent('start', { ref: p.ref, nd_song_id: p.ndSongId, track_id: p.trackId }, { context: p.context, page: p.page, playback_id: p.playbackId, device_id: deviceId, listened_ms: 0, track_metadata: metadata(), account: p.account, launch_id: p.launch.id, launch_origin: p.launch.origin,
    ...(p.manual ? { queue_entry_id: p.manual.id, queue_claim: p.manual.token } : {}) });
  checkpoint();
}

/** The current track is left (`ended`: it played to its end): `finish` or `skip` by where the player was. */
function reportEnd(ended = false, failed = false, interrupted = false) {
  const p = played;
  if (p?.started) accrue(p);
  played = null;
  if (!p?.started || state.track?.ref !== p.ref) return;
  const duration = state.duration || state.track.duration_s || 0;
  const position = ended ? duration : state.position;
  const kind = interrupted ? 'interrupted' : failed ? 'error' : ended || (duration > 0 && position >= duration * FINISH_SHARE) ? 'finish' : 'skip';
  const persisted = playEvent(kind, { ref: p.ref, nd_song_id: p.ndSongId, track_id: p.trackId }, { position_s: position, duration_s: duration || null, context: p.context, page: p.page, playback_id: p.playbackId, device_id: deviceId, listened_ms: Math.round(p.listenedMs), track_metadata: metadata(), account: p.account, launch_id: p.launch.id, launch_origin: p.launch.origin });
  if (persisted) try { localStorage.removeItem(OPEN_PLAY_KEY); } catch (e) { console.error('Could not close listening checkpoint', e); }
}

// --- a soft start and stop -------------------------------------------------------------------------------------------
// A pause and a resume are not a cut: the sound fades out before the pause and in after the resume, as the players of
// the music services do — too short to notice as a fade, long enough not to hit the ears. Only a pause and a resume
// by the listener: a new track, a seek and a failure are as they are. The element's volume is the listener's volume
// times `fadeGain`. In a hidden tab the timers are slowed down to a second, so there it is a plain pause; where the
// volume cannot be changed (iOS) it is a plain pause and start too.

const FADE_OUT_MS = 180;
const FADE_IN_MS = 300;
let fadeGain = 1;
let fadeTimer: number | undefined;
// a resume waits to fade in until the sound really starts (`playing`): a fade during loading would be lost
let fadeInOnPlaying = false;

function applyVolume() {
  if (audio) audio.volume = state.volume * fadeGain;
}

function cancelFade() {
  window.clearInterval(fadeTimer);
  fadeTimer = undefined;
}

/** The gain to `to` over `ms`, then `done`. An equal-power curve: the loudness changes evenly to the ear. */
function fade(to: number, ms: number, done?: () => void) {
  cancelFade();
  if (document.hidden || ms <= 0) {
    fadeGain = to;
    applyVolume();
    done?.();
    return;
  }
  const from = fadeGain;
  const t0 = performance.now();
  const step = () => {
    const k = Math.min(1, (performance.now() - t0) / ms);
    const eased = to > from ? Math.sin((k * Math.PI) / 2) : Math.cos(((1 - k) * Math.PI) / 2);
    fadeGain = from + (to - from) * eased;
    applyVolume();
    if (k >= 1) {
      cancelFade();
      done?.();
    }
  };
  fadeTimer = window.setInterval(step, 16);
  step();
}

/** Stop the sound at once (a new track, a failure, the player closed): no fade is left to finish later. */
function hardPause(a: HTMLAudioElement) {
  cancelFade();
  fadeInOnPlaying = false;
  a.pause();
  fadeGain = 1;
  applyVolume();
}

function el(): HTMLAudioElement {
  if (audio) return audio;
  const a = new Audio();
  a.preload = 'auto';
  a.volume = state.volume;
  const sync = () =>
    set({ position: pendingSeek || a.currentTime, duration: Number.isFinite(a.duration) && a.duration > 0 ? a.duration : state.duration });
  a.addEventListener('loadedmetadata', () => {
    if (pendingPosition) {
      pendingSeek = pendingPosition();
      pendingPosition = null;
    }
    if (pendingSeek > 0) {
      a.currentTime = Number.isFinite(a.duration) ? Math.min(pendingSeek, Math.max(0, a.duration - 0.1)) : pendingSeek;
      pendingSeek = 0;
    }
    sync();
  });
  a.addEventListener('timeupdate', sync);
  a.addEventListener('durationchange', sync);
  a.addEventListener('play', pauseNative);
  a.addEventListener('playing', () => {
    if (fadeInOnPlaying) {
      fadeInOnPlaying = false;
      fade(1, FADE_IN_MS);
    }
    if (resolving) resolving.retryMedia = false;
    set({ phase: 'playing', error: null });
    confirmManualPlayback();
    refreshInfo();
    reportStart();
    if (played?.started && played.anchor === null) played.anchor = performance.now();
  });
  const suspendClock = () => { if (played?.started) { accrue(played); played.anchor = null; checkpoint(); } };
  a.addEventListener('seeking', suspendClock);
  a.addEventListener('seeked', () => {
    if (played?.started && !a.paused && a.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA) played.anchor = performance.now();
  });
  a.addEventListener('waiting', () => {
    suspendClock();
    if (state.phase === 'playing') set({ phase: 'loading' });
  });
  // Pauses from outside (audio focus, lock screen); switching tracks also fires one, during 'resolving'.
  a.addEventListener('pause', () => {
    suspendClock();
    if (!a.ended && (state.phase === 'playing' || state.phase === 'loading')) set({ phase: 'paused' });
  });
  // the same element plays on: it was unlocked by the first tap, so the next track may start without one
  a.addEventListener('ended', () => {
    reportEnd(true);
    if (state.repeat === 'one') start(state.queue, state.index, true);
    else if (!advance(true)) set({ phase: 'ended' });
  });
  a.addEventListener('error', () => mediaFailed(a));
  // the app's player started: one player on screen, so this one closes (the app shows its own again)
  window.addEventListener(NATIVE_PLAYING_EVENT, stopListening);
  audio = a;
  return a;
}

function mediaFailed(a: HTMLAudioElement) {
  // The audio element has no HTTP status. Let the parallel lookup report the cause and decide whether to retry.
  if (!a.getAttribute('src') || resolving?.pending) return;
  // the browser said it plays the file's format and did not: once, the file comes in a format every browser plays
  const src = new URL(a.src, window.location.href);
  if (a.error?.code === MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED && resolving && !resolving.anyFormat && !played?.started
      && src.pathname === '/api/listen/stream' && src.searchParams.get('cant') !== CANT_ANY) {
    resolving.anyFormat = true;
    src.searchParams.set('cant', CANT_ANY);
    const paused = state.phase === 'paused';
    a.src = src.href;
    if (paused) a.load();
    else play(a);
    return;
  }
  if (resolving?.retryMedia) {
    resolving.retryMedia = false;
    const paused = state.phase === 'paused';
    if (!paused) set({ phase: 'loading' });
    a.load();
    if (!paused) play(a);
    return;
  }
  reportEnd(false, true);
  playbackFailed(state.error ?? 'Не удалось воспроизвести');
}

function playbackFailed(message: string) {
  set({ phase: 'error', error: message });
  if (audio) hardPause(audio);
  if (autoAdvanceOnFailure) {
    if (activeManual && !activeManual.started) {
      failedManualEntries.add(activeManual.id);
      releaseManualPlayback();
    }
    advance(true, false);
  }
}

/** `soft`: a resume by the listener — the sound comes in from silence (see `fade`). Otherwise at full volume at once,
 * and a fade-out under way (a pause tapped just before) is dropped: it would pause what plays now. */
function play(a: HTMLAudioElement, soft = false) {
  cancelFade();
  fadeInOnPlaying = soft && !document.hidden;
  if (fadeInOnPlaying && !a.paused) {
    // still sounding, fading out from a pause a moment ago: back up from where it is
    fadeInOnPlaying = false;
    fade(1, FADE_IN_MS);
  } else {
    fadeGain = fadeInOnPlaying ? 0 : 1;
    applyVolume();
  }
  // NotAllowedError (no user gesture): wait for the play button; AbortError: a newer track or a pause won.
  a.play().catch((e: unknown) => {
    if ((e as Error)?.name === 'NotAllowedError') set({ phase: 'paused' });
    if (a.paused) {
      fadeInOnPlaying = false;
      fadeGain = 1;
      applyVolume();
    }
  });
}

/** `auto`: reached by the previous track ending; a track that can't be played is then skipped. */
function start(queue: readonly ListenTrack[], index: number, auto = false, position: number | (() => number) = 0, autoplay = true, claim?: ManualClaim) {
  const track = queue[index];
  cancelManualOpening();
  if (claim || track !== state.track) {
    releaseManualPlayback();
    activeManual = claim;
  }
  // A finished manual track has left the account's queue. Keep the automatic list in its own order.
  if (queue.some((entry, i) => entry.queued && i !== index)) {
    const from = index;
    index = queue.slice(0, from).filter((entry) => !entry.queued).length;
    queue = queue.filter((entry, i) => i === from || !entry.queued);
  }
  const a = el();
  reportEnd();
  // a recommendation of the smart shuffle reports under its session, the playlist's own songs under their list
  played = { ref: track.ref, context: track.queued ? 'queue' : track.rec ? `smart:${track.rec.session}` : listContext,
    page: listPage, launch: listLaunch, started: false, playbackId: newEventId(), listenedMs: 0, anchor: null,
    account: listeningAccount(), manual: activeManual };
  resolving?.controller.abort();
  const ctl = new AbortController();
  const resolution = { controller: ctl, pending: true, retryMedia: false, anyFormat: false };
  resolving = resolution;
  refreshed = null;
  autoAdvanceOnFailure = auto;
  pendingPosition = typeof position === 'function' ? position : null;
  pendingSeek = typeof position === 'function' ? position() : position;
  set({ track, queue, index, context: listContext, page: listPage, phase: autoplay ? 'resolving' : 'paused', info: null,
    error: null, position: pendingSeek, duration: track.duration_s ?? 0,
    ...(claim ? { manualQueue: state.manualQueue.filter((entry) => entry.id !== claim.id) } : {}) });
  const reserve = listContext.startsWith('wave:') ? waveAudio(track.ref) : null;
  const ref = reserve ? track.ref : playbackRef(track);
  a.src = reserve?.url ?? listenStreamUrl(ref);
  if (autoplay) play(a);
  else a.load();
  mediaSession(track);
  // what was asked for ahead is asked anew from here (`prefetchNext`); an abort stops only the page's waiting,
  // the Hub goes on with what it has begun
  for (const p of prefetching.values()) p.ctl.abort();
  prefetching.clear();
  // a track kept on the device (`lib/offline.ts`) plays from it: when the Hub cannot be asked, what it said of the
  // file when the track was kept is shown
  (reserve ? Promise.resolve(reserve.info) : api.listen(ref, ctl.signal)).catch((e: unknown) => {
    const kept = isAbort(e) ? null : keptInfo(track.ref);
    if (kept) return kept;
    if (e instanceof ApiError && e.status === 500 && !ctl.signal.aborted) {
      resolution.retryMedia = a.readyState < HTMLMediaElement.HAVE_CURRENT_DATA && !played?.started;
      return api.listen(ref, ctl.signal);
    }
    throw e;
  }).then(
    (info) => {
      if (resolving !== resolution) return;
      resolution.pending = false;
      set({ info });
      if (a.error) {
        mediaFailed(a);
        if (state.phase === 'error' || resolving !== resolution) return;
      }
      updateMediaTitle();
      if (state.phase === 'playing') refreshInfo();
      reportStart();
      prefetchNext();
    },
    (e: unknown) => {
      if (isAbort(e) || resolving !== resolution) return;
      resolution.pending = false;
      resolution.retryMedia = false;
      if (a.error) reportEnd(false, true);
      playbackFailed(errorMessage(e));
    },
  );
}

/** The Hub opens the picked file only when the stream asks for it, and moves on to the next file if it doesn't
 * open: once sound plays, the source shown is asked for again (the Hub has it in memory). */
let refreshed: string | null = null;
function refreshInfo() {
  const track = state.track;
  if (!track || state.info?.kind !== 'full') return;
  if (state.context.startsWith('wave:') && waveAudio(track.ref)) return;
  const ref = playbackRef(track);
  if (refreshed === ref) return;
  refreshed = ref;
  api.listen(ref).then(
    (info) => {
      if (state.track !== track) return;
      set({ info });
      updateMediaTitle();
    },
    () => {},
  );
}

/** Asks the Hub to pick the next track's file now (it remembers the pick), so it starts at once. */
function prefetchNext() {
  // the tracks the queue plays next, the nearest first: the Hub opens their files now (opening a source is what
  // takes seconds) — the next one whole, the ones after it only their start, so skipping through them is instant
  if (!state.track || !hubReachable() || state.context === OFFLINE_WAVE) return;
  const ahead = state.manualQueue.filter((entry) => !failedManualEntries.has(entry.id)).slice(0, AHEAD).map((entry) => entry.track);
  for (let k = state.index + 1; ahead.length < AHEAD && k < state.queue.length; k++) ahead.push(state.queue[k]);
  if (!ahead.length && state.repeat === 'all' && !onWave()) {
    const first = state.queue.find((track) => !track.queued);
    if (first) ahead.push(first);
  }
  const want = new Map(ahead.flatMap((t, k) =>
    state.context.startsWith('wave:') && waveAudio(t.ref) ? [] : [[playbackRef(t), k === 0 ? 'whole' : 'head'] as const]));
  for (const [ref, p] of prefetching) {
    if (want.get(ref) === p.how) want.delete(ref); // asked for already, and for as much
    else if (!want.has(ref)) {
      p.ctl.abort(); // no longer coming up
      prefetching.delete(ref);
    }
  }
  for (const [ref, how] of want) {
    prefetching.get(ref)?.ctl.abort(); // it was a start, it is the next track now
    const ctl = new AbortController();
    prefetching.set(ref, { how, ctl });
    api.listen(ref, ctl.signal, how).catch(() => {
      /* the track itself will show the error when it is reached */
    });
  }
}

// --- tracks on screen -------------------------------------------------------------------------------------

/** Rows of catalog tracks currently on (or just below) the screen. */
const onScreen = new Map<Element, string>();
let screenObserver: IntersectionObserver | null = null;
let sendTimer: ReturnType<typeof setTimeout> | undefined;
let lastSent = '';
let firstRef: string | null = null;
const SEND_DELAY_MS = 300;

function observer(): IntersectionObserver | null {
  if (screenObserver || typeof IntersectionObserver === 'undefined') return screenObserver;
  screenObserver = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        const ref = (e.target as HTMLElement).dataset.listenRef;
        if (e.isIntersecting && ref) onScreen.set(e.target, ref);
        else onScreen.delete(e.target);
      }
      scheduleSend();
    },
    { rootMargin: '0px 0px 25% 0px' }, // a little ahead of scrolling down
  );
  return screenObserver;
}

function scheduleSend(now = false) {
  clearTimeout(sendTimer);
  sendTimer = setTimeout(sendOnScreen, now ? 0 : SEND_DELAY_MS);
}

/** Tells the Hub which tracks are on screen, top first (a touched one first of all); it drops the rest. */
function sendOnScreen() {
  const rows = [...onScreen].map(([el, ref]) => ({ ref, top: el.getBoundingClientRect().top }));
  rows.sort((a, b) => a.top - b.top);
  const refs = [...new Set([...(firstRef && rows.some((r) => r.ref === firstRef) ? [firstRef] : []), ...rows.map((r) => r.ref)])];
  const key = refs.join('|');
  if (key === lastSent) return;
  lastSent = key;
  api.listenPrefetch(refs).catch(() => {
    lastSent = ''; // try again with the next change
  });
}

/** Watches a catalog track's row: while it is on screen, the Hub picks its file in the background. Returns the
 * cleanup for an effect. */
export function watchOnScreen(el: HTMLElement, ref: string): () => void {
  const o = observer();
  if (!o) return () => {};
  el.dataset.listenRef = ref;
  o.observe(el);
  return () => {
    o.unobserve(el);
    if (onScreen.delete(el)) scheduleSend();
  };
}

/** A finger is down on this track: it goes first in the Hub's background picks (the tap itself comes ~0.1–0.3 s
 * later). Not a pick of its own: a touch that turns into a scroll costs nothing extra. */
export function touchTrack(ref: string) {
  if (firstRef === ref) return;
  firstRef = ref;
  scheduleSend(true);
}

/** Tap on a catalog track: starts it (with `queue`, the list it belongs to, playing on after it), or
 * pauses/resumes it if it is the current one. `origin` — where the list was launched (by default by `context`). */
export function listen(track: ListenTrack, queue?: readonly ListenTrack[], context?: string, origin?: LaunchOrigin) {
  if (state.track?.ref === track.ref && state.phase !== 'error') return toggle();
  if (!playableNow(track, context ?? routeContext())) return notOnDevice();
  startList(track, queue, context, origin);
}

/** Without the network only what is on the device plays: a track kept for offline, or one of «Моя волна»'s reserve
 * when played as the wave. Anything else would only go looking for its file and fail. */
function playableNow(track: ListenTrack, context: string): boolean {
  return hubReachable() || !!keptInfo(track.ref) || (context.startsWith('wave:') && !!waveAudio(track.ref));
}

/** A tap on what cannot play without the network: said, and what plays now goes on. */
function notOnDevice() {
  toast('Нет связи с сервером — трека нет на устройстве');
}

function startList(track: ListenTrack, queue?: readonly ListenTrack[], context?: string, origin?: LaunchOrigin, page?: string) {
  failedManualEntries.clear();
  listContext = context ?? routeContext();
  // offline the list plays through what is on the device, not past tracks that each fail in turn
  if (queue && !hubReachable()) queue = queue.filter((t) => t.ref === track.ref || playableNow(t, listContext));
  listPage = page ?? location.hash.replace(/^#/, '').split('?')[0];
  listLaunch = newLaunch(listContext === OFFLINE_WAVE ? 'other' : listContext, origin);
  const list = queue?.some((t) => t.ref === track.ref) ? queue : [track];
  const at = list.findIndex((t) => t.ref === track.ref);
  smartHooks?.listStarted(listContext); // the previous list's smart session is over; this list may have its own
  // shuffle stays on for the next list: the tapped track first, the rest in random order
  if (state.shuffle && !listContext.startsWith('wave:') && list.length > 1) {
    unshuffled = list;
    set({ smart: false });
    start([list[at], ...shuffled(list.filter((_, i) => i !== at))], 0);
    // the smart shuffle chosen and this list is a playlist / «Любимое»: the Hub orders it and puts recommendations
    // in, a moment after the tapped track has started (the plain shuffle plays until then)
    if (smartChosen && smartHooks?.canStart()) {
      set({ smart: true });
      smartHooks.begin(list, list[at]);
    }
  } else {
    unshuffled = null;
    set({ smart: false });
    start(list, at);
  }
}

/** «Слушать» of a whole list: from its first track (with shuffle on, a random one). `shuffle` sets the mode. */
export function listenAll(list: readonly ListenTrack[], opts: { shuffle?: boolean; context?: string; origin?: LaunchOrigin; page?: string } = {}) {
  if (!list.length) return;
  if (!hubReachable()) {
    const context = opts.context ?? routeContext();
    list = list.filter((t) => playableNow(t, context));
    if (!list.length) return notOnDevice();
  }
  if (opts.shuffle !== undefined) setModes({ shuffle: opts.shuffle });
  // starts over even when the first track is the one playing (listen() would pause it)
  startList(state.shuffle ? list[Math.floor(Math.random() * list.length)] : list[0], list, opts.context, opts.origin, opts.page);
}

// --- the queue: shuffle, repeat, «Играть следующим», «В очередь» -----------------------------------------

/** The queue in its own order while shuffle is on (tracks added since go after it when shuffle is turned off). */
let unshuffled: readonly ListenTrack[] | null = null;

const SESSION_KEY = 'hm-listen-session:';
const QUEUE_MIGRATION_KEY = 'hm-manual-queue-migration:';
let sessionAccount: number | null = null;
let lastSaved = 0;
type ManualClaim = { id: number; token: string; started: boolean };
let activeManual: ManualClaim | undefined;
const failedManualEntries = new Set<number>();
let manualOpening = false;
let manualTransition = 0;
let queueRead = 0;
let queueWrites = Promise.resolve<unknown>(undefined);
const queueConfirmations = new Map<number, string>();

function cancelManualOpening() {
  manualTransition++;
  manualOpening = false;
}

function releaseManualPlayback() {
  const claim = activeManual;
  activeManual = undefined;
  if (claim && !claim.started) {
    void api.releaseManual(claim.id, claim.token).then(refreshManualQueue, () => undefined);
  }
}

export async function refreshManualQueue() {
  const account = sessionAccount;
  if (account === null) return;
  const read = ++queueRead;
  try {
    const entries = await api.manualQueue();
    if (read !== queueRead || account !== sessionAccount) return;
    for (const id of queueConfirmations.keys()) {
      if (!entries.some((entry) => entry.id === id)) queueConfirmations.delete(id);
    }
    set({ manualQueue: entries.filter((entry) => entry.id !== activeManual?.id && !queueConfirmations.has(entry.id)) });
    if (listenBusy()) prefetchNext();
  } catch { /* The device keeps its last view until the Hub is reachable again. */ }
}

function flushQueueConfirmations() {
  const account = sessionAccount;
  for (const [id, token] of queueConfirmations) {
    void api.confirmManual(id, token).then(() => {
      if (account === sessionAccount) void refreshManualQueue();
    }, () => undefined);
  }
}

function confirmManualPlayback() {
  if (!activeManual || activeManual.started) return;
  activeManual.started = true;
  queueConfirmations.set(activeManual.id, activeManual.token);
  persistListening(true);
  flushQueueConfirmations();
}

async function appendManualTracks(tracks: readonly ListenTrack[], account: number, requestId: string) {
  let count = 0;
  for (let i = 0; i < tracks.length; i += 400) {
    if (account !== sessionAccount) break;
    const { count: added } = await api.appendManualQueue(tracks.slice(i, i + 400), account, `${requestId}:${i}`);
    count += added;
  }
  return count;
}

export function watchManualQueue() {
  const account = sessionAccount;
  if (account === null) return;
  const fetchQueue = () => {
    flushQueueConfirmations();
    void refreshManualQueue();
  };
  const onVisible = () => { if (document.visibilityState === 'visible') fetchQueue(); };
  const offChanged = hubEvents.on('playback_queue', () => void refreshManualQueue());
  const offReconnect = hubEvents.on('reconnect', fetchQueue);
  const offPing = hubEvents.on('ping', () => void refreshManualQueue());
  const offBack = onHubBack(fetchQueue);
  document.addEventListener('visibilitychange', onVisible);
  const migration = async () => {
    try {
      const key = QUEUE_MIGRATION_KEY + account;
      const old = JSON.parse(localStorage.getItem(key) ?? 'null') as { id: string; tracks: ListenTrack[] } | null;
      if (old) {
        await appendManualTracks(old.tracks, account, old.id);
        if (sessionAccount === account) localStorage.removeItem(key);
      }
    } catch { /* A saved migration can be retried next time the player is opened. */ }
    fetchQueue();
  };
  queueWrites = queueWrites.then(migration);
  return () => {
    offChanged();
    offReconnect();
    offPing();
    offBack();
    document.removeEventListener('visibilitychange', onVisible);
  };
}

/** What a reload goes on from, small enough to write every second; the queue it plays is kept apart
 * (`lib/listenStore.ts`) and named by `queueId`. */
interface SavedSession {
  version: 3;
  queueId: string;
  index: number;
  context: string;
  page?: string;
  /** the list's launch: a reload goes on with the same one */
  launch?: Launch;
  position: number;
  autoplay: boolean;
  shuffle: boolean;
  repeat: RepeatMode;
  smart?: boolean;
}

/** The queue last given to `saveQueue`, by what it was made of: the same queue is not written again. */
let savedQueueOf: { queue: readonly ListenTrack[]; unshuffled: readonly ListenTrack[] | null; skip: number | null } | null = null;
let savedQueueId = '';

/** What playing is handed to another device with (`lib/playbackDevices.ts`). */
export interface PlaybackSnapshot {
  queue: ListenTrack[];
  index: number;
  context: string;
  page: string;
  position: number;
  autoplay: boolean;
  shuffle: boolean;
  smart: boolean;
  repeat: RepeatMode;
  /** the order before shuffle, as places in `queue` */
  originalOrder: number[] | null;
}

/** The order before shuffle (`before`) as places in `queue` — a track's first place: the same object may stand in
 * the queue twice. */
function placesOf(queue: readonly ListenTrack[], before: readonly ListenTrack[] | null): number[] | null {
  if (!before) return null;
  const place = new Map<ListenTrack, number>();
  queue.forEach((t, i) => { if (!place.has(t)) place.set(t, i); });
  return before.flatMap((t) => place.has(t) ? [place.get(t)!] : []);
}

export function snapshotForTransfer() {
  const { track, queue, index, context, page, phase, shuffle, smart, repeat } = state;
  if (!track || context === OFFLINE_WAVE || queue.some(isUpload) || !['playing', 'paused'].includes(phase)
    || (activeManual && !activeManual.started)) return null;
  const revision = seekRevision;
  const autoplay = phase === 'playing';
  const snapshot: PlaybackSnapshot = {
    queue: [...queue], index, context, page, autoplay, shuffle, smart, repeat,
    position: pendingSeek || audio?.currentTime || 0,
    originalOrder: placesOf(queue, unshuffled),
  };
  const isCurrent = () => state.queue === queue && state.index === index && state.track === track
    && state.shuffle === shuffle && state.repeat === repeat && state.smart === smart && seekRevision === revision
    && (autoplay ? state.phase === 'playing' || state.phase === 'loading' : state.phase === 'paused');
  return { snapshot, isCurrent };
}

export function startTransferredPlayback(snapshot: PlaybackSnapshot, position: () => number) {
  failedManualEntries.clear();
  smartHooks?.end();
  unshuffled = snapshot.originalOrder?.map((i) => snapshot.queue[i]) ?? null;
  listContext = snapshot.context;
  listPage = snapshot.page;
  listLaunch = newLaunch(listContext);
  set({ shuffle: snapshot.shuffle, smart: snapshot.smart, repeat: snapshot.repeat });
  start(snapshot.queue, snapshot.index, false, position, snapshot.autoplay);
  smartHooks?.restored();
}

export function finishOutgoingPlayback() {
  reportEnd(false, false, true);
  stopListening();
}

function persistListening(force = false) {
  if (sessionAccount === null || (!force && Date.now() - lastSaved < 1000)) return;
  const account = sessionAccount;
  try {
    const key = SESSION_KEY + account;
    const unplayedManual = activeManual != null && !activeManual.started;
    const skip = unplayedManual ? state.index : null;
    lastSaved = Date.now();
    if (!state.track || state.queue.length - (skip === null ? 0 : 1) <= 0) {
      localStorage.removeItem(key);
      savedQueueOf = null;
      void dropQueue(account).catch(() => undefined);
      return;
    }
    if (!savedQueueOf || savedQueueOf.queue !== state.queue || savedQueueOf.unshuffled !== unshuffled || savedQueueOf.skip !== skip) {
      const queue = skip === null ? [...state.queue] : state.queue.filter((_, i) => i !== skip);
      const originalOrder = placesOf(queue, unshuffled);
      savedQueueOf = { queue: state.queue, unshuffled, skip };
      savedQueueId = newEventId();
      void saveQueue(account, { id: savedQueueId, queue, originalOrder }).catch(() => undefined);
    }
    const saved: SavedSession = {
      version: 3, queueId: savedQueueId, index: unplayedManual ? Math.max(0, state.index - 1) : state.index,
      context: state.context, page: state.page, launch: listLaunch,
      position: unplayedManual ? 0 : state.position, autoplay: !unplayedManual && listenBusy(),
      shuffle: state.shuffle, smart: state.smart, repeat: state.repeat,
    };
    localStorage.setItem(key, JSON.stringify(saved));
  } catch { /* Playback remains available when storage is full or disabled. */ }
}

/** Restore only after /me confirms the account. Native Android playback owns its own session. */
export function restoreListening(account: number | null) {
  if (sessionAccount === account) return;
  if (sessionAccount !== null) stopListening();
  queueConfirmations.clear();
  failedManualEntries.clear();
  set({ manualQueue: [] });
  sessionAccount = account;
  savedQueueOf = null;
  if (account === null || canPlayAll()) return;
  void restoreSaved(account);
}

const restoredSubs = new Set<() => void>();
/** Called each time a saved session has been played on from (`restoreListening`): it comes a moment after the
 * page is up, the queue being read from IndexedDB — what goes with it («Моя волна») is picked up then. */
export function onListeningRestored(fn: () => void): () => void {
  restoredSubs.add(fn);
  return () => restoredSubs.delete(fn);
}

/** The session saved for `account` and the queue it names, played on from where it was — unless the user started
 * something meanwhile (the queue is read from IndexedDB, a moment after the page is up). */
async function restoreSaved(account: number) {
  try {
    const saved = JSON.parse(localStorage.getItem(SESSION_KEY + account) ?? 'null') as SavedSession | null;
    if (!saved || saved.version !== 3 || typeof saved.queueId !== 'string') return;
    const kept = await loadQueue(account);
    if (sessionAccount !== account || state.track || !kept || kept.id !== saved.queueId) return;
    const queue = kept.queue;
    const validTrack = (t: ListenTrack) => t && typeof t.ref === 'string' && !!t.ref && typeof t.title === 'string' && typeof t.artist === 'string';
    if (!Array.isArray(queue) || !queue.length || !queue.every(validTrack)
      || !Number.isInteger(saved.index) || saved.index < 0 || saved.index >= queue.length
      || typeof saved.context !== 'string' || !Number.isFinite(saved.position) || saved.position < 0) return;
    unshuffled = Array.isArray(kept.originalOrder) && kept.originalOrder.every((i) => Number.isInteger(i) && i >= 0 && i < queue.length)
      ? kept.originalOrder.map((i) => queue[i]) : null;
    listContext = saved.context;
    listPage = typeof saved.page === 'string' ? saved.page : '';
    listLaunch = saved.launch && typeof saved.launch.id === 'string' && saved.launch.id
      ? { id: saved.launch.id, origin: typeof saved.launch.origin === 'string' ? saved.launch.origin : null }
      : newLaunch(saved.context);
    set({ shuffle: saved.shuffle === true, smart: saved.smart === true, repeat: saved.repeat === 'all' || saved.repeat === 'one' ? saved.repeat : 'off' });
    start(queue, saved.index, false, saved.position, saved.autoplay === true);
    smartHooks?.restored();
    for (const fn of restoredSubs) fn();
  } catch { /* Ignore an incomplete or obsolete snapshot. */ }
}

const onWave = () => state.context.startsWith('wave:');

export function shuffled<T>(list: readonly T[]): T[] {
  const out = [...list];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

function setModes(patch: Partial<Pick<ListenState, 'shuffle' | 'repeat'>>) {
  set(patch);
  if (!state.shuffle) smartChosen = false;
  try {
    localStorage.setItem(MODES_KEY, JSON.stringify({ shuffle: state.shuffle, smart: smartChosen, repeat: state.repeat }));
  } catch {
    /* the modes are just not remembered */
  }
}

/** On: the tracks after the current one go in random order; off: back to the list's own order (a smart shuffle ends:
 * its recommendations leave the queue). */
export function setShuffle(on: boolean) {
  const wasSmart = state.smart;
  if (!on) smartChosen = false;
  setModes({ shuffle: on });
  if (wasSmart && !on) {
    smartHooks?.end();
    set({ smart: false });
  }
  if (!state.track || onWave()) return;
  const current = state.queue[state.index];
  if (on) {
    if (wasSmart) return;
    unshuffled = state.queue.filter((t) => !t.queued);
    set({ queue: [current, ...shuffled(state.queue.filter((_, i) => i !== state.index))], index: 0 });
  } else {
    const base = unshuffled ?? state.queue;
    const own = state.queue.filter((t) => !t.rec);
    let queue: readonly ListenTrack[];
    if (current.rec || current.queued) {
      // The current track is outside the list. Put the remaining list tracks after it in their original order.
      const left = new Set(state.queue.slice(state.index + 1).filter((t) => !t.rec));
      queue = [current, ...base.filter((t) => left.has(t)), ...[...left].filter((t) => !base.includes(t))];
    } else {
      queue = [...base.filter((t) => own.includes(t)), ...own.filter((t) => !base.includes(t))];
    }
    unshuffled = null;
    set({ queue, index: Math.max(0, queue.indexOf(current)) });
  }
  prefetchNext();
}

/** The shuffle button: off → shuffle → smart shuffle (only where the playing list can have one: a playlist or
 * «Любимое», and the setting on) → off. */
export function cycleShuffle() {
  if (onWave()) return;
  if (!state.shuffle) return setShuffle(true);
  if (state.smart) return setShuffle(false);
  if (state.track && smartHooks?.canStart()) {
    smartChosen = true;
    setModes({});
    set({ smart: true });
    smartHooks.begin((unshuffled ?? state.queue).filter((t) => !t.rec && !t.queued), state.queue[state.index]);
    return;
  }
  setShuffle(false);
}

/** The shuffle button of a list's page (beside its ▶): the same round — off → shuffle → smart shuffle (`canSmart`:
 * the page is a playlist's or «Любимое») → off. It is the choice for what ▶ starts; a list of this page that plays
 * already takes it at once. */
export function cyclePageShuffle(canSmart: boolean) {
  if (!state.shuffle) return setShuffle(true);
  if (smartChosen || !canSmart) return setShuffle(false);
  if (state.track && !onWave() && smartHooks?.canStart()) return cycleShuffle();
  smartChosen = true;
  setModes({});
}

/** The smart shuffle's own calls (`lib/smartShuffle.ts` registers them). */
export interface SmartHooks {
  /** a new list was started: its screen's smart context (a playlist, «Любимое») is noted, an old session ends */
  listStarted(context: string): void;
  /** the playing list can have a smart shuffle (and the setting is on) */
  canStart(): boolean;
  /** asks the Hub for the order and the recommendations of `list` (its own order) with `current` playing */
  begin(list: readonly ListenTrack[], current: ListenTrack): void;
  /** the smart shuffle is over: the session is dropped (the caller deals with the queue) */
  end(): void;
  /** the page restored its queue: a session of its recommendations goes on */
  restored(): void;
}
let smartHooks: SmartHooks | null = null;
export function registerSmart(hooks: SmartHooks) {
  smartHooks = hooks;
}

/** The smart shuffle chosen (the last choice of the button); `smart` of the state is whether the playing list has it. */
export function useSmartChosen(): boolean {
  return useSyncExternalStore(subscribeListening, () => smartChosen);
}

/** The Hub did not answer or the session is over: the plain shuffle goes on. */
export function endSmart() {
  if (state.smart) set({ smart: false });
}

/** Recommendations into the queue, each right after the own song it names (not behind the player). */
export function insertAfterOwn(items: ReadonlyArray<{ after: string; track: ListenTrack }>) {
  if (!state.track || !items.length) return;
  const queue = [...state.queue];
  let added = 0;
  for (const { after, track } of items) {
    if (queue.some((t) => t.ref === track.ref)) continue;
    const i = queue.findIndex((t) => !t.rec && idOf(t) === after);
    if (i < 0 || i < state.index) continue;
    queue.splice(i + 1, 0, track);
    added++;
  }
  if (!added) return;
  set({ queue });
  if (state.phase === 'ended' && hasNext(state)) advance(true);
  else if (state.phase !== 'error') prefetchNext();
}

/** «+»: the recommendation became the list's own song (no ✨ any more). */
export function markOwn(ref: string) {
  if (!state.queue.some((t) => t.ref === ref && t.rec)) return;
  const queue = state.queue.map((t) => (t.ref === ref && t.rec ? { ...t, rec: null } : t));
  set({ queue, track: queue[state.index] ?? state.track });
}

/** off → all → one → off. */
export function cycleRepeat() {
  setModes({ repeat: state.repeat === 'off' ? 'all' : state.repeat === 'all' ? 'one' : 'off' });
  prefetchNext();
}

/** Adds to the end of the account's manual queue. */
export function addToQueue(tracks: readonly ListenTrack[]): Promise<number> {
  const account = sessionAccount;
  if (!tracks.length || account === null) return Promise.resolve(0);
  const requestId = newEventId();
  const added = queueWrites.then(async () => {
    try {
      return await appendManualTracks(tracks, account, requestId);
    } catch (e) {
      toast(errorMessage(e), 'error');
      return 0;
    } finally {
      if (sessionAccount === account) await refreshManualQueue();
    }
  });
  queueWrites = added;
  return added;
}

/** Takes a track that is not the current one out of the queue. */
export function removeFromQueue(index: number) {
  if (index === state.index || index < 0 || index >= state.queue.length) return;
  set({ queue: state.queue.filter((_, i) => i !== index), index: index < state.index ? state.index - 1 : state.index });
  prefetchNext();
}

export function manualQueueKey(track: ListenTrack): string {
  return track.library_track_id && !track.ref.startsWith('file:') ? `lib:${track.library_track_id}` : track.ref;
}

export function queuedRefs(): Set<string> {
  return new Set(state.manualQueue.map((entry) => manualQueueKey(entry.track)));
}

/** Takes these tracks out of what the user queued by hand (the list that plays keeps its own tracks). */
export function unqueue(tracks: readonly ListenTrack[]): Promise<number> {
  const drop = new Set(tracks.map(manualQueueKey));
  return removeManualQueue(state.manualQueue.filter((entry) => drop.has(manualQueueKey(entry.track))).map((entry) => entry.id));
}

export async function removeManualQueue(ids: number[]) {
  let count = 0;
  try {
    for (let i = 0; i < ids.length; i += 400) {
      count += (await api.removeManualQueue(ids.slice(i, i + 400))).count;
    }
  } catch (e) {
    toast(errorMessage(e), 'error');
  } finally {
    if (ids.length) await refreshManualQueue();
  }
  return count;
}

export async function moveManualQueue(id: number, overId: number) {
  const rows = state.manualQueue;
  const from = rows.findIndex((entry) => entry.id === id);
  const to = rows.findIndex((entry) => entry.id === overId);
  if (from < 0 || to < 0 || from === to) return;
  const before = rows[to + (from < to ? 1 : 0)]?.id ?? null;
  try {
    await api.moveManualQueue(id, before);
  } catch (e) {
    toast(errorMessage(e), 'error');
  } finally {
    await refreshManualQueue();
  }
}

async function openManualQueue(entries: readonly ManualQueueEntry[], auto: boolean, fallback?: () => void) {
  if (manualOpening || sessionAccount === null) return;
  const epoch = ++manualTransition;
  manualOpening = true;
  try {
    for (const entry of entries) {
      const token = newEventId();
      const { ok } = await api.claimManual(entry.id, token);
      if (epoch !== manualTransition) {
        if (ok) void api.releaseManual(entry.id, token).catch(() => undefined);
        return;
      }
      if (!ok) continue;
      const track = { ...entry.track, queued: true };
      const at = state.track ? state.index + 1 : 0;
      const queue = [...state.queue.slice(0, at), track, ...state.queue.slice(at)];
      if (!state.track) {
        listContext = 'queue';
        listPage = '/player';
        listLaunch = newLaunch('queue');
      }
      start(queue, at, auto, 0, true, { id: entry.id, token, started: false });
      return;
    }
    if (!fallback) toast('Очередь обновилась', 'info');
  } catch (e) {
    if (epoch === manualTransition && !fallback) toast(errorMessage(e), 'error');
  } finally {
    if (epoch === manualTransition) {
      manualOpening = false;
      void refreshManualQueue();
      fallback?.();
    }
  }
}

export function playManualQueueAt(id: number) {
  const entry = state.manualQueue.find((row) => row.id === id);
  if (entry) {
    if (!hubReachable()) return notOnDevice(); // the account's queue is opened through the Hub
    failedManualEntries.delete(id);
    void openManualQueue([entry], false);
  }
}

/** Moves a track of the queue to another place (the current one too). */
export function moveInQueue(from: number, to: number) {
  const q = [...state.queue];
  if (from === to || from < 0 || to < 0 || from >= q.length || to >= q.length) return;
  const [t] = q.splice(from, 1);
  q.splice(to, 0, t);
  // where the playing track is now — counted, not looked up: the same track may stand in the queue twice
  const i = state.index;
  const index = from === i ? to : from < i && to >= i ? i - 1 : from > i && to <= i ? i + 1 : i;
  set({ queue: q, index });
  prefetchNext();
}

/** A tap on a track of the queue: it plays now, the queue stays as it is. */
export function playQueueAt(index: number) {
  if (index === state.index) return toggle();
  if (index < 0 || index >= state.queue.length) return;
  if (!playableNow(state.queue[index], state.context)) return notOnDevice();
  start(state.queue, index);
}

/** More tracks of a list were loaded (search results): if it is the list playing, it plays on through them too. */
export function extendQueue(list: readonly ListenTrack[]) {
  const q = state.queue.filter((track) => !track.queued);
  // the playing queue is the list as it was: the same refs in the same order
  if (!state.track || list.length <= q.length || q.some((t, i) => t.ref !== list[i].ref)) return;
  set({ queue: [...state.queue, ...list.slice(q.length)] });
  if (state.phase !== 'error' && state.phase !== 'ended') prefetchNext();
}

/** The tracks after the current one are replaced (the Hub picked «Моя волна» anew, `lib/wave.ts`). A queue that ran
 * out meanwhile plays on. */
export function replaceUpcoming(list: readonly ListenTrack[]) {
  if (!state.track) return;
  const ended = state.phase === 'ended';
  set({ queue: [...state.queue.slice(0, state.index + 1), ...list] });
  if (ended && hasNext(state)) advance(true);
  else if (state.phase !== 'error') prefetchNext();
}

export const hasNext = (s: ListenState) => s.index + 1 < s.queue.length ||
  (hubReachable() && s.context !== OFFLINE_WAVE && s.manualQueue.some((entry) => !failedManualEntries.has(entry.id)));
export const hasPrev = (s: ListenState) => s.index > 0;

/** Plays the next track of the queue; false at its end. */
export function next(): boolean {
  return advance(false);
}

function advance(auto: boolean, wrap = true): boolean {
  if (manualOpening) return true;
  const entries = state.manualQueue.filter((entry) => !failedManualEntries.has(entry.id));
  if (entries.length && sessionAccount !== null && hubReachable() && state.context !== OFFLINE_WAVE) {
    void openManualQueue(entries, auto, () => {
      if (!nextAutomatic(auto, wrap) && auto && state.phase !== 'error') set({ phase: 'ended' });
    });
    return true;
  }
  return nextAutomatic(auto, wrap);
}

function nextAutomatic(auto: boolean, wrap: boolean): boolean {
  const after = ahead(state.index);
  if (after < state.queue.length) start(state.queue, after, auto);
  else if (wrap && state.repeat === 'all' && state.track && !onWave()) {
    const first = state.queue.findIndex((track) => !track.queued && !passed(track));
    if (first < 0) return false;
    start(state.queue, first, auto);
  }
  else return false;
  return true;
}

/** A track the player goes past by itself: of an artist the user does not listen to, or — without the network — one
 * that is not on the device. */
const passed = (track: ListenTrack) => ofHiddenArtist(track) || !playableNow(track, state.context);

/** The place of the queue the player goes to from `from` by itself (`step` 1: on, −1: back): the tracks of an artist
 * the user does not listen to (`lib/dislikes.ts`) and, offline, those not on the device are passed. Such a track plays only when it is tapped. Past the
 * end of the queue (or before its start) when there is none. */
function ahead(from: number, step: 1 | -1 = 1): number {
  let i = from + step;
  while (i >= 0 && i < state.queue.length && passed(state.queue[i])) i += step;
  return i;
}

/** ⏮: back to the start of the track, or (near its start) the previous track. */
export function prev() {
  if (hasPrev(state) && ahead(state.index, -1) >= 0 && (state.position < RESTART_AFTER_S || state.phase === 'error')) start(state.queue, ahead(state.index, -1));
  else if (state.track) {
    seek(0);
    if (state.phase === 'ended' || state.phase === 'paused') resume();
  }
}

export function toggle() {
  if (state.phase === 'playing' || state.phase === 'loading' || state.phase === 'resolving') pause();
  else resume();
}

function resume() {
  const a = audio;
  const track = state.track;
  if (!a || !track) return;
  if (state.phase === 'error') return start(state.queue, state.index);
  if (state.phase === 'ended') return start(state.queue, state.index);
  set({ phase: state.info || state.position ? 'loading' : 'resolving' });
  play(a, true);
}

/** The sound fades out, then the element pauses; the player shows the pause at once. */
export function pause() {
  const a = audio;
  if (!a || !state.track || state.phase === 'error' || state.phase === 'ended') return;
  set({ phase: 'paused' });
  if (a.paused) return hardPause(a);
  fadeInOnPlaying = false;
  fade(0, FADE_OUT_MS, () => {
    a.pause();
    fadeGain = 1;
    applyVolume();
  });
}

export function setVolume(volume: number) {
  if (!Number.isFinite(volume)) return;
  const value = Math.max(0, Math.min(1, volume));
  set({ volume: value });
  applyVolume();
  try { localStorage.setItem(VOLUME_KEY, String(value)); } catch { /* storage is optional */ }
}

export function seek(seconds: number) {
  if (!audio || !Number.isFinite(seconds)) return;
  seekRevision++;
  pendingPosition = null;
  if (audio.readyState === HTMLMediaElement.HAVE_NOTHING) pendingSeek = Math.max(0, seconds);
  else { pendingSeek = 0; audio.currentTime = seconds; }
  set({ position: seconds });
  persistListening(true);
}

/** Stops and hides the player. */
export function stopListening() {
  cancelManualOpening();
  releaseManualPlayback();
  reportEnd();
  resolving?.controller.abort();
  resolving = null;
  for (const p of prefetching.values()) p.ctl.abort();
  prefetching.clear();
  pendingSeek = 0;
  pendingPosition = null;
  if (audio) {
    hardPause(audio);
    audio.removeAttribute('src');
    audio.load();
  }
  unshuffled = null;
  smartHooks?.end();
  set({ track: null, queue: [], index: 0, info: null, error: null, phase: 'paused', position: 0, duration: 0, smart: false });
  if ('mediaSession' in navigator) navigator.mediaSession.metadata = null;
}

/** Queues one catalog track (the Hub downloads the file that was listened to, if any). */
export async function downloadRef(ref: string): Promise<boolean> {
  const r = await startDownload({ track_refs: [ref] });
  if (r) markQueued(ref);
  return !!r;
}

/** A download of this catalog track was queued elsewhere: rows show «качается». */
export function markQueued(ref: string) {
  set({ downloads: new Map(state.downloads).set(ref, 'queued') });
}

/** The app's player queued a download of a catalog track: «Скачать в библиотеку» — rows show «качается»; its ♥
 * (`star`) — only the ♥ shows it (`lib/stars.ts`). */
export const HUB_DOWNLOAD_EVENT = 'hm:hub-download';

window.addEventListener(HUB_DOWNLOAD_EVENT, (e) => {
  const d = (e as CustomEvent<{ ref?: string; star?: boolean }>).detail;
  if (d?.ref && !d.star) markQueued(d.ref);
});

/** The playing track has just been downloaded to the server (a ♥, a transfer, «Скачать»): what the player says of
 * its file is asked anew — «играет из источника» is not true of it any more. The sound goes on as it is. */
function reloadInfoOnceDownloaded(ref: string) {
  const track = state.track;
  if (!track || track.ref !== ref || state.info?.kind !== 'full') return;
  api.listen(track.ref).then(
    (info) => {
      if (state.track !== track || info.kind !== 'library') return;
      set({ info });
      updateMediaTitle();
    },
    () => {},
  );
}

hubEvents.on('download', (d) => {
  const ref = d.track_ref;
  if (ref && d.status === 'done') reloadInfoOnceDownloaded(ref);
  // a quiet download is never «качается», but once imported the track is in the library
  if (ref && d.quiet && d.status === 'done' && state.downloads.get(ref) !== 'done') {
    set({ downloads: new Map(state.downloads).set(ref, 'done') });
    return;
  }
  if (!ref || state.downloads.get(ref) !== 'queued') return;
  const next = new Map(state.downloads);
  if (d.status === 'done') next.set(ref, 'done');
  else if (d.status === 'failed' || d.status === 'cancelled' || d.status === 'needs_review') next.delete(ref);
  else return;
  set({ downloads: next });
});

/** Lock screen / headset controls in a phone browser (the app's WebView ignores them). */
function mediaTitle(track: ListenTrack) {
  return titleWithVersion(track.title, {
    ...track,
    library_track_id: state.info?.track_id ?? track.library_track_id,
    nd_song_id: state.info?.nd_song_id ?? track.nd_song_id,
    page_url: state.info?.page_url ?? track.page_url,
  });
}

function updateMediaTitle() {
  if (state.track && navigator.mediaSession?.metadata) navigator.mediaSession.metadata.title = mediaTitle(state.track);
}

subscribeVersionLabels(updateMediaTitle);

function mediaSession(track: ListenTrack) {
  if (!('mediaSession' in navigator) || typeof MediaMetadata === 'undefined') return;
  const ms = navigator.mediaSession;
  ms.metadata = new MediaMetadata({
    title: mediaTitle(track),
    artist: track.artist,
    artwork: track.cover_url ? [{ src: imageUrl(track.cover_url), sizes: '500x500' }] : [],
  });
  try {
    ms.setActionHandler('play', resume);
    ms.setActionHandler('pause', pause);
    ms.setActionHandler('seekto', (d) => d.seekTime !== undefined && seek(d.seekTime));
    ms.setActionHandler('nexttrack', hasNext(state) || state.repeat === 'all' ? () => void next() : null);
    ms.setActionHandler('previoustrack', prev);
  } catch {
    /* an action the browser doesn't know */
  }
}
