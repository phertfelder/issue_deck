"""Tests for the (Qt-free) issue service orchestration."""

from __future__ import annotations

import datetime as dt

import pytest

from issue_deck.comments import CommentsMode, CommentsOptions
from issue_deck.config import AppConfig
from issue_deck.jira_client import SearchOutcome
from issue_deck.models import JiraComment
from issue_deck.progress import Phase
from issue_deck.schema import NormalizedIssue
from issue_deck.services import issue_service

_ALL = CommentsOptions(mode=CommentsMode.ALL)
_NONE = CommentsOptions(mode=CommentsMode.NONE)


class FakeClient:
    def __init__(self, raw_issues, comments=None, comment_error=None, total=None):
        self._raw = raw_issues
        self._comments = comments or []
        self._comment_error = comment_error
        self._total = total
        self.searched_fields = None

    def search(self, jql, fields, *, on_progress=None, cancel=None, on_retry=None,
               max_results=None):
        self.searched_fields = fields
        if on_progress:
            on_progress(len(self._raw), self._total)
        return SearchOutcome(list(self._raw), total=self._total)

    def get_comments(self, key, *, cancel=None, on_retry=None):
        if self._comment_error:
            raise self._comment_error
        return self._comments


def test_search_fields_appends_custom_fields():
    cfg = AppConfig(client_field="customfield_10050", severity_field="customfield_10060")
    fields = issue_service.search_fields(cfg)
    assert "summary" in fields
    assert fields[-2:] == ["customfield_10050", "customfield_10060"]


def test_search_fields_without_custom_fields():
    fields = issue_service.search_fields(AppConfig())
    assert "customfield_10050" not in fields


def test_fetch_issues_normalizes(cloud_issue, cloud_cfg):
    client = FakeClient([cloud_issue])
    result = issue_service.fetch_issues(client, "jql", cloud_cfg, comments=_NONE)
    assert len(result.issues) == 1
    assert isinstance(result.issues[0], NormalizedIssue)
    assert result.issues[0].key == "CLOUD-1"
    assert result.issues[0].comments == []
    assert result.issues[0].source.origin == "api"
    assert result.issues[0].source.deployment == "cloud"
    assert result.warnings == []


def test_fetch_issues_attaches_comments(cloud_issue, cloud_cfg, cloud_comments):
    client = FakeClient([cloud_issue], comments=cloud_comments)
    result = issue_service.fetch_issues(client, "jql", cloud_cfg, comments=_ALL)
    assert len(result.issues[0].comments) == 2
    assert result.issues[0].comments[0].author == "Ada Lovelace"


def test_fetch_issues_comment_error_becomes_warning(cloud_issue, cloud_cfg):
    client = FakeClient([cloud_issue], comment_error=RuntimeError("boom"))
    result = issue_service.fetch_issues(client, "jql", cloud_cfg, comments=_ALL)
    assert result.issues[0].comments == []
    assert len(result.warnings) == 1
    assert result.warnings[0].key == "CLOUD-1"
    assert "boom" in result.warnings[0].message


def test_fetch_issues_comment_error_can_raise_when_configured(cloud_issue, cloud_cfg):
    client = FakeClient([cloud_issue], comment_error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        issue_service.fetch_issues(client, "jql", cloud_cfg, comments=_ALL,
                                   fail_on_comment_error=True)


def test_fetch_issues_latest_n_trims_comments(cloud_issue, cloud_cfg, cloud_comments):
    client = FakeClient([cloud_issue], comments=cloud_comments)
    opts = CommentsOptions(mode=CommentsMode.LATEST, latest_n=1)
    result = issue_service.fetch_issues(client, "jql", cloud_cfg, comments=opts)
    assert len(result.issues[0].comments) == 1


def test_fetch_issues_none_mode_skips_comment_fetch(cloud_issue, cloud_cfg):
    called = {"n": 0}

    class Counting(FakeClient):
        def get_comments(self, key, *, cancel=None, on_retry=None):
            called["n"] += 1
            return []

    result = issue_service.fetch_issues(Counting([cloud_issue]), "jql", cloud_cfg,
                                        comments=_NONE)
    assert result.issues[0].comments == []
    assert called["n"] == 0


def test_fetch_issues_emits_structured_progress(cloud_issue, cloud_cfg):
    seen = []
    client = FakeClient([cloud_issue], total=1)
    issue_service.fetch_issues(client, "jql", cloud_cfg, comments=_NONE,
                               on_progress=seen.append)
    phases = {p.phase for p in seen}
    assert Phase.SEARCHING in phases and Phase.DONE in phases
    assert seen[-1].phase is Phase.DONE


def test_fetch_issues_reports_cap_truncation(cloud_issue, cloud_cfg):
    class Capping(FakeClient):
        def search(self, jql, fields, *, on_progress=None, cancel=None,
                   on_retry=None, max_results=None):
            return SearchOutcome(list(self._raw), total=5, truncated=True)

    result = issue_service.fetch_issues(Capping([cloud_issue]), "jql", cloud_cfg,
                                        comments=_NONE, max_issues=1)
    assert result.truncated is True
    assert result.cap == 1
    assert "capped" in result.cap_warning.lower()


def test_filter_commented_within_disabled_returns_all():
    issues = [NormalizedIssue(key="A"), NormalizedIssue(key="B")]
    assert issue_service.filter_commented_within(issues, 0) == issues


def test_filter_commented_within_keeps_recent():
    now = dt.datetime.now(dt.timezone.utc)
    recent = NormalizedIssue(key="recent", comments=[
        JiraComment(created=(now - dt.timedelta(days=1)).isoformat())])
    old = NormalizedIssue(key="old", comments=[
        JiraComment(created=(now - dt.timedelta(days=90)).isoformat())])
    kept = issue_service.filter_commented_within([recent, old], 7)
    assert [i.key for i in kept] == ["recent"]
