"""Human-readable presentation of errors (Qt-free).

Turns the typed :mod:`issue_deck.jira_client` exception hierarchy (and a few
common non-Jira failures) into a small :class:`PresentedError` — a title, a
plain sentence, a suggested action, a severity, and whether it's worth retrying
— so the UI can show *"Jira rejected the credentials."* instead of a raw
traceback string. The original text is preserved in ``details`` but **scrubbed
of tokens/credentials** via :func:`issue_deck.redaction.redact_secrets`.

Pure and side-effect free: no Qt, no logging (callers log the real exception).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .jira_client import (
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
from .redaction import redact_secrets

__all__ = ["PresentedError", "present_error"]


@dataclass
class PresentedError:
    """A user-facing rendering of an exception."""

    title: str
    plain_message: str
    suggested_action: str
    severity: str = "error"     # "info" | "warning" | "error"
    retryable: bool = False
    details: str = ""           # the raw (sanitized) exception text, for a Details area


# Per-type copy: (title, message, action, severity, retryable). Ordered most-
# specific first — all the Jira* subclasses are checked before the JiraError base.
def _for_jira(exc: JiraError) -> tuple[str, str, str, str, bool]:
    if isinstance(exc, InvalidJQLError):
        return ("Jira rejected this JQL.",
                "The query isn't valid on this instance.",
                "Fix the highlighted clause, or switch back to guided filters.",
                "error", False)
    if isinstance(exc, AuthError):
        return ("Jira rejected the credentials.",
                "Authentication failed (401).",
                "Check the email/token pair, and that Cloud vs Server/Data Center "
                "is set correctly.",
                "error", False)
    if isinstance(exc, JiraPermissionError):
        return ("The token works, but Jira blocked this action.",
                "You're authenticated but not authorized (403).",
                "Check project permissions, or use a token with issue-search access.",
                "error", False)
    if isinstance(exc, DeploymentMismatchError):
        return ("This looks like the wrong Jira deployment mode.",
                "The endpoint returned 410 Gone for this deployment.",
                "Use Cloud mode for Atlassian Cloud, or Server/Data Center for "
                "PAT-based Jira.",
                "error", False)
    if isinstance(exc, RateLimitedError):
        retry_after = getattr(exc, "retry_after", None)
        wait = f" (retry after ~{retry_after}s)" if retry_after else ""
        return ("Jira is rate limiting requests.",
                f"Too many requests (429){wait}.",
                "Wait a moment and retry; reduce comments or narrow the query.",
                "warning", True)
    if isinstance(exc, NetworkTimeoutError):
        return ("Jira did not respond in time.",
                "The request timed out.",
                "Retry, narrow the query, or increase the timeout in Settings.",
                "warning", True)
    if isinstance(exc, SSLCertError):
        return ("Couldn't verify the Jira TLS certificate.",
                "The secure connection couldn't be established.",
                "Check the base URL and your network's certificate trust.",
                "error", False)
    if isinstance(exc, NetworkError):
        return ("Couldn't reach Jira.",
                "The connection failed before Jira could respond.",
                "Check the base URL and your network connection, then retry.",
                "warning", True)
    if isinstance(exc, ServerError):
        return ("Jira hit a server error.",
                "The instance returned a 5xx error.",
                "This is usually temporary — retry in a moment.",
                "warning", True)
    return ("Jira couldn't complete the request.",
            "The request failed.",
            "Check your connection settings and try again.",
            "error", False)


def present_error(exc: Exception, *, operation: str = "") -> PresentedError:
    """Render ``exc`` as a :class:`PresentedError`.

    ``operation`` (e.g. ``"fetch"``, ``"connection"``, ``"validate"``) is accepted
    for future context-specific wording; the mapping is driven by exception type.
    """
    details = redact_secrets(str(exc) or exc.__class__.__name__)

    if isinstance(exc, JiraError):
        title, message, action, severity, retryable = _for_jira(exc)
    elif isinstance(exc, (json.JSONDecodeError, ValueError)):
        title, message, action, severity, retryable = (
            "Jira returned an unexpected response.",
            "The response couldn't be read as valid data.",
            "Check the base URL points at a Jira instance, then retry.",
            "error", False)
    elif isinstance(exc, (TimeoutError,)):
        title, message, action, severity, retryable = (
            "Jira did not respond in time.",
            "The request timed out.",
            "Retry, narrow the query, or increase the timeout in Settings.",
            "warning", True)
    else:
        title, message, action, severity, retryable = (
            "Something went wrong.",
            "An unexpected error occurred.",
            "Try again; if it persists, check your settings.",
            "error", False)

    return PresentedError(
        title=title, plain_message=message, suggested_action=action,
        severity=severity, retryable=retryable, details=details)
