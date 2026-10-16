// static/usage.js — the browser half of Pinakes usage statistics.
//
// The server already records every page and tool request. This adds what only
// the browser can see: that a person with a real browser loaded the page (most
// scrapers never run JavaScript), switches between tools inside Explore and
// Datastories, clicks out to an article's DOI, file exports, and time on page.
//
// No cookies, no storage, no identifiers: the page-view id below is random,
// lives only as long as the page, and the server ties events to a visitor with
// the same daily-rotating id it gives the page request itself.
(function () {
  'use strict';
  if (!navigator.sendBeacon || !window.JSON || !window.Blob) return;

  var ENDPOINT = '/api/e';
  var pv = Math.random().toString(36).slice(2, 10);
  var visibleSince = document.visibilityState === 'visible' ? Date.now() : 0;
  var engaged = 0;
  var lastHash = location.hash.slice(1);
  var left = false;

  function send(type, fields) {
    var body = { t: type, p: location.pathname, pv: pv };
    if (fields) {
      for (var k in fields) {
        if (Object.prototype.hasOwnProperty.call(fields, k) && fields[k] != null) body[k] = fields[k];
      }
    }
    try {
      navigator.sendBeacon(ENDPOINT, new Blob([JSON.stringify(body)], { type: 'application/json' }));
    } catch (e) { /* never let measurement break the page */ }
  }

  function hashChanged() {
    var h = location.hash.slice(1);
    if (h && h !== lastHash) send('nav', { h: h });
    lastHash = h;
  }

  // Explore and Datastories switch tools with history.replaceState, which does
  // not fire hashchange, so watch the history calls as well.
  ['pushState', 'replaceState'].forEach(function (name) {
    var original = history[name];
    if (typeof original !== 'function') return;
    history[name] = function () {
      var result = original.apply(this, arguments);
      try { hashChanged(); } catch (e) { /* ignore */ }
      return result;
    };
  });
  window.addEventListener('hashchange', hashChanged);

  // Outbound links, DOI links, and downloads (including the Explore panels'
  // CSV/PNG/SVG exports, which click a temporary <a download>).
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('a[href], [data-usage]') : null;
    if (!el) return;
    var tag = el.getAttribute('data-usage');
    if (tag) { send('click', { x: tag.slice(0, 80) }); return; }
    var href = el.getAttribute('href') || '';
    if (el.hasAttribute('download')) {
      var name = el.getAttribute('download') || '';
      var ext = (name.split('.').pop() || 'file').toLowerCase();
      send('dl', { x: ext.slice(0, 12) });
      return;
    }
    var url;
    try { url = new URL(href, location.href); } catch (err) { return; }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return;
    if (url.host === location.host) {
      if (url.pathname.indexOf('/export') === 0) send('dl', { x: url.searchParams.get('format') || 'bibtex' });
      return;
    }
    send('out', { x: (url.host + url.pathname).slice(0, 300) });
  }, true);

  function bank() {
    if (visibleSince) { engaged += Date.now() - visibleSince; visibleSince = 0; }
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') {
      bank();
    } else {
      visibleSince = Date.now();
    }
  });
  function leave() {
    if (left) return;
    left = true;
    bank();
    send('leave', { v: Math.min(engaged, 86400000) });
  }
  window.addEventListener('pagehide', leave);

  send('view', { h: lastHash || null });
})();
