"""Tests for the local-only CSV sample import wizard (:mod:`issue_deck.csv_import`).

Every test drives the pipeline with a synthetic CSV built inline — never a real
file or a live Jira instance. The scenarios mirror the awkward shapes real Jira
exports produce: comma vs semicolon delimiters, quoted commas, duplicate
headers, a missing issue key, multi-value labels/components, a custom
story-points column, and both European and ISO date strings.
"""

from __future__ import annotations

import json

from issue_deck.csv_import import (
    ColumnType,
    ImportOptions,
    auto_detect_mappings,
    build_preview,
    build_profile,
    commit_import,
    infer_column_type,
    load_profile,
    parse_csv,
    profile_columns,
    read_csv_file,
    recommend_filters,
    redact_key,
    to_normalized_issues,
)
from issue_deck.schema import IssueCollection, NormalizedIssue

# --------------------------------------------------------------------------- #
# Synthetic CSV corpus
# --------------------------------------------------------------------------- #
COMMA_CSV = (
    "Issue key,Summary,Status,Issue Type,Priority,Assignee,Story Points\n"
    "PROJ-1,Login fails,Open,Bug,High,Ada Lovelace,3\n"
    "PROJ-2,Add export,In Progress,Story,Medium,Grace Hopper,5\n"
    "PROJ-3,Fix typo,Done,Task,Low,Ada Lovelace,1\n"
    "PROJ-4,Session bug,Open,Bug,High,Grace Hopper,2\n"
    "PROJ-5,Slow query,In Progress,Task,Medium,Ada Lovelace,8\n"
    "PROJ-6,Crash report,Done,Bug,Low,Grace Hopper,3\n"
)

SEMICOLON_CSV = (
    "Issue key;Summary;Status;Assignee\n"
    "PROJ-10;Café crash;Open;Ada Lovelace\n"
    "PROJ-11;Résumé parser;Done;Jean Dupont\n"
)

QUOTED_CSV = (
    'Issue key,Summary,Labels\n'
    'PROJ-20,"Crash, then hang","alpha,beta"\n'
    'PROJ-21,"Normal summary",gamma\n'
)

DUP_HEADER_CSV = (
    "Issue key,Status,Status,Summary\n"
    "PROJ-30,Open,Triaged,First\n"
    "PROJ-31,Done,Verified,Second\n"
)

MISSING_KEY_CSV = (
    "Summary,Status,Assignee\n"
    "No key here,Open,Ada\n"
    "Second row,Done,Grace\n"
)

MULTI_VALUE_CSV = (
    "Issue key,Labels,Components\n"
    "PROJ-40,alpha;beta;gamma,Frontend;Gateway\n"
    "PROJ-41,alpha,Backend\n"
)

EU_DATE_CSV = (
    "Issue key,Created,Due date\n"
    "PROJ-50,01/04/2026,20/04/2026\n"
    "PROJ-51,15/05/2026,30/05/2026\n"
)

ISO_DATE_CSV = (
    "Issue key,Created,Updated\n"
    "PROJ-60,2026-04-01T08:00:00.000+0000,2026-04-10T16:45:00.000+0000\n"
    "PROJ-61,2026-05-01T09:00:00.000+0000,2026-05-02T10:00:00.000+0000\n"
)

DUPLICATE_KEY_CSV = (
    "Issue key,Summary\n"
    "PROJ-70,First\n"
    "PROJ-70,Duplicate\n"
    "PROJ-71,Unique\n"
)


def _profile(csv_text: str, **kw):
    parsed = parse_csv(csv_text, **kw)
    return parsed, build_profile(parsed)


# --------------------------------------------------------------------------- #
# Stage 2 — parsing: delimiters, encoding, quotes, duplicate headers
# --------------------------------------------------------------------------- #
def test_parse_comma_delimited_counts():
    parsed = parse_csv(COMMA_CSV)
    assert parsed.delimiter == ","
    assert parsed.column_count == 7
    assert parsed.row_count == 6
    assert parsed.columns[0] == "Issue key"


def test_parse_semicolon_delimited_autodetected():
    parsed = parse_csv(SEMICOLON_CSV)
    assert parsed.delimiter == ";"
    assert parsed.column_count == 4
    assert parsed.row_count == 2
    assert parsed.rows[0]["Summary"] == "Café crash"


def test_parse_quoted_commas_stay_in_one_field():
    parsed = parse_csv(QUOTED_CSV)
    assert parsed.rows[0]["Summary"] == "Crash, then hang"
    assert parsed.rows[0]["Labels"] == "alpha,beta"


def test_parse_duplicate_headers_are_disambiguated():
    parsed = parse_csv(DUP_HEADER_CSV)
    assert parsed.columns == ["Issue key", "Status", "Status (2)", "Summary"]
    assert parsed.rows[0]["Status"] == "Open"
    assert parsed.rows[0]["Status (2)"] == "Triaged"


def test_parse_bytes_detects_utf8_and_cp1252():
    utf8 = "Issue key,Summary\nPROJ-1,café\n".encode("utf-8")
    p1 = parse_csv(utf8)
    assert p1.encoding == "utf-8"
    assert p1.rows[0]["Summary"] == "café"

    cp1252 = "Issue key,Summary\nPROJ-1,café\n".encode("cp1252")
    p2 = parse_csv(cp1252)
    assert p2.encoding == "cp1252"
    assert p2.rows[0]["Summary"] == "café"


def test_parse_utf8_bom_stripped_from_first_header():
    data = "﻿Issue key,Summary\nPROJ-1,Hi\n".encode("utf-8-sig")
    parsed = parse_csv(data)
    assert parsed.encoding == "utf-8-sig"
    assert parsed.columns[0] == "Issue key"


def test_parse_skips_blank_lines():
    parsed = parse_csv("Issue key,Summary\n\nPROJ-1,Hi\n\n")
    assert parsed.row_count == 1


def test_read_csv_file_keeps_basename_only(tmp_path):
    f = tmp_path / "export.csv"
    f.write_text(COMMA_CSV, encoding="utf-8")
    parsed = read_csv_file(f)
    assert parsed.source_file_name == "export.csv"
    assert str(tmp_path) not in parsed.source_file_name


# --------------------------------------------------------------------------- #
# Stage 3 — auto-detected field mapping
# --------------------------------------------------------------------------- #
def test_auto_detect_common_columns():
    mappings = {m.source: m.target for m in auto_detect_mappings(parse_csv(COMMA_CSV).columns)}
    assert mappings["Issue key"] == "key"
    assert mappings["Summary"] == "summary"
    assert mappings["Status"] == "status"
    assert mappings["Issue Type"] == "issue_type"
    assert mappings["Assignee"] == "assignee"
    assert mappings["Story Points"] == "story_points"


def test_auto_detect_jira_custom_field_header():
    cols = ["Issue key", "Custom field (Story Points)", "Custom field (Client)"]
    mappings = {m.source: m.target for m in auto_detect_mappings(cols)}
    assert mappings["Custom field (Story Points)"] == "story_points"
    assert mappings["Custom field (Client)"] == "client"


def test_auto_detect_transforms_assigned():
    by_target = {m.target: m for m in auto_detect_mappings(
        ["Assignee", "Labels", "Story Points", "Created"]
    )}
    assert by_target["assignee"].transform == "user"
    assert by_target["labels"].transform == "multi_select"
    assert by_target["story_points"].transform == "number"
    assert by_target["created"].transform == "date"


def test_auto_detect_duplicate_headers_map_target_once():
    mappings = auto_detect_mappings(["Issue key", "Status", "Status (2)"])
    status_maps = [m for m in mappings if m.target == "status"]
    assert len(status_maps) == 1
    assert status_maps[0].source == "Status"


def test_epic_link_vs_epic_name_disambiguation():
    mappings = {m.source: m.target for m in auto_detect_mappings(["Epic Link", "Epic Name"])}
    assert mappings["Epic Link"] == "epic_key"
    assert mappings["Epic Name"] == "epic_name"


# --------------------------------------------------------------------------- #
# Stage 4 — column profiling / type inference / recommendations
# --------------------------------------------------------------------------- #
def test_infer_number_date_bool_multi_text():
    assert infer_column_type(["1", "2", "3"]) == ColumnType.NUMBER
    assert infer_column_type(["2026-04-01", "2026-05-02"]) == ColumnType.DATE
    assert infer_column_type(["yes", "no", "yes"]) == ColumnType.BOOLEAN
    assert infer_column_type(["a;b", "c;d;e"]) == ColumnType.MULTI_SELECT
    assert infer_column_type(["Open", "Done", "Open", "Done"]) == ColumnType.SINGLE_SELECT
    # Highly unique free text stays TEXT.
    assert infer_column_type([f"unique summary {i}" for i in range(40)]) == ColumnType.TEXT


def test_infer_european_and_iso_dates():
    assert infer_column_type(["01/04/2026", "15/05/2026"]) == ColumnType.DATE
    assert infer_column_type(["12/Apr/26", "3/May/26"]) == ColumnType.DATE
    assert infer_column_type(["2026-04-01T08:00:00.000+0000"]) == ColumnType.DATE


def test_infer_respects_mapping_target_for_user():
    # Names look like plain text, but a user-mapped column must type as USER.
    assert infer_column_type(["Ada Lovelace", "Grace Hopper"], target="assignee") == ColumnType.USER


def test_profile_columns_coverage_and_uniques():
    parsed, profile = _profile(COMMA_CSV)
    by_name = {p.name: p for p in profile_columns(parsed, profile)}
    assert by_name["Status"].column_type == ColumnType.SINGLE_SELECT
    assert by_name["Status"].coverage == 1.0
    assert by_name["Assignee"].column_type == ColumnType.USER
    assert by_name["Assignee"].unique_count == 2
    assert by_name["Story Points"].column_type == ColumnType.NUMBER
    assert "Ada Lovelace" in by_name["Assignee"].examples


def test_profile_multi_value_unique_counts_elements():
    parsed, profile = _profile(MULTI_VALUE_CSV)
    by_name = {p.name: p for p in profile_columns(parsed, profile)}
    labels = by_name["Labels"]
    assert labels.column_type == ColumnType.MULTI_SELECT
    # alpha, beta, gamma across both rows
    assert labels.unique_count == 3


def test_recommend_filters_prefers_categorical_over_freetext():
    parsed, profile = _profile(COMMA_CSV)
    recs = recommend_filters(profile_columns(parsed, profile))
    cols = [r.column for r in recs]
    assert "Status" in cols
    assert "Assignee" in cols
    # Free-text / identifier columns are not recommended as filters.
    assert "Summary" not in cols
    assert "Issue key" not in cols


# --------------------------------------------------------------------------- #
# Row -> NormalizedIssue conversion for the awkward shapes
# --------------------------------------------------------------------------- #
def test_multi_value_labels_and_components_split():
    parsed, profile = _profile(MULTI_VALUE_CSV)
    issues = to_normalized_issues(parsed, profile)
    assert issues[0].labels == ["alpha", "beta", "gamma"]
    assert issues[0].components == ["Frontend", "Gateway"]
    assert issues[1].components == ["Backend"]


def test_story_points_custom_field_coerced_to_number():
    parsed, profile = _profile(COMMA_CSV)
    issues = to_normalized_issues(parsed, profile)
    assert [i.story_points for i in issues] == [3, 5, 1, 2, 8, 3]
    assert all(isinstance(i.story_points, int) for i in issues)


def test_european_dates_preserved_as_strings():
    parsed, profile = _profile(EU_DATE_CSV)
    issues = to_normalized_issues(parsed, profile)
    assert issues[0].created == "01/04/2026"
    assert issues[0].due_date == "20/04/2026"


def test_iso_dates_preserved_as_strings():
    parsed, profile = _profile(ISO_DATE_CSV)
    issues = to_normalized_issues(parsed, profile)
    assert issues[0].created == "2026-04-01T08:00:00.000+0000"
    assert issues[0].updated == "2026-04-10T16:45:00.000+0000"


def test_quoted_summary_and_labels_normalized():
    parsed, profile = _profile(QUOTED_CSV)
    issues = to_normalized_issues(parsed, profile)
    assert issues[0].summary == "Crash, then hang"
    assert issues[0].labels == ["alpha", "beta"]


# --------------------------------------------------------------------------- #
# Stage 5 — preview: chips, group-by, warnings
# --------------------------------------------------------------------------- #
def test_preview_group_by_status_counts():
    parsed, profile = _profile(COMMA_CSV)
    preview = build_preview(parsed, profile, group_by_fields=["status"])
    assert preview.group_by["status"].groups == {"Open": 2, "In Progress": 2, "Done": 2}


def test_preview_auto_group_by_resolves_to_a_dimension():
    parsed, profile = _profile(COMMA_CSV)
    preview = build_preview(parsed, profile, group_by_fields=["auto"])
    assert "auto" in preview.group_by
    assert preview.group_by["auto"].group_count >= 2


def test_preview_warns_on_missing_required_key():
    parsed, profile = _profile(MISSING_KEY_CSV)
    preview = build_preview(parsed, profile)
    assert any("Issue key" in w for w in preview.warnings)


def test_preview_warns_on_missing_story_points_mapping():
    parsed, profile = _profile(SEMICOLON_CSV)
    preview = build_preview(parsed, profile)
    assert any("Story points" in w for w in preview.warnings)


def test_preview_warns_on_duplicate_keys():
    parsed, profile = _profile(DUPLICATE_KEY_CSV)
    preview = build_preview(parsed, profile)
    assert any("duplicate issue key" in w.lower() and "PROJ-70" in w for w in preview.warnings)


def test_preview_warns_on_empty_grouping_field():
    parsed, profile = _profile(SEMICOLON_CSV)  # no priority column
    preview = build_preview(parsed, profile, group_by_fields=["priority"])
    assert any("priority" in w and "empty" in w for w in preview.warnings)


def test_preview_filter_chips_include_pinned():
    parsed, profile = _profile(COMMA_CSV)
    preview = build_preview(parsed, profile, pinned_filters=["Summary"])
    assert "Summary" in preview.filter_chips  # user pin overrides the recommender


def test_preview_redaction_masks_keys():
    parsed, profile = _profile(COMMA_CSV)
    preview = build_preview(parsed, profile, ImportOptions(redact_keys=True))
    assert preview.redacted is True
    assert preview.issues[0].key == "PROJ-•"
    assert all("1" not in i.key and "2" not in i.key for i in preview.issues)


def test_redact_key_variants():
    assert redact_key("PROJ-123") == "PROJ-•••"
    assert redact_key("ABCD") == "••••"
    assert redact_key("") == ""


# --------------------------------------------------------------------------- #
# Stage 6 — commit
# --------------------------------------------------------------------------- #
def test_commit_adds_to_in_memory_dataset():
    parsed, profile = _profile(COMMA_CSV)
    dataset = IssueCollection()
    result = commit_import(parsed, profile, dataset)
    assert result.added == 6
    assert result.dataset_size == 6
    assert len(dataset) == 6
    assert all(isinstance(i, NormalizedIssue) for i in dataset.issues)
    assert dataset.issues[0].source.origin == "csv"


def test_commit_appends_to_existing_dataset():
    parsed, profile = _profile(COMMA_CSV)
    dataset = IssueCollection(issues=[NormalizedIssue(key="EXIST-1")])
    commit_import(parsed, profile, dataset)
    assert len(dataset) == 7
    assert dataset.issues[0].key == "EXIST-1"


def test_commit_no_persistence_by_default(tmp_path):
    parsed, profile = _profile(COMMA_CSV)
    result = commit_import(parsed, profile, IssueCollection(), out_dir=tmp_path)
    assert result.profile_saved is False
    assert result.dataset_saved is False
    assert not any(tmp_path.iterdir())  # nothing written without opt-in


def test_commit_redacts_keys_in_dataset_when_opted_in():
    parsed, profile = _profile(COMMA_CSV)
    dataset = IssueCollection()
    commit_import(parsed, profile, dataset, ImportOptions(redact_keys=True))
    assert dataset.issues[0].key == "PROJ-•"


def test_commit_opt_in_saves_profile_and_dataset(tmp_path):
    parsed, profile = _profile(COMMA_CSV)
    dataset = IssueCollection()
    opts = ImportOptions(save_profile=True, save_dataset=True)
    result = commit_import(parsed, profile, dataset, opts, out_dir=tmp_path)
    assert result.profile_saved and result.dataset_saved

    # Profile round-trips (schema only).
    reloaded = load_profile(result.profile_path)
    assert reloaded.columns == profile.columns
    assert {m.target for m in reloaded.mappings} == {m.target for m in profile.mappings}

    # Dataset JSONL contains one normalized issue per line.
    lines = [
        json.loads(ln)
        for ln in open(result.dataset_path, encoding="utf-8").read().splitlines()
        if ln.strip()
    ]
    assert len(lines) == 6
    assert lines[0]["key"] == "PROJ-1"
    assert lines[0]["source"]["origin"] == "csv"


# --------------------------------------------------------------------------- #
# Privacy invariant — raw rows / file never persisted
# --------------------------------------------------------------------------- #
def test_saved_profile_contains_no_row_data(tmp_path):
    csv_text = (
        "Issue key,Secret Notes\n"
        "PROJ-1,SENSITIVE-CELL-VALUE-XYZ\n"
    )
    parsed, profile = _profile(csv_text)
    result = commit_import(
        parsed, profile, IssueCollection(),
        ImportOptions(save_profile=True), out_dir=tmp_path,
    )
    text = open(result.profile_path, encoding="utf-8").read()
    assert "SENSITIVE-CELL-VALUE-XYZ" not in text  # only headers + mappings persisted
    assert "Secret Notes" in text  # the column name (schema) is fine to keep


def test_csv_import_profile_has_no_row_storage():
    _, profile = _profile(COMMA_CSV)
    attrs = set(vars(profile))
    assert "rows" not in attrs and "data" not in attrs


def test_saved_profile_keeps_only_basename_not_path(tmp_path):
    f = tmp_path / "nested_export.csv"
    f.write_text(COMMA_CSV, encoding="utf-8")
    parsed = read_csv_file(f)
    profile = build_profile(parsed)
    result = commit_import(
        parsed, profile, IssueCollection(),
        ImportOptions(save_profile=True), out_dir=tmp_path,
    )
    text = open(result.profile_path, encoding="utf-8").read()
    assert "nested_export.csv" in text
    # No absolute path / directory component leaks into the profile.
    assert str(tmp_path) not in text
    assert profile.source_file_name == "nested_export.csv"


def test_empty_csv_is_safe():
    parsed = parse_csv("")
    assert parsed.column_count == 0
    assert parsed.row_count == 0
    preview = build_preview(parsed, build_profile(parsed))
    assert preview.issues == []
