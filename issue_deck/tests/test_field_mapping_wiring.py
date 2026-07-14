"""End-to-end wiring for mapped fields: config, search fields, normalization."""

from __future__ import annotations

import json

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.schema import normalized_from_jira
from issue_deck.services.issue_service import search_fields


# --------------------------------------------------------------------------- #
# Config: backward-compatible load + new fields
# --------------------------------------------------------------------------- #
def test_old_config_without_new_fields_still_loads(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps({"base_url": "https://x", "client_field": "customfield_1"}),
        encoding="utf-8")
    cfg = AppConfig.load()
    assert cfg.base_url == "https://x"
    assert cfg.client_field == "customfield_1"       # existing value preserved
    assert cfg.story_points_field == ""              # new fields default empty
    assert cfg.sprint_field == "" and cfg.epic_field == ""


def test_new_fields_round_trip_and_no_token(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    cfg = AppConfig(base_url="https://x", story_points_field="customfield_10016",
                    sprint_field="customfield_10020", epic_field="customfield_10014")
    cfg.save()
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["story_points_field"] == "customfield_10016"
    assert saved["sprint_field"] == "customfield_10020"
    assert saved["epic_field"] == "customfield_10014"
    assert "token" not in saved
    assert AppConfig.load().sprint_field == "customfield_10020"


# --------------------------------------------------------------------------- #
# search_fields: mapped ids added + deduped
# --------------------------------------------------------------------------- #
def test_search_fields_includes_mapped_ids():
    cfg = AppConfig(story_points_field="customfield_10016",
                    sprint_field="customfield_10020", epic_field="customfield_10014")
    fields = search_fields(cfg)
    for fid in ("customfield_10016", "customfield_10020", "customfield_10014"):
        assert fid in fields


def test_search_fields_dedupes_repeated_ids():
    cfg = AppConfig(client_field="customfield_10050", severity_field="customfield_10050")
    fields = search_fields(cfg)
    assert fields.count("customfield_10050") == 1


# --------------------------------------------------------------------------- #
# Normalization: cfg-driven story points / sprint / epic
# --------------------------------------------------------------------------- #
def _issue(fields: dict) -> dict:
    return {"key": "K-1", "fields": fields}


def test_story_points_from_number_and_string():
    cfg = AppConfig(story_points_field="customfield_10016")
    assert normalized_from_jira(_issue({"customfield_10016": 5}), cfg).story_points == 5
    assert normalized_from_jira(_issue({"customfield_10016": "8"}), cfg).story_points == 8


def test_sprint_from_list_and_legacy_string():
    cfg = AppConfig(sprint_field="customfield_10020")
    from_list = normalized_from_jira(
        _issue({"customfield_10020": [{"name": "Sprint 24"}]}), cfg)
    assert from_list.sprints == ["Sprint 24"]
    legacy = normalized_from_jira(
        _issue({"customfield_10020": ["com.x.Sprint[id=1,name=Sprint 12,state=ACTIVE]"]}), cfg)
    assert legacy.sprints == ["Sprint 12"]


def test_epic_from_string_field():
    cfg = AppConfig(epic_field="customfield_10014")
    got = normalized_from_jira(_issue({"customfield_10014": "PROJ-42"}), cfg)
    assert got.epic_key == "PROJ-42"


def test_missing_mapped_fields_degrade_without_crashing():
    cfg = AppConfig(story_points_field="customfield_10016",
                    sprint_field="customfield_10020", epic_field="customfield_10014")
    got = normalized_from_jira(_issue({}), cfg)   # none present
    assert got.story_points is None
    assert got.sprints == []
    assert got.epic_key == ""


def test_client_severity_behavior_unchanged():
    cfg = AppConfig(client_field="customfield_10050", severity_field="customfield_10060")
    got = normalized_from_jira(
        _issue({"customfield_10050": "Acme", "customfield_10060": "S1"}), cfg)
    assert got.client == "Acme"
    assert got.severity == "S1"
