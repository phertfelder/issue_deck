"""Characterization tests for normalize_issue -> JiraIssue (Cloud + Server)."""

from __future__ import annotations

from issue_deck.config import AppConfig
from issue_deck.models import JiraIssue
from issue_deck.normalization import normalize_issue


def test_cloud_issue_core_fields(cloud_issue, cloud_cfg):
    n = normalize_issue(cloud_issue, cloud_cfg)
    assert isinstance(n, JiraIssue)
    assert n.key == "CLOUD-1"
    assert n.url == "https://example.atlassian.net/browse/CLOUD-1"
    assert n.summary == "Payment reconciliation fails for café orders"
    assert n.status == "In Progress"
    assert n.issuetype == "Bug"
    assert n.priority == "High"
    assert n.assignee == "Ada Lovelace"
    assert n.reporter == "Grace Hopper"
    assert n.created == "2026-01-01T10:00:00.000+0000"
    assert n.updated == "2026-02-01T12:30:00.000+0000"
    assert n.components == ["Backend", "API"]
    assert n.labels == ["urgent", "settlement"]
    assert n.comments == []


def test_cloud_custom_fields_resolved(cloud_issue, cloud_cfg):
    n = normalize_issue(cloud_issue, cloud_cfg)
    assert n.client == "Acme Corp"
    assert n.severity == "S1"


def test_cloud_description_from_adf(cloud_issue, cloud_cfg):
    n = normalize_issue(cloud_issue, cloud_cfg)
    assert n.description == "The reconciliation job **crashes** on non-ASCII names."


def test_server_assignee_reporter_name_fallback(server_issue, server_cfg):
    n = normalize_issue(server_issue, server_cfg)
    assert n.assignee == "jdoe"
    assert n.reporter == "asmith"


def test_server_description_plain_string(server_issue, server_cfg):
    n = normalize_issue(server_issue, server_cfg)
    assert n.description.startswith("Plain wiki text description")


def test_server_custom_fields_string_and_dict(server_issue, server_cfg):
    n = normalize_issue(server_issue, server_cfg)
    assert n.client == "Globex"
    assert n.severity == "Sev-2"
    assert n.url == "https://jira.example.com/browse/SRV-42"


def test_custom_field_value_shapes():
    cfg = AppConfig(base_url="https://example.atlassian.net",
                    client_field="customfield_10050", severity_field="customfield_10060")

    def norm(client_val):
        issue = {"key": "X-1", "fields": {
            "customfield_10050": client_val, "customfield_10060": {"name": "N"}}}
        return normalize_issue(issue, cfg)

    assert norm({"name": "ByName"}).client == "ByName"
    assert norm({"displayName": "ByDisplay"}).client == "ByDisplay"
    assert norm({"value": "ByValue"}).client == "ByValue"
    assert norm("plain").client == "plain"
    assert norm(None).client == ""


def test_missing_optional_fields_do_not_crash():
    cfg = AppConfig(base_url="https://example.atlassian.net")
    n = normalize_issue({"key": "MIN-1", "fields": {}}, cfg)
    assert n.key == "MIN-1"
    assert n.summary == ""
    assert n.status == ""
    assert n.components == []
    assert n.labels == []
    assert n.client == ""
    assert n.severity == ""
    assert n.comments == []


def test_empty_custom_field_config_skips_lookup():
    cfg = AppConfig(base_url="https://example.atlassian.net")
    issue = {"key": "Y-1", "fields": {"customfield_10050": {"value": "Ignored"}}}
    n = normalize_issue(issue, cfg)
    assert n.client == ""
    assert n.severity == ""
