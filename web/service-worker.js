const CACHE = "dazi-shell-v31";
const SHELL = ["/", "/static/styles.css?v=23", "/static/visual-language.css?v=2", "/static/reference-ui.css?v=8", "/static/app.js?v=30", "/static/brand-flower.svg?v=2", "/static/mascot-bloom.svg", "/static/mascot-square.svg", "/static/mascot-activities.svg", "/static/mascot-invitations.svg", "/static/thinking-flower.svg", "/static/floral-canvas.svg", "/static/floral-canvas-dark.svg", "/static/icon-192.png", "/static/launch-art.html"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(Promise.all([
    self.clients.claim(),
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))),
  ]));
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET" || new URL(event.request.url).origin !== self.location.origin) return;
  if (new URL(event.request.url).pathname.startsWith("/api/")) return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});

self.addEventListener("push", (event) => {
  let message = {};
  try { message = event.data?.json() || {}; } catch { message = {}; }
  event.waitUntil(self.registration.showNotification(message.title || "理工搭子局", {
    body: message.body || "你有一条新消息",
    icon: "/static/icon-192.png",
    badge: "/static/icon-192.png",
    tag: message.tag,
    data: {url: message.url || "/?tab=invitations"},
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(async (clients) => {
    const open = clients.find((client) => client.url.startsWith(self.location.origin));
    if (open) { await open.focus(); await open.navigate(url); }
    else await self.clients.openWindow(url);
  }));
});
