/* tag-feedback.js — community tag interactions on the article page.
 *
 * Two flows, both progressive enhancements over the server-rendered Topics row:
 *   1. 👍/👎 on a classifier tag  → POST /api/articles/<id>/tag-feedback
 *   2. "Suggest a topic"          → POST /api/articles/<id>/suggest-tag
 *
 * Vanilla, unobtrusive (no inline handlers), optimistic, and quiet on failure
 * (a 429 rate-limit just shows a "try again later" note). All echoed text is
 * escaped — the server returns only the normalized tag, but never trust it.
 */
(function () {
  "use strict";

  var root = document.querySelector(".topics-dd[data-article-id]");
  if (!root) return;

  var articleId = root.getAttribute("data-article-id");
  var msgEl = root.querySelector(".tag-suggest-msg");

  function esc(s) {
    return (s == null ? "" : String(s)).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function showMsg(html, kind) {
    if (!msgEl) return;
    msgEl.innerHTML = html;
    msgEl.className = "tag-suggest-msg" + (kind ? " is-" + kind : "");
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        return { ok: resp.ok, status: resp.status, data: data };
      });
    });
  }

  function rateLimited(status) { return status === 429; }

  // ── 1. Tag feedback (👍/👎) ──────────────────────────────────────────────
  root.addEventListener("click", function (ev) {
    var btn = ev.target.closest(".tag-vote-btn");
    if (!btn || !root.contains(btn)) return;

    var chip = btn.closest(".topic-chip");
    if (!chip) return;
    var tag = chip.getAttribute("data-tag");
    var vote = parseInt(btn.getAttribute("data-vote"), 10);
    var group = chip.querySelector(".tag-vote");

    if (group) group.classList.add("is-busy");
    postJson("/api/articles/" + articleId + "/tag-feedback", { tag: tag, vote: vote })
      .then(function (r) {
        if (group) group.classList.remove("is-busy");
        if (r.ok) {
          // Reflect the chosen vote; clear the sibling.
          chip.querySelectorAll(".tag-vote-btn").forEach(function (b) {
            b.classList.toggle("is-active", b === btn);
            b.setAttribute("aria-pressed", b === btn ? "true" : "false");
          });
          showMsg("Thanks — noted your feedback on &ldquo;" + esc(tag) + "&rdquo;.", "ok");
        } else if (rateLimited(r.status)) {
          showMsg("You&rsquo;re voting quickly — please try again in a little while.", "warn");
        } else {
          showMsg(esc((r.data && r.data.error) || "Couldn&rsquo;t record that just now."), "warn");
        }
      })
      .catch(function () {
        if (group) group.classList.remove("is-busy");
        showMsg("Network hiccup — your feedback didn&rsquo;t go through.", "warn");
      });
  });

  // ── 2. Suggest a topic ───────────────────────────────────────────────────
  var toggle = root.querySelector(".tag-suggest-toggle");
  var form = root.querySelector(".tag-suggest-form");
  var input = root.querySelector(".tag-suggest-input");

  if (toggle && form) {
    toggle.addEventListener("click", function () {
      var open = form.hasAttribute("hidden");
      if (open) { form.removeAttribute("hidden"); } else { form.setAttribute("hidden", ""); }
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open && input) input.focus();
    });
  }

  // What each backend status means for the reader.
  var SUGGEST_MSG = {
    pending: ["Thanks! Your topic is queued for review and will appear once approved.", "ok"],
    bumped:  ["Thanks! Someone suggested that too — we&rsquo;ve noted the extra support.", "ok"],
    exists:  ["That topic is already on this article.", "warn"],
    approved:["That topic was already suggested and approved — it should be showing above.", "warn"],
    rejected:["That topic was suggested before and wasn&rsquo;t a fit for this article.", "warn"]
  };

  if (form && input) {
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var tag = input.value.trim();
      if (!tag) { showMsg("Type a topic to suggest.", "warn"); input.focus(); return; }

      var submitBtn = form.querySelector(".tag-suggest-submit");
      if (submitBtn) submitBtn.disabled = true;
      showMsg("Sending&hellip;", null);

      postJson("/api/articles/" + articleId + "/suggest-tag", { tag: tag })
        .then(function (r) {
          if (submitBtn) submitBtn.disabled = false;
          if (r.ok) {
            var m = SUGGEST_MSG[(r.data && r.data.status)] || SUGGEST_MSG.pending;
            showMsg(m[0], m[1]);
            input.value = "";
          } else if (rateLimited(r.status)) {
            showMsg("Thanks for the enthusiasm! You&rsquo;ve suggested several — please try again later.", "warn");
          } else {
            showMsg(esc((r.data && r.data.error) || "Couldn&rsquo;t submit that topic."), "warn");
          }
        })
        .catch(function () {
          if (submitBtn) submitBtn.disabled = false;
          showMsg("Network hiccup — your suggestion didn&rsquo;t go through.", "warn");
        });
    });
  }
})();
