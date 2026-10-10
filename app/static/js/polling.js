/* One data loop and one view loop per page. Export the same controller for tests. */
(function (root) {
    "use strict";

    function createPolling(config, env) {
        let revision = config.revision;
        let running = false;
        let reloadPending = false;
        let generation = 0;
        const timers = new Set();
        const requests = new Set();
        const interval = Math.max(100, Number(config.intervalMs) || 5000);
        const active = token => running && !reloadPending && token === generation;

        function schedule(callback, delay) {
            const timer = env.setTimeout(() => {
                timers.delete(timer);
                callback();
            }, delay);
            timers.add(timer);
        }

        function cancelWork() {
            timers.forEach(timer => env.clearTimeout(timer));
            timers.clear();
            requests.forEach(controller => controller.abort());
            requests.clear();
        }

        async function readJson(url) {
            const controller = new env.AbortController();
            requests.add(controller);
            const deadline = env.setTimeout(() => controller.abort(), 35000);
            try {
                const response = await env.fetch(url, {signal: controller.signal, cache: "no-store"});
                if (!response.ok) throw new Error("HTTP " + response.status);
                return await response.json();
            } finally {
                env.clearTimeout(deadline);
                requests.delete(controller);
            }
        }

        function reload() {
            if (!running || reloadPending) return;
            reloadPending = true;
            cancelWork();
            const indicator = env.document.getElementById("loading-indicator");
            if (indicator) indicator.style.display = "block";
            env.document.querySelectorAll("tr, .grid-row").forEach(row => row.classList.add("updated"));
            schedule(() => { if (running) env.location.reload(); }, 800);
        }

        async function pollData(token) {
            if (!active(token)) return;
            let delay = interval;
            try {
                const url = config.updatesUrl + "?revision=" + encodeURIComponent(revision) + "&category=current_race";
                const data = await readJson(url);
                if (!Number.isSafeInteger(data.revision) || typeof data.updated !== "boolean") {
                    throw new Error("Invalid update response");
                }
                if (!active(token)) return;
                revision = data.revision;
                if (data.updated) reload();
            } catch (error) {
                if (!active(token)) return;
                env.console.warn("Ошибка /check-updates:", error);
                delay = Math.max(interval, 10000);
            }
            if (active(token)) schedule(() => pollData(token), delay);
        }

        async function pollView(token) {
            if (!active(token)) return;
            let delay = interval;
            try {
                const data = await readJson(config.stateUrl);
                if (data.view !== "style1" && data.view !== "style2") throw new Error("Invalid view response");
                if (!active(token)) return;
                if (data.view !== config.view) reload();
            } catch (error) {
                if (!active(token)) return;
                env.console.warn("Ошибка /state:", error);
                delay = Math.max(interval, 5000);
            }
            if (active(token)) schedule(() => pollView(token), delay);
        }

        function stop() {
            running = false;
            generation += 1;
            cancelWork();
        }

        function start() {
            if (running) return;
            running = true;
            reloadPending = false;
            generation += 1;
            pollData(generation);
            pollView(generation);
        }

        return {start, stop};
    }

    if (typeof module !== "undefined" && module.exports) {
        module.exports = createPolling;
        return;
    }
    if (root.kartingPolling) return;
    const element = root.document.getElementById("karting-poll-config");
    if (!element) return;
    root.kartingPolling = createPolling(JSON.parse(element.textContent), root);
    root.addEventListener("pagehide", () => root.kartingPolling.stop());
    root.addEventListener("pageshow", event => { if (event.persisted) root.kartingPolling.start(); });
    root.kartingPolling.start();
})(typeof window === "undefined" ? globalThis : window);
