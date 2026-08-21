/* feed-select.js — live feed address for the "build your own" form on /feeds.
 *
 * Progressive enhancement, not the mechanism. The form is a real GET form
 * pointing at /feeds/select, which builds the same URL server-side and
 * redirects; without this script the page still works, it just costs a round
 * trip. That matters for anyone with JavaScript off, and for assistive tech
 * driving the form rather than the script.
 *
 * The code this builds must match blueprints/feeds.encode_selection exactly:
 * each journal's six-character code, sorted, concatenated. Sorting is what
 * makes the address depend on which journals were picked rather than the order
 * the boxes were ticked, so the same selection is always the same URL and the
 * edge cache sees one resource instead of many.
 */
(function () {
  'use strict';

  var form = document.getElementById('feed-builder');
  if (!form) return;

  var out = document.getElementById('fb-address');
  var count = document.getElementById('fb-count');
  var actions = document.getElementById('fb-actions');
  var copyBtn = document.getElementById('fb-copy');
  var openLink = document.getElementById('fb-open');
  var opmlLink = document.getElementById('fb-opml');
  var submit = document.getElementById('fb-submit');
  var clearBtn = document.getElementById('fb-clear');
  var site = form.getAttribute('data-site') || '';

  function selected() {
    return Array.prototype.slice
      .call(form.querySelectorAll('input[name="j"]:checked'))
      .map(function (el) { return el.value; })
      .sort();
  }

  function update() {
    var codes = selected();
    var n = codes.length;

    // Section "select all" boxes reflect their children, including the
    // half-ticked state, which is the only honest rendering of "some".
    Array.prototype.forEach.call(
      form.querySelectorAll('.fb-all'),
      function (master) {
        var kids = form.querySelectorAll(
          'input[name="j"][data-group="' + master.getAttribute('data-group') + '"]'
        );
        var on = 0;
        Array.prototype.forEach.call(kids, function (k) { if (k.checked) on++; });
        master.checked = on === kids.length && kids.length > 0;
        master.indeterminate = on > 0 && on < kids.length;
      }
    );

    if (!n) {
      count.textContent = 'No journals selected yet.';
      out.hidden = true;
      actions.hidden = true;
      if (submit) submit.disabled = true;
      if (clearBtn) clearBtn.hidden = true;
      return;
    }

    var code = codes.join('');
    var url = site + '/feed/select/' + code + '.xml';

    count.textContent =
      n === 1 ? '1 journal selected.' : n + ' journals selected.';
    out.textContent = url;
    out.hidden = false;
    actions.hidden = false;
    if (submit) submit.disabled = false;
    if (clearBtn) clearBtn.hidden = false;
    if (copyBtn) copyBtn.setAttribute('data-feed', url);
    if (openLink) openLink.href = '/feed/select/' + code;
    if (opmlLink) opmlLink.href = '/feed/select/' + code + '.opml';
  }

  form.addEventListener('change', function (ev) {
    var master = ev.target.closest ? ev.target.closest('.fb-all') : null;
    if (master) {
      var kids = form.querySelectorAll(
        'input[name="j"][data-group="' + master.getAttribute('data-group') + '"]'
      );
      Array.prototype.forEach.call(kids, function (k) { k.checked = master.checked; });
    }
    update();
  });

  if (clearBtn) {
    clearBtn.addEventListener('click', function () {
      Array.prototype.forEach.call(
        form.querySelectorAll('input[name="j"]'),
        function (el) { el.checked = false; }
      );
      update();
      // Send focus somewhere meaningful rather than leaving it on a button
      // that just vanished.
      var first = form.querySelector('input[name="j"]');
      if (first) first.focus();
    });
  }

  // The script is live, so the no-JS submit button is redundant noise; the
  // address is already on screen. Keep it in the DOM as a fallback for a
  // failed clipboard, but relabel it to say what it now does.
  if (submit) submit.textContent = 'Open the feed page';

  update();
})();
