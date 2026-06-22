// Tabs: clicking a [data-tab="X"] button activates that button and the
// matching #tab-X panel, deactivating the others within the same .card.
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-tab]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const target = btn.dataset.tab;
            const card = btn.closest(".card");
            card.querySelectorAll("[data-tab]").forEach((b) =>
                b.classList.toggle("active", b.dataset.tab === target),
            );
            card.querySelectorAll(".tab-panel").forEach((p) =>
                p.classList.toggle("active", p.id === `tab-${target}`),
            );
        });
    });
});
