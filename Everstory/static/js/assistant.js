/*
 * Everstory - diary assistant chat.
 * Everything typed here goes only to this app's own server.
 */
(function () {
    "use strict";

    var chat = document.getElementById("chat");

    if (!chat) {
        return;
    }

    var apiUrl = chat.dataset.apiUrl;
    var log = document.getElementById("chatLog");
    var form = document.getElementById("chatForm");
    var input = document.getElementById("chatInput");
    var send = document.getElementById("chatSend");
    var suggestions = document.getElementById("chatSuggestions");

    var UI = window.EverstoryUI || {};
    var busy = false;

    function scrollDown() {
        log.scrollTop = log.scrollHeight;
    }

    // Only allow links that stay inside this app.
    function isLocalPath(url) {
        return typeof url === "string" &&
            url.charAt(0) === "/" && url.charAt(1) !== "/";
    }

    function addMessage(kind, text) {
        var message = document.createElement("div");
        message.className = "chat-message chat-" + kind;

        var bubble = document.createElement("div");
        bubble.className = "chat-bubble";
        bubble.textContent = text;

        message.appendChild(bubble);
        log.appendChild(message);
        scrollDown();

        return message;
    }

    function addBotReply(data) {
        var message = addMessage("bot", data.reply || "");

        if (data.results && data.results.length) {
            var results = document.createElement("div");
            results.className = "chat-results";

            data.results.forEach(function (item) {
                if (!isLocalPath(item.url)) {
                    return;
                }

                var link = document.createElement("a");
                link.className = "chat-result";
                link.href = item.url;

                var title = document.createElement("strong");
                title.textContent = item.title;

                var date = document.createElement("small");
                date.textContent = (item.favorite ? "♥ " : "") + (item.date || "");

                link.appendChild(title);
                link.appendChild(date);

                if (item.snippet) {
                    var snippet = document.createElement("p");
                    snippet.textContent = item.snippet;
                    link.appendChild(snippet);
                }

                results.appendChild(link);
            });

            message.appendChild(results);
        }

        if (data.related && data.related.length) {
            var chips = document.createElement("div");
            chips.className = "chat-chips";

            data.related.forEach(function (word) {
                var chip = document.createElement("button");
                chip.type = "button";
                chip.className = "chip";
                chip.textContent = word;

                chip.addEventListener("click", function () {
                    ask("find " + word);
                });

                chips.appendChild(chip);
            });

            message.appendChild(chips);
        }

        if (data.link && isLocalPath(data.link.url)) {
            var action = document.createElement("a");
            action.className = "chat-link";
            action.href = data.link.url;
            action.textContent = data.link.label + " →";
            message.appendChild(action);
        }

        scrollDown();
    }

    function setBusy(state) {
        busy = state;
        send.disabled = state;
        input.disabled = state;
    }

    function ask(text) {
        text = (text || "").trim();

        if (!text || busy) {
            return;
        }

        addMessage("user", text);
        input.value = "";

        var typing = addMessage("bot", "…");
        typing.querySelector(".chat-bubble").classList.add("chat-typing");

        setBusy(true);

        fetch(apiUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-CSRFToken": UI.csrf || ""
            },
            body: JSON.stringify({ message: text })
        }).then(function (response) {
            if (response.status === 401) {
                throw new Error("session");
            }

            if (!response.ok) {
                throw new Error("server");
            }

            return response.json();
        }).then(function (data) {
            typing.remove();
            addBotReply(data);
        }).catch(function (error) {
            typing.remove();

            var text;

            if (error.message === "session") {
                text = "Your session ended. Please log in again to keep chatting.";
            } else if (!navigator.onLine) {
                text = "You're offline right now. The assistant needs a " +
                    "connection to search your diary.";
            } else {
                text = "Sorry, something went wrong. Please try again.";
            }

            addMessage("bot", text);
        }).then(function () {
            setBusy(false);
            input.focus();
        });
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        ask(input.value);
    });

    suggestions.addEventListener("click", function (event) {
        var button = event.target.closest("button[data-suggestion]");

        if (button) {
            ask(button.dataset.suggestion);
        }
    });

    input.focus();

})();
