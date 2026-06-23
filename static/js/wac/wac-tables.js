// wac-tables.js — ranked-bar and table panels for the WAC dashboard.
import {
  register, getJSON, loading, showError, $, escapeHtml, fmt,
  TYPE_COLORS, TYPE_LABEL, typeLegend, ctlVal, wireControl,
} from "./wac-common.js";

const authorHref = (name) => "/author/" + encodeURIComponent(name);
const SEGS = ["articles", "chapters", "edited", "monographs"];
const SEG_COLOR = {
  articles: TYPE_COLORS["journal-article"], chapters: TYPE_COLORS["book-chapter"],
  edited: TYPE_COLORS["edited-book"], monographs: TYPE_COLORS["monograph"],
};
const SEG_LABEL = { articles: "journal articles", chapters: "chapters", edited: "edited collections", monographs: "monographs" };

function portfolioRow(d, max, valueField) {
  const total = valueField === "citations" ? d.citations : (d.total || SEGS.reduce((a, k) => a + (d[k] || 0), 0));
  const works = SEGS.reduce((a, k) => a + (d[k] || 0), 0) || 1;
  const segs = SEGS.filter(k => d[k]).map(k =>
    '<span class="wac-port-seg" style="width:' + (d[k] / works * 100) + '%;background:' + SEG_COLOR[k] +
    '" title="' + d[k] + " " + SEG_LABEL[k] + '"></span>').join("");
  const barW = (total / max * 100) || 0;
  return '<div class="wac-port-row" data-name="' + escapeHtml(d.name.toLowerCase()) + '">' +
    '<span class="name"><a href="' + authorHref(d.name) + '" target="_blank" rel="noopener">' + escapeHtml(d.name) + "</a></span>" +
    '<span class="wac-port-bar" style="width:' + Math.max(6, barW) + '%">' + segs + "</span>" +
    '<span class="tot">' + fmt(total) + "</span></div>";
}

// ── 2a. Cross-format portfolios ──
register("cross-format-authors", async (card) => {
  const applySearch = () => {
    const search = card.querySelector("#wac-author-search");
    const q = (search && search.value.trim().toLowerCase()) || "";
    $("wac-portfolios").querySelectorAll(".wac-port-row").forEach(r => {
      r.style.display = (!q || r.dataset.name.includes(q)) ? "" : "none";
    });
  };
  const draw = async () => {
    const limit = ctlVal(card, "wac-ctl-port-limit", 60);
    const minTypes = ctlVal(card, "wac-ctl-port-mintypes", 2);
    loading("wac-portfolios", "Loading portfolios…");
    let data;
    try { data = await getJSON(`/api/wac/cross-format-authors?limit=${limit}&min_types=${minTypes}`); }
    catch (e) { return showError("wac-portfolios", e.message); }
    if (!data.length) { $("wac-portfolios").innerHTML = '<div class="wac-empty">No authors meet that format threshold.</div>'; return; }
    const max = Math.max(...data.map(d => d.total), 1);
    $("wac-portfolios").innerHTML = typeLegend() + '<div class="wac-port-list">' + data.map(d => portfolioRow(d, max, "total")).join("") + "</div>";
    applySearch();
  };
  const search = card.querySelector("#wac-author-search");
  if (search && !search._wired) { search._wired = true; search.addEventListener("input", applySearch); }
  wireControl(card, "wac-ctl-port-limit", draw);
  wireControl(card, "wac-ctl-port-mintypes", draw);
  await draw();
});

// ── 2b. Prolific contributors (toggle works / citations) ──
register("prolific-authors", async (card) => {
  let mode = "works";
  const draw = async () => {
    const limit = ctlVal(card, "wac-ctl-prolific-limit", 30);
    loading("wac-prolific", "Loading…");
    let data;
    try { data = await getJSON("/api/wac/house-authors?limit=" + limit); }
    catch (e) { return showError("wac-prolific", e.message); }
    const norm = data.map(d => ({
      name: d.name, articles: d.articles, chapters: d.chapters,
      edited: d.edited_as_author, monographs: d.monographs,
      total: d.total, citations: d.citations,
    }));
    const sorted = norm.slice().sort((a, b) => mode === "works" ? b.total - a.total : b.citations - a.citations);
    const max = Math.max(...sorted.map(d => mode === "works" ? d.total : d.citations), 1);
    $("wac-prolific").innerHTML = typeLegend() + '<div class="wac-port-list">' +
      sorted.map(d => portfolioRow(d, max, mode === "works" ? "total" : "citations")).join("") + "</div>";
  };
  card.querySelectorAll("[data-sort]").forEach(btn => {
    if (btn._wired) return; btn._wired = true;
    btn.addEventListener("click", () => {
      mode = btn.dataset.sort;
      card.querySelectorAll("[data-sort]").forEach(b => b.classList.toggle("is-on", b === btn));
      draw();
    });
  });
  wireControl(card, "wac-ctl-prolific-limit", draw);
  await draw();
});

// ── 2c. Book ⇄ journal crossover ──
register("book-journal-crossover", async () => {
  loading("wac-crossover", "Loading…");
  let d;
  try { d = await getJSON("/api/wac/book-journal-crossover"); }
  catch (e) { return showError("wac-crossover", e.message); }
  const tot = d.journal_only + d.both + d.book_only || 1;
  const seg = (n, color, label) => '<div style="flex:' + n + ' 0 0;min-width:0">' +
    '<div style="height:2.4rem;background:' + color + ';border-radius:4px"></div>' +
    '<div style="font-size:0.72rem;text-align:center;margin-top:0.3rem;color:#5b5349">' + label + "<br><strong>" + fmt(n) + "</strong></div></div>";
  let html = '<div style="display:flex;gap:0.4rem;align-items:flex-start;margin-bottom:0.9rem">' +
    seg(d.journal_only, TYPE_COLORS["journal-article"], "journals only") +
    seg(d.both, "#7a4560", "both") +
    seg(d.book_only, TYPE_COLORS["edited-book"], "books only") + "</div>";
  html += '<p style="font-size:0.78rem;color:#8a8278;margin:0 0 0.5rem">' +
    Math.round(d.both / tot * 100) + "% of contributors work in both the press's journals and its books. Top crossover authors:</p>";
  html += '<table class="wac-table"><tbody>' + d.top_crossover.slice(0, 12).map(a =>
    "<tr><td><a href='" + authorHref(a.name) + "' target='_blank' rel='noopener'>" + escapeHtml(a.name) + "</a></td>" +
    "<td class='num'>" + a.articles + " art.</td><td class='num'>" + a.books + " bk.</td>" +
    "<td class='num'>" + fmt(a.citations) + " cites</td></tr>").join("") + "</tbody></table>";
  $("wac-crossover").innerHTML = html;
});

// ── 3a. Editor brokers (expandable) ──
register("editor-brokers", async (card) => {
  const draw = async () => {
  const limit = ctlVal(card, "wac-ctl-brokers-limit", 40);
  loading("wac-brokers", "Loading editors…");
  let data;
  try { data = await getJSON("/api/wac/editor-brokers?limit=" + limit); }
  catch (e) { return showError("wac-brokers", e.message); }
  const max = Math.max(...data.map(d => d.volumes), 1);
  const el = $("wac-brokers");
  el.innerHTML = '<div class="wac-bars">' + data.map((d, i) =>
    '<div class="wac-bar-row wac-broker" data-i="' + i + '" style="cursor:pointer">' +
    '<span class="lbl">' + escapeHtml(d.name) + "</span>" +
    '<span class="wac-bar-track"><div class="wac-bar-fill" style="width:' + (d.volumes / max * 100) +
    '%"><span class="wac-bar-seg" style="width:100%;background:#3a5a28"></span></div></span>' +
    '<span class="val">' + d.volumes + " vol · " + d.authors_convened + " auth</span></div>" +
    '<div class="wac-expand" id="wac-broker-' + i + '" style="display:none"></div>').join("") + "</div>";
  el.querySelectorAll(".wac-broker").forEach(row => {
    row.addEventListener("click", () => {
      const i = row.dataset.i, det = $("wac-broker-" + i), d = data[i];
      if (det.style.display === "none") {
        det.innerHTML = "<strong>" + escapeHtml(d.name) + "</strong> edited " + d.volumes +
          " collection(s), convening " + d.authors_convened + " distinct chapter authors:<ul>" +
          d.volume_list.map(v => "<li>" + escapeHtml(v.title) + (v.year ? " (" + v.year + ")" : "") +
            " — " + v.n_chapters + " chapter-authors</li>").join("") + "</ul>";
        det.style.display = "block";
      } else det.style.display = "none";
    });
  });
  };
  wireControl(card, "wac-ctl-brokers-limit", draw);
  await draw();
});

// ── 3c. Editor / author role overlap ──
register("editor-author-overlap", async () => {
  loading("wac-overlap", "Loading…");
  let d;
  try { d = await getJSON("/api/wac/editor-author-overlap"); }
  catch (e) { return showError("wac-overlap", e.message); }
  let html = '<div style="display:flex;gap:0.6rem;margin-bottom:0.7rem">' +
    '<div class="wac-stat-big"><span class="n">' + fmt(d.editors_total) + '</span><span class="l">edit</span></div>' +
    '<div class="wac-stat-big"><span class="n">' + fmt(d.both) + '</span><span class="l">do both</span></div>' +
    '<div class="wac-stat-big"><span class="n">' + fmt(d.authors_total) + '</span><span class="l">write</span></div></div>';
  html += '<p style="font-size:0.78rem;color:#8a8278;margin:0 0 0.4rem">Most active in both roles:</p>';
  html += '<table class="wac-table"><thead><tr><th>Name</th><th class="num">edited</th><th class="num">authored</th></tr></thead><tbody>' +
    d.members.slice(0, 14).map(m => "<tr><td><a href='" + authorHref(m.name) + "' target='_blank' rel='noopener'>" +
      escapeHtml(m.name) + "</a></td><td class='num'>" + m.edited + "</td><td class='num'>" + m.authored + "</td></tr>").join("") +
    "</tbody></table>";
  $("wac-overlap").innerHTML = html;
});

// ── 4a. Institution feeders ──
register("institutions", async (card) => {
  const draw = async () => {
    const limit = ctlVal(card, "wac-ctl-inst-limit", 30);
    loading("wac-institutions", "Loading institutions…");
    let data;
    try { data = await getJSON("/api/wac/institutions?limit=" + limit); }
    catch (e) { return showError("wac-institutions", e.message); }
    const max = Math.max(...data.map(d => d.works), 1);
    $("wac-institutions").innerHTML = '<div class="wac-bars">' + data.map(d =>
      '<div class="wac-bar-row"><span class="lbl">' + escapeHtml(d.institution) + "</span>" +
      '<span class="wac-bar-track"><div class="wac-bar-fill" style="width:' + (d.works / max * 100) +
      '%"><span class="wac-bar-seg" style="width:100%;background:#456b40"></span></div></span>' +
      '<span class="val" title="' + d.authors + ' distinct authors">' + d.works + "</span></div>").join("") + "</div>";
  };
  wireControl(card, "wac-ctl-inst-limit", draw);
  await draw();
});

// ── 5a. The house canon (most cited) ──
register("most-cited", async (card) => {
  const draw = async () => {
    const limit = ctlVal(card, "wac-ctl-canon-limit", 40);
    const type = ctlVal(card, "wac-canon-type", "");
    loading("wac-canon", "Loading canon…");
    let data;
    try { data = await getJSON("/api/wac/most-cited?limit=" + limit + (type ? "&type=" + type : "")); }
    catch (e) { return showError("wac-canon", e.message); }
    const max = Math.max(...data.map(d => d.cited_by), 1);
    $("wac-canon").innerHTML = '<table class="wac-table"><thead><tr><th>#</th><th>Work</th><th>Venue</th><th class="num">year</th><th class="num">cited by</th></tr></thead><tbody>' +
      data.map((d, i) => "<tr><td class='num'>" + (i + 1) + "</td>" +
        "<td><a href='" + escapeHtml(d.url) + "' target='_blank' rel='noopener'>" + escapeHtml(d.title) + "</a>" +
        (d.authors ? "<br><span style='color:#9a9189;font-size:0.74rem'>" + escapeHtml(d.authors) + "</span>" : "") +
        " <span class='wac-pill' style='background:" + (TYPE_COLORS[d.type] || "#999") + "'>" + escapeHtml(d.label) + "</span></td>" +
        "<td style='font-size:0.76rem'>" + escapeHtml(d.venue || "") + "</td>" +
        "<td class='num'>" + (d.year || "") + "</td>" +
        "<td class='num'><strong>" + fmt(d.cited_by) + "</strong></td></tr>").join("") + "</tbody></table>";
  };
  const sel = card.querySelector("#wac-canon-type");
  if (sel && !sel._wired) { sel._wired = true; sel.addEventListener("change", draw); }
  wireControl(card, "wac-ctl-canon-limit", draw);
  await draw();
});

// ── 6. Collections list + explorer ──
async function loadCollection(doi, scroll = true) {
  const el = $("wac-explorer");
  el.innerHTML = '<div class="wac-loading">Opening collection…</div>';
  let d;
  try { d = await getJSON("/api/wac/collection/" + doi); }
  catch (e) { return showError("wac-explorer", e.message); }
  let html = "<h4 style='margin:0 0 0.2rem;font-family:Georgia,serif'>" +
    "<a href='https://doi.org/" + escapeHtml(d.doi) + "' target='_blank' rel='noopener' style='color:#3a322a;text-decoration:none'>" +
    escapeHtml(d.title) + "</a></h4>" +
    "<p style='font-size:0.8rem;color:#8a8278;margin:0 0 0.7rem'>" + (d.year || "") +
    (d.editors ? " · edited by " + escapeHtml(d.editors) : "") + " · " + d.chapters.length + " chapters</p>";
  html += "<ol style='margin:0;padding-left:1.3rem;font-size:0.83rem'>" + d.chapters.map(c =>
    "<li style='margin:0.25rem 0'><a href='https://doi.org/" + escapeHtml(c.doi) + "' target='_blank' rel='noopener' style='color:#3a322a'>" +
    escapeHtml(c.title) + "</a>" + (c.authors ? " <span style='color:#9a9189'>— " + escapeHtml(c.authors) + "</span>" : "") +
    (c.cited_by ? " <span style='color:#b6afa4;font-size:0.72rem'>(" + fmt(c.cited_by) + " cites)</span>" : "") + "</li>").join("") + "</ol>";
  el.innerHTML = html;
  if (scroll) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

register("collections-list", async (card) => {
  loading("wac-collections", "Loading collections…");
  let data;
  try { data = await getJSON("/api/wac/collections"); }
  catch (e) { return showError("wac-collections", e.message); }
  if (!data.length) { $("wac-collections").innerHTML = '<div class="wac-empty">No collections.</div>'; return; }
  const render = () => {
    const sort = ctlVal(card, "wac-ctl-coll-sort", "chapters");
    const sorted = data.slice().sort((a, b) =>
      sort === "year" ? (b.year || 0) - (a.year || 0)
      : sort === "title" ? (a.title || "").localeCompare(b.title || "")
      : (b.n_chapters || 0) - (a.n_chapters || 0));
    $("wac-collections").innerHTML = sorted.map(c =>
      "<div class='wac-coll-item' data-doi='" + escapeHtml(c.doi) + "' style='padding:0.4rem 0.3rem;border-bottom:1px solid #f3efe8;cursor:pointer;font-size:0.82rem'>" +
      "<strong>" + escapeHtml(c.title) + "</strong> " +
      "<span style='color:#b6afa4'>(" + (c.year || "?") + ")</span><br>" +
      "<span style='color:#9a9189;font-size:0.76rem'>" + c.n_chapters + " chapters" +
      (c.editors ? " · ed. " + escapeHtml(c.editors) : "") + "</span></div>").join("");
    $("wac-collections").querySelectorAll(".wac-coll-item").forEach(item =>
      item.addEventListener("click", () => loadCollection(item.dataset.doi)));
  };
  render();
  wireControl(card, "wac-ctl-coll-sort", render);
  // Auto-open the largest collection so the explorer panel is never blank.
  loadCollection(data[0].doi, false);
});

register("collection-explorer", () => { /* populated by clicking a collection above */ });

// ── 7e. Spanish-language spotlight ──
register("spanish-spotlight", async () => {
  loading("wac-spanish", "Loading…");
  let d;
  try { d = await getJSON("/api/wac/spanish-spotlight"); }
  catch (e) { return showError("wac-spanish", e.message); }
  let html = "<div style='display:flex;gap:0.8rem;align-items:center;margin-bottom:0.6rem'>" +
    "<div class='wac-stat-big'><span class='n'>" + d.n_works + "</span><span class='l'>works</span></div>" +
    "<div class='wac-stat-big'><span class='n'>" + d.share_pct + "%</span><span class='l'>of the catalog</span></div></div>";
  html += "<p style='font-size:0.8rem;color:#5b5349;margin:0 0 0.6rem'><em>" + escapeHtml(d.journal) +
    "</em> — the press's Spanish-language venue and its reach into Latin American writing studies. (Language is inferred from the venue, not detected per work.)</p>";
  html += "<ul style='margin:0;padding-left:1.1rem;font-size:0.8rem'>" + d.works.slice(0, 30).map(w =>
    "<li style='margin:0.2rem 0'><a href='" + escapeHtml(w.url) + "' target='_blank' rel='noopener' style='color:#3a322a'>" +
    escapeHtml(w.title) + "</a> <span style='color:#b6afa4'>(" + (w.year || "?") + ")</span></li>").join("") + "</ul>";
  $("wac-spanish").innerHTML = html;
});
