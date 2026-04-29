"""Tests for monitoring.py — Sentry init guard rails, PII scrubbing, and
the capture_fetcher_error tagging helper. The conftest sets FLASK_ENV=testing
so the production module's init_sentry() is already a no-op; these tests
verify the underlying logic is correct under the conditions where it WOULD
run in production."""

from unittest.mock import patch, MagicMock

import pytest

import monitoring


@pytest.fixture
def fresh_monitoring(monkeypatch):
    """Reset monitoring._initialised before each test so init can be re-run."""
    monkeypatch.setattr(monitoring, "_initialised", False)
    yield monitoring
    monkeypatch.setattr(monitoring, "_initialised", False)


# ── init_sentry skip paths ───────────────────────────────────────────────────


def test_init_sentry_skips_when_no_dsn(fresh_monitoring, monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with patch("sentry_sdk.init") as init_mock:
        result = monitoring.init_sentry("web")
    assert result is False
    init_mock.assert_not_called()


def test_init_sentry_skips_with_empty_dsn(fresh_monitoring, monkeypatch):
    """An env var set to empty string is the same as unset."""
    monkeypatch.setenv("SENTRY_DSN", "")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with patch("sentry_sdk.init") as init_mock:
        result = monitoring.init_sentry("web")
    assert result is False
    init_mock.assert_not_called()


def test_init_sentry_skips_when_flask_env_testing(fresh_monitoring, monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://x@example.org/1")
    monkeypatch.setenv("FLASK_ENV", "testing")
    with patch("sentry_sdk.init") as init_mock:
        result = monitoring.init_sentry("web")
    assert result is False
    init_mock.assert_not_called()


def test_init_sentry_skips_under_pytest(fresh_monitoring, monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://x@example.org/1")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_x.py::test_y")
    with patch("sentry_sdk.init") as init_mock:
        result = monitoring.init_sentry("web")
    assert result is False
    init_mock.assert_not_called()


# ── init_sentry happy path ───────────────────────────────────────────────────


def test_init_sentry_initializes_with_dsn(fresh_monitoring, monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://abc@example.org/1")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("FLY_RELEASE_VERSION", "v123")
    monkeypatch.setenv("FLY_APP_NAME", "pinakes-test")
    with patch("sentry_sdk.init") as init_mock, \
         patch("sentry_sdk.set_tag") as tag_mock:
        result = monitoring.init_sentry("web")
    assert result is True
    init_mock.assert_called_once()
    kwargs = init_mock.call_args.kwargs
    assert kwargs["dsn"] == "https://abc@example.org/1"
    assert kwargs["traces_sample_rate"] == 0.01
    assert kwargs["profiles_sample_rate"] == 0.0
    assert kwargs["send_default_pii"] is False
    assert kwargs["release"] == "v123"
    assert kwargs["environment"] == "pinakes-test"
    assert kwargs["before_send"] is monitoring._scrub_pii
    tag_mock.assert_called_once_with("component", "web")


def test_init_sentry_idempotent(fresh_monitoring, monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://abc@example.org/1")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with patch("sentry_sdk.init") as init_mock, \
         patch("sentry_sdk.set_tag"):
        monitoring.init_sentry("web")
        monitoring.init_sentry("web")
    assert init_mock.call_count == 1


# ── _scrub_pii ───────────────────────────────────────────────────────────────


def test_scrub_pii_removes_authorization_header():
    event = {"request": {"headers": {
        "Authorization": "Bearer secret-token",
        "User-Agent": "test",
    }}}
    out = monitoring._scrub_pii(event, hint=None)
    assert out["request"]["headers"]["Authorization"] == "[FILTERED]"
    assert out["request"]["headers"]["User-Agent"] == "test"


def test_scrub_pii_removes_cookie_header():
    event = {"request": {"headers": {"Cookie": "session=abc"}}}
    out = monitoring._scrub_pii(event, hint=None)
    assert out["request"]["headers"]["Cookie"] == "[FILTERED]"


def test_scrub_pii_redacts_token_query_params_string():
    event = {"request": {"query_string": "q=foo&admin_token=secret&page=2"}}
    out = monitoring._scrub_pii(event, hint=None)
    qs = out["request"]["query_string"]
    assert "admin_token=[FILTERED]" in qs
    assert "secret" not in qs
    assert "q=foo" in qs


def test_scrub_pii_redacts_token_query_params_list():
    event = {"request": {"query_string": [
        ("q", "foo"), ("admin_token", "secret"), ("api_token", "shh"),
    ]}}
    out = monitoring._scrub_pii(event, hint=None)
    pairs = dict(out["request"]["query_string"])
    assert pairs["q"] == "foo"
    assert pairs["admin_token"] == "[FILTERED]"
    assert pairs["api_token"] == "[FILTERED]"


def test_scrub_pii_clears_request_body():
    event = {"request": {"data": "user search query that might be sensitive"}}
    out = monitoring._scrub_pii(event, hint=None)
    assert out["request"]["data"] is None


def test_scrub_pii_handles_non_http_event():
    """Scheduler errors have no `request` key — must not crash."""
    event = {"level": "error", "message": "boom"}
    out = monitoring._scrub_pii(event, hint=None)
    assert out == {"level": "error", "message": "boom"}


# ── capture_fetcher_error ────────────────────────────────────────────────────


def test_capture_fetcher_error_no_op_when_uninitialized(fresh_monitoring):
    """Uninitialised Sentry — function must silently no-op without raising."""
    monitoring.capture_fetcher_error("crossref", "College English", RuntimeError("x"))


def test_capture_fetcher_error_adds_tags_and_captures(fresh_monitoring, monkeypatch):
    monkeypatch.setattr(monitoring, "_initialised", True)
    fake_scope = MagicMock()
    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=fake_scope)
    fake_cm.__exit__ = MagicMock(return_value=False)
    with patch("sentry_sdk.new_scope", return_value=fake_cm), \
         patch("sentry_sdk.capture_exception") as cap:
        exc = RuntimeError("boom")
        monitoring.capture_fetcher_error("crossref", "College English", exc)
    # Both tags applied
    fake_scope.set_tag.assert_any_call("source", "crossref")
    fake_scope.set_tag.assert_any_call("journal", "College English")
    cap.assert_called_once_with(exc)


def test_capture_fetcher_error_omits_journal_tag_when_none(fresh_monitoring, monkeypatch):
    monkeypatch.setattr(monitoring, "_initialised", True)
    fake_scope = MagicMock()
    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=fake_scope)
    fake_cm.__exit__ = MagicMock(return_value=False)
    with patch("sentry_sdk.new_scope", return_value=fake_cm), \
         patch("sentry_sdk.capture_exception"):
        monitoring.capture_fetcher_error("openalex", None, RuntimeError("x"))
    # Only the source tag — no journal tag was set
    tag_calls = [c.args for c in fake_scope.set_tag.call_args_list]
    assert ("source", "openalex") in tag_calls
    assert not any(c[0] == "journal" for c in tag_calls)


def test_capture_fetcher_error_swallows_sentry_failures(fresh_monitoring, monkeypatch):
    """Sentry SDK raising must not propagate — ingestion keeps running."""
    monkeypatch.setattr(monitoring, "_initialised", True)
    with patch("sentry_sdk.new_scope", side_effect=RuntimeError("network down")):
        # Must not raise
        monitoring.capture_fetcher_error("crossref", "X", RuntimeError("y"))
