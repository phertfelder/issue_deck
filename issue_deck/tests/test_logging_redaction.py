"""Logging must never leak secrets: the redacting filter scrubs tokens and auth
headers from every record, and the HTTP client never logs credentials."""

from __future__ import annotations

import logging

from issue_deck import logging_utils

SENTINEL = "ATATT3xFfGSECRETtoken0123456789"


def _capture(logger: logging.Logger):
    """Attach an in-memory handler and return its record list."""
    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = _H()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return records, handler


def test_get_logger_redacts_token_in_message():
    logger = logging_utils.get_logger("test_redaction")
    records, handler = _capture(logger)
    try:
        logger.info("connecting with token=%s", SENTINEL)
    finally:
        logger.removeHandler(handler)
    joined = "\n".join(records)
    assert SENTINEL not in joined
    assert "[redacted]" in joined


def test_get_logger_redacts_authorization_header():
    logger = logging_utils.get_logger("test_auth")
    records, handler = _capture(logger)
    try:
        logger.warning("Authorization: Bearer %s", SENTINEL)
    finally:
        logger.removeHandler(handler)
    assert SENTINEL not in "\n".join(records)


def test_redact_secrets_is_idempotent():
    from issue_deck.redaction import redact_secrets
    text = f"token={SENTINEL}"
    assert redact_secrets(redact_secrets(text)) == redact_secrets(text)


def test_configure_logging_is_idempotent():
    root1 = logging_utils.configure_logging()
    n = len(root1.handlers)
    root2 = logging_utils.configure_logging()
    assert root1 is root2
    assert len(root2.handlers) == n  # no duplicate handlers
