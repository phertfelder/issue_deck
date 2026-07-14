"""Characterization tests for all four exporters + the run_export dispatcher.

The JSONL/dict shape (via dataclasses.asdict) must remain identical to the
pre-refactor output, so the exact key order is asserted here.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from issue_deck.exporters import (
    export_csv,
    export_jsonl,
    export_markdown_combined,
    export_markdown_per_ticket,
    issue_to_markdown,
    run_export,
)
from issue_deck.exporters.csv_export import COLUMNS
from issue_deck.models import ExportOptions, JiraComment, JiraIssue
from issue_deck.schema import JiraUser, NormalizedIssue

# The exact serialized issue key order (frozen contract for JSONL consumers).
EXPECTED_ISSUE_KEYS = [
    "key", "url", "summary", "status", "issuetype", "priority", "severity",
    "client", "assignee", "reporter", "created", "updated", "components",
    "labels", "description", "comments",
]
EXPECTED_COMMENT_KEYS = ["author", "created", "updated", "body"]


def _full_issue() -> JiraIssue:
    return JiraIssue(
        key="CLOUD-1",
        url="https://example.atlassian.net/browse/CLOUD-1",
        summary="Payment reconciliation fails",
        status="In Progress",
        issuetype="Bug",
        priority="High",
        severity="S1",
        client="Acme Corp",
        assignee="Ada Lovelace",
        reporter="Grace Hopper",
        created="2026-01-01T10:00:00.000+0000",
        updated="2026-02-01T12:30:00.000+0000",
        components=["Backend", "API"],
        labels=["urgent"],
        description="The job **crashes** on café names.",
        comments=[JiraComment(author="Ada Lovelace",
                              created="2026-02-01T10:00:00.000+0000",
                              updated="", body="Investigating.")],
    )


def _sparse_issue() -> JiraIssue:
    return JiraIssue(
        key="MIN-2",
        url="https://example.atlassian.net/browse/MIN-2",
        summary="Sparse ticket",
        status="Open",
        issuetype="Task",
        priority="Medium",
        created="2026-03-01T00:00:00.000+0000",
        updated="2026-03-01T00:00:00.000+0000",
    )


# --------------------------------------------------------------------------- #
# asdict / JSONL shape
# --------------------------------------------------------------------------- #
def test_asdict_key_order_is_frozen():
    d = asdict(_full_issue())
    assert list(d.keys()) == EXPECTED_ISSUE_KEYS
    assert list(d["comments"][0].keys()) == EXPECTED_COMMENT_KEYS


def test_jsonl_one_object_per_line_and_non_ascii(tmp_path):
    out = tmp_path / "e.jsonl"
    export_jsonl([_full_issue(), _sparse_issue()], str(out))
    raw = out.read_text(encoding="utf-8")
    lines = raw.splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["key"] == "CLOUD-1"
    assert json.loads(lines[1])["key"] == "MIN-2"
    # ensure_ascii=False keeps the literal character
    assert "café" in raw
    assert "\\u00e9" not in raw


def test_jsonl_comments_included(tmp_path):
    out = tmp_path / "e.jsonl"
    export_jsonl([_full_issue()], str(out))
    obj = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert obj["comments"][0]["body"] == "Investigating."


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def test_markdown_combined_header_and_content(tmp_path):
    out = tmp_path / "export.md"
    export_markdown_combined([_full_issue()], str(out))
    text = out.read_text(encoding="utf-8")
    assert "Exported 1 Jira issues on" in text
    assert "# CLOUD-1 — Payment reconciliation fails" in text
    assert "**URL:** https://example.atlassian.net/browse/CLOUD-1" in text
    assert "**Status:** In Progress" in text
    assert "**Type:** Bug" in text
    assert "**Priority:** High" in text
    assert "The job **crashes** on café names." in text
    assert "### Ada Lovelace — 2026-02-01T10:00:00.000+0000" in text
    assert "Investigating." in text


def test_markdown_optional_fields_present_when_set(tmp_path):
    out = tmp_path / "export.md"
    export_markdown_combined([_full_issue()], str(out))
    text = out.read_text(encoding="utf-8")
    assert "**Severity:** S1" in text
    assert "**Client:** Acme Corp" in text
    assert "**Components:** Backend, API" in text
    assert "**Labels:** urgent" in text


def test_markdown_optional_fields_absent_when_blank(tmp_path):
    out = tmp_path / "export.md"
    export_markdown_combined([_sparse_issue()], str(out))
    text = out.read_text(encoding="utf-8")
    assert "**Severity:**" not in text
    assert "**Client:**" not in text
    assert "**Components:**" not in text
    assert "**Labels:**" not in text
    assert "_(none)_" in text


def test_markdown_combined_separates_issues_with_rule(tmp_path):
    out = tmp_path / "export.md"
    export_markdown_combined([_full_issue(), _sparse_issue()], str(out))
    assert "\n\n---\n\n" in out.read_text(encoding="utf-8")


def test_markdown_per_ticket_one_file_per_key(tmp_path):
    folder = tmp_path / "tickets"
    export_markdown_per_ticket([_full_issue(), _sparse_issue()], str(folder))
    assert (folder / "CLOUD-1.md").exists()
    assert (folder / "MIN-2.md").exists()
    assert sorted(p.name for p in Path(folder).glob("*.md")) == ["CLOUD-1.md", "MIN-2.md"]


def test_markdown_per_ticket_contents_match(tmp_path):
    folder = tmp_path / "tickets"
    issue = _full_issue()
    export_markdown_per_ticket([issue], str(folder))
    assert (folder / "CLOUD-1.md").read_text(encoding="utf-8") == issue_to_markdown(issue)


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def test_csv_columns_and_values(tmp_path):
    out = tmp_path / "e.csv"
    export_csv([_full_issue()], str(out))
    with open(out, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == COLUMNS
    assert rows[0]["key"] == "CLOUD-1"
    assert rows[0]["severity"] == "S1"
    assert rows[0]["client"] == "Acme Corp"
    assert rows[0]["url"] == "https://example.atlassian.net/browse/CLOUD-1"
    # nested/extra fields never become columns
    assert "reporter" not in rows[0]
    assert "components" not in rows[0]


def test_csv_empty_optional_values(tmp_path):
    out = tmp_path / "e.csv"
    export_csv([_sparse_issue()], str(out))
    with open(out, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["key"] == "MIN-2"
    assert rows[0]["severity"] == ""
    assert rows[0]["client"] == ""


# --------------------------------------------------------------------------- #
# run_export dispatcher
# --------------------------------------------------------------------------- #
def test_run_export_dispatches_each_format(tmp_path):
    issues = [_full_issue()]
    run_export(issues, ExportOptions("markdown_combined", str(tmp_path / "a.md")))
    run_export(issues, ExportOptions("markdown_per_ticket", str(tmp_path / "folder")))
    run_export(issues, ExportOptions("jsonl", str(tmp_path / "a.jsonl")))
    run_export(issues, ExportOptions("csv", str(tmp_path / "a.csv")))
    assert (tmp_path / "a.md").exists()
    assert (tmp_path / "folder" / "CLOUD-1.md").exists()
    assert (tmp_path / "a.jsonl").exists()
    assert (tmp_path / "a.csv").exists()


# --------------------------------------------------------------------------- #
# Backward compatibility: exporters accept NormalizedIssue and emit the SAME
# bytes as the equivalent legacy JiraIssue (the migrated pipeline's contract).
# --------------------------------------------------------------------------- #
def _normalized_equivalent() -> NormalizedIssue:
    """A NormalizedIssue whose legacy down-conversion equals ``_full_issue()``."""
    return NormalizedIssue(
        key="CLOUD-1",
        url="https://example.atlassian.net/browse/CLOUD-1",
        summary="Payment reconciliation fails",
        status="In Progress",
        issue_type="Bug",
        priority="High",
        severity="S1",
        client="Acme Corp",
        assignee=JiraUser(display_name="Ada Lovelace"),
        reporter=JiraUser(display_name="Grace Hopper"),
        created="2026-01-01T10:00:00.000+0000",
        updated="2026-02-01T12:30:00.000+0000",
        components=["Backend", "API"],
        labels=["urgent"],
        description="The job **crashes** on café names.",
        comments=[JiraComment(author="Ada Lovelace",
                              created="2026-02-01T10:00:00.000+0000",
                              updated="", body="Investigating.")],
    )


def test_jsonl_normalized_matches_legacy(tmp_path):
    legacy_out = tmp_path / "legacy.jsonl"
    norm_out = tmp_path / "norm.jsonl"
    export_jsonl([_full_issue()], str(legacy_out))
    export_jsonl([_normalized_equivalent()], str(norm_out))
    assert norm_out.read_text(encoding="utf-8") == legacy_out.read_text(encoding="utf-8")


def test_csv_normalized_matches_legacy(tmp_path):
    legacy_out = tmp_path / "legacy.csv"
    norm_out = tmp_path / "norm.csv"
    export_csv([_full_issue()], str(legacy_out))
    export_csv([_normalized_equivalent()], str(norm_out))
    assert norm_out.read_text(encoding="utf-8") == legacy_out.read_text(encoding="utf-8")


def test_markdown_normalized_matches_legacy():
    assert issue_to_markdown(_normalized_equivalent()) == issue_to_markdown(_full_issue())


def test_run_export_accepts_normalized(tmp_path):
    run_export([_normalized_equivalent()], ExportOptions("jsonl", str(tmp_path / "n.jsonl")))
    obj = json.loads((tmp_path / "n.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert obj["key"] == "CLOUD-1"
    assert obj["issuetype"] == "Bug"  # issue_type -> legacy issuetype key preserved
