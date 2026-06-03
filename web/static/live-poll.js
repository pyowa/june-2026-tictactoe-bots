(function () {
    const script = document.currentScript;
    const targetId = script.dataset.target;
    const interval = parseInt(script.dataset.interval, 10) || 2000;

    async function refresh() {
        if (document.hidden) return;
        try {
            const res = await fetch(window.location.href, {
                headers: { "X-Requested-With": "live-poll" },
                cache: "no-store",
            });
            if (!res.ok) return;
            const html = await res.text();
            const doc = new DOMParser().parseFromString(html, "text/html");
            const next = doc.getElementById(targetId);
            const current = document.getElementById(targetId);
            if (next && current) current.innerHTML = next.innerHTML;
        } catch (_) {
            // ignore — try again next tick
        }
    }

    setInterval(refresh, interval);
})();
