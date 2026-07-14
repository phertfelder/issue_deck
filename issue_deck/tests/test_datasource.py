"""Tests for the DataSource abstraction over API pulls and CSV imports.

Both sources must yield an ``IssueCollection`` of ``NormalizedIssue`` so nothing
downstream cares where the data came from. The API source is exercised with a
fake client (no HTTP); the CSV source runs the real local pipeline.
"""

from __future__ import annotations

from issue_deck.csv_import import ImportOptions, build_profile, parse_csv
from issue_deck.datasource import (
    CsvDataSource,
    DataSource,
    DataSourceKind,
    JiraApiDataSource,
)
from issue_deck.exporters import export_jsonl
from issue_deck.models import SearchFilters
from issue_deck.schema import IssueCollection, NormalizedIssue, SourceMetadata

CSV = (
    "Issue key,Summary,Status,Assignee\n"
    "PROJ-1,First,Open,Ada Lovelace\n"
    "PROJ-2,Second,Done,Grace Hopper\n"
)


class FakeClient:
    def __init__(self, raw_issues, comments=None):
        self._raw = raw_issues
        self._comments = comments or []

    def search(self, jql, fields, *, on_progress=None, cancel=None, on_retry=None,
               max_results=None):
        from issue_deck.jira_client import SearchOutcome
        if on_progress:
            on_progress(len(self._raw), len(self._raw))
        return SearchOutcome(list(self._raw), total=len(self._raw))

    def get_comments(self, key, *, cancel=None, on_retry=None):
        return self._comments


# --------------------------------------------------------------------------- #
# CSV source
# --------------------------------------------------------------------------- #
def test_csv_source_is_a_datasource_and_loads_collection():
    parsed = parse_csv(CSV)
    src = CsvDataSource(parsed, build_profile(parsed))
    assert isinstance(src, DataSource)  # runtime_checkable protocol
    coll = src.load()
    assert isinstance(coll, IssueCollection)
    assert len(coll) == 2
    assert all(isinstance(i, NormalizedIssue) for i in coll.issues)
    assert coll.issues[0].key == "PROJ-1"
    assert coll.issues[0].source.origin == "csv"


def test_csv_source_describe():
    parsed = parse_csv(CSV, source_file_name="export.csv")
    info = CsvDataSource(parsed, build_profile(parsed)).describe()
    assert info.kind == DataSourceKind.CSV
    assert info.label == "CSV import"
    assert info.detail == "export.csv"
    assert info.origin == "csv"


def test_csv_source_redacts_keys_when_opted_in():
    parsed = parse_csv(CSV)
    src = CsvDataSource(parsed, build_profile(parsed), ImportOptions(redact_keys=True))
    coll = src.load()
    assert coll.issues[0].key == "PROJ-•"


def test_csv_source_emits_status():
    msgs: list[str] = []
    parsed = parse_csv(CSV)
    CsvDataSource(parsed, build_profile(parsed)).load(on_status=msgs.append)
    assert any("CSV" in m for m in msgs)


# --------------------------------------------------------------------------- #
# API source
# --------------------------------------------------------------------------- #
def test_api_source_is_a_datasource_and_loads_collection(cloud_issue, cloud_cfg):
    src = JiraApiDataSource(FakeClient([cloud_issue]), cloud_cfg,
                            SearchFilters(load_comments=False))
    assert isinstance(src, DataSource)
    coll = src.load()
    assert isinstance(coll, IssueCollection)
    assert coll.issues[0].key == "CLOUD-1"
    assert coll.issues[0].source.origin == "api"
    assert coll.filters is not None


def test_api_source_describe_includes_jql(cloud_cfg):
    src = JiraApiDataSource(FakeClient([]), cloud_cfg,
                            SearchFilters(statuses=["Open"]))
    info = src.describe()
    assert info.kind == DataSourceKind.JIRA_API
    assert info.label == "Jira API live search"
    assert "assignee = currentUser()" in info.detail
    assert 'status in ("Open")' in info.detail
    assert info.deployment == "cloud"
    assert info.origin == "api"


def test_both_sources_produce_the_same_shape(cloud_issue, cloud_cfg):
    api = JiraApiDataSource(FakeClient([cloud_issue]), cloud_cfg,
                            SearchFilters(load_comments=False)).load()
    csv = CsvDataSource(parse_csv(CSV), build_profile(parse_csv(CSV))).load()
    # Exporters/consumers only rely on IssueCollection[NormalizedIssue].
    for coll in (api, csv):
        assert isinstance(coll, IssueCollection)
        assert all(isinstance(i, NormalizedIssue) for i in coll.issues)


def test_exporters_are_source_agnostic(tmp_path):
    """The same content exports identically whether it came from API or CSV."""
    fields = dict(key="X-1", summary="hi", status="Open", issue_type="Bug",
                  priority="High")
    api_issue = NormalizedIssue(source=SourceMetadata.for_api("cloud"), **fields)
    csv_issue = NormalizedIssue(source=SourceMetadata.for_csv("f.csv"), **fields)

    api_out = tmp_path / "api.jsonl"
    csv_out = tmp_path / "csv.jsonl"
    export_jsonl([api_issue], str(api_out))
    export_jsonl([csv_issue], str(csv_out))
    # Provenance is not part of the frozen legacy export shape, so bytes match.
    assert api_out.read_text(encoding="utf-8") == csv_out.read_text(encoding="utf-8")
