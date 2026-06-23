// wac-graphics.js — D3 SVG panels for the WAC dashboard.
import { enableZoomPan, enableDrag } from "../shared/common.js";
import {
  register, getJSON, loading, showError, $, escapeHtml,
  TYPE_COLORS, TYPE_LABEL, palette, showTip, moveTip, hideTip, fmt,
  ctlVal, wireControl,
} from "./wac-common.js";

function dims(el, hFallback) {
  return [el.clientWidth || 680, el.clientHeight || hFallback || 440];
}

// ── 1b. Format/venue treemap ──
register("format-composition", async () => {
  const el = $("wac-treemap");
  loading("wac-treemap", "Loading catalog…");
  let d;
  try { d = await getJSON("/api/wac/format-composition"); }
  catch (e) { return showError("wac-treemap", e.message); }
  const t = { "book-chapter": 0, "edited-book": 0, "monograph": 0 };
  d.types.forEach(x => { if (x.type in t) t[x.type] = x.count; });
  const rootData = {
    name: "WAC", children: [
      { name: "Journals", children: d.journals.map(j => ({ name: j.journal, full: j.full, value: j.count, kind: "journal" })) },
      { name: "Books", children: [
        { name: "Book chapters", value: t["book-chapter"], kind: "book-chapter" },
        { name: "Edited collections", value: t["edited-book"], kind: "edited-book" },
        { name: "Monographs", value: t["monograph"], kind: "monograph" },
      ] },
    ],
  };
  el.innerHTML = "";
  const [W, H] = dims(el, 420);
  const root = d3.hierarchy(rootData).sum(x => x.value).sort((a, b) => b.value - a.value);
  d3.treemap().size([W, H]).paddingInner(1.5).paddingTop(14)(root);
  const svg = d3.select(el).append("svg").attr("width", W).attr("height", H);
  // branch headers
  svg.selectAll("text.branch").data(root.children).enter().append("text")
    .attr("x", d => d.x0 + 4).attr("y", d => d.y0 + 11)
    .style("font", "600 11px system-ui").style("fill", "#7a7064")
    .text(d => d.data.name + " · " + fmt(d.value));
  const leaf = svg.selectAll("g.leaf").data(root.leaves()).enter().append("g")
    .attr("transform", d => `translate(${d.x0},${d.y0})`);
  let ji = 0;
  leaf.append("rect")
    .attr("width", d => Math.max(0, d.x1 - d.x0)).attr("height", d => Math.max(0, d.y1 - d.y0))
    .attr("fill", d => d.data.kind === "journal" ? palette(ji++) : TYPE_COLORS[d.data.kind])
    .attr("stroke", "#fff").attr("rx", 1.5).style("cursor", "default")
    .on("mousemove", (e, d) => showTip("<strong>" + escapeHtml(d.data.full || d.data.name) + "</strong><br>" + fmt(d.value) + " works", e))
    .on("mouseout", hideTip);
  leaf.filter(d => (d.x1 - d.x0) > 46 && (d.y1 - d.y0) > 16).append("text")
    .attr("x", 4).attr("y", 13).style("font", "10px system-ui").style("fill", "#fff").style("pointer-events", "none")
    .text(d => { const w = d.x1 - d.x0; const s = d.data.name; const max = Math.floor(w / 6); return s.length > max ? s.slice(0, max - 1) + "…" : s; });
});

// ── 1c. Journal lifelines ──
register("journal-lifelines", async () => {
  const el = $("wac-lifelines");
  loading("wac-lifelines", "Loading lifelines…");
  let data;
  try { data = await getJSON("/api/wac/journal-lifelines"); }
  catch (e) { return showError("wac-lifelines", e.message); }
  if (!data || !data.length) { el.innerHTML = '<div class="wac-empty">No data.</div>'; return; }
  el.innerHTML = "";
  const [W] = dims(el, 420);
  const labelW = 132, rowH = 26, top = 18, right = 14;
  const H = top + data.length * rowH + 6;
  const yrMin = d3.min(data, d => d.year_min), yrMax = 2026;
  const x = d3.scaleLinear().domain([yrMin, yrMax]).range([labelW, W - right]);
  const svg = d3.select(el).append("svg").attr("width", W).attr("height", H);
  // decade gridlines
  for (let yr = Math.ceil(yrMin / 10) * 10; yr <= yrMax; yr += 10) {
    svg.append("line").attr("x1", x(yr)).attr("x2", x(yr)).attr("y1", top - 4).attr("y2", H - 4)
      .attr("stroke", "#efeae2");
    svg.append("text").attr("x", x(yr)).attr("y", top - 7).attr("text-anchor", "middle")
      .style("font", "9px system-ui").style("fill", "#b6afa4").text(yr);
  }
  const rows = svg.selectAll("g.lane").data(data).enter().append("g")
    .attr("transform", (d, i) => `translate(0,${top + i * rowH})`);
  rows.append("text").attr("x", labelW - 8).attr("y", rowH / 2 + 3).attr("text-anchor", "end")
    .style("font", "10px system-ui").style("fill", d => d.historical ? "#a9a299" : "#4a4239")
    .text(d => d.short.length > 22 ? d.short.slice(0, 21) + "…" : d.short);
  const maxN = d3.max(data, d => d3.max(d.counts, c => c.n)) || 1;
  const hScale = d3.scaleSqrt().domain([0, maxN]).range([1.5, rowH / 2 - 2]);
  rows.append("line").attr("x1", d => x(d.year_min)).attr("x2", d => x(d.year_max))
    .attr("y1", rowH / 2).attr("y2", rowH / 2).attr("stroke", d => d.historical ? "#d8d1c6" : "#c2a888").attr("stroke-width", 1);
  rows.each(function (d) {
    const g = d3.select(this);
    const bw = Math.max(1.4, (x(yrMax) - x(yrMin)) / (yrMax - yrMin) - 0.4);
    g.selectAll("rect").data(d.counts).enter().append("rect")
      .attr("x", c => x(c.year) - bw / 2).attr("y", c => rowH / 2 - hScale(c.n))
      .attr("width", bw).attr("height", c => hScale(c.n) * 2)
      .attr("fill", d.historical ? "#c9c0b3" : "#8b6045").attr("opacity", 0.9)
      .on("mousemove", (e, c) => showTip("<strong>" + escapeHtml(d.short) + "</strong><br>" + c.year + ": " + c.n + " articles", e))
      .on("mouseout", hideTip);
  });
  rows.filter(d => d.succeeded_by).append("text").attr("x", d => x(d.year_max) + 5).attr("y", rowH / 2 + 3)
    .style("font", "9px system-ui").style("fill", "#9c8f7a").text("⟶ succeeded");
});

// ── 2d. Author career spans (lollipop) ──
register("author-spans", async (card) => {
  const el = $("wac-spans");
  const draw = async () => {
  const minWorks = ctlVal(card, "wac-ctl-spans-min", 5);
  loading("wac-spans", "Loading careers…");
  let data;
  try { data = await getJSON("/api/wac/author-spans?min_works=" + minWorks); }
  catch (e) { return showError("wac-spans", e.message); }
  if (!data || !data.length) { el.innerHTML = '<div class="wac-empty">No data.</div>'; return; }
  data = data.slice(0, 40);
  el.innerHTML = "";
  const [W] = dims(el, 800);
  const labelW = 150, rowH = 19, top = 22, right = 16;
  const H = top + data.length * rowH + 8;
  const yrMin = d3.min(data, d => d.year_min), yrMax = d3.max(data, d => d.year_max);
  const x = d3.scaleLinear().domain([yrMin, yrMax]).range([labelW, W - right]);
  const svg = d3.select(el).append("svg").attr("width", W).attr("height", H);
  for (let yr = Math.ceil(yrMin / 10) * 10; yr <= yrMax; yr += 10) {
    svg.append("line").attr("x1", x(yr)).attr("x2", x(yr)).attr("y1", top - 4).attr("y2", H - 4).attr("stroke", "#f0ece5");
    svg.append("text").attr("x", x(yr)).attr("y", top - 8).attr("text-anchor", "middle").style("font", "9px system-ui").style("fill", "#b3a99d").text(yr);
  }
  const rows = svg.selectAll("g.sp").data(data).enter().append("g").attr("transform", (d, i) => `translate(0,${top + i * rowH})`);
  rows.append("text").attr("x", labelW - 8).attr("y", 4).attr("text-anchor", "end").style("font", "10px system-ui").style("fill", "#4a4239")
    .text(d => d.name.length > 24 ? d.name.slice(0, 23) + "…" : d.name);
  rows.append("line").attr("x1", d => x(d.year_min)).attr("x2", d => x(d.year_max)).attr("y1", 0).attr("y2", 0).attr("stroke", "#e0d8cc").attr("stroke-width", 1);
  rows.each(function (d) {
    d3.select(this).selectAll("circle").data(d.works).enter().append("circle")
      .attr("cx", w => x(w.year)).attr("cy", 0).attr("r", 3).attr("fill", w => TYPE_COLORS[w.type] || "#999").attr("opacity", 0.85)
      .on("mousemove", (e, w) => showTip("<strong>" + escapeHtml(d.name) + "</strong><br>" + w.year + " · " + (TYPE_LABEL[w.type] || w.type), e))
      .on("mouseout", hideTip);
  });
  };
  wireControl(card, "wac-ctl-spans-min", draw);
  await draw();
});

// ── shared force-graph ──
function forceGraph(containerId, data, opts) {
  const el = $(containerId);
  if (!el) return;
  el.innerHTML = "";
  if (!data.nodes || !data.nodes.length) { el.innerHTML = '<div class="wac-empty">No data.</div>'; return; }
  const [W, H] = dims(el, 460);
  const size = opts.size || (() => 5);
  const svg = d3.select(el).append("svg").attr("width", W).attr("height", H);
  const g = enableZoomPan(svg, { scaleExtent: [0.2, 6] });
  const sim = d3.forceSimulation(data.nodes)
    .force("link", d3.forceLink(data.links).id(d => d.id).distance(opts.linkDist || 42).strength(0.35))
    .force("charge", d3.forceManyBody().strength(opts.charge || -85))
    .force("center", d3.forceCenter(W / 2, H / 2))
    .force("collide", d3.forceCollide().radius(d => size(d) + 3));
  const link = g.append("g").selectAll("line").data(data.links).enter().append("line")
    .attr("stroke", "#dcd5c8").attr("stroke-opacity", 0.6).attr("stroke-width", d => Math.min(4, 0.5 + (d.value || 1) * 0.35));
  const node = g.append("g").selectAll("g").data(data.nodes).enter().append("g").style("cursor", "pointer").call(enableDrag(sim));
  node.each(function (d) {
    const sel = d3.select(this);
    if (opts.shape && opts.shape(d) === "rect") {
      const s = size(d) * 1.8;
      sel.append("rect").attr("x", -s / 2).attr("y", -s / 2).attr("width", s).attr("height", s).attr("rx", 1)
        .attr("fill", opts.color(d)).attr("stroke", "#fff").attr("stroke-width", 1);
    } else {
      sel.append("circle").attr("r", size).attr("fill", opts.color(d)).attr("stroke", "#fff").attr("stroke-width", 1.2);
    }
  });
  if (opts.labelShow) {
    node.filter(opts.labelShow).append("text").text(d => (opts.label ? opts.label(d) : d.id))
      .attr("dy", d => size(d) + 9).attr("text-anchor", "middle").style("font", "9px system-ui").style("fill", "#6b6760").style("pointer-events", "none");
  }
  node.on("mousemove", (e, d) => showTip(opts.tooltip(d), e)).on("mouseout", hideTip);
  sim.on("tick", () => {
    link.attr("x1", d => d.source.x).attr("y1", d => d.source.y).attr("x2", d => d.target.x).attr("y2", d => d.target.y);
    node.attr("transform", d => `translate(${d.x},${d.y})`);
  });
}

// ── 3b. Editor → contributor network ──
register("editor-network", async (card) => {
  const draw = async () => {
  const topN = ctlVal(card, "wac-ctl-ednet-top", 220);
  const minLinks = ctlVal(card, "wac-ctl-ednet-min", 1);
  loading("wac-editor-net", "Loading editor network…");
  let d;
  try { d = await getJSON(`/api/wac/editor-network?top_n=${topN}&min_links=${minLinks}`); }
  catch (e) { return showError("wac-editor-net", e.message); }
  const size = (n) => n.role === "author" ? 3 + Math.sqrt(n.weight || 1) : 5 + Math.sqrt(n.weight || 1) * 1.4;
  forceGraph("wac-editor-net", d, {
    size,
    shape: (n) => n.role === "author" ? "circle" : "rect",
    color: (n) => n.role === "author" ? "#b38a6a" : (n.role === "both" ? "#7a4560" : "#3a5a28"),
    labelShow: (n) => n.role !== "author" && (n.weight || 0) >= 12,
    label: (n) => n.id.split(" ").slice(-1)[0],
    tooltip: (n) => "<strong>" + escapeHtml(n.id) + "</strong><br>" +
      (n.role === "author" ? "contributor · in " + n.weight + " editor link(s)"
        : (n.role === "both" ? "editor &amp; author" : "editor") + " · links " + n.weight + " contributors"),
    charge: -75, linkDist: 36,
  });
  };
  wireControl(card, "wac-ctl-ednet-top", draw);
  wireControl(card, "wac-ctl-ednet-min", draw);
  await draw();
});

// ── 7d. Co-presence network ──
register("copresence", async (card) => {
  const draw = async () => {
  const topN = ctlVal(card, "wac-ctl-copres-top", 180);
  const minShared = ctlVal(card, "wac-ctl-copres-min", 1);
  loading("wac-copresence", "Loading co-presence…");
  let d;
  try { d = await getJSON(`/api/wac/copresence?top_n=${topN}&min_shared=${minShared}`); }
  catch (e) { return showError("wac-copresence", e.message); }
  const size = (n) => 3 + Math.sqrt(n.count || 1) * 1.6;
  forceGraph("wac-copresence", d, {
    size,
    color: () => "#8b6045",
    tooltip: (n) => "<strong>" + escapeHtml(n.id) + "</strong><br>chapters in " + n.count + " collection(s)",
    charge: -70, linkDist: 34,
  });
  };
  wireControl(card, "wac-ctl-copres-top", draw);
  wireControl(card, "wac-ctl-copres-min", draw);
  await draw();
});

// ── 4b. Institution × journal heatmap ──
register("institution-journal", async (card) => {
  const el = $("wac-inst-heatmap");
  const draw = async () => {
  const topInst = ctlVal(card, "wac-ctl-heat-top", 22);
  loading("wac-inst-heatmap", "Loading…");
  let d;
  try { d = await getJSON("/api/wac/institution-journal?top_inst=" + topInst); }
  catch (e) { return showError("wac-inst-heatmap", e.message); }
  el.innerHTML = "";
  const insts = d.institutions, jrnls = d.journals;
  if (!insts.length || !jrnls.length) { el.innerHTML = '<div class="wac-empty">No data.</div>'; return; }
  const cell = {};
  let maxV = 1;
  d.cells.forEach(c => { cell[c.institution + "||" + c.journal] = c.value; if (c.value > maxV) maxV = c.value; });
  const [W] = dims(el, 480);
  const left = 150, topM = 96, right = 12, bottom = 8;
  const cw = Math.max(14, (W - left - right) / jrnls.length);
  const ch = 16;
  const H = topM + insts.length * ch + bottom;
  const color = d3.scaleSequential(d3.interpolate("#f3ece2", "#5a3e28")).domain([0, Math.sqrt(maxV)]);
  const svg = d3.select(el).append("svg").attr("width", W).attr("height", H);
  // column headers (rotated)
  svg.selectAll("text.col").data(jrnls).enter().append("text")
    .attr("transform", (_, i) => `translate(${left + i * cw + cw / 2},${topM - 6}) rotate(-45)`)
    .style("font", "9px system-ui").style("fill", "#6b6258")
    .text(j => j.length > 16 ? j.slice(0, 15) + "…" : j);
  const rows = svg.selectAll("g.r").data(insts).enter().append("g").attr("transform", (_, i) => `translate(0,${topM + i * ch})`);
  rows.append("text").attr("x", left - 6).attr("y", ch / 2 + 3).attr("text-anchor", "end").style("font", "9px system-ui").style("fill", "#4a4239")
    .text(n => n.length > 24 ? n.slice(0, 23) + "…" : n);
  rows.each(function (inst) {
    d3.select(this).selectAll("rect").data(jrnls).enter().append("rect")
      .attr("x", (_, i) => left + i * cw).attr("y", 1).attr("width", cw - 1.5).attr("height", ch - 1.5)
      .attr("fill", j => { const v = cell[inst + "||" + j] || 0; return v ? color(Math.sqrt(v)) : "#faf7f1"; })
      .on("mousemove", (e, j) => { const v = cell[inst + "||" + j] || 0; showTip("<strong>" + escapeHtml(inst) + "</strong><br>" + escapeHtml(j) + ": " + v + " articles", e); })
      .on("mouseout", hideTip);
  });
  };
  wireControl(card, "wac-ctl-heat-top", draw);
  await draw();
});
