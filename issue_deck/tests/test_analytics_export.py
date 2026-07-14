"""Tests for Markdown/CSV rendering of an analytics report."""

from __future__ import annotations

import csv
import datetime as _dt
import io

from issue_deck.analytics import build_report
from issue_deck.exporters import render_analytics_csv, render_analytics_markdown
from issue_deck.schema import JiraUser, NormalizedIssue

NOW = _dt.datetime(2026, 7, 9, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _issue(key, *, assignee="", **kw):
    kw.setdefault("updated", NOW.isoformat())
    kw.setdefault("created", NOW.isoformat())
    return NormalizedIssue(key=key, assignee=JiraUser(display_name=assignee), **kw)


def _report():
    return build_report(
        [
            _issue("A", status="Open", assignee="Alice", story_points=3),
            _issue("B", status="Open", assignee="Alice"),
            _issue("C", status="Done", status_category="Done", assignee=""),
        ],
        now=NOW,
    )


def test_markdown_has_header_and_sections():
    md = render_analytics_markdown(_report())
    assert md.startswith("# Analytics summary")
    assert "**Total issues:** 3" in md
    assert "## By status" in md
    assert "## Workload by assignee" in md
    # Workload carries a points column; other sections do not.
    assert "| Story points |" in md
    assert md.endswith("\n")


def test_markdown_notes_missing_comments():
    md = render_analytics_markdown(_report())
    assert "## Recent activity — commented" in md
    assert "Comments were not loaded" in md


def test_csv_is_parseable_flat_schema():
    out = render_analytics_csv(_report())
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == ["section", "metric", "count", "percent", "story_points"]
    by_status = [r for r in rows if r[0] == "By status"]
    assert {r[1] for r in by_status} == {"Open", "Done"}
    open_row = next(r for r in by_status if r[1] == "Open")
    assert open_row[2] == "2"
    assert open_row[3] == "66.7%"
    # Workload row surfaces the summed points.
    alice = next(r for r in rows if r[0] == "Workload by assignee" and r[1] == "Alice")
    assert alice[4] == "3"
