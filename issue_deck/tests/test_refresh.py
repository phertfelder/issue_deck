"""Tests for refresh: extended delta categories, validation, and the report.

The delta *primitives* (new/removed/carried/status/assignee/estimate/priority/
severity) are covered in ``test_merge.py``; this module focuses on the pieces the
refresh feature adds: the extra change categories, the flattened ``rows()`` view,
the CSV-missing-key block, and report rendering.
"""

from __future__ import annotations

import csv
import io

from issue_deck.merge import DeltaCategory, build_delta
from issue_deck.refresh import (
    RefreshPlan,
    removed_keys,
    render_delta_report,
    validate_incoming,
)
from issue_deck.schema import JiraComment, JiraUser, NormalizedIssue, SourceMetadata

API = SourceMetadata.for_api("cloud")
CSV = SourceMetadata.for_csv("f.csv")


def issue(key, *, origin="api", **kw):
    if isinstance(kw.get("assignee"), str):
        kw["assignee"] = JiraUser(display_name=kw["assignee"])
    if isinstance(kw.get("reporter"), str):
        kw["reporter"] = JiraUser(display_name=kw["reporter"])
    return NormalizedIssue(key=key, source=API if origin == "api" else CSV, **kw)


# --------------------------------------------------------------------------- #
# Extended delta categories
# --------------------------------------------------------------------------- #
def test_delta_reopened_is_the_inverse_of_newly_resolved():
    current = [issue("A", status="Done", status_category="Done",
                     resolved="2026-01-01T00:00:00+00:00")]
    incoming = [issue("A", status="In Progress", status_category="In Progress")]
    delta = build_delta(current, incoming)
    assert delta.reopened == ["A"]
    assert delta.newly_resolved == []
    assert delta.is_destructive is True


def test_delta_reporter_summary_and_multivalue_changes():
    current = [issue("A", reporter="Ada", summary="Old title",
                     components=["UI"], labels=["x"],
                     project_key="P1", project_name="One",
                     epic_key="E1", epic_name="Epic One",
                     updated="2026-01-01T00:00:00+00:00")]
    incoming = [issue("A", reporter="Bob", summary="New title",
                      components=["UI", "API"], labels=["y"],
                      project_key="P2", project_name="Two",
                      epic_key="E2", epic_name="Epic Two",
                      updated="2026-06-01T00:00:00+00:00")]
    delta = build_delta(current, incoming)
    assert delta.reporter_changes[0].after == "Bob"
    assert delta.summary_changes[0].before == "Old title"
    assert delta.component_changes[0].after == "UI, API"
    assert delta.label_changes[0].before == "x"
    assert "P2" in delta.project_changes[0].after
    assert "E2" in delta.epic_changes[0].after
    assert delta.updated_changes[0].before == "2026-01-01T00:00:00+00:00"


def test_component_order_change_is_not_a_change():
    # Multi-valued fields compare order-insensitively.
    current = [issue("A", components=["UI", "API"])]
    incoming = [issue("A", components=["API", "UI"])]
    delta = build_delta(current, incoming)
    assert delta.component_changes == []


def test_comment_count_only_registers_when_comments_available():
    c = JiraComment(author="Ada", body="hi", created="2026-01-01")
    # Both sides empty (comments never loaded) -> not a change.
    assert build_delta([issue("A")], [issue("A")]).comment_count_changes == []
    # One side has comments -> counted.
    delta = build_delta([issue("A")], [issue("A", comments=[c])])
    assert delta.comment_count_changes[0].before == "0"
    assert delta.comment_count_changes[0].after == "1"


def test_unchanged_keys_excludes_any_changed_issue():
    current = [issue("A", status="Open"), issue("B", status="Open")]
    incoming = [issue("A", status="Done"), issue("B", status="Open")]
    delta = build_delta(current, incoming)
    assert delta.unchanged_keys == ["B"]        # A changed status
    assert "A" in delta.changed_keys


def test_counts_only_lists_nonempty_categories():
    current = [issue("A", status="Open"), issue("B")]
    incoming = [issue("A", status="Done"), issue("C")]
    counts = build_delta(current, incoming).counts()
    assert counts[DeltaCategory.NEW] == 1
    assert counts[DeltaCategory.REMOVED] == 1
    assert counts[DeltaCategory.STATUS] == 1
    assert DeltaCategory.SEVERITY not in counts   # no severity change


def test_rows_flattens_every_category():
    current = [issue("A", status="Open", assignee="Ada"), issue("B")]
    incoming = [issue("A", status="Done", assignee="Grace"), issue("C")]
    rows = build_delta(current, incoming).rows()
    cats = {(r.key, r.category) for r in rows}
    assert ("C", DeltaCategory.NEW) in cats
    assert ("B", DeltaCategory.REMOVED) in cats
    assert ("A", DeltaCategory.STATUS) in cats
    assert ("A", DeltaCategory.ASSIGNEE) in cats


# --------------------------------------------------------------------------- #
# Validation — the CSV missing-key block (acceptance criterion)
# --------------------------------------------------------------------------- #
def test_csv_missing_key_blocks_refresh():
    incoming = [issue("PROJ-1", origin="csv"), issue("", origin="csv")]
    v = validate_incoming(incoming, is_csv=True)
    assert not v.ok
    assert "no issue key" in v.blocking[0]


def test_api_missing_key_does_not_block():
    # Only CSV imports are blocked; an API result with a blank key is unusual
    # but not the modeled failure mode.
    v = validate_incoming([issue("")], is_csv=False)
    assert v.ok


def test_empty_incoming_warns_but_does_not_block():
    v = validate_incoming([], is_csv=False)
    assert v.ok
    assert v.warnings and "no issues" in v.warnings[0]


def test_duplicate_keys_warn():
    incoming = [issue("A", origin="csv"), issue("A", origin="csv")]
    v = validate_incoming(incoming, is_csv=True)
    assert v.ok
    assert any("duplicate" in w for w in v.warnings)


def test_removed_keys_helper():
    delta = build_delta([issue("A"), issue("B")], [issue("A")])
    assert removed_keys(delta) == ["B"]


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #
def test_render_csv_report_is_parseable_and_categorized():
    current = [issue("A", status="Open"), issue("B")]
    incoming = [issue("A", status="Done"), issue("C", summary="fresh")]
    text = render_delta_report(build_delta(current, incoming), fmt="csv")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["Key", "Category", "Summary", "Before", "After"]
    body = rows[1:]
    assert ["A", "Status changed", "", "Open", "Done"] in body
    assert any(r[0] == "C" and r[1] == "New issues" for r in body)


def test_render_text_report_groups_and_reports_no_change():
    same = [issue("A", status="Open")]
    text = render_delta_report(build_delta(same, same), fmt="text")
    assert "No changes" in text

    changed = render_delta_report(
        build_delta([issue("A", status="Open")], [issue("A", status="Done")]),
        fmt="text",
    )
    assert "Status changed (1)" in changed
    assert "A: Open -> Done" in changed


def test_refresh_plan_is_merge_flag():
    assert RefreshPlan(apply_mode="merge").is_merge
    assert not RefreshPlan(apply_mode="replace").is_merge
