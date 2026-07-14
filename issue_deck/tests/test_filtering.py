"""Tests for in-memory filtering — the same SearchFilters on API and CSV data."""

from __future__ import annotations

import datetime as dt

from issue_deck.filtering import apply_filters, matches
from issue_deck.models import JiraComment, SearchFilters
from issue_deck.schema import NormalizedIssue, SourceMetadata

NOW = dt.datetime(2026, 7, 8, tzinfo=dt.timezone.utc)


def api_issue(**kw):
    return NormalizedIssue(source=SourceMetadata.for_api("cloud"), **kw)


def csv_issue(**kw):
    return NormalizedIssue(source=SourceMetadata.for_csv("f.csv"), **kw)


def test_status_filter_case_insensitive():
    issues = [api_issue(key="A", status="Open"), api_issue(key="B", status="Done")]
    kept = apply_filters(issues, SearchFilters(statuses=["open"]), now=NOW)
    assert [i.key for i in kept] == ["A"]


def test_issue_type_filter():
    issues = [api_issue(key="A", issue_type="Bug"), api_issue(key="B", issue_type="Story")]
    kept = apply_filters(issues, SearchFilters(issue_types=["Story"]), now=NOW)
    assert [i.key for i in kept] == ["B"]


def test_severity_is_equality():
    issues = [api_issue(key="A", severity="S1"), api_issue(key="B", severity="S12")]
    kept = apply_filters(issues, SearchFilters(severity="s1"), now=NOW)
    assert [i.key for i in kept] == ["A"]  # equality, not substring


def test_client_is_substring():
    issues = [api_issue(key="A", client="Acme Corp"), api_issue(key="B", client="Globex")]
    kept = apply_filters(issues, SearchFilters(client="acme"), now=NOW)
    assert [i.key for i in kept] == ["A"]


def test_text_searches_summary_description_and_comments():
    a = api_issue(key="A", summary="login crash on submit")
    b = api_issue(key="B", description="unrelated", comments=[JiraComment(body="the CRASH log")])
    c = api_issue(key="C", summary="nothing here")
    kept = apply_filters([a, b, c], SearchFilters(text="crash"), now=NOW)
    assert {i.key for i in kept} == {"A", "B"}


def test_updated_within_days():
    recent = api_issue(key="recent", updated="2026-07-05T00:00:00+00:00")
    old = api_issue(key="old", updated="2026-01-01T00:00:00+00:00")
    kept = apply_filters([recent, old], SearchFilters(updated_days=7), now=NOW)
    assert [i.key for i in kept] == ["recent"]


def test_updated_days_drops_unparseable_when_active():
    bad = api_issue(key="bad", updated="")
    kept = apply_filters([bad], SearchFilters(updated_days=7), now=NOW)
    assert kept == []
    # But with no updated filter, it's kept.
    assert apply_filters([bad], SearchFilters(), now=NOW) == [bad]


def test_commented_within_days():
    recent = api_issue(key="r", comments=[JiraComment(created="2026-07-06T00:00:00+00:00")])
    old = api_issue(key="o", comments=[JiraComment(created="2026-01-01T00:00:00+00:00")])
    none = api_issue(key="n")
    kept = apply_filters([recent, old, none], SearchFilters(commented_days=7), now=NOW)
    assert [i.key for i in kept] == ["r"]


def test_extra_jql_is_ignored_locally():
    issues = [api_issue(key="A", status="Open")]
    # extra can't be evaluated client-side; it must not exclude anything.
    kept = apply_filters(issues, SearchFilters(extra="project = ZZZ"), now=NOW)
    assert [i.key for i in kept] == ["A"]


def test_filters_apply_identically_to_csv_and_api():
    filters = SearchFilters(statuses=["Done"], text="ready")
    api = [api_issue(key="A", status="Done", summary="ready to ship"),
           api_issue(key="B", status="Open", summary="ready")]
    csv = [csv_issue(key="A", status="Done", summary="ready to ship"),
           csv_issue(key="B", status="Open", summary="ready")]
    assert [i.key for i in apply_filters(api, filters, now=NOW)] == ["A"]
    assert [i.key for i in apply_filters(csv, filters, now=NOW)] == ["A"]


def test_multiple_clauses_are_anded():
    issues = [
        api_issue(key="A", status="Open", issue_type="Bug", client="Acme"),
        api_issue(key="B", status="Open", issue_type="Bug", client="Globex"),
    ]
    f = SearchFilters(statuses=["Open"], issue_types=["Bug"], client="acme")
    assert [i.key for i in apply_filters(issues, f, now=NOW)] == ["A"]


def test_matches_single_issue():
    assert matches(api_issue(key="A", status="Open"), SearchFilters(statuses=["Open"]), now=NOW)
    assert not matches(api_issue(key="A", status="Done"),
                       SearchFilters(statuses=["Open"]), now=NOW)


# --------------------------------------------------------------------------- #
# Workbench additions applied client-side (work on CSV data too)
# --------------------------------------------------------------------------- #
def test_project_filter_matches_key_or_name():
    issues = [api_issue(key="A", project_key="ABC"),
              api_issue(key="B", project_name="Widgets"),
              api_issue(key="C", project_key="ZZZ")]
    kept = apply_filters(issues, SearchFilters(projects=["abc", "widgets"]), now=NOW)
    assert {i.key for i in kept} == {"A", "B"}


def test_status_category_filter():
    issues = [api_issue(key="A", status_category="Done"),
              api_issue(key="B", status_category="In Progress")]
    kept = apply_filters(issues, SearchFilters(status_categories=["Done"]), now=NOW)
    assert [i.key for i in kept] == ["A"]


def test_unresolved_filter_drops_resolved():
    issues = [api_issue(key="open", status_category="In Progress"),
              api_issue(key="done", resolved="2026-05-01T00:00:00+00:00")]
    kept = apply_filters(issues, SearchFilters(unresolved=True), now=NOW)
    assert [i.key for i in kept] == ["open"]


def test_created_and_resolved_day_filters():
    issues = [api_issue(key="new", created="2026-07-05T00:00:00+00:00"),
              api_issue(key="old", created="2026-01-01T00:00:00+00:00")]
    kept = apply_filters(issues, SearchFilters(created_days=7), now=NOW)
    assert [i.key for i in kept] == ["new"]


def test_due_within_days():
    soon = api_issue(key="soon", due_date="2026-07-10")
    later = api_issue(key="later", due_date="2026-09-01")
    none = api_issue(key="none")
    kept = apply_filters([soon, later, none], SearchFilters(due_days=7), now=NOW)
    assert [i.key for i in kept] == ["soon"]
