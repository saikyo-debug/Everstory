/*
 * Runs in the <head>, before the page paints, so the right colours
 * show immediately. It resolves "System" mode (follow the device)
 * and, on the offline page, restores the last theme from this device.
 */
(function () {
    "use strict";

    var root = document.documentElement;

    try {
        if (root.getAttribute("data-theme-source") === "local") {
            var saved = JSON.parse(
                localStorage.getItem("everstory.theme") || "null"
            );

            if (saved) {
                root.setAttribute("data-theme-mode", saved.mode || "system");
                root.setAttribute("data-season", saved.season || "classic");
                root.setAttribute("data-font", saved.font || "modern");
                root.setAttribute("data-text-size", saved.textSize || "medium");
                root.setAttribute(
                    "data-reduced-motion",
                    saved.reducedMotion ? "true" : "false"
                );
            }
        }

        var mode = root.getAttribute("data-theme-mode") || "system";
        var dark = mode === "dark";

        if (mode === "system") {
            dark = Boolean(
                window.matchMedia &&
                window.matchMedia("(prefers-color-scheme: dark)").matches
            );
        }

        root.setAttribute("data-theme", dark ? "dark" : "light");
    } catch (error) {
        /* The server-rendered theme stays in place. */
    }
})();
