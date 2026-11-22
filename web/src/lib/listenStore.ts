import type { ListenTrack } from './listen';

/** The page player's queue kept on the device for the next start (`lib/listen.ts`, `persistListening`). In
 * IndexedDB, not localStorage: a queue of «Любимое» is thousands of tracks — megabytes of JSON written on the main
 * thread, and past localStorage's few megabytes it was not kept at all. Written when the queue changes, not with
 * the position (that is a small record of its own, which names the queue it goes with by `id`). */

export interface SavedQueue {
  /** the session record says which queue it was saved with: a queue whose writing did not finish is not used */
  id: string;
  queue: ListenTrack[];
  /** the order before shuffle, as places in `queue` */
  originalOrder: number[] | null;
}

const DB = 'homemusic-listen';
const STORE = 'queues';
let opened: Promise<IDBDatabase> | null = null;

function db(): Promise<IDBDatabase> {
  opened ??= new Promise((resolve, reject) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
  opened.catch(() => { opened = null; });
  return opened;
}

async function run<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const tx = (await db()).transaction(STORE, mode);
  const req = fn(tx.objectStore(STORE));
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve(req.result);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

export const saveQueue = (account: number, saved: SavedQueue): Promise<unknown> => run('readwrite', (s) => s.put(saved, account));
export const loadQueue = (account: number): Promise<SavedQueue | undefined> => run('readonly', (s) => s.get(account));
export const dropQueue = (account: number): Promise<unknown> => run('readwrite', (s) => s.delete(account));
