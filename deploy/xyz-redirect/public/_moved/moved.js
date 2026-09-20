// The "Pinakes has moved" page: carry any #fragment over to the new address,
// run the two copy buttons, and remember "skip this page next time".
(function () {
  'use strict';
  document.documentElement.classList.add('js');

  var link = document.getElementById('new-url');
  var go = document.getElementById('go');
  var status = document.getElementById('copy-status');
  if (!link || !go) return;

  // A #fragment never reaches the server, so it's added to the links here.
  if (location.hash.length > 1) {
    var target = link.href.split('#')[0] + location.hash;
    link.href = target;
    go.href = target;
    var path = document.getElementById('new-path');
    if (path) path.textContent += location.hash;
  }

  function announce(message) {
    if (status) status.textContent = message;
  }

  // Older browsers, and pages shown inside another app, can refuse the
  // Clipboard API while still allowing the selection-based copy.
  function legacyCopy(text) {
    var previous = document.activeElement;
    var area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.top = '0';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    var copied = false;
    try { copied = document.execCommand('copy'); } catch (e) { copied = false; }
    document.body.removeChild(area);
    if (previous && previous.focus) previous.focus();
    return copied;
  }

  // The citation goes to the clipboard as HTML too, so the title stays
  // italic when it's pasted into a word processor.
  function copy(plain, html) {
    var clipboard = navigator.clipboard;
    var attempt;
    if (clipboard && html && window.ClipboardItem && clipboard.write) {
      var item = new ClipboardItem({
        'text/plain': new Blob([plain], { type: 'text/plain' }),
        'text/html': new Blob([html], { type: 'text/html' })
      });
      attempt = clipboard.write([item]).catch(function () { return clipboard.writeText(plain); });
    } else if (clipboard && clipboard.writeText) {
      attempt = clipboard.writeText(plain);
    } else {
      attempt = Promise.reject(new Error('Clipboard API unavailable'));
    }
    return attempt.catch(function (error) {
      if (!legacyCopy(plain)) throw error;
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-copy]'), function (button) {
    var idle = button.textContent;
    button.addEventListener('click', function () {
      var isLink = button.getAttribute('data-copy') === 'link';
      var citation = document.getElementById('citation');
      var plain = isLink ? link.href : citation.textContent.replace(/\s+/g, ' ').trim();
      var html = isLink ? null : citation.innerHTML.replace(/<(\/?)cite>/g, '<$1i>').trim();
      copy(plain, html).then(function () {
        button.textContent = 'Copied';
        announce(isLink ? 'Link copied' : 'Citation copied');
      }, function () {
        button.textContent = 'Copy failed';
        announce('Your browser blocked copying. Select the text and copy it instead.');
      }).then(function () {
        setTimeout(function () { button.textContent = idle; }, 2000);
      });
    });
  });

  // The Worker reads this cookie and sends the visitor straight through next time.
  var box = document.getElementById('skip-next');
  if (box) {
    box.checked = /(?:^|;\s*)pinakes_skip=1(?:;|$)/.test(document.cookie);
    box.addEventListener('change', function () {
      var secure = location.protocol === 'https:' ? '; Secure' : '';
      document.cookie = box.checked
        ? 'pinakes_skip=1; Max-Age=31536000; Path=/; SameSite=Lax' + secure
        : 'pinakes_skip=; Max-Age=0; Path=/; SameSite=Lax' + secure;
    });
  }
})();
