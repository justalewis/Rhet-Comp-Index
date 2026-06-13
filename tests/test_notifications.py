"""Tests for the SMTP helper behind the redaction email-verification flow."""

import smtplib

import notifications


def test_email_not_configured_returns_false(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    assert notifications.email_configured() is False
    # No SMTP server contacted; logs instead of raising.
    assert notifications.send_email("a@b.edu", "subj", "body") is False


def test_send_email_sets_headers_and_reply_to(monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=15):
            captured["host"] = host
            captured["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            captured["starttls"] = True

        def login(self, user, password):
            captured["login"] = (user, password)

        def send_message(self, msg):
            captured["msg"] = msg

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.resend.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "resend")
    monkeypatch.setenv("SMTP_PASSWORD", "re_testkey")
    monkeypatch.setenv("SMTP_FROM", "Pinakes <no-reply@send.pinakes.xyz>")
    monkeypatch.setenv("SMTP_REPLY_TO", "justalewis1@gmail.com")

    assert notifications.email_configured() is True
    assert notifications.send_email("author@uni.edu", "Confirm", "the link") is True

    msg = captured["msg"]
    assert msg["From"] == "Pinakes <no-reply@send.pinakes.xyz>"
    assert msg["To"] == "author@uni.edu"
    assert msg["Reply-To"] == "justalewis1@gmail.com"
    assert captured["host"] == "smtp.resend.com" and captured["port"] == 587
    assert captured["starttls"] is True
    assert captured["login"] == ("resend", "re_testkey")


def test_send_email_omits_reply_to_when_unset(monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=15):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            captured["msg"] = msg

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.resend.com")
    monkeypatch.setenv("SMTP_FROM", "no-reply@send.pinakes.xyz")
    monkeypatch.delenv("SMTP_REPLY_TO", raising=False)

    assert notifications.send_email("author@uni.edu", "Confirm", "the link") is True
    assert captured["msg"]["Reply-To"] is None
