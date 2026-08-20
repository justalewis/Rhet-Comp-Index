"""alerts Blueprint — saved-search email alerts.

    GET  /alerts/new                     signup page, prefilled from the filters
    POST /alerts/subscribe               create a pending subscription
    GET  /alerts/verify/<token>          confirm (double opt-in)
    GET  /alerts/unsubscribe/<token>     one-click unsubscribe
    POST /alerts/unsubscribe/<token>     RFC 8058 one-click (List-Unsubscribe-Post)
    GET  /alerts/manage/<token>          view / delete a subscription

Reached from the index's filter form, which already carries every active
filter, so `formaction="/alerts/new"` hands this blueprint the exact saved
search the visitor is looking at.

Enumeration
-----------
POST /alerts/subscribe renders the same confirmation whatever happens: address
already subscribed, address new, address at its subscription cap. The endpoint
is public and the subscriber list is a list of named scholars, so any response
that varied by outcome would answer "is this person using Pinakes?" for anyone
who asked. The only visible failures are ones the submitter can see for
themselves — a malformed address, or a missing one.
"""

import logging

from flask import (
    Blueprint, request, render_template, url_for, abort, Response,
)

import alerts as alerts_mod
from auth import _client_ip
from digest import alerts_enabled
from journals import UNAVAILABLE_JOURNALS
from notifications import send_email
from rate_limit import limiter, LIMITS

log = logging.getLogger(__name__)

bp = Blueprint("alerts", __name__)


def _get_sidebar():
    from app import _get_sidebar as _impl
    return _impl()


def _page(template, **ctx):
    """Render an alerts page with the sidebar context every base.html page
    needs."""
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    from db import get_new_article_count
    return render_template(
        template,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=get_new_article_count(days=7),
        **ctx,
    )


def _require_enabled():
    """404 the whole feature while PINAKES_ALERTS_ENABLED is off.

    404 rather than 503: until the sending domain is verified there is nothing
    here, and a signup form that silently fails to mail anyone is worse than no
    signup form.
    """
    if not alerts_enabled():
        abort(404)


@bp.route("/alerts/new")
def new():
    """Signup page. Filters arrive as ordinary query params from the index's
    filter form, so the visitor can see exactly which search they are about to
    subscribe to before typing an address."""
    _require_enabled()
    filters = alerts_mod.canonicalize_filters(request.args)
    return _page(
        "alerts_new.html",
        filters=filters,
        label=alerts_mod.describe_filters(filters),
        cadences=list(alerts_mod.CADENCES),
        active_nav="feeds",
    )


@bp.route("/alerts/subscribe", methods=["POST"])
@limiter.limit(LIMITS["alert_signup"])
def subscribe():
    """Create a pending subscription and mail a confirmation link."""
    _require_enabled()

    email = (request.form.get("email") or "").strip()
    cadence = (request.form.get("cadence") or alerts_mod.DEFAULT_CADENCE).strip()

    # Honeypot. A field CSS-hidden from humans and irresistible to form bots;
    # a filled one gets the ordinary confirmation page and no email, so the bot
    # cannot tell it was rejected.
    if (request.form.get("website") or "").strip():
        log.info("Alert signup honeypot tripped (ip=%s)", _client_ip())
        return _page("alerts_sent.html", email=email)

    filters = alerts_mod.canonicalize_filters(request.form)

    if not email:
        return _page("alerts_new.html", filters=filters,
                     label=alerts_mod.describe_filters(filters),
                     cadences=list(alerts_mod.CADENCES),
                     error="Enter an email address.",
                     email=email, active_nav="feeds"), 400

    if not alerts_mod.valid_email(email):
        return _page("alerts_new.html", filters=filters,
                     label=alerts_mod.describe_filters(filters),
                     cadences=list(alerts_mod.CADENCES),
                     error="That doesn't look like an email address.",
                     email=email, active_nav="feeds"), 400

    try:
        sub_id, raw_verify, _raw_unsub, already_active = \
            alerts_mod.create_subscription(
                email, filters, cadence=cadence, ip=_client_ip(),
            )
    except alerts_mod.TooManySubscriptions:
        # Same page as success — see the module docstring on enumeration.
        log.info("Alert signup refused: subscription cap reached (ip=%s)",
                 _client_ip())
        return _page("alerts_sent.html", email=email)

    if already_active:
        log.info("Alert signup for an already-active subscription #%s", sub_id)
        return _page("alerts_sent.html", email=email)

    label = alerts_mod.describe_filters(filters)
    verify_url = url_for("alerts.verify", token=raw_verify, _external=True)
    send_email(
        email,
        "Confirm your Pinakes alert",
        "You (or someone using this address) asked Pinakes to send new "
        "scholarship matching a saved search.\n\n"
        f"Saved search: {label}\n"
        f"Frequency: {cadence}\n\n"
        "If this was you, confirm by opening this one-time link:\n\n"
        f"  {verify_url}\n\n"
        "Nothing is sent until you confirm. If you didn't ask for this, "
        "ignore this message — the request is deleted after a week.\n",
    )
    log.info("Alert signup: pending subscription #%s created (ip=%s)",
             sub_id, _client_ip())
    return _page("alerts_sent.html", email=email)


@bp.route("/alerts/verify/<token>")
def verify(token):
    """Consume the one-time confirmation token and activate the subscription."""
    _require_enabled()
    sub_id = alerts_mod.verify_by_token(token)
    if not sub_id:
        return _page("alerts_result.html",
                     heading="That link has already been used",
                     message="Confirmation links work once. If your alert is "
                             "already active you have nothing to do; if you "
                             "are not sure, subscribe again and use the new "
                             "link.",
                     active_nav="feeds"), 404

    from db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT label, cadence FROM alert_subscriptions WHERE id = ?",
            (sub_id,),
        ).fetchone()

    log.info("Alert subscription #%s verified and activated", sub_id)
    return _page(
        "alerts_result.html",
        heading="Your alert is on",
        message=f"Pinakes will email you {row['cadence']} when new work "
                f"matches: {row['label']}. Weeks with nothing new are silent.",
        active_nav="feeds",
    )


def _do_unsubscribe(token):
    result = alerts_mod.unsubscribe_by_token(token)
    if not result:
        return None
    log.info("Alert subscription #%s unsubscribed", result["id"])
    return result


@bp.route("/alerts/unsubscribe/<token>", methods=["POST"])
def unsubscribe_post(token):
    """RFC 8058 one-click endpoint.

    Gmail and Yahoo POST here directly from their own unsubscribe button, with
    no browser session and no confirmation step. It answers 200 with a bare
    body whether or not the token is known, because a mail provider is not a
    person who can act on an error page.
    """
    _do_unsubscribe(token)
    return Response("Unsubscribed.", mimetype="text/plain")


@bp.route("/alerts/unsubscribe/<token>", methods=["GET"])
def unsubscribe(token):
    """One-click unsubscribe from the link in a digest.

    No "are you sure?" step: an interstitial is how people end up still
    subscribed after trying to leave. Deliberately idempotent — an unsubscribe
    link in a two-year-old email must still land somewhere calm.
    """
    result = _do_unsubscribe(token)
    if not result:
        return _page("alerts_result.html",
                     heading="Nothing to unsubscribe",
                     message="This link doesn't match an alert. It may have "
                             "already been removed.",
                     active_nav="feeds"), 404
    return _page(
        "alerts_result.html",
        heading="Unsubscribed",
        message=f"You will not get any more email about: {result['label']}.",
        manage_token=None,
        active_nav="feeds",
    )


@bp.route("/alerts/manage/<token>")
def manage(token):
    """Show one subscription and offer permanent deletion.

    Like the unsubscribe routes, this deliberately does NOT check
    PINAKES_ALERTS_ENABLED. Turning the feature off must never strand someone
    who wants out of a list they are already on, or block a deletion request.
    """
    sub = alerts_mod.get_by_unsub_token(token)
    if not sub:
        abort(404)
    return _page("alerts_manage.html", sub=sub, token=token, active_nav="feeds")


@bp.route("/alerts/manage/<token>/delete", methods=["POST"])
def delete(token):
    """Erase the subscription and its delivery history."""
    deleted = alerts_mod.delete_by_unsub_token(token)
    if not deleted:
        abort(404)
    log.info("Alert subscription deleted via manage link")
    return _page(
        "alerts_result.html",
        heading="Deleted",
        message="Your email address and saved search have been removed from "
                "Pinakes, along with the record of what was sent to you.",
        active_nav="feeds",
    )
