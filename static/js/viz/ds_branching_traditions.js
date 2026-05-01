// static/js/viz/ds_branching_traditions.js — Ch 3, Tool 2: Branching Traditions
import { setLoading, setError, fetchJson, escapeHtml, GROUP_COLORS, GROUP_LABELS } from "./_ds_common.js";
import { renderFilterBar, filterParams } from "./_ds_filters.js";
import { renderExportToolbar } from "./_ds_export.js";

let _filtersWired_loadDsBranchingTraditions = false;

let _exportWired_loadDsBranchingTraditions = false;

async function loadDsBranchingTraditions() {
  if (!_exportWired_loadDsBranchingTraditions) {
    renderExportToolbar('tab-ds-branching-traditions', { svgSelector: '#ds-bt-container svg', dataProvider: () => ((window.__dsBranchingData && window.__dsBranchingData.groups || []).flatMap(g => (g.journals || []).map(j => Object.assign({cluster: g.label}, j)))) });
    _exportWired_loadDsBranchingTraditions = true;
  }
  if (!_filtersWired_loadDsBranchingTraditions) {
    renderFilterBar('tab-ds-branching-traditions', { scope: 'year-only', onApply: () => loadDsBranchingTraditions() });
    _filtersWired_loadDsBranchingTraditions = true;
  }
  setLoading('ds-bt-container', 'Classifying journals by tradition…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-branching-traditions').toString(); return fetchJson('/api/datastories/ch3-branching-traditions' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsBranchingData = data;
    render(data);
  } catch (e) {
    setError('ds-bt-container', 'Failed to load: ' + e.message);
  }
}

function render(data) {
  const container = d3.select('#ds-bt-container');
  container.selectAll('*').remove();

  const groups = data.groups || [];
  if (groups.length === 0) {
    setError('ds-bt-container', 'No journals to classify.');
    return;
  }

  // Top-level summary block
  const summary = container.append('div')
    .style('display', 'flex')
    .style('gap', '1rem')
    .style('margin-bottom', '1.2rem')
    .style('flex-wrap', 'wrap');

  groups.forEach(g => {
    const card = summary.append('div')
      .style('flex', '1 1 200px')
      .style('padding', '0.8rem 1rem')
      .style('border-left', '4px solid ' + (GROUP_COLORS[g.group] || '#9c9890'))
      .style('background', '#fdfbf7')
      .style('font-size', '0.84rem');
    card.append('div').style('font-weight', '600').style('color', '#3a3026').style('font-size', '0.78rem')
      .text((g.label || GROUP_LABELS[g.group] || g.group).toUpperCase());
    card.append('div').style('font-size', '1.4rem').style('font-weight', '700').style('color', '#3a3026')
      .text((g.article_count || 0).toLocaleString() + ' articles');
    card.append('div').style('color', '#7a7268').text(g.journal_count + ' journals');
  });

  // Per-journal table grouped by tradition
  groups.forEach(g => {
    const wrap = container.append('div').style('margin-bottom', '1.4rem');
    wrap.append('h4').attr('class', 'methodology-heading').style('color', GROUP_COLORS[g.group] || '#3a3026')
      .text(g.label || GROUP_LABELS[g.group] || g.group);
    const tbl = wrap.append('table')
      .attr('class', 'ds-table')
      .style('width', '100%')
      .style('border-collapse', 'collapse')
      .style('font-size', '0.84rem');
    const thead = tbl.append('thead').append('tr');
    ['Journal', 'Articles', 'Earliest', 'Latest'].forEach(h => thead.append('th')
      .style('border-bottom', '2px solid #e8e4de').style('padding', '0.4rem 0.5rem').style('text-align', 'left').text(h));
    const tbody = tbl.append('tbody');
    (g.journals || []).forEach(j => {
      const row = tbody.append('tr');
      row.append('td').style('padding', '0.3rem 0.5rem').style('border-bottom', '1px solid #f1ede6').text(j.journal);
      row.append('td').style('padding', '0.3rem 0.5rem').style('border-bottom', '1px solid #f1ede6').text((j.article_count || 0).toLocaleString());
      row.append('td').style('padding', '0.3rem 0.5rem').style('border-bottom', '1px solid #f1ede6').text(j.earliest_year || '—');
      row.append('td').style('padding', '0.3rem 0.5rem').style('border-bottom', '1px solid #f1ede6').text(j.latest_year || '—');
    });
  });
}

window.loadDsBranchingTraditions = loadDsBranchingTraditions;
