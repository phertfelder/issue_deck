"""Tests for the in-memory store, the session indicator, and saved profiles."""

from __future__ import annotations

import json

from issue_deck.config import AppConfig
from issue_deck.csv_import import build_profile, parse_csv
from issue_deck.datasource import DataSourceInfo, DataSourceKind
from issue_deck.merge import ConflictRule
from issue_deck.models import SearchFilters
from issue_deck.schema import IssueCollection, NormalizedIssue, SourceMetadata
from issue_deck.store import (
    ImportSession,
    InMemoryIssueStore,
    SavedProfile,
    SessionKind,
)

API_INFO = DataSourceInfo(DataSourceKind.JIRA_API, "Jira API live search", "jql…", "cloud")
CSV_INFO = DataSourceInfo(DataSourceKind.CSV, "CSV import", "export.csv")


def coll(*keys, origin="api"):
    src = SourceMetadata.for_api("cloud") if origin == "api" else SourceMetadata.for_csv("f.csv")
    return IssueCollection(issues=[NormalizedIssue(key=k, source=src) for k in keys])


# --------------------------------------------------------------------------- #
# Store: clear / replace / merge
# --------------------------------------------------------------------------- #
def test_store_starts_empty():
    store = InMemoryIssueStore()
    assert store.is_empty()
    assert len(store) == 0
    assert store.kind == SessionKind.EMPTY


def test_store_replace_swaps_dataset():
    store = InMemoryIssueStore()
    store.replace(coll("A", "B"), API_INFO)
    assert [i.key for i in store.issues] == ["A", "B"]
    store.replace(coll("C"), API_INFO)  # replace is destructive
    assert [i.key for i in store.issues] == ["C"]
    assert store.kind == SessionKind.JIRA_API


def test_store_clear_resets_issues_and_session():
    store = InMemoryIssueStore()
    store.replace(coll("A"), API_INFO)
    store.clear()
    assert store.is_empty()
    assert store.kind == SessionKind.EMPTY


def test_store_merge_appends_and_reports():
    store = InMemoryIssueStore()
    store.replace(coll("A", "B"), API_INFO)
    result = store.merge(coll("B", "C"), ConflictRule.NEWEST_WINS, API_INFO)
    assert [i.key for i in store.issues] == ["A", "B", "C"]
    assert result.added == 1
    assert result.conflicts == 1


def test_store_collection_snapshot_is_independent():
    store = InMemoryIssueStore()
    store.replace(coll("A"), API_INFO)
    snap = store.collection()
    snap.issues.append(NormalizedIssue(key="X"))
    assert len(store) == 1  # mutating the snapshot must not touch the store


def test_store_preview_delta_does_not_mutate():
    store = InMemoryIssueStore()
    store.replace(coll("A", "B"), API_INFO)
    delta = store.preview_delta(coll("B", "C"))
    assert [i.key for i in delta.new_issues] == ["C"]
    assert [i.key for i in delta.removed_issues] == ["A"]
    assert len(store) == 2  # unchanged


# --------------------------------------------------------------------------- #
# Session: current-source indicator (API / CSV / Mixed)
# --------------------------------------------------------------------------- #
def test_session_kind_api_only():
    store = InMemoryIssueStore()
    store.replace(coll("A"), API_INFO)
    assert store.kind == SessionKind.JIRA_API
    assert "Jira API live search" in store.describe_source()


def test_session_kind_csv_only():
    store = InMemoryIssueStore()
    store.replace(coll("A", origin="csv"), CSV_INFO)
    assert store.kind == SessionKind.CSV
    assert "CSV import" in store.describe_source()


def test_session_kind_mixed_when_both_used():
    store = InMemoryIssueStore()
    store.replace(coll("A"), API_INFO)
    store.merge(coll("B", origin="csv"), ConflictRule.NEWEST_WINS, CSV_INFO)
    assert store.kind == SessionKind.MIXED
    assert "Mixed" in store.describe_source()


def test_session_describe_empty():
    assert ImportSession().describe() == "No data loaded"


def test_session_kind_labels():
    assert SessionKind.JIRA_API.label == "Jira API live search"
    assert SessionKind.CSV.label == "CSV import"
    assert SessionKind.MIXED.label == "Mixed/session data"


# --------------------------------------------------------------------------- #
# SavedProfile: round-trip + privacy
# --------------------------------------------------------------------------- #
def test_saved_profile_api_round_trip(tmp_path):
    cfg = AppConfig(base_url="https://x.atlassian.net", deployment="cloud",
                    email="tester@example.com")
    filters = SearchFilters(statuses=["Open"], text="crash")
    prof = SavedProfile.for_api("my-query", cfg, filters)
    path = prof.save(tmp_path)
    reloaded = SavedProfile.load(path)
    assert reloaded.kind == "jira_api"
    assert reloaded.base_url == "https://x.atlassian.net"
    assert reloaded.filters.statuses == ["Open"]
    assert reloaded.filters.text == "crash"


def test_saved_profile_csv_round_trip(tmp_path):
    parsed = parse_csv("Issue key,Summary\nPROJ-1,Hi\n", source_file_name="s.csv")
    prof = SavedProfile.for_csv("csv-recipe", build_profile(parsed))
    path = prof.save(tmp_path)
    reloaded = SavedProfile.load(path)
    assert reloaded.kind == "csv"
    assert reloaded.csv_profile.columns == ["Issue key", "Summary"]
    assert {m.target for m in reloaded.csv_profile.mappings} == {"key", "summary"}


def test_saved_api_profile_persists_no_credentials(tmp_path):
    cfg = AppConfig(base_url="https://x.atlassian.net", deployment="cloud",
                    email="tester@example.com")
    prof = SavedProfile.for_api("q", cfg, SearchFilters())
    text = prof.save(tmp_path).read_text(encoding="utf-8")
    data = json.loads(text)
    assert "token" not in text and "password" not in text
    # Identity is base_url + deployment only — no email/PII, no secrets.
    assert "email" not in data
    assert "tester@example.com" not in text


def test_saved_csv_profile_persists_no_rows(tmp_path):
    parsed = parse_csv("Issue key,Notes\nPROJ-1,SECRET-CELL\n", source_file_name="s.csv")
    prof = SavedProfile.for_csv("c", build_profile(parsed))
    text = prof.save(tmp_path).read_text(encoding="utf-8")
    assert "SECRET-CELL" not in text  # schema only, never rows
    assert "Notes" in text            # column names are fine
