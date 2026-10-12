// Shared failure display for the PyOps browser pages (window.pyopsAlert).
//
// A failed user action shows the server's message in a dismissible alert at
// the top of the page — one alert, a newer failure replaces its text — and
// is logged to the console as well:
//
//   await pyopsAlert.failed(action, response)  a non-2xx answer:
//                                               "<action>: <status> <message>"
//   pyopsAlert.error(action, e)                 a request that did not reach pyops
//
// A failed page load or background poll pops up nothing: it shows in the
// page header's status (the element `#pyops-load-status`) until the same
// load succeeds again, and is logged to the console:
//
//   await pyopsAlert.loadFailed(load, response) a non-2xx answer
//   pyopsAlert.loadError(load, e)               a request that did not reach pyops
//   pyopsAlert.loaded(load)                     the load succeeded: its status goes
//
// The message is the body's `detail` (a validation error's list: one
// "<field>: <msg>" per entry) or `error` (the controller proxy's), else the
// status text.

(function () {
    "use strict";

    async function message(response) {
        const body = await response.json().catch(() => undefined);
        let text = response.statusText;
        if (Array.isArray(body?.detail))
            text = body.detail.map((e) => `${e.loc.slice(1).join(".") || e.loc[0]}: ${e.msg}`).join("; ");
        else if (typeof body?.detail === "string")
            text = body.detail;
        else if (typeof body?.error === "string")
            text = body.error;
        return `${response.status} ${text}`;
    }

    function show(text) {
        let alert = document.getElementById("pyops-alert");
        if (!alert) {
            alert = document.createElement("div");
            alert.id = "pyops-alert";
            alert.className = "alert alert-danger alert-dismissible position-fixed top-0 start-50 translate-middle-x mt-2 w-75";
            alert.style.zIndex = 1090;
            alert.setAttribute("role", "alert");
            alert.dataset.testid = "pyops-alert";
            const textEl = document.createElement("span");
            textEl.className = "pyops-alert-text";
            const close = document.createElement("button");
            close.type = "button";
            close.className = "btn-close";
            close.setAttribute("data-bs-dismiss", "alert");
            close.setAttribute("aria-label", "Close");
            alert.append(textEl, close);
            document.body.append(alert);
        }
        alert.querySelector(".pyops-alert-text").textContent = text;
    }

    async function failed(action, response) {
        const text = `${action}: ${await message(response)}`;
        console.error(text);
        show(text);
    }

    function error(action, e) {
        const text = `${action}: ${e}`;
        console.error(text);
        show(text);
    }

    const loadFailures = new Map();

    function showLoadStatus() {
        const status = document.getElementById("pyops-load-status");
        status.textContent = [...loadFailures.values()].join(" · ");
        status.hidden = loadFailures.size === 0;
    }

    async function loadFailed(load, response) {
        const text = `${load}: ${await message(response)}`;
        console.error(text);
        loadFailures.set(load, text);
        showLoadStatus();
    }

    function loadError(load, e) {
        const text = `${load}: ${e}`;
        console.error(text);
        loadFailures.set(load, text);
        showLoadStatus();
    }

    function loaded(load) {
        if (loadFailures.delete(load))
            showLoadStatus();
    }

    window.pyopsAlert = Object.freeze({ failed, error, loadFailed, loadError, loaded });
})();
