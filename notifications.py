"""notifications.py — minimal transactional email for Pinakes.

The codebase had no email integration before the author-redaction feature; the
opt-out request flow needs to send a one-time verification link. This is the
smallest thing that works: SMTP via env vars. Swap `send_email` for a
SendGrid/SES call later without touching callers.

Env vars (all read at call time, so tests/dev can set them per-process):
    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM (the From: address; also gates whether email is "configured"),
    SMTP_REPLY_TO (optional Reply-To, e.g. an inbox you read — useful when
        SMTP_FROM is a no-reply domain sender with no mailbox behind it),
    SMTP_STARTTLS (default "1").

Graceful degradation: if SMTP isn't configured, send_email logs the message
(including the verification link) at WARNING and returns False instead of
raising — so local dev and the test env never block on a mail server, and a
production misconfiguration still leaves an operator-visible trail in the logs
rather than 500-ing the request form.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def email_configured() -> bool:
    """True iff enough SMTP settings exist to actually send mail."""
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns True if handed to the SMTP server,
    False if email isn't configured (message is logged instead) or sending
    failed. Never raises — callers treat email as best-effort."""
    sender = os.environ.get("SMTP_FROM", "")
    if not email_configured():
        log.warning(
            "SMTP not configured (SMTP_HOST/SMTP_FROM unset); email NOT sent.\n"
            "  To: %s\n  Subject: %s\n  Body:\n%s",
            to, subject, body,
        )
        return False

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    reply_to = os.environ.get("SMTP_REPLY_TO")
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    use_starttls = os.environ.get("SMTP_STARTTLS", "1") != "0"

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            if use_starttls:
                server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        log.info("Sent email to %s (subject=%s)", to, subject)
        return True
    except Exception as exc:  # noqa: BLE001 — email must never crash the caller
        log.error("Email send to %s failed: %s", to, exc)
        return False
