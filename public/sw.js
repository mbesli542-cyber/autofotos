/* AutoExperten Photo – service worker (plain JS, no build step).
 *
 * - Precaches the offline page, icons and camera overlays.
 * - Static Next.js assets: cache-first (file names are content-hashed).
 * - Page navigations: network-first, falling back to the last cached copy
 *   and finally to /offline.
 * - Never touches API routes, uploads or Supabase requests (other origin).
 *
 * Photos waiting for upload are kept in IndexedDB by the app itself.
 */
const VERSION = "ae-photo-v1";
const STATIC_CACHE = `${VERSION}-static`;
const PAGE_CACHE = `${VERSION}-pages`;

const PRECACHE_URLS = [
  "/offline",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/overlays/front-left-45.svg",
  "/overlays/front.svg",
  "/overlays/front-right-45.svg",
  "/overlays/side.svg",
  "/overlays/rear-left-45.svg",
  "/overlays/rear.svg",
  "/overlays/rear-right-45.svg",
  "/overlays/cockpit.svg",
  "/overlays/wheel.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => !key.startsWith(VERSION)).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  );
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(STATIC_CACHE);
    cache.put(request, response.clone());
  }
  return response;
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => cached);
  return cached || network;
}

async function networkFirstPage(request) {
  const cache = await caches.open(PAGE_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok && response.type === "basic") cache.put(request, response.clone());
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    const offline = await caches.match("/offline");
    return offline || new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain" } });
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith(cacheFirst(request));
    return;
  }
  if (/^\/(overlays|icons|brand|demo)\//.test(url.pathname)) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(networkFirstPage(request));
  }
});
