/*
 * Everstory - attachment picker.
 *
 * Keeps the list of chosen files, shows them with a Remove button, and
 * checks the size limits before anything is uploaded (the server checks
 * again, so these checks are only to give a quick, friendly answer).
 *
 * Exposed as window.EverstoryFiles.attach(...)
 */
(function (global) {
    "use strict";

    var LIMITS = {
        image: 10 * 1024 * 1024,
        audio: 25 * 1024 * 1024,
        video: 50 * 1024 * 1024
    };

    function kindOf(file) {
        var type = (file.type || "").toLowerCase();

        if (type.indexOf("image/") === 0) { return "image"; }
        if (type.indexOf("audio/") === 0) { return "audio"; }
        if (type.indexOf("video/") === 0) { return "video"; }

        return null;
    }

    function formatSize(bytes) {
        if (bytes >= 1024 * 1024) {
            return (bytes / (1024 * 1024)).toFixed(1) + " MB";
        }

        return Math.max(1, Math.round(bytes / 1024)) + " KB";
    }

    function sameFile(a, b) {
        return a.name === b.name && a.size === b.size &&
            a.lastModified === b.lastModified;
    }

    function attach(options) {
        var input = options.input;
        var list = options.list;
        var maxFiles = options.maxFiles || 10;
        var onProblem = options.onProblem || function () {};
        var onChange = options.onChange || function () {};

        var files = [];

        function render() {
            list.textContent = "";

            files.forEach(function (file) {
                var item = document.createElement("li");

                var name = document.createElement("span");
                name.className = "file-name";
                name.textContent = file.name;

                var size = document.createElement("span");
                size.className = "file-size";
                size.textContent = formatSize(file.size);

                var remove = document.createElement("button");
                remove.type = "button";
                remove.textContent = "Remove";
                remove.setAttribute("aria-label", "Remove " + file.name);

                remove.addEventListener("click", function () {
                    api.remove(file);
                });

                item.appendChild(name);
                item.appendChild(size);
                item.appendChild(remove);
                list.appendChild(item);
            });
        }

        // Put the chosen files back into the real <input> so the browser
        // uploads them with the form.
        function sync() {
            try {
                var transfer = new DataTransfer();

                files.forEach(function (file) {
                    transfer.items.add(file);
                });

                input.files = transfer.files;
            } catch (error) {
                /* Old browsers: the input keeps its own selection. */
            }

            render();
            onChange(files.slice());
        }

        var api = {
            add: function (file) {
                var kind = kindOf(file);

                if (!kind) {
                    onProblem(
                        "“" + file.name + "” is not a photo, voice note or video."
                    );
                    return false;
                }

                if (file.size > LIMITS[kind]) {
                    onProblem(
                        "“" + file.name + "” is larger than the " +
                        formatSize(LIMITS[kind]) + " limit for " + kind + " files."
                    );
                    return false;
                }

                if (files.some(function (existing) {
                    return sameFile(existing, file);
                })) {
                    return false;
                }

                if (files.length >= maxFiles) {
                    onProblem("You can attach up to " + maxFiles + " files.");
                    return false;
                }

                files.push(file);
                return true;
            },

            remove: function (file) {
                files = files.filter(function (existing) {
                    return existing !== file;
                });

                sync();
            },

            commit: sync,

            files: function () {
                return files.slice();
            },

            clear: function () {
                files = [];
                sync();
            }
        };

        input.addEventListener("change", function () {
            Array.prototype.forEach.call(input.files, function (file) {
                api.add(file);
            });

            sync();
        });

        return api;
    }

    global.EverstoryFiles = { attach: attach };

})(window);
