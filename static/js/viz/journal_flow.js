// static/js/viz/journal_flow.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


function toggleAllJflowJournals(master) {
  document.querySelectorAll('.jflow-journal-check').forEach(c => c.checked = master.checked);
  updateJflowJournalCount();
}

function updateJflowJournalCount() {
  const checked = document.querySelectorAll('.jflow-journal-check:checked').length;
  const total   = document.querySelectorAll('.jflow-journal-check').length;
  document.getElementById('jflow-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}/${total})`;
  document.getElementById('jflow-check-all').checked = (checked === total);
}

const JFLOW_ABBREV = {
  'College Composition and Communication': 'CCC',
  'College English': 'CE',
  'Written Communication': 'WC',
  'Rhetoric Society Quarterly': 'RSQ',
  'Rhetoric Review': 'RR',
  'Technical Communication Quarterly': 'TCQ',
  'Research in the Teaching of English': 'RTE',
  'Journal of Business and Technical Communication': 'JBTC',
  'Journal of Technical Writing and Communication': 'JTWC',
  'Philosophy & Rhetoric': 'P&R',
  'Rhetoric & Public Affairs': 'R&PA',
  'Teaching English in the Two-Year College': 'TETYC',
  'Pedagogy': 'Pedagogy',
  'Community Literacy Journal': 'CLJ',
  'Assessing Writing': 'AW',
  'Business and Professional Communication Quarterly': 'BPCQ',
  'Computers and Composition': 'C&C',
  'Composition Studies': 'CS',
  'Composition Forum': 'CF',
  'Literacy in Composition Studies': 'LiCS',
  'Advances in the History of Rhetoric': 'AHR',
  'Journal of Writing Analytics': 'JWA',
  'Communication Design Quarterly': 'CDQ',
  'Communication Design Quarterly Review': 'CDQR',
  'Rhetoric of Health and Medicine': 'RHM',
  'Double Helix': 'DH',
  'Poroi': 'Poroi',
  'Peitho': 'Peitho',
  'Enculturation': 'Enc',
  'The WAC Journal': 'WAC J',
  'Across the Disciplines': 'AtD',
  'Writing Center Journal': 'WCJ',
  'The Peer Review': 'TPR',
  'Kairos: A Journal of Rhetoric, Technology, and Pedagogy': 'Kairos',
  'KB Journal: The Journal of the Kenneth Burke Society': 'KB J',
  'Present Tense: A Journal of Rhetoric in Society': 'PT',
  'Reflections: A Journal of Community-Engaged Writing and Rhetoric': 'Reflections',
  'Journal of Multimodal Rhetorics': 'JMR',
  'Basic Writing e-Journal': 'BWe-J',
  'Writing on the Edge': 'WoE',
  'Praxis: A Writing Center Journal': 'Praxis',
  'Pre/Text': 'Pre/Text',
  'Prompt: A Journal of Academic Writing Assignments': 'Prompt',
  'Writing Lab Newsletter': 'WLN',
};

function jflowAbbrev(name) { return JFLOW_ABBREV[name] || name; }

async function loadJournalFlow() {
  const container = document.getElementById('jflow-container');
  container.innerHTML = '<div class="loading-msg">Computing journal citation flows\u2026</div>';
  document.getElementById('jflow-stats').textContent = '';

  // Remove old tooltips
  d3.selectAll('.jflow-tip').remove();

  const minCit   = document.getElementById('jflow-min-slider').value;
  let yearFrom = document.getElementById('jflow-year-from').value;
  let yearTo   = document.getElementById('jflow-year-to').value;
  // Auto-swap reversed year range
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
    [yearFrom, yearTo] = [yearTo, yearFrom];
  }
  const checked  = [...document.querySelectorAll('.jflow-journal-check:checked')].map(c => c.value);
  const total    = document.querySelectorAll('.jflow-journal-check').length;

  const params = new URLSearchParams({ min_citations: minCit });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/journal-flow?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load journal citation flow data.</p>';
    return;
  }

  if (!data.journals || data.journals.length === 0) {
    container.innerHTML =
      '<p class="explore-hint">No citation flows match these filters \u2014 try a lower minimum, ' +
      'or run <code>python cite_fetcher.py</code> to populate citation data.</p>';
    return;
  }

  document.getElementById('jflow-stats').textContent =
    `${data.journals.length} journals \u00b7 ${data.total_citations.toLocaleString()} total citations \u00b7 ` +
    `${data.self_citations.toLocaleString()} self-citations (${Math.round(100*data.self_citations/data.total_citations)}%)`;

  renderChordDiagram(container, data);
}

function renderChordDiagram(container, data) {
  container.innerHTML = '';

  const W = container.clientWidth || 820;
  const H = Math.min(W, 750);
  const outerRadius = Math.min(W, H) / 2 - 60;
  const innerRadius = outerRadius - 24;

  const svg = d3.select(container).append('svg')
    .attr('width', W).attr('height', H);

  const g = svg.append('g')
    .attr('transform', `translate(${W/2},${H/2})`);

  // Chord layout
  const chord = d3.chord()
    .padAngle(0.04)
    .sortSubgroups(d3.descending)
    .sortChords(d3.descending);

  const chords = chord(data.matrix);

  const arc    = d3.arc().innerRadius(innerRadius).outerRadius(outerRadius);
  const ribbon = d3.ribbon().radius(innerRadius);

  // Colour: reuse the existing journal colour map
  const color = name => citnetJournalColor(name);

  // Tooltip
  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip jflow-tip')
    .style('opacity', 0).style('pointer-events', 'none');

  // Draw ribbons (chords) first — behind arcs
  const ribbons = g.append('g')
    .attr('class', 'jflow-ribbons')
    .selectAll('path')
    .data(chords)
    .enter().append('path')
    .attr('d', ribbon)
    .style('fill', d => color(data.journals[d.source.index]))
    .style('fill-opacity', 0.55)
    .style('stroke', '#fff')
    .style('stroke-width', 0.3)
    .on('mouseover', function(event, d) {
      // Highlight this chord
      ribbons.style('fill-opacity', r => r === d ? 0.85 : 0.08);
      const src = data.journals[d.source.index];
      const tgt = data.journals[d.target.index];
      const fwd = data.matrix[d.source.index][d.target.index];
      const rev = data.matrix[d.target.index][d.source.index];
      let html;
      if (d.source.index === d.target.index) {
        html = `<strong>${jflowAbbrev(src)}</strong> self-citations: ${fwd.toLocaleString()}`;
      } else {
        const net = fwd - rev;
        const arrow = net > 0 ? '\u2192' : net < 0 ? '\u2190' : '\u2194';
        html = `<strong>${jflowAbbrev(src)}</strong> \u2192 <strong>${jflowAbbrev(tgt)}</strong>: ${fwd.toLocaleString()}<br>` +
               `<strong>${jflowAbbrev(tgt)}</strong> \u2192 <strong>${jflowAbbrev(src)}</strong>: ${rev.toLocaleString()}<br>` +
               `<span style="color:#888;">Net flow ${arrow} ${Math.abs(net).toLocaleString()}</span>`;
      }
      tip.html(html).style('opacity', 1);
      positionTooltip(tip, event);
    })
    .on('mousemove', function(event) {
      positionTooltip(tip, event);
    })
    .on('mouseout', function() {
      ribbons.style('fill-opacity', 0.55);
      tip.style('opacity', 0);
    });

  // Draw outer arcs (groups)
  const groups = g.append('g')
    .attr('class', 'jflow-groups')
    .selectAll('g')
    .data(chords.groups)
    .enter().append('g');

  groups.append('path')
    .attr('d', arc)
    .style('fill', d => color(data.journals[d.index]))
    .style('stroke', '#fff')
    .style('stroke-width', 1)
    .style('cursor', 'pointer')
    .on('mouseover', function(event, d) {
      // Highlight only chords connected to this journal
      ribbons.style('fill-opacity', r =>
        r.source.index === d.index || r.target.index === d.index ? 0.85 : 0.06);
      groups.selectAll('path').style('opacity', g =>
        g.index === d.index ? 1 : 0.3);
      const name = data.journals[d.index];
      const sent = data.matrix[d.index].reduce((a, b) => a + b, 0);
      const received = data.matrix.reduce((a, row) => a + row[d.index], 0);
      const selfCit = data.matrix[d.index][d.index];
      tip.html(
        `<strong>${name}</strong><br>` +
        `Citations sent: ${sent.toLocaleString()}<br>` +
        `Citations received: ${received.toLocaleString()}<br>` +
        `Self-citations: ${selfCit.toLocaleString()}`
      ).style('opacity', 1);
      positionTooltip(tip, event);
    })
    .on('mousemove', function(event) {
      positionTooltip(tip, event);
    })
    .on('mouseout', function() {
      ribbons.style('fill-opacity', 0.55);
      groups.selectAll('path').style('opacity', 1);
      tip.style('opacity', 0);
    });

  // Labels along arcs
  groups.append('text')
    .each(d => { d.angle = (d.startAngle + d.endAngle) / 2; })
    .attr('dy', '0.35em')
    .attr('transform', d =>
      `rotate(${(d.angle * 180 / Math.PI - 90)})` +
      `translate(${outerRadius + 8})` +
      (d.angle > Math.PI ? 'rotate(180)' : '')
    )
    .attr('text-anchor', d => d.angle > Math.PI ? 'end' : 'start')
    .style('font-size', '0.72rem')
    .style('font-family', 'var(--font-ui, sans-serif)')
    .style('fill', 'var(--text, #3a2e1f)')
    .text(d => {
      // Hide label if arc is too small
      const arcLen = d.endAngle - d.startAngle;
      if (arcLen < 0.08) return '';
      return jflowAbbrev(data.journals[d.index]);
    });
}

// ── Inline-handler globals ────────────────────────────────────
window.toggleAllJflowJournals = toggleAllJflowJournals;
window.updateJflowJournalCount = updateJflowJournalCount;
window.loadJournalFlow = loadJournalFlow;
