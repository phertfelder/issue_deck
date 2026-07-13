"""Centralized redaction & secret-scrubbing utilities.

Single source of truth for every way this app removes identifying or sensitive
information before it leaves the process:

* **Export redaction** — deterministic pseudonymization (people, clients),
  issue-key masking, and free-text scrubbing of emails/URLs. The export pipeline
  (:mod:`issue_deck.exporters.transform`) and the CSV wizard
  (:mod:`issue_deck.csv_import`) both build on the primitives here so redaction
  behaves identically no matter which artifact (Markdown / JSONL / CSV / pack) is
  produced.
* **Log/secret scrubbing** — :func:`redact_secrets` strips tokens, ``Authorization``
  headers and credentials-in-URL so a secret can never reach a log, an error
  message, or an export by accident. :mod:`issue_deck.logging_utils` applies it
  to every log record automatically.

Everything here is pure and Qt-free, so it stays trivially testable and importable
without a display.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "EMAIL_RE",
    "URL_RE",
    "EMAIL_PLACEHOLDER",
    "URL_PLACEHOLDER",
    "RedactionSettings",
    "redact_key",
    "redact_emails",
    "redact_urls",
    "scrub_text",
    "pseudonymize",
    "redact_secrets",
]

# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #
# Deliberately conservative email/URL matchers — they favour not mangling normal
# prose over catching every exotic form.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"\bhttps?://[^\s<>()\[\]{}'\"]+", re.IGNORECASE)

EMAIL_PLACEHOLDER = "[email redacted]"
URL_PLACEHOLDER = "[url redacted]"

# Secret patterns for log/error scrubbing. Order matters: broad credential-in-URL
# and header rules run before the generic key/value rule. Every replacement keeps
# enough structure to stay readable while removing the secret itself.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Atlassian Cloud API tokens / PATs (recognizable prefixes).
    (re.compile(r"\bAT[A-Z]{2}[A-Za-z0-9_\-=]{8,}"), "[token redacted]"),
    # Authorization: Bearer <token> / Basic <base64>
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer|basic)\s+\S+"),
     r"\1\2 [redacted]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-=/+]{8,}"), r"\1 [redacted]"),
    # Credentials embedded in a URL: scheme://user:pass@host
    (re.compile(r"(://)[^/\s:@]+:[^/\s:@]+@"), r"\1[redacted]@"),
    # Generic token / password / api key key=value or "key": "value".
    (re.compile(
        r"(?i)\b(token|password|passwd|pwd|api[_-]?key|secret|access[_-]?token)"
        r"(\"?\s*[:=]\s*\"?)([^\s\"',&}]+)"),
     r"\1\2[redacted]"),
)


# --------------------------------------------------------------------------- #
# Redaction settings model
# --------------------------------------------------------------------------- #
@dataclass
class RedactionSettings:
    """The full set of redaction toggles the app understands.

    This is the *vocabulary* of redaction; concrete callers (the export config,
    the CSV wizard) map their own options onto it so a single description of
    "what was redacted" can be recorded in manifests and shown in previews.

    * ``keys`` — mask issue keys (``PROJ-123`` -> ``PROJ-•••``) and keys in URLs.
    * ``people`` — replace assignee/reporter/comment authors with stable
      pseudonyms (``Person 1`` …) and drop their account ids/usernames/emails.
    * ``emails`` — scrub email addresses out of all free text.
    * ``clients`` — replace client/customer names with pseudonyms (``Client 1`` …).
    * ``urls`` — scrub ``http(s)`` URLs out of all free text.
    * ``comments`` — drop comment bodies entirely.
    * ``descriptions`` — drop issue descriptions entirely.
    """

    keys: bool = False
    people: bool = False
    emails: bool = False
    clients: bool = False
    urls: bool = False
    comments: bool = False
    descriptions: bool = False

    def any(self) -> bool:
        """True when at least one redaction is enabled."""
        return any((
            self.keys, self.people, self.emails, self.clients,
            self.urls, self.comments, self.descriptions,
        ))

    def describe(self) -> dict[str, bool]:
        """Serializable summary for manifests / provenance."""
        return {
            "keys": self.keys,
            "people": self.people,
            "emails": self.emails,
            "clients": self.clients,
            "urls": self.urls,
            "comments": self.comments,
            "descriptions": self.descriptions,
        }

    def labels(self) -> list[str]:
        """Human labels for the enabled redactions (for UI/README summaries)."""
        return [name for name, on in (
            ("issue keys", self.keys),
            ("people names", self.people),
            ("emails", self.emails),
            ("client names", self.clients),
            ("URLs", self.urls),
            ("comments", self.comments),
            ("descriptions", self.descriptions),
        ) if on]


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
def redact_key(key: str) -> str:
    """Mask the identifying digits of an issue key, keeping the project prefix.

    ``"PROJ-123"`` -> ``"PROJ-•••"``. Keys with no digits are masked from the
    last separator onward so nothing identifying survives.
    """
    if not key:
        return key
    if any(ch.isdigit() for ch in key):
        return re.sub(r"\d", "•", key)
    prefix, sep, tail = key.rpartition("-")
    return f"{prefix}{sep}{'•' * len(tail)}" if sep else "•" * len(key)


def redact_emails(text: str) -> str:
    """Replace every email address in ``text`` with :data:`EMAIL_PLACEHOLDER`."""
    return EMAIL_RE.sub(EMAIL_PLACEHOLDER, text) if text else text


def redact_urls(text: str) -> str:
    """Replace every ``http(s)`` URL in ``text`` with :data:`URL_PLACEHOLDER`."""
    return URL_RE.sub(URL_PLACEHOLDER, text) if text else text


def scrub_text(text: str, *, emails: bool = False, urls: bool = False) -> str:
    """Scrub ``text`` of URLs and/or emails per the flags (URLs first).

    URLs are scrubbed before emails so an address embedded in a link is removed
    with the link rather than leaving a dangling placeholder.
    """
    if not text:
        return text
    if urls:
        text = redact_urls(text)
    if emails:
        text = redact_emails(text)
    return text


def pseudonymize(names: Sequence[str], prefix: str) -> dict[str, str]:
    """Map each distinct non-empty ``name`` to ``"{prefix} {n}"`` by first sight.

    Deterministic given input order, so an export run over sorted issues always
    yields the same pseudonyms and two runs diff cleanly.
    """
    mapping: dict[str, str] = {}
    for name in names:
        if name and name not in mapping:
            mapping[name] = f"{prefix} {len(mapping) + 1}"
    return mapping


def redact_secrets(text: str) -> str:
    """Strip tokens, ``Authorization`` headers and credentials-in-URL from ``text``.

    Used to sanitize anything that might carry a secret before it is logged,
    surfaced in an error, or written to disk. Best-effort and idempotent: running
    it twice yields the same result.
    """
    if not text:
        return text
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text
