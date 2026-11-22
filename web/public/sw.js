/* HomeMusic service worker: app shell cache, Web Share Target, notification clicks — and what plays and shows without
   the network (`src/lib/offline.ts`): the audio kept for offline, the lists and covers read lately.
   BUILD_ID is stamped by vite.config.ts at build time. */
const BUILD_ID = '__BUILD_ID__';
const SHELL = `hm-shell-${BUILD_ID}`;
const ASSETS = 'hm-assets-v1'; // hashed file names: safe to keep across builds
// Kept across builds, and filled by the page (`src/lib/offline.ts`) as well as here:
const AUDIO = 'hm-audio-v1'; // tracks kept for offline, by `/api/listen/stream?ref=<ref>`
const WAVE_AUDIO = 'hm-wave-audio-v1'; // separate from manually saved lists
const API = 'hm-api-v1'; // the last answer of the Hub to what a page reads: shown when the Hub cannot be reached
const IMG = 'hm-img-v1'; // covers
/* What a page reads to draw the library: kept as last answered, for when there is no network. Not what must be live
   (downloads, the search of the catalog, events) nor what is asked once (the assistant, the admin's pages). */
const KEPT = ['/api/auth/me', '/api/playlists', '/api/nd/', '/api/for-you', '/api/tags', '/api/starred', '/api/stats/counts',
  '/api/lyrics', '/api/mixes/today', '/api/plays/history', '/api/library/version-labels'];
const NOT_KEPT = ['/api/lyrics/candidates', '/api/lyrics/search', '/api/lyrics/probe', '/api/lyrics/sources', '/api/lyrics/settings', '/api/lyrics/translators'];
const audioKey = (ref) => `${self.location.origin}/api/listen/stream?ref=${encodeURIComponent(ref)}`;
/* How long a page waits for the network before it is given what is kept. Without the network a request on a phone
   often does not fail but hangs (Wi-Fi without the internet, a weak signal, a VPN): the app stayed a black screen. */
const SHELL_WAIT_MS = 3000;
const API_WAIT_MS = 4000;
/* Caddy on the VPS answers for a Hub it cannot reach (the computer or the tunnel is down): that is no answer. */
const UNREACHABLE = new Set([502, 503, 504]);
const SHELL_URLS = ['/', '/manifest.webmanifest', '/icons/icon.svg', '/icons/icon-192.png', '/icons/badge-96.png'];

// Same layout as src/lib/shareStore.ts
const SHARE_DB = 'homemusic-share';
const SHARE_STORE = 'shares';
const SHARE_KEY = 'pending';

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const shell = await caches.open(SHELL);
      await shell.addAll(SHELL_URLS);
      // Precache the hashed bundles referenced by index.html so the shell works offline right away.
      try {
        const html = await (await shell.match('/')).text();
        const urls = [...new Set([...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]))];
        const assets = await caches.open(ASSETS);
        await assets.addAll(urls);
      } catch (e) {
        /* best effort */
      }
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keep = new Set([SHELL, ASSETS, AUDIO, WAVE_AUDIO, API, IMG]);
      for (const key of await caches.keys()) if (!keep.has(key)) await caches.delete(key);
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // covers from Deezer etc.: browser cache

  if (req.method === 'POST' && url.pathname === '/share-target') {
    event.respondWith(handleShare(req));
    return;
  }
  if (req.method !== 'GET') return;
  if (url.pathname === '/api/listen/stream') {
    event.respondWith(audio(req, url));
    return;
  }
  if (url.pathname.startsWith('/api/')) {
    if (/\/cover(\/|$)/.test(url.pathname)) event.respondWith(staleWhileRevalidate(req, IMG));
    else if (KEPT.some((x) => url.pathname.startsWith(x)) && !NOT_KEPT.some((x) => url.pathname.startsWith(x)))
      networkFirst(event, API, req.url, (res) => (res.headers.get('content-type') || '').includes('json'), API_WAIT_MS);
    return;
  }
  if (url.pathname === '/healthz' || url.pathname.startsWith('/app/')) return;

  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(cacheFirst(req, ASSETS));
    return;
  }
  if (req.mode === 'navigate' || url.pathname === '/' || url.pathname === '/index.html') {
    // every page of the app is the same index.html: kept as '/'
    networkFirst(event, SHELL, '/', (res) => (res.headers.get('content-type') || '').includes('text/html'), SHELL_WAIT_MS);
    return;
  }
  if (SHELL_URLS.includes(url.pathname) || url.pathname.startsWith('/icons/')) {
    event.respondWith(staleWhileRevalidate(req, SHELL));
  }
});

async function cacheFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) cache.put(req, res.clone());
  return res;
}

async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(req);
  const net = fetch(req)
    .then((res) => {
      if (res.ok) cache.put(req, res.clone());
      return res;
    })
    .catch(() => hit);
  return hit || net;
}

/* A track kept for offline plays from this device, with or without the network; any other goes to the Hub. A player
   asks for a part of the file (`Range`): it gets that part (206), cut from the copy kept whole. */
async function audio(req, url) {
  const ref = url.searchParams.get('ref');
  const wave = url.searchParams.get('wave');
  const reserved = wave ? await caches.match(url.href, { cacheName: WAVE_AUDIO }) : null;
  const hit = reserved || (ref ? await (await caches.open(AUDIO)).match(audioKey(ref)) : null);
  if (!hit) return fetch(req);
  const blob = await hit.blob();
  const size = blob.size;
  const type = hit.headers.get('content-type') || 'audio/mpeg';
  const base = { 'content-type': type, 'accept-ranges': 'bytes', 'cache-control': 'no-store', 'x-homemusic-offline': '1' };
  const m = /^bytes=(\d*)-(\d*)$/.exec(req.headers.get('range') || '');
  if (!m || (!m[1] && !m[2])) return new Response(blob, { status: 200, headers: { ...base, 'content-length': String(size) } });
  let start = m[1] ? Number(m[1]) : Math.max(0, size - Number(m[2]));
  let end = m[1] && m[2] ? Math.min(Number(m[2]), size - 1) : size - 1;
  if (start >= size || start > end) return new Response(null, { status: 416, headers: { ...base, 'content-range': `bytes */${size}` } });
  return new Response(blob.slice(start, end + 1, type), {
    status: 206,
    headers: { ...base, 'content-range': `bytes ${start}-${end}/${size}`, 'content-length': String(end - start + 1) },
  });
}

/* The network's answer, and a copy of it kept under `key`; the copy instead when the network fails, is late
   (`waitMs`: the answer still goes into the copy when it comes) or is a gateway that cannot reach the Hub. Nothing
   kept: whatever the network gives. Used for the app shell and for what a page reads of the library. */
function networkFirst(event, cacheName, key, keep, waitMs) {
  const cache = caches.open(cacheName);
  const kept = () => cache.then((c) => c.match(key));
  const net = fetch(event.request);
  // registered first: the copy is cloned before the page reads the body
  const stored = net.then(async (res) => {
    if (res.ok && keep(res)) await (await cache).put(key, res.clone());
  });
  event.waitUntil(stored.catch(() => {}));
  event.respondWith(
    (async () => {
      let timer;
      const late = new Promise((resolve) => (timer = setTimeout(resolve, waitMs, null)));
      let res;
      try {
        res = await Promise.race([net, late]);
      } catch (e) {
        const hit = await kept();
        if (hit) return hit;
        throw e;
      } finally {
        clearTimeout(timer);
      }
      if (res === null) return (await kept()) || net;
      if (UNREACHABLE.has(res.status)) return (await kept()) || res;
      return res;
    })(),
  );
}

// The page says when the account leaves: what was read for it is not shown to the next one.
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'hm-forget') event.waitUntil(Promise.all([caches.delete(API), caches.delete(AUDIO)]));
});

// --- Web Share Target ------------------------------------------------------------------------------

function openShareDb() {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(SHARE_DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(SHARE_STORE);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

async function handleShare(req) {
  try {
    const form = await req.formData();
    const files = form.getAll('files').filter((f) => f && typeof f === 'object' && 'size' in f && f.size > 0);
    const payload = {
      files,
      title: String(form.get('title') || ''),
      text: String(form.get('text') || ''),
      url: String(form.get('url') || ''),
      ts: Date.now(),
    };
    const db = await openShareDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(SHARE_STORE, 'readwrite');
      tx.objectStore(SHARE_STORE).put(payload, SHARE_KEY);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
    db.close();
    return Response.redirect('/#/share', 303);
  } catch (e) {
    // Could not store locally: let the server-side fallback handle it next time.
    return Response.redirect('/#/assistant', 303);
  }
}

// --- notifications -----------------------------------------------------------------------------------

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/#/downloads';
  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      for (const c of all) {
        if (new URL(c.url).origin === self.location.origin) {
          await c.focus();
          if ('navigate' in c) return c.navigate(target);
          return;
        }
      }
      return self.clients.openWindow(target);
    })(),
  );
});
