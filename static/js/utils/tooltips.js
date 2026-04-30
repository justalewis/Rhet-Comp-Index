// static/js/utils/tooltips.js
//
// HTML-escaping, tooltip positioning, and the network-info-bar helpers
// shared across force-layout visualisations.

function showNetInfobar(d, neighbors) {
  const nb = neighbors[d.id] ? neighbors[d.id].size : 0;
  const bar = document.getElementById('net-infobar');
  document.getElementById('net-infobar-name').textContent = d.id;
  document.getElementById('net-infobar-detail').textContent =
    `${d.count} article${d.count !== 1 ? 's' : ''} · ${nb} co-author${nb !== 1 ? 's' : ''} in index`;
  document.getElementById('net-infobar-link').href = '/author/' + encodeURIComponent(d.id);
  bar.style.display = 'flex';
}

function clearNetInfobar() {
  document.getElementById('net-infobar').style.display = 'none';
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function positionTooltip(tipSel, event, opts) {
  const pad  = opts && opts.pad  != null ? opts.pad  : 14;   // gap between cursor and tooltip
  const vPad = opts && opts.vPad != null ? opts.vPad : -10;  // vertical offset from cursor
  const margin = 8;                          // min distance from viewport edge

  // Make sure the tooltip is visible before measuring
  const el = tipSel.node();
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const tw = rect.width  || 200;   // fallback if still hidden
  const th = rect.height || 60;

  let x = event.clientX + pad;
  let y = event.clientY + vPad;

  // Right edge: flip to left of cursor
  if (x + tw + margin > window.innerWidth) {
    x = event.clientX - tw - pad;
  }
  // Bottom edge: push up
  if (y + th + margin > window.innerHeight) {
    y = window.innerHeight - th - margin;
  }
  // Top edge: push down
  if (y < margin) {
    y = margin;
  }
  // Left edge: push right
  if (x < margin) {
    x = margin;
  }

  tipSel.style('left', x + 'px').style('top', y + 'px');
}

export { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar };
