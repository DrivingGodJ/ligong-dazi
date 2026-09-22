const CACHE = "dazi-shell-v17";
const SHELL = ["/", "/static/styles.css?v=17", "/static/app.js?v=17", "/static/icon-192.png"];

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
