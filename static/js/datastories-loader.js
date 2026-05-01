// static/js/datastories-loader.js
//
// Entry point for /datastories. Eager-imports every per-tool viz module
// so inline `onclick=` handlers in templates/_datastories_panels.html
// resolve before they fire.
//
// Mirrors static/js/explore-loader.js but for the chapter-organized
// Datastories tools.

import "./viz/ds_braided_path.js";
import "./viz/ds_branching_traditions.js";
import "./viz/ds_origins_frontiers.js";
import "./viz/ds_shifting_currents.js";
import "./viz/ds_speed_of_influence.js";
import "./viz/ds_border_crossers.js";
import "./viz/ds_two_way_street.js";
import "./viz/ds_shape_of_influence.js";
import "./viz/ds_long_tail.js";
import "./viz/ds_fair_ranking.js";
import "./viz/ds_shifting_canons.js";
import "./viz/ds_reach_of_citation.js";
import "./viz/ds_inside_outside.js";
import "./viz/ds_communities_time.js";
import "./viz/ds_walls_bridges.js";
import "./viz/ds_first_spark.js";
import "./viz/ds_shared_foundations.js";
import "./viz/ds_two_maps.js";
import "./viz/ds_books_everyone_reads.js";
import "./viz/ds_uneven_debts.js";
import "./viz/ds_solo_to_squad.js";
import "./viz/ds_academic_lineages.js";
import "./viz/ds_lasting_partnerships.js";
import "./viz/ds_prince_network.js";
import "./viz/ds_disciplinary_calendar.js";
import "./viz/ds_unread_canon.js";


// ── Tool dispatch table ───────────────────────────────────────
// One entry per tool: tab name (matches the showTab arg and the
// tab-panel id suffix), the loader window function, and a
// "loaded" flag so the loader runs once per tab visit.

const TOOLS = {
  "ds-braided-path":          { loader: "loadDsBraidedPath",          loaded: false },
  "ds-branching-traditions":  { loader: "loadDsBranchingTraditions",  loaded: false },
  "ds-origins-frontiers":     { loader: "loadDsOriginsFrontiers",     loaded: false },
  "ds-shifting-currents":     { loader: "loadDsShiftingCurrents",     loaded: false },
  "ds-speed-of-influence":    { loader: "loadDsSpeedOfInfluence",     loaded: false },
  "ds-border-crossers":       { loader: "loadDsBorderCrossers",       loaded: false },
  "ds-two-way-street":        { loader: "loadDsTwoWayStreet",         loaded: false },
  "ds-shape-of-influence":    { loader: "loadDsShapeOfInfluence",     loaded: false },
  "ds-long-tail":             { loader: "loadDsLongTail",             loaded: false },
  "ds-fair-ranking":          { loader: "loadDsFairRanking",          loaded: false },
  "ds-shifting-canons":       { loader: "loadDsShiftingCanons",       loaded: false },
  "ds-reach-of-citation":     { loader: "loadDsReachOfCitation",      loaded: false },
  "ds-inside-outside":        { loader: "loadDsInsideOutside",        loaded: false },
  "ds-communities-time":      { loader: "loadDsCommunitiesTime",      loaded: false },
  "ds-walls-bridges":         { loader: "loadDsWallsBridges",         loaded: false },
  "ds-first-spark":           { loader: "loadDsFirstSpark",           loaded: false },
  "ds-shared-foundations":    { loader: "loadDsSharedFoundations",    loaded: false },
  "ds-two-maps":              { loader: "loadDsTwoMaps",              loaded: false },
  "ds-books-everyone-reads":  { loader: "loadDsBooksEveryoneReads",   loaded: false },
  "ds-uneven-debts":          { loader: "loadDsUnevenDebts",          loaded: false },
  "ds-solo-to-squad":         { loader: "loadDsSoloToSquad",          loaded: false },
  "ds-academic-lineages":     { loader: "loadDsAcademicLineages",     loaded: false },
  "ds-lasting-partnerships":  { loader: "loadDsLastingPartnerships",  loaded: false },
  "ds-prince-network":        { loader: "loadDsPrinceNetwork",        loaded: false },
  "ds-disciplinary-calendar": { loader: "loadDsDisciplinaryCalendar", loaded: false },
  "ds-unread-canon":          { loader: "loadDsUnreadCanon",          loaded: false },
};


function toggleAccordion(headerBtn) {
  const section = headerBtn.closest('.accordion-section');
  const wasOpen = section.classList.contains('open');
  document.querySelectorAll('.accordion-section.open').forEach(s => {
    s.classList.remove('open');
    s.querySelector('.accordion-body').style.maxHeight = '0';
  });
  if (!wasOpen) {
    section.classList.add('open');
    const body = section.querySelector('.accordion-body');
    body.style.maxHeight = body.scrollHeight + 'px';
  }
}

function openAccordionForHash(hash) {
  const card = document.querySelector('.accordion-card[data-hash="' + hash + '"]');
  if (!card) return;
  const section = card.closest('.accordion-section');
  if (!section) return;
  document.querySelectorAll('.accordion-section.open').forEach(s => {
    s.classList.remove('open');
    s.querySelector('.accordion-body').style.maxHeight = '0';
  });
  section.classList.add('open');
  const body = section.querySelector('.accordion-body');
  body.style.maxHeight = body.scrollHeight + 'px';
}

function highlightAccordionCard(hash) {
  document.querySelectorAll('.accordion-card.active').forEach(c => c.classList.remove('active'));
  if (hash) {
    const card = document.querySelector('.accordion-card[data-hash="' + hash + '"]');
    if (card) card.classList.add('active');
  }
}

function showTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.style.display = 'none');
  document.querySelectorAll('.explore-tab').forEach(b => b.classList.remove('active'));
  const panel = document.getElementById('tab-' + name);
  if (!panel) {
    console.warn('No panel for tab', name);
    return;
  }
  panel.style.display = 'block';
  if (btn && btn.classList) btn.classList.add('active');

  const hash = (btn && btn.getAttribute('data-hash')) || name;
  history.replaceState(null, '', '#' + hash);

  const tool = TOOLS[name];
  if (tool && !tool.loaded) {
    tool.loaded = true;
    const fn = window[tool.loader];
    if (typeof fn === 'function') {
      try { fn(); }
      catch (err) { console.error('Datastories loader error:', name, err); }
    } else {
      console.warn('Loader missing for', name, '(expected window.' + tool.loader + ')');
    }
  }

  highlightAccordionCard(hash);
  openAccordionForHash(hash);
}

function clickAccordionCard(card) {
  const tab = card.getAttribute('data-tab');
  if (!tab) return;
  const btn = document.querySelector('.explore-tab[onclick*="\'' + tab + '\'"]');
  if (btn) btn.click();
  // Scroll the now-visible panel into view
  setTimeout(() => {
    const panel = document.getElementById('tab-' + tab);
    if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 50);
}

// Wire accordion-card clicks to showTab dispatch
window.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.accordion-card[data-tab]').forEach(card => {
    card.addEventListener('click', (e) => {
      e.preventDefault();
      clickAccordionCard(card);
    });
  });

  // Auto-activate from URL hash (deep links from nav menu)
  const hash = location.hash.replace('#', '');
  if (hash) {
    const btn = document.querySelector('.explore-tab[data-hash="' + hash + '"]');
    if (btn) { btn.click(); return; }
  }
});

// ── Inline-handler globals ────────────────────────────────────
window.toggleAccordion = toggleAccordion;
window.showTab         = showTab;
