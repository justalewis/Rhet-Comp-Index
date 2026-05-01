// static/js/viz/ds_first_spark.js — Ch 6, Tool 16: The First Spark
import { setLoading, setError, fetchJson, escapeHtml, GROUP_COLORS } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

// Cap routes shown alongside the global path so columns stay readable.
const MAX_VISIBLE_ROUTES = 3;

// Cap the visible chain length so a very long Global path doesn't make the
// SVG twice as tall as the parallel routes (which then collides with the
// table below). Articles beyond this cap are listed in the per-route
// expandable summaries underneath the chart.
const MAX_VISIBLE_PATH_LEN = 14;

// Generous per-column width — wide enough for a multi-line header without
// the SPC value crashing into the next column. The container is allowed to
// scroll horizontally if the parent panel is narrower than total width.
const MIN_COL_WIDTH = 200;

// Vertical spacing between article rows. Tall enough that labels sit clearly
// in the gap between successive nodes rather than on the connecting line.
const ROW_HEIGHT = 36;

// Reserve enough vertical space for the two-line header above the first node.
const HEADER_HEIGHT = 56;

let _filtersWired_loadDsFirstSpark = false;

let _exportWired_loadDsFirstSpark = false;

async function loadDsFirstSpark() {
  if (!_exportWired_loadDsFirstSpark) {
    renderExportToolbar('tab-ds-first-spark', { svgSelector: '#ds-fs-routes svg', dataProvider: () => (window.__dsFsData && (window.__dsFsData.routes || []).flatMap((r, ri) => (r.articles || []).map((a, ai) => Object.assign({route: ri+1, position: ai+1, spc_total: r.spc_total}, a)))) });
    _exportWired_loadDsFirstSpark = true;
  }
  if (!_filtersWired_loadDsFirstSpark) {
    renderFilterBar('tab-ds-first-spark', {  onApply: () => loadDsFirstSpark() });
    _filtersWired_loadDsFirstSpark = true;
  }
  const loading = document.getElementById('ds-fs-loading');
  if (loading) loading.style.display = 'block';
  setLoading('ds-fs-routes', 'Computing key-route SPC paths (cached after first run)…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-first-spark').toString(); return fetchJson('/api/datastories/ch6-first-spark' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsFsData = data;
    if (loading) loading.style.display = 'none';
    renderRoutes(data);
    renderTable(data);
  } catch (e) {
    if (loading) loading.style.display = 'none';
    setError('ds-fs-routes', 'Failed to load: ' + e.message);
  }
}

function renderRoutes(data) {
  const el = d3.select('#ds-fs-routes');
  el.selectAll('*').remove();
  const routes = (data.routes || []).slice(0, MAX_VISIBLE_ROUTES);
  const globalPath = data.global_path || [];
  if (!routes.length && !globalPath.length) {
    el.append('p').attr('class','explore-hint').text('No routes.');
    return;
  }

  function _trim(arr) {
    if (arr.length <= MAX_VISIBLE_PATH_LEN) return { items: arr, hidden: 0 };
    return { items: arr.slice(0, MAX_VISIBLE_PATH_LEN), hidden: arr.length - MAX_VISIBLE_PATH_LEN };
  }

  const globalTrim = _trim(globalPath);
  const allPaths = [{
    label:    'Global path',
    sublabel: 'single highest-SPC chain',
    path:     globalTrim.items,
    hidden:   globalTrim.hidden,
    color:    '#3a3026',
  }].concat(routes.map((r, i) => {
    const t = _trim(r.articles || []);
    return {
      label:    'Key route ' + (i + 1),
      sublabel: 'SPC ' + (r.spc_total || 0).toLocaleString(),
      path:     t.items,
      hidden:   t.hidden,
      color:    ['#5a3e28','#3a5a28','#a04525','#608aac','#8b6045','#b38a6a'][i % 6],
    };
  })).filter(rp => (rp.path || []).length);

  // Compute column width and the SVG width separately. If the parent panel
  // is narrower than the total of all columns, the SVG overflows and the
  // wrapping div scrolls horizontally — better than squishing.
  const parentW = el.node().clientWidth || 720;
  const colW    = Math.max(MIN_COL_WIDTH, Math.floor((parentW - 30) / allPaths.length));
  const svgW    = Math.max(parentW, colW * allPaths.length + 30);
  const maxLen  = d3.max(allPaths, p => p.path.length) || 1;
  const svgH    = HEADER_HEIGHT + maxLen * ROW_HEIGHT + 16;

  // Wrap in a horizontally-scrolling div so the parent layout never clips
  // the SVG when there are too many columns to fit.
  const wrap = el.append('div').style('overflow-x', 'auto').style('overflow-y', 'hidden');
  const svg  = wrap.append('svg').attr('width', svgW).attr('height', svgH);

  allPaths.forEach((rp, ci) => {
    const x = 15 + ci * colW + colW / 2;

    // Two-line header — title bold, SPC sub-line lighter.
    svg.append('text')
      .attr('x', x).attr('y', 18)
      .attr('text-anchor', 'middle').attr('font-size', 12)
      .attr('font-weight', 600).attr('fill', rp.color)
      .text(rp.label);
    svg.append('text')
      .attr('x', x).attr('y', 36)
      .attr('text-anchor', 'middle').attr('font-size', 10)
      .attr('fill', '#7a7268')
      .text(rp.sublabel);

    rp.path.forEach((art, i) => {
      const y = HEADER_HEIGHT + i * ROW_HEIGHT;
      const next = rp.path[i + 1];

      // Connector line to next node. Drawn first so labels paint on top.
      if (next) {
        svg.append('line')
          .attr('x1', x).attr('x2', x)
          .attr('y1', y + 8).attr('y2', y + ROW_HEIGHT - 8)
          .attr('stroke', rp.color).attr('stroke-width', 1.5).attr('opacity', 0.4);
      }

      // Node circle
      svg.append('circle')
        .attr('cx', x).attr('cy', y).attr('r', 6)
        .attr('fill', GROUP_COLORS[art.group] || rp.color)
        .attr('stroke', '#fdfbf7').attr('stroke-width', 1.5);

      // Label sits in the right-side gutter of the column so it never
      // overpaints the connector line. Anchor 'start' from x + 10.
      const lbl = art.authors ? art.authors.split(';')[0].trim().split(' ').slice(-1)[0] : '';
      const yr  = art.year || '';
      const labelText = lbl + (yr ? ' (' + yr + ')' : '');

      const a = svg.append('a').attr('href', '/article/' + art.id);
      a.append('text')
        .attr('x', x + 10).attr('y', y + 3)
        .attr('text-anchor', 'start')
        .attr('font-size', 10).attr('fill', '#3a3026')
        .text(labelText);
      a.append('title').text(art.title || '#' + art.id);
    });

    // "+N more" indicator below the last visible node when the chain was trimmed.
    if (rp.hidden > 0 && rp.path.length > 0) {
      const lastY = HEADER_HEIGHT + (rp.path.length - 1) * ROW_HEIGHT;
      svg.append('text')
        .attr('x', x).attr('y', lastY + ROW_HEIGHT - 4)
        .attr('text-anchor', 'middle').attr('font-size', 9)
        .attr('fill', '#9c9890').attr('font-style', 'italic')
        .text('+ ' + rp.hidden + ' more (see below)');
    }
  });
}

function renderTable(data) {
  const el = document.getElementById('ds-fs-table');
  const routes = data.routes || [];
  const stats  = data.stats || {};
  const hidden = Math.max(0, routes.length - MAX_VISIBLE_ROUTES);

  let html = `<div style="padding:0.5rem 0.8rem;background:#fdfbf7;border-left:3px solid #5a3e28;font-size:0.84rem;margin-bottom:0.6rem;">
    <strong>${stats.n_routes || 0}</strong> key routes through the citation DAG (${(stats.n_nodes || 0).toLocaleString()} articles, ${(stats.n_edges || 0).toLocaleString()} edges).${hidden ? ' Top ' + MAX_VISIBLE_ROUTES + ' shown above; full list below.' : ''}
  </div>`;

  routes.forEach((r, i) => {
    html += `<details style="margin:0.4rem 0;"${i === 0 ? ' open' : ''}>
      <summary style="cursor:pointer;font-weight:600;padding:0.3rem 0;">Route ${i + 1} — ${r.n_nodes} articles, SPC ${(r.spc_total || 0).toLocaleString()}</summary>
      <ol style="font-size:0.84rem;padding-left:1.2rem;">`;
    (r.articles || []).forEach(a => {
      html += `<li><a href="/article/${a.id}" style="color:#5a3e28;">${escapeHtml(a.title || '#'+a.id)}</a> <span style="color:#9c9890;">${escapeHtml(a.journal || '')} · ${a.year || '—'}</span></li>`;
    });
    html += '</ol></details>';
  });
  el.innerHTML = html;
}

window.loadDsFirstSpark = loadDsFirstSpark;
