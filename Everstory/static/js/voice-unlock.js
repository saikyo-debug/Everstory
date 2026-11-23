/*
 * Everstory - "say your passphrase" step-up page.
 * The password form on the same page is the always-available fallback.
 */
(function () {
    "use strict";

    var card = document.getElementById("unlockCard");
    var Voice = window.EverstoryVoice;

    if (!card || !Voice) {
        return;
    }

    var UI = window.EverstoryUI || {};

    var button = document.getElementById("unlockRecord");
    var message = document.getElementById("unlockMessage");
    var meterBar = document.getElementById("voiceMeterBar");

    function say(text, kind) {
        message.textContent = text || "";
        message.classList.toggle("is-error", kind === "error");
        message.classList.toggle("is-success", kind === "success");
    }

    function isLocalPath(url) {
        return typeof url === "string" &&
            url.charAt(0) === "/" && url.charAt(1) !== "/";
    }

    button.addEventListener("click", function () {
        button.disabled = true;
        say("Listening… say the phrase now.");

        Voice.recordClip({
            seconds: 4,
            onLevel: function (value) {
                meterBar.style.width = Math.round(value * 100) + "%";
            }
        }).then(function (clip) {
            if (!clip.features || clip.voiced_ms < 700) {
                say(
                    "I couldn't hear you clearly. Move closer and try again.",
                    "error"
                );
                button.disabled = false;
                return null;
            }

            return fetch("/api/voice/verify", {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-CSRFToken": UI.csrf || ""
                },
                body: JSON.stringify({
                    features: clip.features,
                    voiced_ms: clip.voiced_ms
                })
            }).then(function (response) {
                return response.json().catch(function () {
                    return {};
                }).then(function (data) {
                    if (data.ok) {
                        say("Voice recognized. 💜", "success");

                        var target = card.dataset.next;

                        window.location.href = isLocalPath(target)
                            ? target
                            : "/capsules";
                        return;
                    }

                    say(data.message || "That did not work.", "error");

                    // Too many tries: leave the button off, password still works.
                    button.disabled = Boolean(data.locked);
                });
            });
        }).catch(function (error) {
            say(
                error && error.name === "NotAllowedError"
                    ? "Microphone access was blocked. Use your password below instead."
                    : "We couldn't use the microphone. Use your password below instead.",
                "error"
            );
            button.disabled = false;
        }).then(function () {
            meterBar.style.width = "0";
        });
    });

})();
