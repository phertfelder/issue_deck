"""Tests for AppConfig load/save. Never touches the real data directory."""

from __future__ import annotations

import json

import pytest

from issue_deck import constants
from issue_deck.config import AppConfig, Config


@pytest.fixture
def redirect_appdir(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    return tmp_path


def test_backward_compat_alias():
    assert Config is AppConfig


def test_load_defaults_when_missing(redirect_appdir):
    cfg = AppConfig.load()
    assert cfg.base_url == ""
    assert cfg.deployment == "cloud"
    assert cfg.remember_token is False


def test_save_writes_expected_json(redirect_appdir):
    cfg = AppConfig(
        base_url="https://example.atlassian.net",
        deployment="cloud",
        email="tester@example.com",
        client_field="customfield_10050",
        severity_field="customfield_10060",
    )
    cfg.save()
    data = json.loads((redirect_appdir / "config.json").read_text(encoding="utf-8"))
    assert data["base_url"] == "https://example.atlassian.net"
    assert data["deployment"] == "cloud"
    assert data["email"] == "tester@example.com"
    assert data["client_field"] == "customfield_10050"


def test_config_json_has_no_token_field(redirect_appdir):
    AppConfig(base_url="https://example.atlassian.net").save()
    data = json.loads((redirect_appdir / "config.json").read_text(encoding="utf-8"))
    assert "token" not in data


def test_load_tolerates_malformed_json(redirect_appdir):
    (redirect_appdir / "config.json").write_text("{not valid json", encoding="utf-8")
    cfg = AppConfig.load()
    assert cfg.base_url == ""
    assert cfg.deployment == "cloud"


def test_load_ignores_unknown_keys(redirect_appdir):
    (redirect_appdir / "config.json").write_text(
        json.dumps({"base_url": "https://x", "bogus": 1}), encoding="utf-8")
    cfg = AppConfig.load()
    assert cfg.base_url == "https://x"


def test_round_trip(redirect_appdir):
    AppConfig(base_url="https://jira.example.com", deployment="server").save()
    loaded = AppConfig.load()
    assert loaded.base_url == "https://jira.example.com"
    assert loaded.deployment == "server"


def test_authoring_defaults(redirect_appdir):
    cfg = AppConfig()
    assert cfg.default_data_source == "ask"
    assert cfg.default_query_authoring_mode == "structured"
    assert cfg.default_query_scope == "assigned_to_me"


def test_authoring_choices_round_trip(redirect_appdir):
    AppConfig(
        default_data_source="csv",
        default_query_authoring_mode="raw",
        default_query_scope="reported_by_me",
    ).save()
    loaded = AppConfig.load()
    assert loaded.default_data_source == "csv"
    assert loaded.default_query_authoring_mode == "raw"
    assert loaded.default_query_scope == "reported_by_me"


def test_authoring_choices_are_valid_members(redirect_appdir):
    from issue_deck import constants
    cfg = AppConfig()
    assert cfg.default_data_source in constants.DATA_SOURCE_CHOICES
    assert cfg.default_query_authoring_mode in constants.QUERY_AUTHORING_MODES
    assert cfg.default_query_scope in constants.QUERY_SCOPE_CHOICES


def test_preference_defaults(redirect_appdir):
    cfg = AppConfig()
    assert cfg.default_export_folder == ""
    assert cfg.max_issues == 0
    assert cfg.comments_mode == "all"
    assert cfg.comments_latest_n == 5
    assert cfg.export_redact_keys is False
    assert cfg.onboarded is False


def test_preference_fields_round_trip(redirect_appdir):
    AppConfig(
        default_export_folder="/exports", max_issues=250,
        comments_mode="latest", comments_latest_n=3, comments_since="2026-01-01",
        export_redact_keys=True, export_redact_people=True, onboarded=True,
    ).save()
    loaded = AppConfig.load()
    assert loaded.default_export_folder == "/exports"
    assert loaded.max_issues == 250
    assert loaded.comments_mode == "latest"
    assert loaded.comments_latest_n == 3
    assert loaded.comments_since == "2026-01-01"
    assert loaded.export_redact_keys is True
    assert loaded.export_redact_people is True
    assert loaded.onboarded is True
