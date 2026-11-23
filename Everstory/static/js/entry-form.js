/*
 * Everstory - write / edit a memory.
 *
 *  - live character count
 *  - photo / voice / video picker and the voice recorder
 *  - draft autosave, so a closed tab or a crash never loses your words
 *  - offline writing: when there is no connection the memory is kept
 *    on this device and sent automatically later (text-only memories)
 */
(function () {
    "use strict";

    var form = document.getElementById("entryForm");

    if (!form) {
        return;
    }

    var Store = window.EverstoryOffline;
    var UI = window.EverstoryUI || {};

    var mode = form.dataset.mode;
    var apiUrl = form.dataset.apiUrl;
    var entryUrlBase = form.dataset.entryUrl;       // e.g. /entries/0

    var titleInput = document.getElementById("title");
    var contentInput = document.getElementById("content");
    var counter = document.getElementById("contentCount");
    var saveButton = document.getElementById("saveButton");
    var offlineNote = document.getElementById("offlineNote");
    var fileInput = document.getElementById("mediaInput");
    var fileList = document.getElementById("fileList");

    var userId = UI.userId || parseInt(
        (document.querySelector('meta[name="user-id"]') || {}).content, 10
    ) || null;

    var csrf = UI.csrf || (
        document.querySelector('meta[name="csrf-token"]') || {}
    ).content || "";

    var DRAFT_KEY = "everstory.draft." + userId;

    var saveLabel = saveButton ? saveButton.innerHTML : "";
    var clientId = Store ? Store.newClientId() : null;


    /* ---------- Messages ---------- */

    function say(message, kind) {
        if (UI.toast) {
            UI.toast(message, kind || "error", 6000);
        } else {
            window.alert(message);
        }
    }


    /* ---------- Character count ---------- */

    function updateCount() {
        counter.textContent = contentInput.value.length.toLocaleString();
    }

    contentInput.addEventListener("input", updateCount);
    updateCount();


    /* ---------- Attachments and recorder ---------- */

    var recorderApi = null;
    var recordedFile = null;

    var picker = window.EverstoryFiles.attach({
        input: fileInput,
        list: fileList,
        maxFiles: parseInt(form.dataset.maxFiles, 10) || 10,
        onProblem: function (message) {
            say(message, "error");
        }
    });

    var recorderRoot = document.getElementById("recorder");

    if (recorderRoot && window.EverstoryRecorder &&
        window.EverstoryRecorder.supported()) {

        recorderApi = window.EverstoryRecorder.init({
            root: recorderRoot,
            startButton: document.getElementById("recStart"),
            stopButton: document.getElementById("recStop"),
            timerEl: document.getElementById("recTimer"),
            previewEl: document.getElementById("recPreview"),
            audioEl: document.getElementById("recAudio"),
            discardButton: document.getElementById("recDiscard"),
            statusEl: document.getElementById("recStatus"),
            maxSeconds: 600,

            onRecorded: function (file) {
                recordedFile = file;

                if (picker.add(file)) {
                    picker.commit();
                }
            },

            onDiscard: function (file) {
                picker.remove(file);
                recordedFile = null;
            }
        });
    }


    /* ---------- Draft autosave (new memories only) ---------- */

    var draftTimer = null;

    function readDraft() {
        try {
            return JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
        } catch (error) {
            return null;
        }
    }

    function writeDraft(extra) {
        if (mode !== "new" || !userId) {
            return;
        }

        var title = titleInput.value;
        var content = contentInput.value;

        try {
            if (!title.trim() && !content.trim()) {
                localStorage.removeItem(DRAFT_KEY);
                return;
            }

            localStorage.setItem(DRAFT_KEY, JSON.stringify(Object.assign({
                title: title,
                content: content,
                saved: Date.now()
            }, extra || {})));
        } catch (error) { /* storage full or blocked: skip */ }
    }

    function clearDraft() {
        try {
            localStorage.removeItem(DRAFT_KEY);
        } catch (error) { /* ignore */ }
    }

    if (mode === "new") {
        var draft = readDraft();

        if (draft && !titleInput.value.trim() && !contentInput.value.trim()) {
            titleInput.value = draft.title || "";
            contentInput.value = draft.content || "";
            updateCount();

            if (draft.title || draft.content) {
                say("Restored the draft you were writing. 💜", "success");
            }
        }

        function scheduleDraft() {
            window.clearTimeout(draftTimer);
            draftTimer = window.setTimeout(writeDraft, 500);
        }

        titleInput.addEventListener("input", scheduleDraft);
        contentInput.addEventListener("input", scheduleDraft);
    }


    /* ---------- Offline note ---------- */

    function updateOfflineNote() {
        if (offlineNote) {
            offlineNote.hidden = navigator.onLine;
        }
    }

    window.addEventListener("online", updateOfflineNote);
    window.addEventListener("offline", updateOfflineNote);
    updateOfflineNote();


    /* ---------- Saving ---------- */

    function setSaving(isSaving) {
        if (!saveButton) {
            return;
        }

        saveButton.disabled = isSaving;
        saveButton.textContent = isSaving ? "Saving…" : "";

        if (!isSaving) {
            saveButton.innerHTML = saveLabel;
        }
    }

    function entryUrl(id) {
        return entryUrlBase.replace(/\/0$/, "/" + id);
    }

    function keepOnThisDevice(title, content) {
        return Store.queueAdd({
            client_id: clientId,
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
            updateCount();
            clearDraft();

            clientId = Store.newClientId();

            say(
                "Saved on this device. It will be added to your diary " +
                "when you're back online. 💜",
                "success"
            );
        });
    }

    function saveTextOnly() {
        var title = titleInput.value.trim();
        var content = contentInput.value.trim();

        if (!Store || !userId) {
            // No offline support in this browser: use the normal form.
            form.dataset.plain = "1";
            form.submit();
            return;
        }

        setSaving(true);

        function offlineFallback() {
            return keepOnThisDevice(title, content).then(function () {
                setSaving(false);
            }, function () {
                setSaving(false);
                say(
                    "We couldn't save your memory. Your words are still in " +
                    "the form, so please copy them somewhere safe.",
                    "error"
                );
            });
        }

        if (!navigator.onLine) {
            offlineFallback();
            return;
        }

        Store.fetchWithTimeout(apiUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-CSRFToken": csrf
            },
            body: JSON.stringify({
                client_id: clientId,
                title: title,
                content: content,
                created_at_utc: new Date().toISOString()
            })
        }, 10000).then(function (response) {

            if (response.status === 200 || response.status === 201) {
                return response.json().then(function (data) {
                    clearDraft();
                    Store.markDirty();

                    try {
                        sessionStorage.setItem(
                            "everstory.toast",
                            "Your memory has been saved successfully! 💜"
                        );
                    } catch (error) { /* ignore */ }

                    window.location.href = entryUrl(data.entry.id);
                });
            }

            if (response.status === 401) {
                // The session ended. Keep the words safe on this device.
                return keepOnThisDevice(title, content).then(function () {
                    setSaving(false);
                    say(
                        "Your session ended, so your memory was kept on this " +
                        "device. Log in again and it will be added.",
                        "error"
                    );
                });
            }

            if (response.status >= 500) {
                return offlineFallback();
            }

            return response.json().catch(function () {
                return {};
            }).then(function (data) {
                setSaving(false);
                say(
                    data.error || data.detail ||
                    "We couldn't save your memory. Please try again.",
                    "error"
                );
            });

        }, function () {
            // No connection, or the server did not answer in time.
            return offlineFallback();
        });
    }

    form.addEventListener("submit", function (event) {

        if (form.dataset.plain === "1") {
            return;
        }

        if (recorderApi && recorderApi.isRecording()) {
            event.preventDefault();
            say("Press Stop to finish your voice note first.", "error");
            return;
        }

        var hasFiles = picker.files().length > 0;

        if (mode === "edit") {
            if (!navigator.onLine) {
                event.preventDefault();
                say("Editing a memory needs an internet connection.", "error");
            }

            return;
        }

        // New memory with attachments: a normal upload, which needs the
        // connection. The draft stays until the save is confirmed.
        if (hasFiles) {
            if (!navigator.onLine) {
                event.preventDefault();
                say(
                    "Photos, voice notes and videos need an internet " +
                    "connection. Remove them to save just the text on this " +
                    "device, or try again when you're online.",
                    "error"
                );
                return;
            }

            writeDraft({ submitting: Date.now() });
            return;
        }

        // Text only: send through the sync API so it can fall back to
        // the offline queue if the network fails midway.
        event.preventDefault();

        if (!form.checkValidity()) {
            form.reportValidity();
            return;
        }

        saveTextOnly();
    });

})();
