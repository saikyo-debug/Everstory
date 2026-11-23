/*
 * Everstory - Settings page.
 *
 *  - Show / Hide buttons for the password fields
 *  - checks that the new password and its confirmation match
 *  - the "Offline use" card (offline copy on/off, sync now, clear)
 *
 * (The appearance and personalization controls are handled by theme.js.)
 */
(function () {
    "use strict";

    var Store = window.EverstoryOffline;
    var UI = window.EverstoryUI || {};


    /* ---------- Show / hide password ---------- */

    Array.prototype.forEach.call(
        document.querySelectorAll(".password-toggle[data-password-target]"),
        function (button) {
            var input = document.getElementById(button.dataset.passwordTarget);

            if (!input) {
                return;
            }

            var label = (button.getAttribute("aria-label") || "")
                .replace(/^(Show|Hide)\s+/i, "");

            button.addEventListener("click", function () {
                var show = input.type === "password";

                input.type = show ? "text" : "password";
                button.textContent = show ? "Hide" : "Show";
                button.setAttribute(
                    "aria-label",
                    (show ? "Hide " : "Show ") + label
                );
            });
        }
    );


    /* ---------- New password must match its confirmation ---------- */

    var passwordForm = document.getElementById("passwordChangeForm");

    if (passwordForm) {
        var newPassword = document.getElementById("newPassword");
        var confirmPassword = document.getElementById("confirmPassword");

        function checkMatch() {
            if (
                newPassword && confirmPassword &&
                confirmPassword.value &&
                newPassword.value !== confirmPassword.value
            ) {
                confirmPassword.setCustomValidity("The passwords do not match.");
            } else if (confirmPassword) {
                confirmPassword.setCustomValidity("");
            }
        }

        if (newPassword && confirmPassword) {
            newPassword.addEventListener("input", checkMatch);
            confirmPassword.addEventListener("input", checkMatch);
        }
    }


    /* ---------- Offline use ---------- */

    var card = document.getElementById("offlineCard");

    if (!card || !Store) {
        return;
    }

    var userId = UI.userId;

    var toggle = document.getElementById("offlineCacheToggle");
    var badge = document.getElementById("offlineStatusBadge");
    var cachedCount = document.getElementById("offlineCachedCount");
    var pendingCount = document.getElementById("offlinePendingCount");
    var lastSync = document.getElementById("offlineLastSync");
    var syncButton = document.getElementById("offlineSyncNow");
    var clearButton = document.getElementById("offlineClear");
    var message = document.getElementById("offlineMessage");

    function say(text, kind) {
        message.textContent = text || "";
        message.classList.toggle("is-error", kind === "error");
        message.classList.toggle("is-success", kind === "success");
    }

    function refresh() {
        var enabled = Store.isCacheEnabled();

        toggle.checked = enabled;
        badge.textContent = enabled ? "ON" : "OFF";

        Store.getCachedEntries(userId).then(function (rows) {
            cachedCount.textContent = enabled ? String(rows.length) : "Off";
        }, function () {
            cachedCount.textContent = "–";
        });

        Store.pendingFor(userId).then(function (items) {
            pendingCount.textContent = String(items.length);
        }, function () {
            pendingCount.textContent = "–";
        });

        Store.getMeta("last_sync").then(function (time) {
            lastSync.textContent = time
                ? new Date(time).toLocaleString()
                : "Never";
        }, function () {
            lastSync.textContent = "–";
        });
    }

    toggle.addEventListener("change", function () {
        Store.setCacheEnabled(toggle.checked);

        if (toggle.checked) {
            say("Saving a copy of your memories on this device…");

            Store.refreshCache({ userId: userId, force: true }).then(
                function (done) {
                    say(
                        done
                            ? "Your memories are now available offline."
                            : "Turned on. The copy will be saved as soon as " +
                              "you are online.",
                        done ? "success" : ""
                    );
                    refresh();
                }
            );
        } else {
            Store.clearCache().then(function () {
                say("Offline copy turned off and removed from this device.", "success");
                refresh();
            });
        }

        refresh();
    });

    syncButton.addEventListener("click", function () {
        if (!navigator.onLine) {
            say("You're offline right now. Try again when you're connected.", "error");
            return;
        }

        say("Syncing…");
        syncButton.disabled = true;

        var done = function () {
            syncButton.disabled = false;
            refresh();
        };

        (UI.syncNow ? UI.syncNow(true) : Promise.resolve(null)).then(
            function (result) {
                if (result && result.loginRequired) {
                    say("Your session ended. Please log in again.", "error");
                } else if (result && result.pending) {
                    say(result.pending + " memory is still waiting to sync.", "error");
                } else {
                    say("Everything is up to date. 💜", "success");
                }

                done();
            },
            done
        );
    });

    clearButton.addEventListener("click", function () {
        if (!window.confirm(
            "Remove the saved copy of your memories from this device? " +
            "Your diary online is not affected."
        )) {
            return;
        }

        Store.clearCache().then(function () {
            say("The offline copy was removed from this device.", "success");
            refresh();
        });
    });

    document.addEventListener("everstory:synced", refresh);

    refresh();

})();
