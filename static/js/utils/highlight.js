// static/js/utils/highlight.js
//
// applyHighlight / clearHighlight: shared neighbour-highlight pattern used
// by the force-layout visualisations.

function applyHighlight(id, neighbors, nodeGroup, linkSel) {
  const nb = neighbors[id] || new Set();
  nodeGroup.style('opacity', d => (d.id === id || nb.has(d.id)) ? 1 : 0.12);
  linkSel.style('opacity', l => {
    const s = typeof l.source === 'object' ? l.source.id : l.source;
    const t = typeof l.target === 'object' ? l.target.id : l.target;
    return (s === id || t === id) ? 0.8 : 0.05;
  }).style('stroke', l => {
    const s = typeof l.source === 'object' ? l.source.id : l.source;
    const t = typeof l.target === 'object' ? l.target.id : l.target;
    return (s === id || t === id) ? '#8b6340' : '#ccc7bb';
  });
}

function clearHighlight(nodeGroup, linkSel) {
  nodeGroup.style('opacity', 1);
  linkSel.style('opacity', 0.55).style('stroke', '#ccc7bb');
}

export { applyHighlight, clearHighlight };
