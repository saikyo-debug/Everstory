/*
 * Everstory - theme and personalization.
 *
 * The server already writes the saved theme onto <html> for every
 * page, so there is nothing to download before the page looks right.
 * This script only handles LIVE changes: when the person picks an
 * option in Settings it is applied instantly, then saved.
 */
(function () {
    "use strict";

    var root = document.documentElement;
    var body = document.body;

    var appearanceForm = document.getElementById("appearanceForm");
    var preferencesForm = document.getElementById("preferencesForm");

    var isSettingsPage = Boolean(appearanceForm || preferencesForm);

    var SCROLL_STORAGE_KEY = "everstory_settings_scroll_position";

    var systemPreference = window.matchMedia(
        "(prefers-color-scheme: dark)"
    );

    var state = {
        mode: root.dataset.themeMode || "system",
        season: root.dataset.season || "classic",
        font: root.dataset.font || "modern",
        textSize: root.dataset.textSize || "medium",
        reducedMotion: root.dataset.reducedMotion === "true"
    };


    /* ---------- Apply ---------- */

    function applyTheme() {
        var dark = state.mode === "dark" || (
            state.mode === "system" && systemPreference.matches
        );

        root.dataset.theme = dark ? "dark" : "light";
        root.dataset.themeMode = state.mode;

        if (body) {
            body.dataset.themeMode = state.mode;
        }
    }

    function remember() {
        // The offline page has no server, so it restores the look from here.
        try {
            localStorage.setItem("everstory.theme", JSON.stringify(state));
        } catch (error) { /* storage unavailable: harmless */ }
    }

    function applyPersonalization() {
        root.dataset.season = state.season;
        root.dataset.font = state.font;
        root.dataset.textSize = state.textSize;
        root.dataset.reducedMotion = state.reducedMotion ? "true" : "false";

        applyTheme();
        remember();
    }

    applyPersonalization();


    /* ---------- Follow the device in "System" mode ---------- */

    function handleSystemChange() {
        if (state.mode === "system") {
            applyTheme();
        }
    }

    if (systemPreference.addEventListener) {
        systemPreference.addEventListener("change", handleSystemChange);
    } else if (systemPreference.addListener) {
        systemPreference.addListener(handleSystemChange);
    }


    /* ---------- Keep the Settings scroll position across saves ---------- */

    function saveScrollPosition() {
        if (!isSettingsPage) {
            return;
        }

        try {
            sessionStorage.setItem(SCROLL_STORAGE_KEY, String(window.scrollY));
        } catch (error) { /* ignore */ }
    }

    function restoreScrollPosition() {
        if (!isSettingsPage) {
            return;
        }

        try {
            var saved = sessionStorage.getItem(SCROLL_STORAGE_KEY);

            if (saved === null) {
                return;
            }

            sessionStorage.removeItem(SCROLL_STORAGE_KEY);

            var position = Number(saved);

            if (Number.isFinite(position)) {
                window.requestAnimationFrame(function () {
                    window.scrollTo({ top: position, behavior: "auto" });
                });
            }
        } catch (error) { /* ignore */ }
    }

    if (isSettingsPage) {
        if (document.readyState === "complete") {
            restoreScrollPosition();
        } else {
            window.addEventListener("load", restoreScrollPosition, {
                once: true
            });
        }
    }


    /* ---------- Appearance form: light / dark / system ---------- */

    if (appearanceForm) {
        appearanceForm.addEventListener("submit", saveScrollPosition);

        appearanceForm.addEventListener("change", function (event) {
            if (event.target.name !== "theme_mode" || !event.target.checked) {
                return;
            }

            state.mode = event.target.value;
            applyPersonalization();
            appearanceForm.requestSubmit();
        });
    }


    /* ---------- Personalization form: season, font, size, motion ---------- */

    if (preferencesForm) {
        preferencesForm.addEventListener("submit", saveScrollPosition);

        preferencesForm.addEventListener("change", function (event) {
            var target = event.target;

            if (!target.name) {
                return;
            }

            if (target.name === "seasonal_theme") {
                state.season = target.value;
            }

            if (target.name === "font_style") {
                state.font = target.value;
            }

            if (target.name === "text_size") {
                state.textSize = target.value;
            }

            if (target.name === "reduced_animations") {
                state.reducedMotion = (
                    target.value === "1" || target.value === "true"
                );
            }

            applyPersonalization();
            preferencesForm.requestSubmit();
        });
    }

})();
