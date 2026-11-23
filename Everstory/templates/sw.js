/*
 * Everstory service worker.
 *
 *  - keeps the app's styles, scripts and icons so pages load fast
 *  - when a page cannot be reached (no internet), shows the offline page
 *
 * Private pages and API responses are NEVER stored here: only public
 * static files and the empty offline page.
 */
"use strict";

const CACHE_NAME = "everstory-{{ cache_version }}";
const OFFLINE_URL = {{ offline_url|tojson }};
const ASSETS = {{ assets|tojson }};

self.addEventListener("install", function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            return cache.addAll(
                [OFFLINE_URL].concat(ASSETS).map(function (url) {
                    return new Request(url, { cache: "reload" });
                })
            );
        }).then(function () {
            return self.skipWaiting();
        })
    );
});

self.addEventListener("activate", function (event) {
    event.waitUntil(
        caches.keys().then(function (names) {
            return Promise.all(
                names
                    .filter(function (name) {
                        return name.indexOf("everstory-") === 0 &&
                            name !== CACHE_NAME;
                    })
                    .map(function (name) {
                        return caches.delete(name);
                    })
            );
        }).then(function () {
            return self.clients.claim();
        })
    );
});

self.addEventListener("fetch", function (event) {
    const request = event.request;

    if (request.method !== "GET") {
        return;
    }

    const url = new URL(request.url);

    if (url.origin !== self.location.origin) {
        return;
    }

    // Public static files: answer from the cache, fill it on first use.
    if (url.pathname.indexOf("/static/") === 0) {
        event.respondWith(
            caches.match(request).then(function (hit) {
                if (hit) {
                    return hit;
                }

                return fetch(request).then(function (response) {
                    if (response && response.ok) {
                        const copy = response.clone();

                        caches.open(CACHE_NAME).then(function (cache) {
                            cache.put(request, copy);
                        });
                    }

                    return response;
                });
            })
        );

        return;
    }

    // Pages: always try the network first (they hold private data and
    // must be fresh). Only if the network fails, show the offline page.
    if (request.mode === "navigate") {
        event.respondWith(
            fetch(request).catch(function () {
                return caches.match(OFFLINE_URL);
            })
        );
    }
});
