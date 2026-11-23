/*
 * Everstory - the offline page.
 *
 * Shows the memories kept on this device, lets the person write new
 * ones (saved on this device, sent later) and reviews what is waiting
 * to sync. Everything is built with textContent, never innerHTML, so
 * memory text can never run as code.
 */
(function () {
    "use strict";

    var Store = window.EverstoryOffline;
    var app = document.getElementById("offlineApp");

    if (!app || !Store) {
        return;
    }

    var userId = null;

    try {
        userId = parseInt(localStorage.getItem("everstory.userId"), 10) || null;
    } catch (error) { /* ignore */ }

    var hero = document.getElementById("offlineHero");
    var heroTitle = document.getElementById("offlineHeroTitle");
    var heroText = document.getElementById("offlineHeroText");
    var form = document.getElementById("offlineForm");
    var titleInput = document.getElementById("offTitle");
    var contentInput = document.getElementById("offContent");
    var pendingSection = document.getElementById("pendingSection");
    var pendingList = document.getElementById("pendingList");
    var pendingCount = document.getElementById("pendingCount");
    var syncLink = document.getElementById("syncLink");
    var cacheNote = document.getElementById("cacheNote");
    var cacheList = document.getElementById("cacheList");
    var retry = document.getElementById("offlineRetry");
    var toastRegion = document.getElementById("toastRegion");


    function toast(message, kind) {
        var element = document.createElement("div");
        element.className = "toast toast-" + (kind || "info");
        element.textContent = message;
        toastRegion.appendChild(element);

        window.setTimeout(function () {
            element.remove();
        }, 5000);
    }

    function prettyDate(value) {
        // Timestamps look like "2026-11-21 22:07:02" (Philippine time).
        var parsed = new Date(String(value).replace(" ", "T"));

        if (isNaN(parsed.getTime())) {
            return String(value || "");
        }

        return parsed.toLocaleString(undefined, {
            year: "numeric",
            month: "long",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit"
        });
    }

    function card(title, dateText, content, extras) {
        var article = document.createElement("article");
        article.className = "entry-card offline-entry";

        var heading = document.createElement("h3");
        heading.textContent = title;
        article.appendChild(heading);

        var date = document.createElement("span");
        date.className = "entry-date";
        date.textContent = dateText;
        article.appendChild(date);

        var details = document.createElement("details");
        var summary = document.createElement("summary");
        var body = document.createElement("div");

        var preview = content.length > 160
            ? content.slice(0, 160).replace(/\s+\S*$/, "") + "…"
            : content;

        summary.textContent = preview;
        body.className = "offline-entry-body";
        body.textContent = content;

        details.appendChild(summary);
        details.appendChild(body);
        article.appendChild(details);

        if (extras) {
            article.appendChild(extras);
        }

        return article;
    }


    /* ---------- Connection state ---------- */

    function paintConnection() {
        var online = navigator.onLine;

        hero.classList.toggle("is-online", online);

        if (online) {
            heroTitle.textContent = "You're back online";
            heroText.textContent =
                "Open your diary to sync anything you wrote while you were away.";
        } else {
            heroTitle.textContent = "You're offline";
            heroText.textContent =
                "You can still write memories. They are saved on this device " +
                "and added to your diary when you reconnect.";
        }

        syncLink.hidden = !online;
    }

    window.addEventListener("online", paintConnection);
    window.addEventListener("offline", paintConnection);

    retry.addEventListener("click", function () {
        window.location.reload();
    });


    /* ---------- Waiting to sync ---------- */

    function renderPending() {
        return Store.queueAll().then(function (items) {
            var mine = items.filter(function (item) {
                return item.user_id === userId;
            });

            pendingList.textContent = "";
            pendingSection.hidden = mine.length === 0;
            pendingCount.textContent = mine.length ? String(mine.length) : "";

            mine.sort(function (a, b) {
                return String(b.created_at_utc).localeCompare(
                    String(a.created_at_utc)
                );
            });

            mine.forEach(function (item) {
                var extras = null;

                if (item.status === "error") {
                    extras = document.createElement("div");
                    extras.className = "offline-actions";

                    var problem = document.createElement("p");
                    problem.className = "settings-inline-status is-error";
                    problem.textContent =
                        (item.error || "This memory could not be accepted.") +
                        " Copy your words, then remove it.";

                    var remove = document.createElement("button");
                    remove.type = "button";
                    remove.className = "button-danger";
                    remove.textContent = "Remove from this device";

                    remove.addEventListener("click", function () {
                        Store.queueRemove(item.client_id).then(renderPending);
                    });

                    extras.appendChild(problem);
                    extras.appendChild(remove);
                }

                pendingList.appendChild(
                    card(
                        item.title,
                        "Written " + prettyDate(
                            item.saved_at
                                ? new Date(item.saved_at).toISOString()
                                : item.created_at_utc
                        ),
                        item.content,
                        extras
                    )
                );
            });
        }).catch(function () {
            pendingSection.hidden = true;
        });
    }


    /* ---------- The copy of the diary on this device ---------- */

    function renderCache() {
        if (!userId) {
            cacheNote.textContent =
                "Log in once while you are online so this device can keep " +
                "your memories for offline reading.";
            return Promise.resolve();
        }

        if (!Store.isCacheEnabled()) {
            cacheNote.textContent =
                "Offline reading is turned off on this device. You can turn it " +
                "on in Settings → Offline use.";
            return Promise.resolve();
        }

        return Store.getCachedEntries(userId).then(function (rows) {
            cacheList.textContent = "";

            if (!rows.length) {
                cacheNote.textContent =
                    "No saved memories yet. Open your diary while you are " +
                    "online and they will be kept here for offline reading.";
                return;
            }

            cacheNote.textContent =
                rows.length + (rows.length === 1 ? " memory" : " memories") +
                " saved on this device (read only).";

            rows.slice(0, 200).forEach(function (row) {
                cacheList.appendChild(
                    card(
                        (row.is_favorite ? "♥ " : "") + row.title,
                        prettyDate(row.created_at),
                        row.content
                    )
                );
            });
        }, function () {
            cacheNote.textContent =
                "The saved copy could not be read on this device.";
        });
    }


    /* ---------- Writing a memory offline ---------- */

    form.addEventListener("submit", function (event) {
        event.preventDefault();

        var title = titleInput.value.trim();
        var content = contentInput.value.trim();

        if (!title || !content) {
            toast("Please enter both a title and your memory.", "error");
            return;
        }

        if (!userId) {
            toast(
                "Log in once while online so this device knows whose diary " +
                "to add this to.",
                "error"
            );
            return;
        }

        Store.queueAdd({
            client_id: Store.newClientId(),
            user_id: userId,
            title: title,
            content: content,
            created_at_utc: new Date().toISOString(),
            saved_at: Date.now(),
            status: "pending"
        }).then(function () {
            Store.markDirty();
            titleInput.value = "";
            contentInput.value = "";
            toast("Saved on this device. It will sync when you're online. 💜", "success");
            return renderPending();
        }, function () {
            toast("We couldn't save that on this device.", "error");
        });
    });


    paintConnection();
    renderPending();
    renderCache();

})();
