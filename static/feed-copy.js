/* feed-copy.js — "Copy address" buttons on /feeds and /feed/<slug>.
 *
 * Any element with class .feed-copy and a data-feed attribute becomes a copy
 * button. Delegated from document, so it works for the ~56 buttons on the
 * directory page without binding 56 listeners.
 *
 * The important part is the failure path. navigator.clipboard.writeText()
 * rejects in more situations than people expect — no user activation, a
 * permissions policy, an insecure context, an older browser — and a bare
 * .then(success) leaves the button doing nothing at all when it does. A user
 * who clicks and sees no response has no way to tell whether it worked. So
 * every path ends in visible feedback: clipboard API first, execCommand
 * second, and if both fail, select the address so it can be copied by hand and
 * say so.
 */
(function () {
  'use strict';

  var RESET_MS = 1600;

  /* Changing a button's own text is invisible to a screen reader — the label
   * of a control the user just activated is not re-announced. Without this,
   * "Copied" is feedback only sighted users get. */
  var announcer = null;

  function announce(message) {
    if (!announcer) {
      announcer = document.createElement('p');
      announcer.setAttribute('role', 'status');
      announcer.className = 'visually-hidden';
      announcer.style.cssText =
        'position:absolute;width:1px;height:1px;margin:-1px;padding:0;' +
        'overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0;';
      document.body.appendChild(announcer);
    }
    // Clearing first forces a re-announcement when the same text repeats.
    announcer.textContent = '';
    window.setTimeout(function () { announcer.textContent = message; }, 50);
  }

  function flash(btn, message) {
    if (!btn.hasAttribute('data-label')) {
      btn.setAttribute('data-label', btn.textContent);
    }
    var original = btn.getAttribute('data-label');
    btn.textContent = message;
    btn.setAttribute('data-copied', '1');
    window.setTimeout(function () {
      btn.textContent = original;
      btn.removeAttribute('data-copied');
    }, RESET_MS);
  }

  /* Off-screen textarea + execCommand. Deprecated, still the most widely
   * supported path, and it works without the clipboard permission. */
  function legacyCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-9999px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    var ok = false;
    try {
      ok = document.execCommand('copy');
    } catch (e) {
      ok = false;
    }
    document.body.removeChild(ta);
    return ok;
  }

  /* Last resort: put the address on screen under the user's cursor as a
   * selection, so Ctrl+C finishes the job manually. */
  function selectPrintedAddress(btn) {
    var container = btn.closest('.fl-url, .fb-summary, .feeds-panel, li');
    var code = container && container.querySelector('code');
    if (!code || !window.getSelection || !document.createRange) return false;
    var range = document.createRange();
    range.selectNodeContents(code);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    return true;
  }

  function fallback(btn, url) {
    if (legacyCopy(url)) {
      flash(btn, 'Copied');
      announce('Feed address copied.');
    } else if (selectPrintedAddress(btn)) {
      flash(btn, 'Press Ctrl+C');
      announce('The feed address is selected. Press Control C to copy it.');
    } else {
      flash(btn, 'Copy manually');
      announce('Copying failed. The feed address is shown on the page.');
    }
  }

  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest ? ev.target.closest('.feed-copy') : null;
    if (!btn) return;
    ev.preventDefault();

    var url = btn.getAttribute('data-feed');
    if (!url) return;

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(
        function () { flash(btn, 'Copied'); announce('Feed address copied.'); },
        function () { fallback(btn, url); }
      );
    } else {
      fallback(btn, url);
    }
  });
})();
