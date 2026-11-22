import { hubReachable, onHubReach } from './reach';
import { dismissToast, toast } from './ui';

/** The connection to the Hub as a passing word, not a line that stays on the screen: a toast when it is lost (and at
 * start, if the app opens without it) and one when it comes back. «Online» is whether the Hub answers
 * (`lib/reach.ts`). The toasts live in a store of their own: said before the page has drawn, the first one is shown
 * as soon as it does. */

const OFFLINE = 'Нет связи с сервером';
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

onHubReach((reachable) => (reachable ? cameBack() : wentOffline()));
if (!hubReachable()) wentOffline();
