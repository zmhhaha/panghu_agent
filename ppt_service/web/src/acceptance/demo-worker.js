/* Only controls the opt-in /src/acceptance/ demo documents. Never included in site builds. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const match = /^\/api\/v1\/versions\/demo-v[12]\/slides\/([12])\/preview$/.exec(url.pathname);
  if (url.origin !== self.location.origin || !match || url.search) return;
  event.respondWith(fetch(new URL(`demo-slide-${match[1]}.png`, self.location.href), { cache: 'no-store' }).then(async response => new Response(await response.arrayBuffer(), { status: response.status, headers: { 'Content-Type': 'image/png', 'Cache-Control': 'no-store' } })));
});
