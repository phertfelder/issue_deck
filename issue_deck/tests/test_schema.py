"""Tests for the normalized issue schema and its conversion helpers.

Covers the source shapes that historically diverge: Cloud ADF bodies vs
Server plain-string bodies, absent fields, multi-select custom fields, user
fields (accountId vs name), numbers, and dates — plus the CSV import path and
the privacy invariants (no raw blobs, mapped custom fields only).
"""

from __future__ import annotations

import json

from issue_deck.config import AppConfig
from issue_deck.models import JiraIssue
from issue_deck.schema import (
    CsvImportProfile,
    ExportManifest,
    FieldMapping,
    IssueCollection,
    JiraDeployment,
    JiraFieldDefinition,
    JiraUser,
    NormalizedIssue,
    SourceMetadata,
    normalized_from_jira,
)

# Per-instance custom-field ids used by the rich fixture.
RICH_KW = dict(
    story_points_field="customfield_10016",
    sprint_field="customfield_10020",
)


# --------------------------------------------------------------------------- #
# JiraDeployment
# --------------------------------------------------------------------------- #
def test_deployment_coerce():
    assert JiraDeployment.coerce("cloud") is JiraDeployment.CLOUD
    assert JiraDeployment.coerce("server") is JiraDeployment.SERVER
    assert JiraDeployment.coerce("SERVER") is JiraDeployment.SERVER
    assert JiraDeployment.coerce(None) is JiraDeployment.CLOUD
    assert JiraDeployment.coerce("nonsense") is JiraDeployment.CLOUD
    assert JiraDeployment.coerce(JiraDeployment.SERVER) is JiraDeployment.SERVER
    # str-enum: serializes / compares as the bare string.
    assert JiraDeployment.CLOUD == "cloud"
    assert json.dumps({"d": JiraDeployment.CLOUD.value}) == '{"d": "cloud"}'


# --------------------------------------------------------------------------- #
# JiraUser (user fields: Cloud accountId vs Server name)
# --------------------------------------------------------------------------- #
def test_user_from_cloud_dict():
    u = JiraUser.from_raw({"displayName": "Ada", "accountId": "acc-1", "emailAddress": "ada@x"})
    assert u.display_name == "Ada"
    assert u.account_id == "acc-1"
    assert u.email == "ada@x"
    assert u.name == "Ada"


def test_user_from_server_dict():
    u = JiraUser.from_raw({"name": "jdoe", "key": "jdoe"})
    assert u.username == "jdoe"
    assert u.display_name == "jdoe"  # falls back to name when no displayName
    assert u.name == "jdoe"


def test_user_from_none_and_string():
    assert JiraUser.from_raw(None) == JiraUser()
    assert JiraUser.from_raw("").name == ""
    assert JiraUser.from_raw("Bare Name").name == "Bare Name"


def test_user_name_prefers_display_then_username_then_email():
    assert JiraUser(username="u", email="e").name == "u"
    assert JiraUser(email="e").name == "e"
    assert JiraUser().name == ""


# --------------------------------------------------------------------------- #
# Cloud ADF body
# --------------------------------------------------------------------------- #
def test_cloud_adf_body_flattened(cloud_issue, cloud_cfg):
    n = normalized_from_jira(cloud_issue, cloud_cfg)
    assert n.description == "The reconciliation job **crashes** on non-ASCII names."
    assert n.summary == "Payment reconciliation fails for café orders"
    assert n.assignee.name == "Ada Lovelace"
    assert n.issue_type == "Bug"
    assert n.source.origin == "api"
    assert n.source.deployment == "cloud"


# --------------------------------------------------------------------------- #
# Server plain-string body
# --------------------------------------------------------------------------- #
def test_server_string_body(server_issue, server_cfg):
    n = normalized_from_jira(server_issue, server_cfg, deployment="server")
    assert n.description.startswith("Plain wiki text description")
    assert n.assignee.username == "jdoe"
    assert n.reporter.username == "asmith"
    assert n.client == "Globex"
    assert n.severity == "Sev-2"
    assert n.url == "https://jira.example.com/browse/SRV-42"
    assert n.source.deployment == "server"


# --------------------------------------------------------------------------- #
# Missing fields
# --------------------------------------------------------------------------- #
def test_missing_fields_never_crash():
    cfg = AppConfig(base_url="https://example.atlassian.net")
    n = normalized_from_jira({"key": "MIN-1", "fields": {}}, cfg)
    assert n.key == "MIN-1"
    assert n.summary == ""
    assert n.description == ""
    assert n.status == ""
    assert n.status_category == ""
    assert n.assignee == JiraUser()
    assert n.reporter == JiraUser()
    assert n.labels == []
    assert n.components == []
    assert n.fix_versions == []
    assert n.sprints == []
    assert n.story_points is None
    assert n.resolved == ""
    assert n.due_date == ""
    assert n.comments == []
    assert n.raw_field_values == {}


def test_entirely_absent_fields_key():
    cfg = AppConfig(base_url="https://example.atlassian.net")
    n = normalized_from_jira({"key": "MIN-2"}, cfg)  # no "fields" at all
    assert n.key == "MIN-2"
    assert n.summary == ""


# --------------------------------------------------------------------------- #
# Rich cloud issue: status category, project, epic, versions, dates, numbers
# --------------------------------------------------------------------------- #
def test_rich_core_and_optional_fields(rich_issue, cloud_cfg):
    n = normalized_from_jira(rich_issue, cloud_cfg, **RICH_KW)
    assert n.key == "RICH-7"
    assert n.status == "In Progress"
    assert n.status_category == "In Progress"
    assert n.project_key == "RICH"
    assert n.project_name == "Rich Commerce"
    assert n.epic_key == "RICH-1"
    assert n.epic_name == "Q2 Performance Epic"
    assert n.fix_versions == ["2026.4.0", "2026.4.1"]
    assert n.components == ["Frontend", "Gateway"]
    assert n.labels == ["performance", "checkout"]


def test_rich_dates(rich_issue, cloud_cfg):
    n = normalized_from_jira(rich_issue, cloud_cfg, **RICH_KW)
    assert n.created == "2026-04-01T08:00:00.000+0000"
    assert n.updated == "2026-04-10T16:45:00.000+0000"
    assert n.resolved == "2026-04-12T09:15:00.000+0000"
    assert n.due_date == "2026-04-20"


def test_rich_numbers_story_points(rich_issue, cloud_cfg):
    n = normalized_from_jira(rich_issue, cloud_cfg, **RICH_KW)
    assert n.story_points == 8
    assert isinstance(n.story_points, int)


def test_rich_sprints_mixed_string_and_dict(rich_issue, cloud_cfg):
    n = normalized_from_jira(rich_issue, cloud_cfg, **RICH_KW)
    # Legacy GreenHopper string form + modern dict form both resolve to names.
    assert n.sprints == ["Sprint 12", "Sprint 13"]


def test_rich_cloud_user_has_account_id(rich_issue, cloud_cfg):
    n = normalized_from_jira(rich_issue, cloud_cfg, **RICH_KW)
    assert n.assignee.account_id == "acc-123"
    assert n.assignee.email == "ada@example.com"
    assert n.reporter.account_id == "acc-456"


# --------------------------------------------------------------------------- #
# Multi-select custom fields
# --------------------------------------------------------------------------- #
def test_multi_select_custom_field(rich_issue, cloud_cfg):
    n = normalized_from_jira(
        rich_issue, cloud_cfg, extra_field_ids=["customfield_10099"], **RICH_KW
    )
    assert n.raw_field_values["customfield_10099"] == ["Web", "Mobile"]


# --------------------------------------------------------------------------- #
# raw_field_values: mapped custom fields ONLY, never the full blob
# --------------------------------------------------------------------------- #
def test_raw_field_values_only_mapped(rich_issue, cloud_cfg):
    n = normalized_from_jira(rich_issue, cloud_cfg, **RICH_KW)
    # Mapped (client/severity from cfg, story points + sprint from kwargs) present:
    assert n.raw_field_values["customfield_10050"] == "Acme Corp"
    assert n.raw_field_values["customfield_10060"] == "S1"
    assert n.raw_field_values["customfield_10016"] == 8
    # Unmapped custom field is NOT copied in.
    assert "customfield_10099" not in n.raw_field_values
    # Standard fields are never stashed as raw values.
    assert "summary" not in n.raw_field_values
    assert "description" not in n.raw_field_values


def test_no_client_severity_config_skips_lookup():
    cfg = AppConfig(base_url="https://example.atlassian.net")
    issue = {"key": "Y-1", "fields": {"customfield_10050": {"value": "Ignored"}}}
    n = normalized_from_jira(issue, cfg)
    assert n.client == ""
    assert n.severity == ""
    assert n.raw_field_values == {}


# --------------------------------------------------------------------------- #
# Numbers helper edge cases
# --------------------------------------------------------------------------- #
def test_story_points_float_and_string_and_bad():
    cfg = AppConfig(base_url="https://x", client_field="", severity_field="")
    kw = dict(story_points_field="cf")

    def sp(v):
        return normalized_from_jira({"key": "K", "fields": {"cf": v}}, cfg, **kw).story_points

    assert sp(3.5) == 3.5
    assert sp("13") == 13
    assert sp("2.5") == 2.5
    assert sp("not-a-number") is None
    assert sp(None) is None
    assert sp(True) is None  # bool must not count as a number


# --------------------------------------------------------------------------- #
# Backward-compatible export bridge
# --------------------------------------------------------------------------- #
def test_to_legacy_issue_maps_fields(rich_issue, cloud_cfg):
    n = normalized_from_jira(rich_issue, cloud_cfg, **RICH_KW)
    legacy = n.to_legacy_issue()
    assert isinstance(legacy, JiraIssue)
    assert legacy.key == "RICH-7"
    assert legacy.issuetype == "Story"          # issue_type -> issuetype
    assert legacy.assignee == "Ada Lovelace"    # JiraUser -> display name
    assert legacy.reporter == "Grace Hopper"
    assert legacy.components == ["Frontend", "Gateway"]
    assert legacy.description == "Latency **doubles** at peak."


def test_legacy_issue_jsonl_key_order_unchanged(rich_issue, cloud_cfg, tmp_path):
    from dataclasses import asdict

    legacy = normalized_from_jira(rich_issue, cloud_cfg, **RICH_KW).to_legacy_issue()
    keys = list(asdict(legacy).keys())
    assert keys == [
        "key", "url", "summary", "status", "issuetype", "priority", "severity",
        "client", "assignee", "reporter", "created", "updated", "components",
        "labels", "description", "comments",
    ]


# --------------------------------------------------------------------------- #
# SourceMetadata
# --------------------------------------------------------------------------- #
def test_source_metadata_api_default_deployment(cloud_issue, cloud_cfg):
    n = normalized_from_jira(cloud_issue, cloud_cfg)
    assert n.source.origin == "api"
    assert n.source.deployment == "cloud"
    assert n.source.imported_at  # auto-stamped
    assert n.source.source_file_name == ""


def test_source_metadata_explicit_override(cloud_issue, cloud_cfg):
    src = SourceMetadata.for_api("cloud", imported_at="2026-07-08T00:00:00+00:00")
    n = normalized_from_jira(cloud_issue, cloud_cfg, source=src)
    assert n.source.imported_at == "2026-07-08T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# CSV import path
# --------------------------------------------------------------------------- #
def _csv_profile() -> CsvImportProfile:
    return CsvImportProfile(
        name="jira-export",
        columns=["Key", "Summary", "Status", "Assignee", "Labels", "Story Points", "Team"],
        source_file_name="sample.csv",
        mappings=[
            FieldMapping("Key", "key"),
            FieldMapping("Summary", "summary"),
            FieldMapping("Status", "status"),
            FieldMapping("Assignee", "assignee", "user"),
            FieldMapping("Labels", "labels", "multi_select"),
            FieldMapping("Story Points", "story_points", "number"),
            FieldMapping("Team", "team", "text"),  # unknown target -> raw_field_values
        ],
    )


def test_csv_row_to_normalized_issue():
    row = {
        "Key": "CSV-1",
        "Summary": "Imported from spreadsheet",
        "Status": "Done",
        "Assignee": "Jane Roe",
        "Labels": "alpha; beta, gamma",
        "Story Points": "5",
        "Team": "Platform",
    }
    n = NormalizedIssue.from_csv_row(row, _csv_profile())
    assert n.key == "CSV-1"
    assert n.summary == "Imported from spreadsheet"
    assert n.status == "Done"
    assert n.assignee.name == "Jane Roe"
    assert n.labels == ["alpha", "beta", "gamma"]  # split on ; and ,
    assert n.story_points == 5
    # Unknown target column preserved under its source name only.
    assert n.raw_field_values == {"Team": "Platform"}
    assert n.source.origin == "csv"
    assert n.source.source_file_name == "sample.csv"


def test_csv_row_missing_columns_are_empty():
    profile = _csv_profile()
    n = NormalizedIssue.from_csv_row({"Key": "CSV-2"}, profile)
    assert n.key == "CSV-2"
    assert n.summary == ""
    assert n.labels == []
    assert n.story_points is None
    assert n.assignee == JiraUser()


def test_csv_profile_holds_no_rows():
    # Type-level privacy invariant: the profile exposes schema, not data.
    profile = _csv_profile()
    field_names = {f for f in vars(profile)}
    assert "rows" not in field_names
    assert "data" not in field_names
    assert profile.mapping_for("Labels").transform == "multi_select"
    assert profile.mapping_for("nope") is None


# --------------------------------------------------------------------------- #
# JiraFieldDefinition
# --------------------------------------------------------------------------- #
def test_field_definition_from_dict():
    fd = JiraFieldDefinition.from_field_dict({
        "id": "customfield_10020",
        "name": "Sprint",
        "custom": True,
        "schema": {"type": "array", "items": "json"},
    })
    assert fd.id == "customfield_10020"
    assert fd.name == "Sprint"
    assert fd.custom is True
    assert fd.schema_type == "array"
    assert fd.item_type == "json"


def test_field_definition_infers_custom_from_id():
    fd = JiraFieldDefinition.from_field_dict({"id": "customfield_99", "name": "X"})
    assert fd.custom is True
    std = JiraFieldDefinition.from_field_dict({"id": "summary", "name": "Summary"})
    assert std.custom is False


# --------------------------------------------------------------------------- #
# IssueCollection + ExportManifest
# --------------------------------------------------------------------------- #
def test_issue_collection_len_and_legacy(rich_issue, cloud_cfg):
    n = normalized_from_jira(rich_issue, cloud_cfg, **RICH_KW)
    coll = IssueCollection(issues=[n, n], generated_at="2026-07-08T00:00:00+00:00")
    assert len(coll) == 2
    legacy = coll.to_legacy_issues()
    assert all(isinstance(x, JiraIssue) for x in legacy)
    assert legacy[0].key == "RICH-7"


def test_export_manifest_build(rich_issue, cloud_cfg):
    n = normalized_from_jira(rich_issue, cloud_cfg, **RICH_KW)
    coll = IssueCollection(issues=[n])
    man = ExportManifest.build("jsonl", "/tmp/out.jsonl", coll,
                               exported_at="2026-07-08T00:00:00+00:00")
    assert man.fmt == "jsonl"
    assert man.destination == "/tmp/out.jsonl"
    assert man.issue_count == 1
    assert man.origin == "api"
    assert man.deployment == "cloud"
    assert man.includes_comments is False
    assert man.includes_raw is False  # raw payload requires explicit opt-in
