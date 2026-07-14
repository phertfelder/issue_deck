"""Tests for dataset merging (conflict rules) and delta preview."""

from __future__ import annotations

from issue_deck.merge import (
    ConflictRule,
    build_delta,
    is_resolved,
    merge_collections,
    parse_timestamp,
)
from issue_deck.schema import JiraUser, NormalizedIssue, SourceMetadata

API = SourceMetadata.for_api("cloud", imported_at="2026-01-01T00:00:00+00:00")
CSV = SourceMetadata.for_csv("f.csv", imported_at="2026-01-01T00:00:00+00:00")


def issue(key, *, updated="", origin="api", status="", assignee="",
          story_points=None, priority="", severity="", resolved="",
          status_category=""):
    return NormalizedIssue(
        key=key,
        updated=updated,
        status=status,
        status_category=status_category,
        assignee=JiraUser(display_name=assignee),
        story_points=story_points,
        priority=priority,
        severity=severity,
        resolved=resolved,
        source=API if origin == "api" else CSV,
    )


# --------------------------------------------------------------------------- #
# merge — additions & ordering
# --------------------------------------------------------------------------- #
def test_merge_adds_new_keys_and_preserves_order():
    base = [issue("A"), issue("B")]
    incoming = [issue("C"), issue("D")]
    result = merge_collections(base, incoming)
    assert [i.key for i in result.issues] == ["A", "B", "C", "D"]
    assert result.added == 2
    assert result.conflicts == 0


def test_merge_empty_key_always_appended():
    base = [issue("A")]
    incoming = [issue(""), issue("")]
    result = merge_collections(base, incoming)
    assert len(result.issues) == 3
    assert result.added == 2
    assert result.conflicts == 0


# --------------------------------------------------------------------------- #
# merge — conflict rules
# --------------------------------------------------------------------------- #
def test_newest_wins_replaces_in_place():
    base = [issue("A", updated="2026-01-01T00:00:00+00:00", status="Open")]
    incoming = [issue("A", updated="2026-06-01T00:00:00+00:00", status="Done")]
    result = merge_collections(base, incoming, ConflictRule.NEWEST_WINS)
    assert len(result.issues) == 1
    assert result.issues[0].status == "Done"
    assert result.updated == 1
    assert result.conflicts == 1


def test_newest_wins_keeps_existing_when_incoming_older():
    base = [issue("A", updated="2026-06-01T00:00:00+00:00", status="Done")]
    incoming = [issue("A", updated="2026-01-01T00:00:00+00:00", status="Open")]
    result = merge_collections(base, incoming, ConflictRule.NEWEST_WINS)
    assert result.issues[0].status == "Done"
    assert result.unchanged == 1
    assert result.updated == 0


def test_api_wins_prefers_api_origin():
    base = [issue("A", origin="csv", status="CsvStatus")]
    incoming = [issue("A", origin="api", status="ApiStatus")]
    result = merge_collections(base, incoming, ConflictRule.API_WINS)
    assert result.issues[0].status == "ApiStatus"
    # And if the incoming is CSV while base is API, API (existing) is kept.
    base2 = [issue("A", origin="api", status="ApiStatus")]
    incoming2 = [issue("A", origin="csv", status="CsvStatus")]
    r2 = merge_collections(base2, incoming2, ConflictRule.API_WINS)
    assert r2.issues[0].status == "ApiStatus"
    assert r2.unchanged == 1


def test_csv_wins_prefers_csv_origin():
    base = [issue("A", origin="api", status="ApiStatus")]
    incoming = [issue("A", origin="csv", status="CsvStatus")]
    result = merge_collections(base, incoming, ConflictRule.CSV_WINS)
    assert result.issues[0].status == "CsvStatus"


def test_ask_uses_resolver_callback():
    base = [issue("A", status="Old")]
    incoming = [issue("A", status="New")]
    seen = []

    def resolver(existing, inc):
        seen.append((existing.status, inc.status))
        return existing  # user chose to keep the existing one

    result = merge_collections(base, incoming, ConflictRule.ASK, resolver=resolver)
    assert result.issues[0].status == "Old"
    assert seen == [("Old", "New")]


def test_ask_without_resolver_falls_back_to_newest():
    base = [issue("A", updated="2026-01-01T00:00:00+00:00", status="Old")]
    incoming = [issue("A", updated="2026-06-01T00:00:00+00:00", status="New")]
    result = merge_collections(base, incoming, ConflictRule.ASK)
    assert result.issues[0].status == "New"


# --------------------------------------------------------------------------- #
# resolved detection + timestamp parsing
# --------------------------------------------------------------------------- #
def test_is_resolved_by_date_or_category():
    assert is_resolved(issue("A", resolved="2026-05-01T00:00:00+00:00"))
    assert is_resolved(issue("A", status_category="Done"))
    assert not is_resolved(issue("A", status_category="In Progress"))


def test_parse_timestamp_handles_jira_offset_and_bare_date():
    assert parse_timestamp("2026-04-01T08:00:00.000+0000") is not None
    assert parse_timestamp("2026-04-20") is not None
    assert parse_timestamp("not-a-date") is None
    assert parse_timestamp("") is None


# --------------------------------------------------------------------------- #
# delta preview
# --------------------------------------------------------------------------- #
def test_delta_new_removed_carried_over():
    current = [issue("A"), issue("B")]
    incoming = [issue("B"), issue("C")]
    delta = build_delta(current, incoming)
    assert [i.key for i in delta.new_issues] == ["C"]
    assert [i.key for i in delta.removed_issues] == ["A"]
    assert delta.carried_over == ["B"]


def test_delta_newly_resolved():
    current = [issue("A", status="In Progress", status_category="In Progress")]
    incoming = [issue("A", status="Done", status_category="Done",
                      resolved="2026-06-01T00:00:00+00:00")]
    delta = build_delta(current, incoming)
    assert delta.newly_resolved == ["A"]
    # Status change is also captured.
    assert delta.status_changes[0].before == "In Progress"
    assert delta.status_changes[0].after == "Done"


def test_delta_field_changes():
    current = [issue("A", status="Open", assignee="Ada", story_points=3,
                     priority="Low", severity="S3")]
    incoming = [issue("A", status="Done", assignee="Grace", story_points=8,
                      priority="High", severity="S1")]
    delta = build_delta(current, incoming)
    assert delta.status_changes[0].after == "Done"
    assert delta.assignee_changes[0].before == "Ada"
    assert delta.assignee_changes[0].after == "Grace"
    assert delta.estimate_changes[0].before == "3"
    assert delta.estimate_changes[0].after == "8"
    assert delta.priority_changes[0].after == "High"
    assert delta.severity_changes[0].after == "S1"
    assert delta.is_destructive is True


def test_delta_no_changes_is_not_destructive():
    current = [issue("A", status="Open", assignee="Ada")]
    incoming = [issue("A", status="Open", assignee="Ada")]
    delta = build_delta(current, incoming)
    assert delta.carried_over == ["A"]
    assert delta.is_destructive is False
    assert delta.summary()["status_changes"] == 0


def test_delta_summary_counts():
    current = [issue("A", status="Open"), issue("B")]
    incoming = [issue("A", status="Done"), issue("C")]
    summary = build_delta(current, incoming).summary()
    assert summary["new"] == 1        # C
    assert summary["removed"] == 1    # B
    assert summary["carried_over"] == 1  # A
    assert summary["status_changes"] == 1
