/*
 * Everstory - seal a time capsule: quick date buttons and the
 * attachment picker.
 */
(function () {
    "use strict";

    var form = document.getElementById("capsuleForm");

    if (!form) {
        return;
    }

    var dateInput = document.getElementById("unlock_date");
    var timeInput = document.getElementById("unlock_time");
    var UI = window.EverstoryUI || {};

    window.EverstoryFiles.attach({
        input: document.getElementById("mediaInput"),
        list: document.getElementById("fileList"),
        maxFiles: 10,
        onProblem: function (message) {
            if (UI.toast) {
                UI.toast(message, "error", 6000);
            }
        }
    });

    function two(value) {
        return ("0" + value).slice(-2);
    }

    function toInputValue(date) {
        return date.getFullYear() + "-" + two(date.getMonth() + 1) + "-" +
            two(date.getDate());
    }

    // Adding months must not overflow (31 Jan + 1 month is 28/29 Feb).
    function addMonths(date, months) {
        var result = new Date(date.getTime());
        var day = result.getDate();

        result.setDate(1);
        result.setMonth(result.getMonth() + months);

        var lastDay = new Date(
            result.getFullYear(), result.getMonth() + 1, 0
        ).getDate();

        result.setDate(Math.min(day, lastDay));

        return result;
    }

    var quick = document.getElementById("quickDates");

    if (quick) {
        quick.addEventListener("click", function (event) {
            var button = event.target.closest("button");

            if (!button) {
                return;
            }

            var target = new Date();

            if (button.dataset.addDays) {
                target.setDate(target.getDate() + Number(button.dataset.addDays));
            }

            if (button.dataset.addMonths) {
                target = addMonths(target, Number(button.dataset.addMonths));
            }

            dateInput.value = toInputValue(target);

            if (!timeInput.value) {
                timeInput.value = "09:00";
            }
        });
    }

})();
