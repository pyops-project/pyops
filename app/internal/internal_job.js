// JS library: pyops-internal job launcher with a live-log modal.
//
// Usage from any internal-set view:
//     runInternalJob(url, method, params, title)
//         .then(({ ok, state, detail }) => { ... });
//
// Sequence:
//   1. POST/DELETE to the controller proxy URL — the bound service
//      enqueues a task scriptlet with drop_done=true and returns
//      {"job": <id>}.
//   2. Open a Bootstrap modal with a <pre> tail.
//   3. Poll /scriptlet/job/item?id=<id> every 500 ms; render stdout +
//      stderr; advance the state badge.
//   4. On terminal state (done / failed / cancelled / remove) enable the
//      Close button and resolve the returned promise.
//
// The modal is created lazily on first use and reused across calls. No
// dependency on the host view's markup beyond Bootstrap + jQuery being
// loaded.

(function () {
    "use strict";

    const TERMINAL_STATES = new Set(["done", "failed", "cancelled", "removed", "remove"]);
    let modalEl = null;

    function ensureModal() {
        if (modalEl) return modalEl;
        modalEl = document.createElement("div");
        modalEl.className = "modal fade";
        modalEl.setAttribute("tabindex", "-1");
        modalEl.setAttribute("data-bs-backdrop", "static");
        modalEl.setAttribute("data-bs-keyboard", "false");
        modalEl.setAttribute("data-testid", "internal-job-modal");
        modalEl.innerHTML = `
            <div class="modal-dialog modal-lg modal-dialog-scrollable">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" data-testid="internal-job-title">Operation</h5>
                        <span class="badge bg-secondary ms-2" data-testid="internal-job-state">starting…</span>
                    </div>
                    <div class="modal-body p-0">
                        <pre data-testid="internal-job-stdout" style="margin:0;padding:1rem;max-height:50vh;overflow:auto;white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:0.85rem;"></pre>
                        <pre data-testid="internal-job-stderr" style="margin:0;padding:1rem;max-height:25vh;overflow:auto;white-space:pre-wrap;color:#dc3545;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:0.85rem;"></pre>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-primary" data-bs-dismiss="modal" disabled data-testid="internal-job-close">Close</button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modalEl);
        return modalEl;
    }

    function setBadge(badge, state) {
        const classes = {
            "running":    "badge bg-primary ms-2",
            "loading":    "badge bg-primary ms-2",
            "dequeued":   "badge bg-info ms-2",
            "todo":       "badge bg-info ms-2",
            "done":       "badge bg-success ms-2",
            "failed":     "badge bg-danger ms-2",
            "cancel":     "badge bg-warning ms-2",
            "cancelling": "badge bg-warning ms-2",
            "cancelled":  "badge bg-warning ms-2",
            "remove":     "badge bg-secondary ms-2",
            "removed":    "badge bg-secondary ms-2",
        };
        badge.className = classes[state] || "badge bg-secondary ms-2";
        badge.textContent = state;
    }

    function tailAppend(pre, text) {
        if (text === null || text === undefined) return;
        const nearBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 16;
        pre.textContent = text;
        if (nearBottom) pre.scrollTop = pre.scrollHeight;
    }

    window.runInternalJob = function runInternalJob(url, method, params, title) {
        const el = ensureModal();
        const titleEl = el.querySelector('[data-testid="internal-job-title"]');
        const stateEl = el.querySelector('[data-testid="internal-job-state"]');
        const outEl = el.querySelector('[data-testid="internal-job-stdout"]');
        const errEl = el.querySelector('[data-testid="internal-job-stderr"]');
        const closeBtn = el.querySelector('[data-testid="internal-job-close"]');

        titleEl.textContent = title || "Operation";
        outEl.textContent = "";
        errEl.textContent = "";
        setBadge(stateEl, "starting");
        closeBtn.disabled = true;

        const bsModal = bootstrap.Modal.getOrCreateInstance(el);
        bsModal.show();

        const opts = { method, headers: {} };
        if (method !== "GET" && params !== null && params !== undefined) {
            opts.headers["Content-Type"] = "application/json";
            opts.body = JSON.stringify(params);
        }

        return fetch(url, opts).then(async (r) => {
            const body = await r.json().catch(() => ({}));
            if (!r.ok || !body.job) {
                tailAppend(errEl, body.error || body.detail || `HTTP ${r.status} ${r.statusText}`);
                setBadge(stateEl, "failed");
                closeBtn.disabled = false;
                return { ok: false, state: "failed", detail: body.error || body.detail };
            }
            const jobId = body.job;
            return new Promise((resolve) => {
                function poll() {
                    fetch(`/scriptlet/job/item?id=${encodeURIComponent(jobId)}`).then(async (rr) => {
                        if (!rr.ok) {
                            tailAppend(errEl, `Poll failed: ${rr.statusText}`);
                            setBadge(stateEl, "failed");
                            closeBtn.disabled = false;
                            resolve({ ok: false, state: "failed", detail: rr.statusText });
                            return;
                        }
                        const d = await rr.json();
                        setBadge(stateEl, d.state || "running");
                        tailAppend(outEl, d.stdout || "");
                        tailAppend(errEl, d.stderr || "");
                        if (TERMINAL_STATES.has(d.state)) {
                            closeBtn.disabled = false;
                            resolve({ ok: d.state === "done", state: d.state, detail: d.detail });
                            return;
                        }
                        setTimeout(poll, 500);
                    }).catch((e) => {
                        tailAppend(errEl, `Poll error: ${e}`);
                        setBadge(stateEl, "failed");
                        closeBtn.disabled = false;
                        resolve({ ok: false, state: "failed", detail: e.toString() });
                    });
                }
                poll();
            });
        }).catch((e) => {
            tailAppend(errEl, `Request failed: ${e}`);
            setBadge(stateEl, "failed");
            closeBtn.disabled = false;
            return { ok: false, state: "failed", detail: e.toString() };
        });
    };
})();
