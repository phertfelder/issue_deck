"""Unit tests for the pure analytics engine (no Qt, no network)."""

from __future__ import annotations

import datetime as _dt

from issue_deck.analytics import (
    NONE_LABEL,
    UNASSIGNED_LABEL,
    build_report,
)
from issue_deck.schema import JiraComment, JiraUser, NormalizedIssue

NOW = _dt.datetime(2026, 7, 9, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _iso(days_ago: int) -> str:
    return (NOW - _dt.timedelta(days=days_ago)).isoformat()


def _issue(key="X-1", *, assignee="", reporter="", **kw) -> NormalizedIssue:
    kw.setdefault("updated", _iso(0))
    kw.setdefault("created", _iso(0))
    return NormalizedIssue(
        key=key,
        assignee=JiraUser(display_name=assignee),
        reporter=JiraUser(display_name=reporter),
        **kw,
    )


# --------------------------------------------------------------------------- #
# Totals / open-done
# --------------------------------------------------------------------------- #
def test_total_and_open_done():
    issues = [
        _issue("A", status="Open"),
        _issue("B", resolved=_iso(1)),
        _issue("C", status_category="Done"),
    ]
    r = build_report(issues, now=NOW)
    assert r.total == 3
    assert r.open_count == 1
    assert r.done_count == 2
    ovd = r.section("Open vs done")
    assert ovd.row("Open").count == 1
    assert ovd.row("Done").count == 2
    assert set(ovd.row("Done").keys) == {"B", "C"}


def test_empty_dataset_is_safe():
    r = build_report([], now=NOW)
    assert r.total == 0
    # Every section still renders (with zero counts), so the UI has nothing to
    # special-case.
    assert r.section("By status") is not None
    assert r.section("Risk") is not None


# --------------------------------------------------------------------------- #
# Breakdowns + graceful degradation
# --------------------------------------------------------------------------- #
def test_by_assignee_and_missing_value_bucket():
    issues = [
        _issue("A", assignee="Alice"),
        _issue("B", assignee="Alice"),
        _issue("C", assignee=""),  # unassigned -> (none)
    ]
    rows = build_report(issues, now=NOW).section("By assignee").rows
    top = rows[0]
    assert top.label == "Alice" and top.count == 2
    assert any(r.label == NONE_LABEL and r.count == 1 for r in rows)


def test_by_component_is_multivalued():
    issues = [
        _issue("A", components=["UI", "API"]),
        _issue("B", components=["UI"]),
        _issue("C", components=[]),  # -> (none)
    ]
    grp = build_report(issues, now=NOW).section("By component")
    assert grp.row("UI").count == 2
    assert grp.row("API").count == 1
    assert grp.row(NONE_LABEL).count == 1


def test_rows_sorted_by_count_desc():
    issues = [_issue("A", priority="Low")] + [
        _issue(f"H{i}", priority="High") for i in range(3)
    ]
    rows = build_report(issues, now=NOW).section("By priority").rows
    assert [r.label for r in rows] == ["High", "Low"]


# --------------------------------------------------------------------------- #
# Staleness / aging
# --------------------------------------------------------------------------- #
def test_staleness_buckets_are_nested_thresholds():
    issues = [
        _issue("fresh", updated=_iso(2)),
        _issue("d20", updated=_iso(20)),
        _issue("d45", updated=_iso(45)),
        _issue("d90", updated=_iso(90)),
    ]
    grp = build_report(issues, now=NOW).section("Stale issues")
    assert grp.row("Updated > 14 days ago").count == 3   # 20,45,90
    assert grp.row("Updated > 30 days ago").count == 2   # 45,90
    assert grp.row("Updated > 60 days ago").count == 1   # 90


def test_created_age_buckets_and_unknown():
    issues = [
        _issue("new", created=_iso(3)),
        _issue("mid", created=_iso(20)),
        _issue("old", created=_iso(200)),
        _issue("bad", created="not-a-date"),
    ]
    grp = build_report(issues, now=NOW).section("Age since created")
    assert grp.row("≤ 7 days").count == 1
    assert grp.row("8–30 days").count == 1
    assert grp.row("> 90 days").count == 1
    assert grp.row(NONE_LABEL).count == 1


# --------------------------------------------------------------------------- #
# Workload
# --------------------------------------------------------------------------- #
def test_workload_points_and_unassigned():
    issues = [
        _issue("A", assignee="Alice", story_points=3),
        _issue("B", assignee="Alice", story_points=2),
        _issue("C", assignee="", story_points=5),
    ]
    grp = build_report(issues, now=NOW).section("Workload by assignee")
    assert grp.has_points
    assert grp.row("Alice").count == 2
    assert grp.row("Alice").points == 5     # whole total stays an int
    un = grp.row(UNASSIGNED_LABEL)
    assert un.count == 1 and un.points == 5
    # Unassigned always sorts last.
    assert grp.rows[-1].label == UNASSIGNED_LABEL


def test_workload_points_none_when_unavailable():
    grp = build_report([_issue("A", assignee="Al")], now=NOW).section("Workload by assignee")
    assert grp.row("Al").points is None
    assert not grp.has_points


# --------------------------------------------------------------------------- #
# Risk
# --------------------------------------------------------------------------- #
def test_risk_flags():
    issues = [
        _issue("stalehigh", priority="Critical", updated=_iso(40)),
        _issue("blocked", status="Blocked"),
        _issue("old", created=_iso(200)),                      # unresolved + old
        _issue("noassignee"),
        _issue("nopoints"),                                    # story_points None
        _issue("duesoon", due_date=_iso(-3)),                  # due in 3 days
        _issue("overdue", due_date=_iso(5)),                   # 5 days past due
        _issue("done", resolved=_iso(1), created=_iso(200)),   # resolved -> excluded from "old"
    ]
    grp = build_report(issues, now=NOW).section("Risk")
    assert "stalehigh" in grp.row("High priority/severity & stale (> 14d)").keys
    assert grp.row("Blocked").count == 1
    old = grp.row("Old unresolved (created > 90d)")
    assert "old" in old.keys and "done" not in old.keys
    assert grp.row("Missing story points (unresolved)").count >= 1
    assert grp.row("Due soon (≤ 7d, unresolved)").count == 1
    assert grp.row("Overdue (unresolved)").count == 1


# --------------------------------------------------------------------------- #
# Recent activity / comments degradation
# --------------------------------------------------------------------------- #
def test_updated_activity_windows():
    issues = [
        _issue("today", updated=_iso(0)),
        _issue("d5", updated=_iso(5)),
        _issue("d20", updated=_iso(20)),
    ]
    grp = build_report(issues, now=NOW).section("Recent activity — updated")
    assert grp.row("Updated in last 1 day(s)").count == 1
    assert grp.row("Updated in last 7 day(s)").count == 2
    assert grp.row("Updated in last 30 day(s)").count == 3


def test_commented_activity_degrades_when_no_comments():
    grp = build_report([_issue("A")], now=NOW).section("Recent activity — commented")
    assert grp.rows == []
    assert grp.note  # explains comments weren't loaded


def test_commented_activity_when_comments_present():
    issues = [
        _issue("A", comments=[JiraComment(author="x", created=_iso(2), body="hi")]),
        _issue("B", comments=[JiraComment(author="y", created=_iso(20), body="ho")]),
    ]
    r = build_report(issues, now=NOW)
    assert r.comments_loaded
    grp = r.section("Recent activity — commented")
    assert grp.row("Commented in last 7 day(s)").count == 1
    assert grp.row("Commented in last 30 day(s)").count == 2


# --------------------------------------------------------------------------- #
# Click-through invariant
# --------------------------------------------------------------------------- #
def test_metric_keys_resolve_to_counts():
    issues = [_issue("A", status="Open"), _issue("B", status="Open"),
              _issue("C", status="Done", status_category="Done")]
    r = build_report(issues, now=NOW)
    for section in r.sections:
        for row in section.rows:
            # Keys never exceed the count and reference real issues.
            assert len(row.keys) <= row.count
            assert all(k in {"A", "B", "C"} for k in row.keys)
