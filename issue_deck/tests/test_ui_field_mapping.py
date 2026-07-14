"""Headless tests for the field-mapping modal + Discover Fields wiring."""

from __future__ import annotations

import json

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.ui.credentials_panel import CredentialsPanel
from issue_deck.ui.field_mapping_dialog import FieldMappingDialog


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


class _FakeClient:
    """A client exposing the field_map() the field service reads."""

    def field_map(self):
        return {
            "customfield_10050": "Client",
            "customfield_10060": "Severity",
            "customfield_10016": "Story point estimate",
            "customfield_10020": "Sprint",
            "customfield_10014": "Epic Link",
            "summary": "Summary",
        }


def _dialog(cfg=None):
    cfg = cfg if cfg is not None else AppConfig(base_url="https://x")
    return FieldMappingDialog(None, cfg=cfg, client_provider=lambda: _FakeClient())


# --------------------------------------------------------------------------- #
# Suggestions render
# --------------------------------------------------------------------------- #
def test_dialog_preselects_suggestions(qapp):
    dlg = _dialog()
    ids = dlg._selected_ids()
    assert ids["client"] == "customfield_10050"
    assert ids["sprint"] == "customfield_10020"
    assert ids["epic"] == "customfield_10014"
    assert "Exact name match" in dlg._reasons["client"].text()


def test_dialog_honors_existing_mapping_over_suggestion(qapp):
    # A previously-saved id wins over the fresh suggestion.
    cfg = AppConfig(base_url="https://x", client_field="customfield_10050")
    dlg = _dialog(cfg)
    assert dlg._selected_ids()["client"] == "customfield_10050"


def test_client_list_failure_degrades_gracefully(qapp):
    def boom():
        raise RuntimeError("not connected")
    dlg = FieldMappingDialog(None, cfg=AppConfig(base_url="https://x"), client_provider=boom)
    assert "Couldn't list fields" in dlg.lbl_status.text()
    assert dlg._selected_ids()["client"] == ""     # no crash, empty selection


# --------------------------------------------------------------------------- #
# Save / cancel
# --------------------------------------------------------------------------- #
def test_save_writes_mappings_to_config(qapp, tmp_path):
    cfg = AppConfig(base_url="https://x")
    dlg = _dialog(cfg)
    dlg._save()
    assert cfg.client_field == "customfield_10050"
    assert cfg.severity_field == "customfield_10060"
    assert cfg.story_points_field == "customfield_10016"
    assert cfg.sprint_field == "customfield_10020"
    assert cfg.epic_field == "customfield_10014"
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["sprint_field"] == "customfield_10020"
    assert "token" not in saved


def test_cancel_does_not_touch_config(qapp):
    cfg = AppConfig(base_url="https://x")
    dlg = _dialog(cfg)
    dlg.reject()
    assert cfg.client_field == ""      # untouched
    assert cfg.sprint_field == ""


def test_manual_typed_id_is_saved(qapp):
    cfg = AppConfig(base_url="https://x")
    dlg = _dialog(cfg)
    dlg._combos["severity"].setEditText("customfield_99999")   # power-user override
    dlg._save()
    assert cfg.severity_field == "customfield_99999"


# --------------------------------------------------------------------------- #
# Discover Fields now opens the modal (not the old QMessageBox)
# --------------------------------------------------------------------------- #
def test_discover_fields_opens_mapping_dialog(qapp, monkeypatch):
    opened = []
    monkeypatch.setattr(
        "issue_deck.ui.field_mapping_dialog.FieldMappingDialog",
        lambda *a, **k: opened.append(True) or _Stub())
    panel = CredentialsPanel(AppConfig(base_url="https://x"))
    panel._discover_fields()
    assert opened == [True]


class _Stub:
    def exec(self):
        return 0
