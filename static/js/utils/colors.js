// static/js/utils/colors.js
//
// Warm palette + per-journal color map shared across the network
// visualisations (citation network, cocitation, bibcoupling, centrality,
// communities, journal flow). _journalColorMap is initialised once when
// this module loads, against window.ALL_JOURNALS injected by the template.

const PALETTE = [
  '#5a3e28','#8b6045','#b38a6a','#c9a882','#d4bc99',
  '#3a5a28','#456b40','#6b8f60','#8aac82','#b2ccaa',
  '#28445a','#405c78','#608aac','#82aacc','#aaccdd',
  '#5a2840','#7a4560','#ac6882','#cc8aaa','#ddaac4',
  '#5a5828','#787845','#aaaa60','#c8c880','#e0e0a8',
];


function journalColor(i) {
  return PALETTE[i % PALETTE.length];
}

// ALL_JOURNALS is injected via <script> tag in explore.html before this file loads
const _journalColorMap = {};
(window.ALL_JOURNALS || []).forEach((j, i) => { _journalColorMap[j.name] = PALETTE[i % PALETTE.length]; });

function citnetJournalColor(name) { return _journalColorMap[name] || '#9c9890'; }

export { PALETTE, journalColor, citnetJournalColor, _journalColorMap };
