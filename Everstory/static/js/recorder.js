/*
 * Everstory - voice note recorder (uses the browser's microphone).
 *
 * The recording stays in the browser until the person saves the
 * memory. Exposed as window.EverstoryRecorder.
 */
(function (global) {
    "use strict";

    var TYPES = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4",
        "audio/ogg;codecs=opus"
    ];

    function supported() {
        return Boolean(
            global.MediaRecorder &&
            navigator.mediaDevices &&
            navigator.mediaDevices.getUserMedia
        );
    }

    function pickType() {
        for (var i = 0; i < TYPES.length; i += 1) {
            if (MediaRecorder.isTypeSupported(TYPES[i])) {
                return TYPES[i];
            }
        }

        return "";
    }

    function extensionFor(type) {
        if (type.indexOf("mp4") !== -1) { return "m4a"; }
        if (type.indexOf("ogg") !== -1) { return "ogg"; }

        return "webm";
    }

    function formatTime(total) {
        return Math.floor(total / 60) + ":" + ("0" + (total % 60)).slice(-2);
    }

    function stamp() {
        var now = new Date();

        function two(n) {
            return ("0" + n).slice(-2);
        }

        return now.getFullYear() + two(now.getMonth() + 1) + two(now.getDate()) +
            "-" + two(now.getHours()) + two(now.getMinutes()) +
            two(now.getSeconds());
    }

    function init(o) {
        var stream = null;
        var recorder = null;
        var chunks = [];
        var timerId = null;
        var seconds = 0;
        var previewUrl = null;
        var recordedFile = null;
        var recording = false;

        var maxSeconds = o.maxSeconds || 600;

        function status(text, isError) {
            o.statusEl.textContent = text || "";
            o.statusEl.classList.toggle("is-error", Boolean(isError));
        }

        function stopTracks() {
            if (stream) {
                stream.getTracks().forEach(function (track) {
                    track.stop();
                });

                stream = null;
            }
        }

        function setRecordingUi(isRecording) {
            recording = isRecording;
            o.root.classList.toggle("is-recording", isRecording);
            o.startButton.hidden = isRecording;
            o.stopButton.hidden = !isRecording;
        }

        function finish() {
            window.clearInterval(timerId);
            stopTracks();
            setRecordingUi(false);

            var type = (recorder && recorder.mimeType) || pickType() ||
                "audio/webm";

            var blob = new Blob(chunks, { type: type });
            chunks = [];

            if (blob.size === 0) {
                status("Nothing was recorded. Please try again.", true);
                return;
            }

            recordedFile = new File(
                [blob],
                "voice-note-" + stamp() + "." + extensionFor(type),
                { type: type, lastModified: Date.now() }
            );

            if (previewUrl) {
                URL.revokeObjectURL(previewUrl);
            }

            previewUrl = URL.createObjectURL(blob);
            o.audioEl.src = previewUrl;
            o.previewEl.hidden = false;
            o.startButton.textContent = "🎙 Record again";

            status("Voice note ready. It will be saved with your memory.");

            o.onRecorded(recordedFile);
        }

        function start() {
            status("");

            if (recordedFile) {
                discard(true);
            }

            navigator.mediaDevices.getUserMedia({ audio: true }).then(
                function (micStream) {
                    stream = micStream;

                    var type = pickType();

                    try {
                        recorder = type
                            ? new MediaRecorder(stream, { mimeType: type })
                            : new MediaRecorder(stream);
                    } catch (error) {
                        stopTracks();
                        status("This browser cannot record audio.", true);
                        return;
                    }

                    chunks = [];

                    recorder.ondataavailable = function (event) {
                        if (event.data && event.data.size) {
                            chunks.push(event.data);
                        }
                    };

                    recorder.onstop = finish;

                    recorder.start();

                    seconds = 0;
                    o.timerEl.textContent = formatTime(0);
                    setRecordingUi(true);
                    status("Recording… speak now.");

                    timerId = window.setInterval(function () {
                        seconds += 1;
                        o.timerEl.textContent = formatTime(seconds);

                        if (seconds >= maxSeconds) {
                            stop();
                        }
                    }, 1000);
                },
                function (error) {
                    var message = "We could not start the microphone.";

                    if (error && error.name === "NotAllowedError") {
                        message = "Microphone access was blocked. Allow it " +
                            "in your browser's site settings, then try again.";
                    } else if (error && error.name === "NotFoundError") {
                        message = "No microphone was found on this device.";
                    }

                    status(message, true);
                }
            );
        }

        function stop() {
            if (recorder && recorder.state !== "inactive") {
                recorder.stop();
            }
        }

        function discard(quiet) {
            if (previewUrl) {
                URL.revokeObjectURL(previewUrl);
                previewUrl = null;
            }

            o.audioEl.removeAttribute("src");
            o.previewEl.hidden = true;
            o.startButton.textContent = "🎙 Record voice note";
            o.timerEl.textContent = "0:00";

            var removed = recordedFile;
            recordedFile = null;

            if (!quiet) {
                status("");
            }

            if (removed) {
                o.onDiscard(removed);
            }
        }

        o.startButton.addEventListener("click", start);
        o.stopButton.addEventListener("click", stop);
        o.discardButton.addEventListener("click", function () {
            discard(false);
        });

        o.root.hidden = false;

        return {
            isRecording: function () { return recording; },
            stop: stop
        };
    }

    global.EverstoryRecorder = { supported: supported, init: init };

})(window);
