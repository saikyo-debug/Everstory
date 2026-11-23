/*
 * Everstory - Settings: set up, re-record or remove voice unlock.
 * Needs voice-features.js. The password is required to change it.
 */
(function () {
    "use strict";

    var card = document.getElementById("voiceCard");
    var Voice = window.EverstoryVoice;

    if (!card || !Voice) {
        return;
    }

    var UI = window.EverstoryUI || {};

    var setupPanel = document.getElementById("voiceSetup");
    var managePanel = document.getElementById("voiceManage");
    var passwordInput = document.getElementById("voicePassword");
    var removePasswordInput = document.getElementById("voiceRemovePassword");
    var recordButton = document.getElementById("voiceRecord");
    var saveButton = document.getElementById("voiceSave");
    var retrainButton = document.getElementById("voiceRetrain");
    var removeButton = document.getElementById("voiceRemove");
    var meterBar = document.getElementById("voiceMeterBar");
    var message = document.getElementById("voiceMessage");
    var steps = document.querySelectorAll("#voiceSteps .voice-step");

    var NEEDED = 5;
    var samples = [];
    var busy = false;

    function say(text, kind) {
        message.textContent = text || "";
        message.classList.toggle("is-error", kind === "error");
        message.classList.toggle("is-success", kind === "success");
    }

    function level(value) {
        meterBar.style.width = Math.round(value * 100) + "%";
    }

    function paintSteps() {
        Array.prototype.forEach.call(steps, function (step, index) {
            step.classList.toggle("is-done", index < samples.length);
            step.classList.toggle("is-current", index === samples.length);
        });

        if (samples.length < NEEDED) {
            recordButton.hidden = false;
            saveButton.hidden = true;
            recordButton.textContent =
                "🎙 Record sample " + (samples.length + 1) + " of " + NEEDED;
        } else {
            recordButton.hidden = true;
            saveButton.hidden = false;
        }
    }

    function post(url, body) {
        return fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-CSRFToken": UI.csrf || ""
            },
            body: JSON.stringify(body)
        }).then(function (response) {
            return response.json().catch(function () {
                return {};
            }).then(function (data) {
                data.status = response.status;
                return data;
            });
        });
    }

    recordButton.addEventListener("click", function () {
        if (busy) {
            return;
        }

        busy = true;
        recordButton.disabled = true;
        say("Listening… say the phrase now.");

        Voice.recordClip({ seconds: 4, onLevel: level }).then(
            function (clip) {
                if (!clip.features || clip.voiced_ms < 700) {
                    say(
                        "I couldn't hear you clearly. Move closer to the " +
                        "microphone and try that sample again.",
                        "error"
                    );
                    return;
                }

                samples.push({
                    features: clip.features,
                    voiced_ms: clip.voiced_ms
                });

                paintSteps();

                say(
                    samples.length < NEEDED
                        ? "Got it. Say the same phrase again."
                        : "All five recorded. Enter your password and turn it on.",
                    "success"
                );
            },
            function (error) {
                say(
                    error && error.name === "NotAllowedError"
                        ? "Microphone access was blocked. Allow it in your browser's site settings."
                        : "We could not use the microphone on this device.",
                    "error"
                );
            }
        ).then(function () {
            busy = false;
            recordButton.disabled = false;
            level(0);
        });
    });

    saveButton.addEventListener("click", function () {
        if (!passwordInput.value) {
            say("Please enter your password to turn on voice unlock.", "error");
            passwordInput.focus();
            return;
        }

        saveButton.disabled = true;
        say("Saving…");

        post(card.dataset.enrollUrl, {
            password: passwordInput.value,
            samples: samples
        }).then(function (data) {
            if (data.ok) {
                say("Voice unlock is on. 💜", "success");
                window.setTimeout(function () {
                    window.location.reload();
                }, 900);
                return;
            }

            saveButton.disabled = false;

            if (data.status === 422) {
                // The recordings were too different: start over.
                samples = [];
                paintSteps();
            }

            say(data.message || "We couldn't turn on voice unlock.", "error");
        }, function () {
            saveButton.disabled = false;
            say("We couldn't reach the server. Please try again.", "error");
        });
    });

    retrainButton.addEventListener("click", function () {
        samples = [];
        paintSteps();
        managePanel.hidden = true;
        setupPanel.hidden = false;
        say("");
    });

    removeButton.addEventListener("click", function () {
        if (!removePasswordInput.value) {
            say("Please enter your password to turn off voice unlock.", "error");
            removePasswordInput.focus();
            return;
        }

        removeButton.disabled = true;

        post(card.dataset.deleteUrl, {
            password: removePasswordInput.value
        }).then(function (data) {
            if (data.ok) {
                say("Voice unlock was turned off.", "success");
                window.setTimeout(function () {
                    window.location.reload();
                }, 900);
                return;
            }

            removeButton.disabled = false;
            say(data.message || "We couldn't turn off voice unlock.", "error");
        }, function () {
            removeButton.disabled = false;
            say("We couldn't reach the server. Please try again.", "error");
        });
    });

    paintSteps();

})();
