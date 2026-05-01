// static/js/explore-loader.js
//
// Single entry point loaded by templates/explore.html. Imports the per-viz
// modules at DOMContentLoaded — eager rather than lazy to avoid race
// conditions where an inline `onclick=` handler fires before its module's
// `window.X = X;` assignment runs.
//
// Browser baseline: Chrome 91+, Firefox 89+, Safari 15+ (native ES module
// + dynamic import support; no bundler required).

// Eager imports — every viz module attaches its inline-handler functions
// to `window` at top level when imported.
import "./viz/timeline.js";
import "./viz/topics.js";
import "./viz/author_network.js";
import "./viz/most_cited.js";
import "./viz/citation_trends.js";
import "./viz/citation_network.js";
import "./viz/centrality.js";
import "./viz/cocitation.js";
import "./viz/bibcoupling.js";
import "./viz/journal_flow.js";
import "./viz/half_life.js";
import "./viz/communities.js";
import "./viz/main_path.js";
import "./viz/temporal_evolution.js";
import "./viz/sleeping_beauties.js";
import "./viz/institutions.js";
import "./viz/author_cocitation.js";
import "./viz/reading_path.js";

function toggleAccordion(headerBtn) {
  const section = headerBtn.closest('.accordion-section');
  const wasOpen = section.classList.contains('open');
  // Close all sections
  document.querySelectorAll('.accordion-section.open').forEach(s => {
    s.classList.remove('open');
    s.querySelector('.accordion-body').style.maxHeight = '0';
  });
  // Open this one if it wasn't already open
  if (!wasOpen) {
    section.classList.add('open');
    const body = section.querySelector('.accordion-body');
    body.style.maxHeight = body.scrollHeight + 'px';
  }
}

function openAccordionForHash(hash) {
  // Find the accordion card with matching data-hash
  const card = document.querySelector('.accordion-card[data-hash="' + hash + '"]');
  if (!card) return;
  const section = card.closest('.accordion-section');
  if (!section) return;
  // Close all, open this section
  document.querySelectorAll('.accordion-section.open').forEach(s => {
    s.classList.remove('open');
    s.querySelector('.accordion-body').style.maxHeight = '0';
  });
  section.classList.add('open');
  const body = section.querySelector('.accordion-body');
  body.style.maxHeight = body.scrollHeight + 'px';
}

function highlightAccordionCard(hash) {
  // Remove active from all cards
  document.querySelectorAll('.accordion-card.active').forEach(c => c.classList.remove('active'));
  // Activate matching card
  if (hash) {
    const card = document.querySelector('.accordion-card[data-hash="' + hash + '"]');
    if (card) card.classList.add('active');
  }
}

let topicsLoaded       = false;
let timelineLoaded     = false;
let networkLoaded      = false;
let authorcocitLoaded  = false;
let citationsLoaded    = false;
let citTrendsLoaded    = false;
let citnetLoaded       = false;
let centralityLoaded   = false;
let communitiesLoaded  = false;
let cocitationLoaded   = false;
let bibcouplingLoaded  = false;
let sleepersLoaded     = false;
let journalflowLoaded  = false;
let halflifeLoaded     = false;
let mainpathLoaded     = false;
let temporalLoaded     = false;
let readingpathLoaded  = false;
let institutionsLoaded = false;

function showTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.style.display = 'none');
  document.querySelectorAll('.explore-tab').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).style.display = 'block';
  btn.classList.add('active');
  // Update URL hash for deep-linking
  const hash = btn.getAttribute('data-hash') || name;
  history.replaceState(null, '', '#' + hash);

  if (name === 'timeline' && !timelineLoaded) {
    timelineLoaded = true;
    window.loadTimeline();
  }
  if (name === 'topics' && !topicsLoaded) {
    topicsLoaded = true;
    window.loadHeatmap();
  }
  if (name === 'network' && !networkLoaded) {
    networkLoaded = true;
    // The reload / reset / infobar-close button wiring was originally
    // inline here; it now lives in viz/author_network.js loadNetwork()
    // because it touches that module's private state (netSvgEl etc.).
    window.loadNetwork();
  }
  if (name === 'authorcocit' && !authorcocitLoaded) {
    authorcocitLoaded = true;
    window.initAuthorCocitation();
  }
  if (name === 'citations' && !citationsLoaded) {
    citationsLoaded = true;
    window.loadCitations();
  }
  if (name === 'cittrends' && !citTrendsLoaded) {
    citTrendsLoaded = true;
    window.loadCitTrends();
  }
  if (name === 'citnet' && !citnetLoaded) {
    citnetLoaded = true;
    window.loadCitationNetwork();
  }
  if (name === 'centrality' && !centralityLoaded) {
    // Don't auto-load — user clicks "Compute" to start
    centralityLoaded = true;
  }
  if (name === 'communities' && !communitiesLoaded) {
    communitiesLoaded = true;
  }
  if (name === 'cocitation' && !cocitationLoaded) {
    // Don't auto-load — user clicks "Compute" to start
    cocitationLoaded = true;
  }
  if (name === 'bibcoupling' && !bibcouplingLoaded) {
    // Don't auto-load — user clicks "Compute" to start
    bibcouplingLoaded = true;
  }
  if (name === 'sleepers' && !sleepersLoaded) {
    // Don't auto-load — user clicks "Compute" to start
    sleepersLoaded = true;
  }
  if (name === 'journalflow' && !journalflowLoaded) {
    // Don't auto-load — user clicks "Compute" to start
    journalflowLoaded = true;
  }
  if (name === 'halflife' && !halflifeLoaded) {
    // Don't auto-load — user clicks "Compute" to start
    halflifeLoaded = true;
  }
  if (name === 'mainpath' && !mainpathLoaded) {
    mainpathLoaded = true;
  }
  if (name === 'temporal' && !temporalLoaded) {
    temporalLoaded = true;
  }
  if (name === 'readingpath' && !readingpathLoaded) {
    readingpathLoaded = true;
    window.initReadingPath();
  }
  if (name === 'institutions' && !institutionsLoaded) {
    institutionsLoaded = true;
    window.loadInstitutions();
  }

  // Sync accordion state
  const activeHash = btn.getAttribute('data-hash') || name;
  highlightAccordionCard(activeHash);
  openAccordionForHash(activeHash);
}

// ── Inline-handler globals ────────────────────────────────────
window.toggleAccordion = toggleAccordion;
window.showTab = showTab;


// ── Filter persistence ────────────────────────────────────────
// Each Explore tool's filter widgets (year-from / year-to inputs, journal
// checkboxes, mode selects, etc.) are independent <input>/<select> elements
// with stable ids. Persist their values to localStorage so a refresh or
// reopened tab doesn't wipe the user's choice. Datastories already does
// this via shared/filters.js's per-panel localStorage; this is the Explore
// equivalent, lifted up to the loader so it covers every tool uniformly
// without per-tool code changes.
const _PERSIST_PREFIX = 'explore-filter:';

function persistExploreFilters() {
  document.querySelectorAll('.tab-panel').forEach(panel => {
    const panelId = panel.id;
    if (!panelId) return;
    panel.querySelectorAll('input[id], select[id]').forEach(el => {
      // Skip search boxes and one-shot triggers that shouldn't survive refresh.
      if (el.type === 'search') return;
      if (/-search$/.test(el.id) || /-go$/.test(el.id)) return;
      const storeKey = _PERSIST_PREFIX + panelId + ':' + el.id;
      // Restore
      try {
        const saved = localStorage.getItem(storeKey);
        if (saved !== null) {
          if (el.type === 'checkbox') el.checked = saved === '1';
          else el.value = saved;
        }
      } catch (e) { /* private mode / quota — ignore */ }
      // Save on change
      el.addEventListener('change', () => {
        try {
          localStorage.setItem(storeKey,
            el.type === 'checkbox' ? (el.checked ? '1' : '0') : el.value);
        } catch (e) { /* ignore */ }
      });
    });
  });
}


// ── Auto-activate tab from URL hash or ?tab= param (from explore.js end) ─
window.addEventListener('DOMContentLoaded', function () {
  // Restore filter values from localStorage BEFORE any tab loader runs so
  // the first request uses the persisted filter set, not the HTML defaults.
  persistExploreFilters();

  // Build a lookup: hash → tab name (from data-hash attributes)
  const hashMap = {};
  document.querySelectorAll('.explore-tab[data-hash]').forEach(b => {
    hashMap[b.getAttribute('data-hash')] = b;
  });

  // Check hash first
  const hash = location.hash.replace('#', '');
  if (hash) {
    // Special case: #citation-trends → cittrends tab
    const btn = hashMap[hash];
    if (btn) { btn.click(); return; }
  }

  // Fall back to ?tab= param
  const tp = new URLSearchParams(location.search).get('tab');
  if (tp) {
    const btn = Array.from(document.querySelectorAll('.explore-tab'))
                     .find(b => (b.getAttribute('onclick') || '').includes("'" + tp + "'"));
    if (btn) { btn.click(); return; }
  }

  // No hash and no ?tab= — activate the default tab (first / Author Network)
  const defaultBtn = document.querySelector('.explore-tab.active');
  if (defaultBtn) defaultBtn.click();
});
