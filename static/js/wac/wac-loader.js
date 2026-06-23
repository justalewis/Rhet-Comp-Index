// wac-loader.js — entry point for /wac. Eager-imports the panel modules (which
// register themselves), then lazy-renders each card as it scrolls into view.
import { registry } from "./wac-common.js";
import "./wac-charts.js";
import "./wac-graphics.js";
import "./wac-tables.js";

function init() {
  const cards = document.querySelectorAll(".wac-card[data-viz]");
  const seen = new Set();

  const run = (card) => {
    const viz = card.dataset.viz;
    if (seen.has(viz)) return;
    seen.add(viz);
    const fn = registry[viz];
    if (typeof fn !== "function") { console.warn("No WAC renderer for", viz); return; }
    try { Promise.resolve(fn(card)).catch(err => console.error("WAC viz error:", viz, err)); }
    catch (err) { console.error("WAC viz error:", viz, err); }
  };

  if (!("IntersectionObserver" in window)) { cards.forEach(run); return; }
  const obs = new IntersectionObserver((entries) => {
    entries.forEach(e => { if (e.isIntersecting) { run(e.target); obs.unobserve(e.target); } });
  }, { rootMargin: "250px 0px" });
  cards.forEach(c => obs.observe(c));
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
