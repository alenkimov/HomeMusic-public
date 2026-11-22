// Global toasts and confirm dialogs (module-level stores, rendered by <Overlays/>).
import { useSyncExternalStore } from 'react';
import { errorMessage, isAbort } from '../api/client';

type Listener = () => void;

function createStore<T>(initial: T) {
  let state = initial;
  const listeners = new Set<Listener>();
  return {
    get: () => state,
    set(next: T) {
      state = next;
      listeners.forEach((l) => l());
    },
    subscribe(l: Listener) {
      listeners.add(l);
      return () => listeners.delete(l);
    },
  };
}

// --- toasts ---------------------------------------------------------------------------------

export interface Toast {
  id: number;
  text: string;
  kind: 'info' | 'success' | 'error';
  /** a button: goes to `to`, or runs `run` (an undo) */
  link?: { label: string; to?: string; run?: () => void };
}

const toastStore = createStore<Toast[]>([]);
let toastSeq = 0;

/** An error shown to a listener is told to the Hub too (its log): what was said and on which page — so that «у меня
 * не работает» can be looked up, not guessed at. Nothing waits for it, and nothing comes of its failing. */
function report(text: string) {
  try {
    void fetch('/api/system/client-error', {
      method: 'POST',
      credentials: 'include',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text.slice(0, 500), page: window.location.hash.slice(0, 200) }),
    }).catch(() => undefined);
  } catch {
    /* no network, no matter */
  }
}

const toastTimers = new Map<number, number>();

/** Said again while it is still shown (a button tapped twice), a toast is not shown twice: the one on the screen
 * stays where it is, for its whole time anew — with the new button, whose undo is of the last action. */
export function toast(text: string, kind: Toast['kind'] = 'info', link?: Toast['link']): number {
  if (kind === 'error') report(text);
  const shown = toastStore.get().find((x) => x.text === text && x.kind === kind);
  const t: Toast = { id: shown?.id ?? ++toastSeq, text, kind, link };
  toastStore.set(shown ? toastStore.get().map((x) => (x === shown ? t : x)) : [...toastStore.get().slice(-2), t]);
  window.clearTimeout(toastTimers.get(t.id));
  toastTimers.set(t.id, window.setTimeout(() => dismissToast(t.id), kind === 'error' ? 6000 : 3500));
  return t.id;
}

export function dismissToast(id: number) {
  window.clearTimeout(toastTimers.get(id));
  toastTimers.delete(id);
  toastStore.set(toastStore.get().filter((t) => t.id !== id));
}

export const toastError = (e: unknown) => { if (!isAbort(e)) toast(errorMessage(e), 'error'); };

export function useToasts() {
  return useSyncExternalStore(toastStore.subscribe, toastStore.get, toastStore.get);
}

// --- confirm dialog ---------------------------------------------------------------------------

export interface DialogState {
  title: string;
  body?: string;
  okLabel: string;
  cancelLabel: string;
  danger: boolean | 'solid';
  resolve: (ok: boolean) => void;
}

const dialogStore = createStore<DialogState | null>(null);

export function clearAccountMessages() {
  toastTimers.forEach((timer) => window.clearTimeout(timer));
  toastTimers.clear();
  toastStore.set([]);
  dialogStore.get()?.resolve(false);
}

export function confirmDialog(opts: {
  title: string;
  body?: string;
  okLabel?: string;
  cancelLabel?: string;
  danger?: DialogState['danger'];
}): Promise<boolean> {
  return new Promise((resolve) => {
    dialogStore.get()?.resolve(false);
    dialogStore.set({
      title: opts.title,
      body: opts.body,
      okLabel: opts.okLabel ?? 'Да',
      cancelLabel: opts.cancelLabel ?? 'Отмена',
      danger: opts.danger ?? false,
      resolve: (ok) => {
        dialogStore.set(null);
        resolve(ok);
      },
    });
  });
}

export function useDialog() {
  return useSyncExternalStore(dialogStore.subscribe, dialogStore.get, dialogStore.get);
}

/** Run an action, toast its error, return undefined on failure. */
export async function attempt<R>(fn: () => Promise<R>, success?: string): Promise<R | undefined> {
  try {
    const r = await fn();
    if (success) toast(success, 'success');
    return r;
  } catch (e) {
    toastError(e);
    return undefined;
  }
}
