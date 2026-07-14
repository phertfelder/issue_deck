"""Tests for the Qt-free human-readable error presenter."""

from __future__ import annotations

import json

from issue_deck.error_presenter import present_error
from issue_deck.jira_client import (
    AuthError,
    DeploymentMismatchError,
    InvalidJQLError,
    JiraError,
    JiraPermissionError,
    NetworkError,
    NetworkTimeoutError,
    RateLimitedError,
    ServerError,
    SSLCertError,
)


def test_invalid_jql():
    p = present_error(InvalidJQLError("Invalid JQL: bad field"))
    assert p.title == "Jira rejected this JQL."
    assert "guided filters" in p.suggested_action
    assert p.severity == "error" and p.retryable is False


def test_auth_error():
    p = present_error(AuthError("401 Unauthorized"))
    assert "credentials" in p.title.lower()
    assert "Cloud vs Server" in p.suggested_action


def test_permission_error():
    p = present_error(JiraPermissionError("403 Forbidden"))
    assert "blocked this action" in p.title
    assert p.retryable is False


def test_deployment_mismatch():
    p = present_error(DeploymentMismatchError("410 Gone"))
    assert "deployment mode" in p.title
    assert "Cloud mode" in p.suggested_action


def test_rate_limited_is_retryable_and_warning():
    exc = RateLimitedError("429", retry_after=30)
    p = present_error(exc)
    assert p.severity == "warning" and p.retryable is True
    assert "30s" in p.plain_message   # retry_after surfaced


def test_timeout_is_retryable():
    p = present_error(NetworkTimeoutError("timed out"))
    assert p.retryable is True and p.severity == "warning"


def test_ssl_error():
    p = present_error(SSLCertError("cert verify failed"))
    assert "TLS certificate" in p.title
    assert p.retryable is False


def test_network_error_retryable():
    p = present_error(NetworkError("connection refused"))
    assert p.retryable is True


def test_server_error_retryable():
    p = present_error(ServerError("500"))
    assert p.retryable is True and p.severity == "warning"


def test_generic_jira_error_falls_through_to_base():
    p = present_error(JiraError("HTTP 418: teapot"))
    assert p.title == "Jira couldn't complete the request."


def test_malformed_response():
    p = present_error(json.JSONDecodeError("bad", "doc", 0))
    assert "unexpected response" in p.title


def test_unknown_exception():
    p = present_error(RuntimeError("kaboom"))
    assert p.title == "Something went wrong."
    assert p.severity == "error"


# --------------------------------------------------------------------------- #
# Privacy: the raw details must never leak tokens/credentials
# --------------------------------------------------------------------------- #
def test_details_scrub_bearer_token():
    p = present_error(JiraError("failed with Authorization: Bearer abcdEFGH12345678xyz"))
    assert "abcdEFGH12345678xyz" not in p.details
    assert "[redacted]" in p.details


def test_details_scrub_atlassian_token():
    p = present_error(AuthError("token=ATATT3xFfGF0abcdefgh12345 rejected"))
    assert "ATATT3xFfGF0abcdefgh12345" not in p.details
    assert "redacted" in p.details.lower()


def test_details_preserve_nonsecret_text():
    p = present_error(InvalidJQLError("Invalid JQL: field 'foo' does not exist"))
    assert "foo" in p.details   # the actionable part is kept
