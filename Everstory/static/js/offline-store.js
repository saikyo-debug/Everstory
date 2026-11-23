/*
 * Everstory - offline storage and sync.
 *
 * Two things are kept on this device, in IndexedDB:
 *
 *   queue    memories written while offline, waiting to be sent
 *   entries  an optional read-only copy of the diary, so memories
 *            can be read without internet (can be switched off)
 *
 * Every queued memory has a random client_id. The server stores a
 * memory with a given client_id only once, so a sync that is cut off
 * and repeated never creates duplicates.
 *
 * Exposed as window.EverstoryOffline.
 */
(function (global) {
    "use strict";

    var DB_NAME = "everstory-offline";
    var DB_VERSION = 1;

    var CACHE_FLAG = "everstory.offlineCache";
    var DIRTY_FLAG = "everstory.cacheDirty";

    var REFRESH_INTERVAL_MS = 2 * 60 * 1000;

    var dbPromise = null;
    var syncing = false;


    /* ---------- IndexedDB helpers ---------- */

    function openDb() {
        if (dbPromise) {
            return dbPromise;
        }

        dbPromise = new Promise(function (resolve, reject) {
            if (!("indexedDB" in global)) {
                reject(new Error("IndexedDB is not available."));
                return;
            }

            var request = indexedDB.open(DB_NAME, DB_VERSION);

            request.onupgradeneeded = function () {
                var db = request.result;

                if (!db.objectStoreNames.contains("entries")) {
                    db.createObjectStore("entries", { keyPath: "id" });
                }

                if (!db.objectStoreNames.contains("queue")) {
                    db.createObjectStore("queue", { keyPath: "client_id" });
                }

                if (!db.objectStoreNames.contains("meta")) {
                    db.createObjectStore("meta", { keyPath: "key" });
                }
            };

            request.onsuccess = function () {
                resolve(request.result);
            };

            request.onerror = function () {
                dbPromise = null;
                reject(request.error);
            };
        });

        return dbPromise;
    }

    function run(storeNames, mode, work) {
        return openDb().then(function (db) {
            return new Promise(function (resolve, reject) {
                var transaction = db.transaction(storeNames, mode);
                var holder = { value: undefined };

                transaction.oncomplete = function () {
                    resolve(holder.value);
                };

                transaction.onerror = function () {
                    reject(transaction.error);
                };

                transaction.onabort = function () {
                    reject(transaction.error || new Error("Aborted."));
                };

                work(transaction, holder);
            });
        });
    }

    function getAll(storeName) {
        return run([storeName], "readonly", function (transaction, holder) {
            var request = transaction.objectStore(storeName).getAll();

            request.onsuccess = function () {
                holder.value = request.result;
            };
        });
    }


    /* ---------- Meta ---------- */

    function getMeta(key) {
        return run(["meta"], "readonly", function (transaction, holder) {
            var request = transaction.objectStore("meta").get(key);

            request.onsuccess = function () {
                holder.value = request.result ? request.result.value : null;
            };
        });
    }

    function setMeta(key, value) {
        return run(["meta"], "readwrite", function (transaction) {
            transaction.objectStore("meta").put({ key: key, value: value });
        });
    }


    /* ---------- Offline copy of the diary ---------- */

    function replaceEntries(userId, entries) {
        return run(["entries", "meta"], "readwrite", function (transaction) {
            var store = transaction.objectStore("entries");
            var meta = transaction.objectStore("meta");

            store.clear();

            entries.forEach(function (entry) {
                store.put({
                    id: entry.id,
                    user_id: userId,
                    title: entry.title,
                    content: entry.content,
                    created_at: entry.created_at,
                    updated_at: entry.updated_at,
                    is_favorite: Boolean(entry.is_favorite)
                });
            });

            meta.put({ key: "cache_user", value: userId });
            meta.put({ key: "last_sync", value: Date.now() });
        });
    }

    function getCachedEntries(userId) {
        return getAll("entries").then(function (rows) {
            return rows
                .filter(function (row) {
                    return userId == null || row.user_id === userId;
                })
                .sort(function (a, b) {
                    return String(b.created_at).localeCompare(
                        String(a.created_at)
                    ) || b.id - a.id;
                });
        });
    }

    function clearCache() {
        return run(["entries", "meta"], "readwrite", function (transaction) {
            transaction.objectStore("entries").clear();
            transaction.objectStore("meta").delete("cache_user");
            transaction.objectStore("meta").delete("last_sync");
        });
    }


    /* ---------- Queue of memories written offline ---------- */

    function queueAdd(item) {
        return run(["queue"], "readwrite", function (transaction) {
            transaction.objectStore("queue").put(item);
        });
    }

    function queueAll() {
        return getAll("queue");
    }

    function queueRemove(clientId) {
        return run(["queue"], "readwrite", function (transaction) {
            transaction.objectStore("queue").delete(clientId);
        });
    }

    function pendingFor(userId) {
        return queueAll().then(function (items) {
            return items.filter(function (item) {
                return item.user_id === userId && item.status !== "error";
            });
        });
    }


    /* ---------- Settings kept in localStorage ---------- */

    function isCacheEnabled() {
        try {
            return localStorage.getItem(CACHE_FLAG) !== "off";
        } catch (error) {
            return true;
        }
    }

    function setCacheEnabled(enabled) {
        try {
            localStorage.setItem(CACHE_FLAG, enabled ? "on" : "off");
        } catch (error) { /* ignore */ }
    }

    function markDirty() {
        try {
            localStorage.setItem(DIRTY_FLAG, "1");
        } catch (error) { /* ignore */ }
    }

    function isDirty() {
        try {
            return localStorage.getItem(DIRTY_FLAG) === "1";
        } catch (error) {
            return false;
        }
    }

    function clearDirty() {
        try {
            localStorage.removeItem(DIRTY_FLAG);
        } catch (error) { /* ignore */ }
    }


    /* ---------- Ids ---------- */

    function newClientId() {
        if (global.crypto && typeof global.crypto.randomUUID === "function") {
            return global.crypto.randomUUID();
        }

        var bytes = new Uint8Array(16);

        if (global.crypto && global.crypto.getRandomValues) {
            global.crypto.getRandomValues(bytes);
        } else {
            for (var i = 0; i < bytes.length; i += 1) {
                bytes[i] = Math.floor(Math.random() * 256);
            }
        }

        return Array.prototype.map.call(bytes, function (byte) {
            return ("0" + byte.toString(16)).slice(-2);
        }).join("");
    }


    /* ---------- Sync ---------- */

    function fetchWithTimeout(url, options, timeoutMs) {
        var controller = typeof AbortController === "function"
            ? new AbortController()
            : null;

        var timer = null;

        if (controller) {
            options.signal = controller.signal;
            timer = setTimeout(function () {
                controller.abort();
            }, timeoutMs);
        }

        return fetch(url, options).then(
            function (response) {
                clearTimeout(timer);
                return response;
            },
            function (error) {
                clearTimeout(timer);
                throw error;
            }
        );
    }

    /*
     * Send every waiting memory for this user to the server.
     * Returns { synced, failed, loginRequired, pending }.
     */
    function sync(options) {
        var result = {
            synced: 0,
            failed: 0,
            loginRequired: false,
            pending: 0,
            skipped: false
        };

        if (syncing || !navigator.onLine) {
            result.skipped = true;
            return Promise.resolve(result);
        }

        syncing = true;

        function next(items, index) {
            if (index >= items.length) {
                return Promise.resolve();
            }

            var item = items[index];

            return fetchWithTimeout("/api/entries", {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-CSRFToken": options.csrf
                },
                body: JSON.stringify({
                    client_id: item.client_id,
                    title: item.title,
                    content: item.content,
                    created_at_utc: item.created_at_utc
                })
            }, 15000).then(function (response) {

                if (response.status === 200 || response.status === 201) {
                    result.synced += 1;
                    return queueRemove(item.client_id).then(function () {
                        return next(items, index + 1);
                    });
                }

                if (response.status === 401) {
                    result.loginRequired = true;
                    return null;
                }

                return response.json().catch(function () {
                    return null;
                }).then(function (data) {
                    var detail = data
                        ? String(data.detail || "") + String(data.error || "")
                        : "";

                    // A stale page token is temporary: keep the memory
                    // and try again after the next page load.
                    if (
                        response.status === 400 &&
                        /security token/i.test(detail)
                    ) {
                        return null;
                    }

                    // The server refused this memory for good (too long,
                    // empty ...). Keep it visible so nothing is lost.
                    if (response.status >= 400 && response.status < 500) {
                        item.status = "error";
                        item.error = (data && data.error) ||
                            "The server could not accept this memory.";
                        result.failed += 1;

                        return queueAdd(item).then(function () {
                            return next(items, index + 1);
                        });
                    }

                    // Server trouble: stop and try again later.
                    return null;
                });
            }, function () {
                // Network trouble: stop and try again later.
                return null;
            });
        }

        return pendingFor(options.userId).then(function (items) {
            items.sort(function (a, b) {
                return String(a.created_at_utc).localeCompare(
                    String(b.created_at_utc)
                );
            });

            return next(items, 0);
        }).then(function () {
            return pendingFor(options.userId);
        }).then(function (left) {
            result.pending = left.length;
            syncing = false;
            return result;
        }, function () {
            syncing = false;
            return result;
        });
    }

    /*
     * Refresh the offline copy of the diary (when it is switched on).
     * Throttled so ordinary page views do not download it every time.
     */
    function refreshCache(options) {
        if (!isCacheEnabled() || !navigator.onLine) {
            return Promise.resolve(false);
        }

        return getMeta("last_sync").then(function (last) {
            var fresh = last && (Date.now() - last) < REFRESH_INTERVAL_MS;

            if (!options.force && fresh && !isDirty()) {
                return false;
            }

            return fetchWithTimeout("/api/entries", {
                credentials: "same-origin",
                headers: { "Accept": "application/json" }
            }, 20000).then(function (response) {
                if (!response.ok) {
                    return false;
                }

                return response.json().then(function (data) {
                    return replaceEntries(
                        data.user_id || options.userId,
                        data.entries || []
                    ).then(function () {
                        clearDirty();
                        return true;
                    });
                });
            });
        }).catch(function () {
            return false;
        });
    }


    global.EverstoryOffline = {
        getAll: getAll,
        getMeta: getMeta,
        setMeta: setMeta,
        getCachedEntries: getCachedEntries,
        replaceEntries: replaceEntries,
        clearCache: clearCache,
        queueAdd: queueAdd,
        queueAll: queueAll,
        queueRemove: queueRemove,
        pendingFor: pendingFor,
        isCacheEnabled: isCacheEnabled,
        setCacheEnabled: setCacheEnabled,
        markDirty: markDirty,
        newClientId: newClientId,
        fetchWithTimeout: fetchWithTimeout,
        sync: sync,
        refreshCache: refreshCache
    };

})(window);
