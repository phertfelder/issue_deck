"""Structured progress model for long-running fetches (Qt-free).

Workers emit :class:`FetchProgress` snapshots instead of pre-formatted strings so
the UI can drive both a status line *and* a determinate progress bar, and so the
model stays unit-testable without Qt. :class:`RetryEvent` carries the detail the
client hands back each time it backs off, so the retry/rate-limit reason can
surface in the same stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Phase(str, Enum):
    """Which stage of a fetch a :class:`FetchProgress` snapshot describes."""

    SEARCHING = "searching"
    LOADING_COMMENTS = "loading_comments"
    RETRYING = "retrying"
    DONE = "done"


@dataclass
class RetryEvent:
    """One backoff decision made by the client before it sleeps and retries."""

    attempt: int            # 1-based: the attempt that just failed
    max_retries: int
    delay: float            # seconds we are about to sleep
    status: int | None      # HTTP status, or None for a timeout/connection error
    reason: str             # short human reason ("Server error (503)")
    rate_limited: bool      # True for HTTP 429 specifically

    def message(self) -> str:
        return (f"{self.reason} — retrying in {self.delay:.0f}s "
                f"(attempt {self.attempt}/{self.max_retries})")


@dataclass
class FetchProgress:
    """A snapshot of an in-flight fetch."""

    phase: Phase
    fetched: int = 0                 # issues fetched so far
    total: int | None = None         # total available, when the API reports it
    current_key: str = ""            # issue whose comments are loading
    message: str = ""                # retry/rate-limit or free-form note

    def describe(self) -> str:
        """A single-line human summary for a status label."""
        if self.phase is Phase.SEARCHING:
            if self.total is not None:
                return f"Searching… {self.fetched} of {self.total} issues"
            return f"Searching… {self.fetched} issues" if self.fetched else "Searching…"
        if self.phase is Phase.LOADING_COMMENTS:
            key = f" ({self.current_key})" if self.current_key else ""
            return f"Loading comments{key}"
        if self.phase is Phase.RETRYING:
            return self.message or "Retrying…"
        if self.phase is Phase.DONE:
            return f"Done. {self.fetched} issues."
        return self.message
