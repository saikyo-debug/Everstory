/*
 * Everstory - shared behaviour for every signed-in page.
 *
 *  - toasts and the "you are offline" banner
 *  - registers the service worker (offline pages and fast loading)
 *  - sends memories written offline when the connection returns
 *  - asks before destructive actions (forms with data-confirm)
 *  - clears this device's private offline copy when logging out
 */
(function () {
    "use strict";

    function meta(name) {
        var element = document.querySelector('meta[name="' + name + '"]');
        return element ? element.content : "";
    }

    var csrf = meta("csrf-token");
    var userId = parseInt(meta("user-id"), 10) || null;
    var Store = window.EverstoryOffline;

    try {
        if (userId) {
            localStorage.setItem("everstory.userId", String(userId));
        }
    } catch (error) { /* ignore */ }


    /* ---------- Toasts ---------- */

    var region = document.getElementById("toastRegion");

    function toast(message, kind, duration) {
        if (!region) {
            return;
        }

        var element = document.createElement("div");
        element.className = "toast toast-" + (kind || "info");
        element.textContent = message;
        region.appendChild(element);

        window.setTimeout(function () {
            element.remove();
        }, duration || 5000);
    }

    window.EverstoryUI = {
        toast: toast,
        csrf: csrf,
        userId: userId
    };

    try {
        var waiting = sessionStorage.getItem("everstory.toast");

        if (waiting) {
            sessionStorage.removeItem("everstory.toast");
            toast(waiting, "success");
        }
    } catch (error) { /* ignore */ }


    /* ---------- Confirm before destructive actions ---------- */

    document.addEventListener("submit", function (event) {
        var form = event.target;

        if (form && form.dataset && form.dataset.confirm) {
            if (!window.confirm(form.dataset.confirm)) {
                event.preventDefault();
            }
        }
    }, true);

    // Any change made through a form means the offline copy is out of date.
    document.addEventListener("submit", function (event) {
        var form = event.target;

        if (
            !event.defaultPrevented &&
            form && form.method && form.method.toLowerCase() === "post" &&
            Store
        ) {
            Store.markDirty();
        }
    });


    /* ---------- Logging out removes the offline copy ---------- */

    Array.prototype.forEach.call(
        document.querySelectorAll("[data-logout-form]"),
        function (form) {
            form.addEventListener("submit", function (event) {
                if (form.dataset.cleared === "1") {
                    return;
                }

                event.preventDefault();

                var finished = false;

                function finish() {
                    if (finished) {
                        return;
                    }

                    finished = true;
                    form.dataset.cleared = "1";
                    form.submit();
                }

                try {
                    if (userId) {
                        localStorage.removeItem("everstory.draft." + userId);
                    }

                    localStorage.removeItem("everstory.cacheDirty");
                } catch (error) { /* ignore */ }

                // Memories that have not synced yet are deliberately kept,
                // so nothing the person wrote is ever lost.
                if (Store) {
                    Store.clearCache().then(finish, finish);
                    window.setTimeout(finish, 1500);
                } else {
                    finish();
                }
            });
        }
    );


    /* ---------- Connection banner ---------- */

    var banner = document.getElementById("connectionBanner");
    var offlineBannerShown = false;

    function showBanner(text, ok) {
        if (!banner) {
            return;
        }

        banner.textContent = text;
        banner.classList.toggle("is-ok", Boolean(ok));
        banner.hidden = false;
    }

    function hideBanner() {
        if (banner) {
            banner.hidden = true;
        }
    }

    function showOfflineBanner() {
        offlineBannerShown = true;

        showBanner(
            "You're offline. New memories are saved on this device and " +
            "will sync when you reconnect."
        );
    }


    /* ---------- Sync ---------- */

    function syncNow(manual) {
        if (!userId || !Store) {
            return Promise.resolve(null);
        }

        return Store.sync({ csrf: csrf, userId: userId }).then(
            function (result) {

                if (result.synced) {
                    toast(
                        result.synced === 1
                            ? "1 memory synced to your diary. 💜"
                            : result.synced + " memories synced to your diary. 💜",
                        "success"
                    );

                    Store.markDirty();

                    if (
                        window.location.pathname === "/dashboard" ||
                        window.location.pathname === "/entries"
                    ) {
                        window.setTimeout(function () {
                            window.location.reload();
                        }, 1500);
                    }
                }

                if (result.failed) {
                    toast(
                        result.failed + " offline memory could not be " +
                        "accepted. Open the offline page to review it.",
                        "error"
                    );
                }

                if (result.loginRequired && result.pending) {
                    toast(
                        "Your session ended. Log in again and your offline " +
                        "memories will sync.",
                        "error"
                    );
                }

                return Store.refreshCache({
                    userId: userId,
                    force: Boolean(manual)
                }).then(function () {
                    document.dispatchEvent(
                        new CustomEvent("everstory:synced", {
                            detail: result
                        })
                    );

                    return result;
                });
            }
        ).catch(function () {
            return null;
        });
    }

    window.EverstoryUI.syncNow = syncNow;

    window.addEventListener("offline", showOfflineBanner);

    window.addEventListener("online", function () {
        if (offlineBannerShown) {
            showBanner("Back online. Syncing your memories…", true);
        }

        syncNow(false).then(function () {
            if (offlineBannerShown) {
                offlineBannerShown = false;
                window.setTimeout(hideBanner, 2500);
            }
        });
    });

    if (!navigator.onLine) {
        showOfflineBanner();
    } else {
        syncNow(false);
    }

    document.addEventListener("visibilitychange", function () {
        if (!document.hidden && navigator.onLine) {
            syncNow(false);
        }
    });


    /* ---------- Service worker ---------- */

    if ("serviceWorker" in navigator) {
        window.addEventListener("load", function () {
            navigator.serviceWorker.register("/sw.js", { scope: "/" })
                .catch(function () { /* offline pages just will not be available */ });
        });
    }


    /* ---------- Mobile "More" menu ---------- */

    var more = document.getElementById("mobileMore");

    if (more) {
        document.addEventListener("click", function (event) {
            if (more.open && !more.contains(event.target)) {
                more.open = false;
            }
        });
    }

})();
