// Minimalny service worker – pozwala zainstalować stronę jako aplikację.
// Zawsze pobiera świeże dane z sieci (nic nie zapisuje w pamięci podręcznej),
// żeby stan powietrza nigdy nie był nieaktualny.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => e.respondWith(fetch(e.request)));
