"""Application logging with automatic secret redaction.

Every logger obtained through :func:`get_logger` carries a :class:`RedactingFilter`
that runs :func:`issue_deck.redaction.redact_secrets` over the fully-formatted
message. That means a token, ``Authorization`` header or credentials-in-URL is
stripped *before* it can reach any handler, file, or the console — even if a
caller accidentally passes a secret to ``logger.info(...)``.

Redaction happens on the ``LogRecord`` in place, so it also protects handlers
attached higher up (via propagation) and third-party capture (e.g. pytest's
``caplog``).

Logging is opt-in and quiet by default: nothing is configured until
:func:`configure_logging` is called (the app does so at startup), and the library
never emits to stderr on its own.
"""

from __future__ import annotations

import logging

from . import redaction

_ROOT = "issue_deck"
_configured = False


class RedactingFilter(logging.Filter):
    """A logging filter that scrubs secrets from every record it sees.

    It collapses ``msg`` + ``args`` into a single pre-formatted, redacted string
    and clears ``args`` so no later formatting can re-introduce a secret.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - Filter API
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive: never break logging
            message = str(record.msg)
        record.msg = redaction.redact_secrets(message)
        record.args = ()
        return True


def get_logger(name: str = "") -> logging.Logger:
    """Return a ``issue_deck``-namespaced logger with secret redaction attached.

    ``name`` is appended under the ``issue_deck`` root (``get_logger("jira_client")``
    -> ``issue_deck.jira_client``). The redacting filter is attached to the logger
    itself so it runs for records emitted on it regardless of where handlers live.
    """
    full = _ROOT if not name else f"{_ROOT}.{name}"
    logger = logging.getLogger(full)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger


def configure_logging(level: int = logging.WARNING) -> logging.Logger:
    """Install a redacting console handler on the ``issue_deck`` root logger.

    Idempotent — safe to call more than once. Returns the root logger. Handlers
    also carry the redacting filter as a belt-and-braces guard for records that
    reach them via propagation from loggers created without :func:`get_logger`.
    """
    global _configured
    root = logging.getLogger(_ROOT)
    root.setLevel(level)
    if not any(isinstance(f, RedactingFilter) for f in root.filters):
        root.addFilter(RedactingFilter())
    if not _configured:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
        _configured = True
    return root
