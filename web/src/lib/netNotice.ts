import { dismissToast, toast } from './ui';

/** The network as a passing word, not a line that stays on the screen: a toast when the device loses it (and at
 * start, if it opens without one) — what is kept on it still plays — and one when it comes back. The toasts live in
 * a store of their own: said before the page has drawn, the first one is shown as soon as it does. */

const OFFLINE = 'Нет сети';
const ONLINE = 'Соединение восстановлено';

let offlineToast: number | null = null;
let onlineToast: number | null = null;

function wentOffline() {
  if (onlineToast != null) dismissToast(onlineToast);
  onlineToast = null;
  offlineToast = toast(OFFLINE);
}

function cameBack() {
  if (offlineToast != null) dismissToast(offlineToast);
  offlineToast = null;
  onlineToast = toast(ONLINE, 'success');
}

if (typeof window !== 'undefined') {
  window.addEventListener('offline', wentOffline);
  window.addEventListener('online', cameBack);
  if (!navigator.onLine) wentOffline();
}
