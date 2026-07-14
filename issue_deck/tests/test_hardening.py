"""Unit tests for the client/worker hardening primitives (Qt-free).

Covers comment-mode trimming, cancellation, backoff/Retry-After math, the
progress model, and CSV-parse cancellation.
"""

from __future__ import annotations

import random

import pytest

from issue_deck.cancellation import CancelledError, CancelToken
from issue_deck.comments import (
    CommentsMode,
    CommentsOptions,
    trim_comments,
)
from issue_deck.csv_import import parse_csv
from issue_deck.jira_client import RetryPolicy, _backoff_delay, parse_retry_after
from issue_deck.models import JiraComment
from issue_deck.progress import FetchProgress, Phase, RetryEvent


# --------------------------------------------------------------------------- #
# Comment-mode trimming
# --------------------------------------------------------------------------- #
def _c(day: int) -> JiraComment:
    return JiraComment(created=f"2026-02-{day:02d}T10:00:00.000+0000", body=f"c{day}")


def test_trim_none_drops_all():
    assert trim_comments([_c(1), _c(2)], CommentsOptions(mode=CommentsMode.NONE)) == []


def test_trim_all_keeps_everything():
    cs = [_c(1), _c(2)]
    assert trim_comments(cs, CommentsOptions(mode=CommentsMode.ALL)) == cs


def test_trim_latest_keeps_newest_n_chronological():
    cs = [_c(1), _c(5), _c(3)]
    out = trim_comments(cs, CommentsOptions(mode=CommentsMode.LATEST, latest_n=2))
    assert [c.body for c in out] == ["c3", "c5"]   # newest two, oldest-first


def test_trim_latest_zero_keeps_none():
    assert trim_comments([_c(1)], CommentsOptions(mode=CommentsMode.LATEST, latest_n=0)) == []


def test_trim_since_filters_by_date():
    cs = [_c(1), _c(10), _c(20)]
    out = trim_comments(cs, CommentsOptions(mode=CommentsMode.SINCE, since="2026-02-10"))
    assert [c.body for c in out] == ["c10", "c20"]


def test_trim_since_unparseable_keeps_all():
    cs = [_c(1), _c(2)]
    assert trim_comments(cs, CommentsOptions(mode=CommentsMode.SINCE, since="junk")) == cs


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #
def test_cancel_token_raises_after_cancel():
    t = CancelToken()
    t.raise_if_cancelled()          # no-op before cancel
    t.cancel()
    assert t.cancelled
    with pytest.raises(CancelledError):
        t.raise_if_cancelled()


def test_parse_csv_honours_cancel_token():
    header = "key,summary\n"
    body = "\n".join(f"K-{i},row {i}" for i in range(5000))
    t = CancelToken()
    t.cancel()
    with pytest.raises(CancelledError):
        parse_csv(header + body, cancel=t)


def test_parse_csv_without_cancel_parses_fully():
    parsed = parse_csv("key,summary\nK-1,hi\nK-2,yo")
    assert parsed.row_count == 2


# --------------------------------------------------------------------------- #
# Backoff + Retry-After
# --------------------------------------------------------------------------- #
def test_backoff_is_bounded_and_grows():
    policy = RetryPolicy(base_delay=1.0, backoff_factor=2.0, max_delay=30.0)
    rng = random.Random(0)
    # Full jitter: delay in [0, ceiling]; ceiling grows then caps at max_delay.
    for attempt, ceiling in [(0, 1.0), (1, 2.0), (2, 4.0), (10, 30.0)]:
        for _ in range(20):
            d = _backoff_delay(attempt, policy, rng)
            assert 0.0 <= d <= ceiling


def test_retry_after_seconds():
    assert parse_retry_after("12") == 12.0


def test_retry_after_http_date_is_non_negative():
    # A past date clamps to 0 rather than going negative.
    assert parse_retry_after("Wed, 01 Jan 2000 00:00:00 GMT") == 0.0


def test_retry_after_none_and_garbage():
    assert parse_retry_after(None) is None
    assert parse_retry_after("not-a-date") is None


# --------------------------------------------------------------------------- #
# Progress model
# --------------------------------------------------------------------------- #
def test_progress_describe_searching_with_total():
    p = FetchProgress(phase=Phase.SEARCHING, fetched=5, total=20)
    assert "5 of 20" in p.describe()


def test_progress_describe_done():
    assert "Done" in FetchProgress(phase=Phase.DONE, fetched=3).describe()


def test_retry_event_message_mentions_attempt_and_delay():
    ev = RetryEvent(attempt=2, max_retries=4, delay=3.0, status=503,
                    reason="Server error (503)", rate_limited=False)
    msg = ev.message()
    assert "attempt 2/4" in msg and "3s" in msg


def test_retry_event_rate_limited_flag():
    ev = RetryEvent(attempt=1, max_retries=4, delay=7.0, status=429,
                    reason="Rate limited (429)", rate_limited=True)
    assert ev.rate_limited and "Rate limited" in ev.message()
