// static/js/shared/export.js
//
// Shared export utility for every Datastories (and Explore) tool. Adds
// three buttons to a panel's toolbar:
//
//   SVG  — serialize the panel's <svg> element and download as .svg
//   PNG  — rasterize that <svg> into a <canvas>, download as .png at 2x scale
//   CSV  — call a panel-supplied dataProvider() for rows, download as .csv
//          (Excel opens .csv natively, which covers the "xls" request)
//
// API:
//   renderExportToolbar(panelId, options)
//     panelId      — id of the tab-panel <div>
//     options.svgSelector? — CSS selector for the <svg> to export, scoped
//                            to the panel. Defaults to first <svg>.
//     options.filenameStem? — basename for downloaded files. Default is the
//                             panel id with the "tab-" prefix stripped.
//     options.dataProvider? — () => Array<Object>. The first object's keys
//                             become the CSV header. Skip CSV if omitted.
//     options.csvDataProvider? — alias for dataProvider (legacy callers)

const TOOLBAR_CLASS = 'ds-export-toolbar';

export function renderExportToolbar(panelId, options) {
  options = options || {};
  const panel = document.getElementById(panelId);
  if (!panel) return;

  // If a previous toolbar exists for this panel, leave it. Re-renders are
  // idempotent — we just keep the same toolbar.
  if (panel.querySelector(':scope > .' + TOOLBAR_CLASS)) return;

  const stem = options.filenameStem ||
               panelId.replace(/^tab-/, '').replace(/^ds-/, 'datastories-');
  const svgSelector = options.svgSelector || 'svg';
  const dataProvider = options.dataProvider || options.csvDataProvider;

  const bar = document.createElement('div');
  bar.className = TOOLBAR_CLASS;
  bar.style.cssText = 'display:flex;gap:0.4rem;align-items:center;justify-content:flex-end;'
    + 'margin:0.2rem 0 0.5rem;font-size:0.78rem;';

  const label = document.createElement('span');
  label.style.cssText = 'color:#9c9890;text-transform:uppercase;letter-spacing:0.04em;font-size:0.72rem;';
  label.textContent = 'Export';
  bar.appendChild(label);

  // SVG button
  const svgBtn = _btn('SVG', () => exportSvg(panel, svgSelector, stem));
  bar.appendChild(svgBtn);

  // PNG button
  const pngBtn = _btn('PNG', () => exportPng(panel, svgSelector, stem));
  bar.appendChild(pngBtn);

  // CSV button (only if a data provider is supplied)
  if (typeof dataProvider === 'function') {
    const csvBtn = _btn('CSV', () => {
      try {
        const rows = dataProvider();
        if (!rows || rows.length === 0) {
          alert('No data to export.');
          return;
        }
        downloadCsv(rows, stem + '.csv');
      } catch (e) {
        console.error('CSV export failed:', e);
        alert('CSV export failed: ' + e.message);
      }
    });
    bar.appendChild(csvBtn);
  } else {
    const placeholder = document.createElement('span');
    placeholder.style.cssText = 'color:#c8c4bc;font-size:0.72rem;font-style:italic;';
    placeholder.textContent = 'CSV n/a';
    placeholder.title = 'This tool does not expose tabular data for CSV export.';
    bar.appendChild(placeholder);
  }

  // Insert at the top of the panel, immediately after the methodology details
  // (or after a filter bar if one is present), but above the chart-toolbar.
  const filterBar = panel.querySelector(':scope > .ds-filter-bar');
  const methodology = panel.querySelector(':scope > details.methodology-section, :scope > .methodology-section');
  const anchor = filterBar || methodology;
  if (anchor && anchor.nextSibling) {
    panel.insertBefore(bar, anchor.nextSibling);
  } else if (anchor) {
    anchor.after(bar);
  } else {
    panel.insertBefore(bar, panel.firstChild);
  }
}


function _btn(label, onclick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = label;
  b.style.cssText = 'padding:0.25rem 0.55rem;background:#fdfbf7;border:1px solid #c8c4bc;'
    + 'cursor:pointer;font-size:0.78rem;color:#5a3e28;border-radius:2px;';
  b.addEventListener('click', onclick);
  b.addEventListener('mouseenter', () => { b.style.background = '#f1ede6'; });
  b.addEventListener('mouseleave', () => { b.style.background = '#fdfbf7'; });
  return b;
}


// ── SVG export ─────────────────────────────────────────────────────────────

export function exportSvg(panel, svgSelector, stem) {
  const svg = panel.querySelector(svgSelector);
  if (!svg) { alert('No SVG to export.'); return; }
  const xml = serialiseSvg(svg);
  const blob = new Blob([xml], { type: 'image/svg+xml;charset=utf-8' });
  triggerDownload(blob, stem + '.svg');
}


// ── PNG export (SVG → canvas → blob) ───────────────────────────────────────

export function exportPng(panel, svgSelector, stem) {
  const svg = panel.querySelector(svgSelector);
  if (!svg) { alert('No SVG to export.'); return; }

  const xml = serialiseSvg(svg);
  const w = parseInt(svg.getAttribute('width')  || svg.clientWidth  || 800);
  const h = parseInt(svg.getAttribute('height') || svg.clientHeight || 600);
  const scale = 2;  // 2x resolution for crisp output

  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement('canvas');
    canvas.width  = w * scale;
    canvas.height = h * scale;
    const ctx = canvas.getContext('2d');
    // White background so dark themes don't render as transparent
    ctx.fillStyle = '#fdfbf7';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(blob => {
      if (blob) triggerDownload(blob, stem + '.png');
    }, 'image/png');
  };
  img.onerror = () => alert('PNG export failed: could not rasterise SVG. Try the SVG export instead.');
  // data: URL — works without CORS for inline SVG
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(xml);
}


function serialiseSvg(svg) {
  // Clone so we don't disturb the live SVG, and ensure the standard XML
  // namespaces are present (needed for standalone .svg files).
  const clone = svg.cloneNode(true);
  if (!clone.getAttribute('xmlns'))       clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  if (!clone.getAttribute('xmlns:xlink')) clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
  // Inline computed styles for elements that depend on CSS classes from the
  // page stylesheet (axes, etc.). We do a shallow inline of a few common
  // properties; full CSS inlining would balloon the file.
  inlineStylesShallow(svg, clone);
  const ser = new XMLSerializer();
  return '<?xml version="1.0" standalone="no"?>\n' + ser.serializeToString(clone);
}


function inlineStylesShallow(liveRoot, cloneRoot) {
  // For each element matched by axis-relevant selectors, copy a few computed
  // styles onto the clone so the exported SVG renders the same outside the
  // page's stylesheet.
  const STYLES = ['fill', 'stroke', 'stroke-width', 'font-family', 'font-size', 'opacity', 'stroke-dasharray', 'stroke-opacity', 'fill-opacity'];
  const lives  = liveRoot.querySelectorAll('*');
  const clones = cloneRoot.querySelectorAll('*');
  if (lives.length !== clones.length) return; // structure changed; bail
  for (let i = 0; i < lives.length; i++) {
    const cs = window.getComputedStyle(lives[i]);
    let styleStr = '';
    STYLES.forEach(p => {
      const v = cs.getPropertyValue(p);
      if (v && v !== 'rgba(0, 0, 0, 0)' && v !== 'normal') {
        styleStr += p + ':' + v + ';';
      }
    });
    if (styleStr) {
      const existing = clones[i].getAttribute('style') || '';
      clones[i].setAttribute('style', styleStr + existing);
    }
  }
}


// ── CSV export ─────────────────────────────────────────────────────────────

export function downloadCsv(rows, filename) {
  if (!rows || rows.length === 0) return;
  const headers = Array.from(new Set(rows.flatMap(r => Object.keys(r))));
  const escape = v => {
    if (v == null) return '';
    let s = typeof v === 'object' ? JSON.stringify(v) : String(v);
    if (s.includes('"') || s.includes(',') || s.includes('\n')) {
      s = '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  };
  const lines = [headers.join(',')];
  rows.forEach(r => {
    lines.push(headers.map(h => escape(r[h])).join(','));
  });
  // BOM so Excel reads UTF-8 correctly
  const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  triggerDownload(blob, filename);
}


// ── Generic download trigger ───────────────────────────────────────────────

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(url);
    a.remove();
  }, 100);
}
