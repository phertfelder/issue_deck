"""Characterization tests for comment conversion and latest_comment_dt."""

from __future__ import annotations

import datetime as dt

from issue_deck.comments import (
    attach_comments,
    build_comments,
    latest_comment_dt,
)
from issue_deck.models import JiraComment, JiraIssue


def test_build_comments_cloud_adf(cloud_comments):
    comments = build_comments(cloud_comments)
    assert len(comments) == 2
    first = comments[0]
    assert first.author == "Ada Lovelace"
    assert first.created == "2026-02-01T10:00:00.000+0000"
    assert first.updated == "2026-02-01T10:05:00.000+0000"
    assert first.body == "Looking into the café encoding issue."


def test_build_comments_author_name_fallback(server_comments):
    comments = build_comments(server_comments)
    assert comments[0].author == "jdoe"


def test_build_comments_plain_body(server_comments):
    comments = build_comments(server_comments)
    assert comments[0].body == "Plain-text comment: increased the timeout to 300s."


def test_build_comments_missing_author():
    comments = build_comments([{"created": "2026-01-01T00:00:00.000+0000", "body": "x"}])
    assert comments[0].author == ""


def test_attach_comments_sets_issue_comments(cloud_comments):
    issue = JiraIssue(key="CLOUD-1")
    attach_comments(issue, cloud_comments)
    assert len(issue.comments) == 2
    assert all(isinstance(c, JiraComment) for c in issue.comments)


def test_latest_comment_dt_returns_max():
    issue = JiraIssue(comments=[
        JiraComment(created="2026-02-01T10:00:00.000+0000"),
        JiraComment(created="2026-02-03T11:00:00.000+0000"),
        JiraComment(created="2026-01-15T09:00:00.000+0000"),
    ])
    assert latest_comment_dt(issue) == dt.datetime(2026, 2, 3, 11, 0, tzinfo=dt.timezone.utc)


def test_latest_comment_dt_empty():
    assert latest_comment_dt(JiraIssue()) is None


def test_latest_comment_dt_ignores_unparsable():
    issue = JiraIssue(comments=[
        JiraComment(created="not-a-date"),
        JiraComment(created="2026-05-05T05:05:00.000+0000"),
        JiraComment(created=""),
    ])
    assert latest_comment_dt(issue) == dt.datetime(2026, 5, 5, 5, 5, tzinfo=dt.timezone.utc)


def test_latest_comment_dt_all_unparsable_returns_none():
    issue = JiraIssue(comments=[JiraComment(created="nope"), JiraComment(created="also")])
    assert latest_comment_dt(issue) is None
