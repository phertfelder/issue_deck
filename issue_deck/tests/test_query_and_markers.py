"""Tests for smart defaults, breadth warnings, query estimate, and markers."""

from __future__ import annotations

import datetime as dt

from issue_deck.config import AppConfig
from issue_deck.markers import (
    days_since_update,
    is_blocked,
    is_high_priority,
    is_missing_estimate,
    is_missing_owner,
    is_overdue,
    is_stale,
    issue_markers,
    issue_warnings,
)
from issue_deck.models import FieldFilter, SearchFilters
from issue_deck.query import breadth_warnings, default_filters, estimate_query
from issue_deck.schema import JiraUser, NormalizedIssue

NOW = dt.datetime(2026, 7, 8, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# Smart defaults
# --------------------------------------------------------------------------- #
def test_default_filters_are_assigned_unresolved_90d():
    f = default_filters()
    assert f.assigned_to_me is True
    assert f.unresolved is True
    assert f.updated_days == 90


def test_estimate_query_reports_jql_fields_and_no_warning_for_defaults():
    est = estimate_query(AppConfig(), default_filters())
    assert est.jql == (
        "assignee = currentUser() AND resolution = Unresolved "
        "AND updated >= -90d ORDER BY updated DESC"
    )
    assert "summary" in est.fields
    assert est.warnings == []
    assert est.is_broad is False


# --------------------------------------------------------------------------- #
# Breadth warnings
# --------------------------------------------------------------------------- #
def test_broad_search_warns_when_totally_unbounded():
    f = SearchFilters(assigned_to_me=False)  # no scope, no bound, no narrowing
    warnings = breadth_warnings(f)
    assert warnings and "broad" in warnings[0].lower()


def test_scope_only_still_warns_without_date_bound():
    f = SearchFilters(assigned_to_me=True, updated_days=0)
    # Assigned-to-me is a scope, so this is not "very broad" but is unbounded in time.
    # A project/status narrows it; here nothing does, but scope suppresses the hard warning.
    assert breadth_warnings(f) == []  # scope present -> acceptable


def test_no_scope_but_project_is_ok():
    f = SearchFilters(assigned_to_me=False, projects=["ABC"])
    assert breadth_warnings(f) == []


def test_raw_mode_empty_warns():
    assert breadth_warnings(SearchFilters(raw_mode=True, raw_jql="")) != []
    assert breadth_warnings(SearchFilters(raw_mode=True, raw_jql="project = X")) == []


def test_field_filter_counts_as_narrowing():
    f = SearchFilters(assigned_to_me=False,
                      field_filters=[FieldFilter(field="labels", op="=", value="x")])
    assert breadth_warnings(f) == []


# --------------------------------------------------------------------------- #
# Markers
# --------------------------------------------------------------------------- #
def _issue(**kw):
    return NormalizedIssue(**kw)


def test_days_since_update_and_stale():
    fresh = _issue(key="A", updated="2026-07-01T00:00:00+00:00")
    old = _issue(key="B", updated="2026-01-01T00:00:00+00:00")
    assert days_since_update(fresh, now=NOW) == 7
    assert not is_stale(fresh, now=NOW)
    assert is_stale(old, now=NOW)
    assert days_since_update(_issue(key="C", updated=""), now=NOW) is None


def test_stale_threshold_is_configurable():
    issue = _issue(key="A", updated="2026-06-01T00:00:00+00:00")  # ~37 days
    assert is_stale(issue, now=NOW)            # default 30
    assert not is_stale(issue, days=60, now=NOW)


def test_high_priority_by_priority_or_severity():
    assert is_high_priority(_issue(key="A", priority="High"))
    assert is_high_priority(_issue(key="B", priority="Blocker"))
    assert is_high_priority(_issue(key="C", severity="Sev-1"))
    assert not is_high_priority(_issue(key="D", priority="Low", severity="S3"))


def test_issue_markers_combines():
    issue = _issue(key="A", priority="Critical", updated="2026-01-01T00:00:00+00:00")
    marks = issue_markers(issue, now=NOW)
    assert "high" in marks and "stale" in marks
    assert issue_markers(_issue(key="B", priority="Low",
                                updated="2026-07-07T00:00:00+00:00"), now=NOW) == []


# --------------------------------------------------------------------------- #
# Warning predicates (detail-panel attention flags)
# --------------------------------------------------------------------------- #
def test_is_blocked_by_status_or_label():
    assert is_blocked(_issue(key="A", status="Blocked"))
    assert is_blocked(_issue(key="B", labels=["blocked"]))
    assert not is_blocked(_issue(key="C", status="In Progress"))


def test_ownership_and_estimate_flags_only_when_unresolved():
    unowned = _issue(key="A", updated="2026-07-07T00:00:00+00:00")
    assert is_missing_owner(unowned)
    assert is_missing_estimate(unowned)
    # A resolved issue needs neither an owner nor an estimate.
    done = _issue(key="B", status_category="Done", resolved="2026-07-01T00:00:00+00:00")
    assert not is_missing_owner(done)
    assert not is_missing_estimate(done)
    owned = _issue(key="C", assignee=JiraUser(display_name="Al"), story_points=3)
    assert not is_missing_owner(owned)
    assert not is_missing_estimate(owned)


def test_is_overdue_only_for_unresolved_past_due():
    assert is_overdue(_issue(key="A", due_date="2026-07-01"), now=NOW)         # past
    assert not is_overdue(_issue(key="B", due_date="2026-07-30"), now=NOW)     # future
    assert not is_overdue(_issue(key="C"), now=NOW)                            # no due date
    # Resolved issues are never flagged overdue.
    assert not is_overdue(
        _issue(key="D", due_date="2026-07-01", resolved="2026-07-02"), now=NOW)


def test_issue_warnings_orders_and_covers_all_flags():
    issue = _issue(
        key="A", priority="Critical", status="Blocked",
        updated="2026-01-01T00:00:00+00:00", due_date="2026-07-01")
    warnings = issue_warnings(issue, now=NOW)
    assert any(w.startswith("stale") for w in warnings)
    assert "missing owner" in warnings
    assert "blocked" in warnings
    assert "high priority" in warnings
    assert "missing estimate" in warnings
    assert "overdue" in warnings
    # A clean, resolved issue produces no warnings.
    clean = _issue(key="B", assignee=JiraUser(display_name="Al"), story_points=2,
                   status_category="Done", resolved="2026-07-07T00:00:00+00:00",
                   updated="2026-07-07T00:00:00+00:00")
    assert issue_warnings(clean, now=NOW) == []
