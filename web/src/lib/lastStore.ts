/** What pages showed the last time (`useAsync`'s `keep`), kept on the device: the next start of the app shows a page
 * at once from it while the fresh answer is on its way. In IndexedDB: an answer is written as it is (no JSON on the
 * main thread) and «Любимое» of thousands of tracks is megabytes — past what localStorage holds. The answers read
 * longest ago leave first once there are more than `KEEP`. */

const DB = 'homemusic-last';
const STORE = 'answers';
const KEEP = 80;
let opened: Promise<IDBDatabase> | null = null;

interface Kept { value: unknown; at: number }

function db(): Promise<IDBDatabase> {
  opened ??= new Promise((resolve, reject) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE).createIndex('at', 'at');
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
  opened.catch(() => { opened = null; });
  return opened;
}

function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

export async function readLast(key: string): Promise<unknown> {
  try {
    const tx = (await db()).transaction(STORE, 'readonly');
    const req = tx.objectStore(STORE).get(key);
    await done(tx);
    return (req.result as Kept | undefined)?.value;
  } catch {
    return undefined;
  }
}

let writes = 0;

export async function writeLast(key: string, value: unknown): Promise<void> {
  try {
    const tx = (await db()).transaction(STORE, 'readwrite');
    tx.objectStore(STORE).put({ value, at: Date.now() } satisfies Kept, key);
    await done(tx);
    if (++writes % 20 === 1) await prune();
  } catch {
    /* not kept: the page waits for the Hub next time */
  }
}

/** Past `KEEP` answers, the ones written longest ago go (walked by the time alone: the answers are not read). */
async function prune() {
  const tx = (await db()).transaction(STORE, 'readwrite');
  const store = tx.objectStore(STORE);
  const count = store.count();
  count.onsuccess = () => {
    let extra = count.result - KEEP;
    if (extra <= 0) return;
    const walk = store.index('at').openKeyCursor();
    walk.onsuccess = () => {
      const cursor = walk.result;
      if (!cursor || extra-- <= 0) return;
      store.delete(cursor.primaryKey);
      cursor.continue();
    };
  };
  await done(tx);
}

export async function dropLast(key: string): Promise<void> {
  try {
    const tx = (await db()).transaction(STORE, 'readwrite');
    tx.objectStore(STORE).delete(key);
    await done(tx);
  } catch {
    /* nothing to drop */
  }
}

export async function clearLast(): Promise<void> {
  try {
    const tx = (await db()).transaction(STORE, 'readwrite');
    tx.objectStore(STORE).clear();
    await done(tx);
  } catch {
    /* nothing to clear */
  }
}
