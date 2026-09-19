"""digest.py — build and send saved-search email digests.

Called from POST /api/admin/send-digests (weekly cron) and runnable by hand:

    python digest.py --dry-run
    python digest.py

The loop is deliberately dull. For each due subscription: run the saved search
restricted to articles newer than the subscriber's watermark, skip if there is
nothing, send, advance the watermark. Everything interesting is in the failure
handling.

Three rules the loop obeys
--------------------------
**Silence when there is nothing.** Most saved searches — one journal plus one
tag — produce nothing most weeks. A "0 new articles" email every Friday is how
a useful alert becomes a filtered one.

**One bad subscriber cannot stop the run.** Every send is wrapped; a rejected
address logs and the loop moves on. Without this, subscriber #3's dead domain
silently cancels mail for everyone after them.

**A failed send does not advance the watermark.** The next run retries the same
articles. A duplicate digest is an annoyance; a permanently skipped week is
scholarship the subscriber never learns about, and neither they nor we would
ever know.
"""

from __future__ import annotations

import argparse
import html as html_mod
import logging
import os
import time
from urllib.parse import urlencode

import alerts
from db import get_articles
from notifications import send_email, email_configured

log = logging.getLogger(__name__)

SITE_URL = os.environ.get("PINAKES_SITE_URL", "https://pinakes.wacclearinghouse.org")

# Items shown in the email. Beyond this the digest links back to the filtered
# index rather than becoming a catalogue nobody scrolls.
MAX_ITEMS_PER_EMAIL = 25

# Hard ceiling on what one send may cover. A subscription that has gone unsent
# through a large backfill should not try to summarize 10,000 rows; it takes
# the newest MAX_QUERY and advances past the rest.
MAX_QUERY = 200

# Courtesy pause between sends. One worker, one SMTP connection at a time, and
# Resend rate-limits — this is not a throughput-critical path.
SEND_DELAY_SECONDS = float(os.environ.get("PINAKES_DIGEST_DELAY", "0.4"))


def alerts_enabled() -> bool:
    """Alerts ship dark. Set PINAKES_ALERTS_ENABLED=1 once the sending domain
    is verified; until then the signup form and the digest run are both off, so
    a half-configured deploy cannot mail anybody."""
    return os.environ.get("PINAKES_ALERTS_ENABLED", "0") == "1"


def filtered_index_url(filters: dict) -> str:
    """The website URL showing the same saved search — the 'and N more' target
    and the digest footer's 'see this search on Pinakes' link."""
    params = {k: v for k, v in (filters or {}).items() if v}
    return f"{SITE_URL}/?{urlencode(params, doseq=True)}" if params else f"{SITE_URL}/"


def new_articles_for(sub) -> list:
    """Articles matching the subscription that arrived after its watermark."""
    filters = alerts.subscription_filters(sub)
    return get_articles(
        min_id=sub.get("watermark_id") or 0,
        limit=MAX_QUERY,
        **filters,
    )


def _authors(article) -> str:
    return (article.get("authors") or "").strip()


def render_text(sub, articles, unsub_url, filters) -> str:
    label = sub.get("label") or alerts.describe_filters(filters)
    lines = [
        f"New in Pinakes — {label}",
        "",
        f"{len(articles)} newly indexed "
        f"{'article' if len(articles) == 1 else 'articles'} match your saved search.",
        "",
    ]
    for a in articles[:MAX_ITEMS_PER_EMAIL]:
        lines.append(f"* {a.get('title') or 'Untitled'}")
        if _authors(a):
            lines.append(f"  {_authors(a)}")
        meta = " · ".join(x for x in (a.get("journal"), a.get("pub_date")) if x)
        if meta:
            lines.append(f"  {meta}")
        lines.append(f"  {SITE_URL}/article/{a['id']}")
        lines.append("")

    remaining = len(articles) - MAX_ITEMS_PER_EMAIL
    if remaining > 0:
        lines.append(f"...and {remaining} more: {filtered_index_url(filters)}")
        lines.append("")

    lines += [
        "—",
        "You are receiving this because you subscribed to a saved search on",
        f"Pinakes ({SITE_URL}), a free index of rhetoric and composition",
        "scholarship.",
        "",
        f"Unsubscribe: {unsub_url}",
    ]
    return "\n".join(lines)


def render_html(sub, articles, unsub_url, filters) -> str:
    """Plain, table-free HTML. Mail clients mangle layout; the goal here is a
    readable list in every client, not a designed newsletter."""
    esc = html_mod.escape
    label = esc(sub.get("label") or alerts.describe_filters(filters))

    items = []
    for a in articles[:MAX_ITEMS_PER_EMAIL]:
        title = esc(a.get("title") or "Untitled")
        url = f"{SITE_URL}/article/{a['id']}"
        meta = " · ".join(
            esc(x) for x in (a.get("journal"), a.get("pub_date")) if x
        )
        authors = esc(_authors(a))
        items.append(
            f'<li style="margin:0 0 1.1em 0;">'
            f'<a href="{url}" style="color:#7a2e2e;text-decoration:none;'
            f'font-weight:600;">{title}</a>'
            + (f'<br><span style="color:#444;">{authors}</span>' if authors else "")
            + (f'<br><span style="color:#777;font-size:0.9em;">{meta}</span>'
               if meta else "")
            + "</li>"
        )

    remaining = len(articles) - MAX_ITEMS_PER_EMAIL
    more = (
        f'<p><a href="{filtered_index_url(filters)}" style="color:#7a2e2e;">'
        f"…and {remaining} more on Pinakes →</a></p>"
        if remaining > 0 else ""
    )
    count = len(articles)

    return f"""\
<div style="font-family:Georgia,'Times New Roman',serif;font-size:16px;
            line-height:1.5;color:#1a1a1a;max-width:38em;">
  <p style="font-size:0.95em;color:#777;margin:0 0 0.2em 0;">New in Pinakes</p>
  <h1 style="font-size:1.25em;margin:0 0 1em 0;">{label}</h1>
  <p>{count} newly indexed {'article' if count == 1 else 'articles'}
     match your saved search.</p>
  <ul style="list-style:none;padding:0;margin:1.5em 0;">
    {''.join(items)}
  </ul>
  {more}
  <hr style="border:none;border-top:1px solid #ddd;margin:2em 0 1em 0;">
  <p style="font-size:0.85em;color:#777;">
    You are receiving this because you subscribed to a saved search on
    <a href="{SITE_URL}" style="color:#777;">Pinakes</a>, a free index of
    rhetoric and composition scholarship.<br>
    <a href="{unsub_url}" style="color:#777;">Unsubscribe</a>
  </p>
</div>"""


def send_one(sub, dry_run=False):
    """Build and send one subscriber's digest.

    Returns (sent, article_count). `sent` is False both when there was nothing
    to send and when sending failed — the caller distinguishes via the log,
    because from the loop's perspective the correct action is the same: move on
    without advancing anything that should not advance.
    """
    articles = new_articles_for(sub)
    if not articles:
        return False, 0

    filters = alerts.subscription_filters(sub)
    max_id = max(a["id"] for a in articles)
    unsub_url = f"{SITE_URL}/alerts/unsubscribe/{sub['unsub_token']}"

    label = sub.get("label") or alerts.describe_filters(filters)
    subject = (
        f"Pinakes: {len(articles)} new "
        f"{'article' if len(articles) == 1 else 'articles'} — {label}"
    )

    if dry_run:
        log.info("[dry-run] would send %s articles to %s (sub #%s, watermark "
                 "%s → %s)", len(articles), sub["email"], sub["id"],
                 sub.get("watermark_id"), max_id)
        return True, len(articles)

    ok = send_email(
        to=sub["email"],
        subject=subject,
        body=render_text(sub, articles, unsub_url, filters),
        html=render_html(sub, articles, unsub_url, filters),
        headers={
            # One-click unsubscribe. Required by Gmail/Yahoo bulk-sender rules;
            # its absence is enough on its own to route the digest to spam.
            "List-Unsubscribe": f"<{unsub_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            "Auto-Submitted": "auto-generated",
        },
    )
    alerts.record_send(sub["id"], len(articles), max_id, ok=ok)
    if not ok:
        log.error("Digest send failed for sub #%s; watermark left at %s so the "
                  "next run retries.", sub["id"], sub.get("watermark_id"))
    return ok, len(articles)


def run(dry_run=False):
    """Send every due digest. Returns a summary dict for the caller to log."""
    if not alerts_enabled():
        log.warning("Digest run skipped: PINAKES_ALERTS_ENABLED is not 1.")
        return {"skipped": "disabled"}

    if not dry_run and not email_configured():
        # Without this guard send_email would 'succeed' by logging each digest
        # and record_send would advance every watermark — silently consuming
        # the backlog so the articles never go out once SMTP is fixed.
        log.error("Digest run aborted: SMTP is not configured. Refusing to "
                  "advance watermarks for mail that cannot be sent.")
        return {"skipped": "smtp-unconfigured"}

    purged = alerts.purge_stale_pending()
    if purged:
        log.info("Purged %s unconfirmed subscription(s).", purged)

    due = alerts.due_subscriptions()
    sent = skipped = failed = articles_total = 0

    for sub in due:
        try:
            ok, count = send_one(sub, dry_run=dry_run)
        except Exception:  # noqa: BLE001 — one subscriber must not end the run
            log.exception("Digest failed for subscription #%s", sub.get("id"))
            failed += 1
            continue
        if not ok and count == 0:
            skipped += 1
        elif ok:
            sent += 1
            articles_total += count
        else:
            failed += 1
        if not dry_run and SEND_DELAY_SECONDS:
            time.sleep(SEND_DELAY_SECONDS)

    summary = {
        "due": len(due), "sent": sent, "skipped_empty": skipped,
        "failed": failed, "articles": articles_total, "dry_run": dry_run,
    }
    log.info("Digest run complete: %s", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Send saved-search digests.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would send; change nothing.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
