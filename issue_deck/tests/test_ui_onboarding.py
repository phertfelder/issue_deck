"""Headless tests for the first-run onboarding wizard."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.ui.onboarding import OnboardingDialog


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def test_apply_to_config_sets_connection_and_defaults(qapp):
    # Onboarding now reuses the shared CredentialsPanel (dlg.creds) for the
    # connection fields; the default-axis combos remain on the dialog.
    cfg = AppConfig()
    dlg = OnboardingDialog(None, cfg=cfg)
    dlg.creds.rb_server.setChecked(True)
    dlg.creds.ed_url.setText("  https://jira.example.com  ")
    dlg.creds.cb_remember.setChecked(True)
    dlg.cmb_data_source.setCurrentIndex(dlg.cmb_data_source.findData("csv"))
    dlg.cmb_authoring.setCurrentIndex(dlg.cmb_authoring.findData("raw"))
    dlg.cmb_scope.setCurrentIndex(dlg.cmb_scope.findData("reported_by_me"))

    dlg.apply_to_config()
    assert cfg.base_url == "https://jira.example.com"
    assert cfg.deployment == "server"
    assert cfg.remember_token is True
    assert cfg.default_data_source == "csv"
    assert cfg.default_query_authoring_mode == "raw"
    assert cfg.default_query_scope == "reported_by_me"
    assert cfg.onboarded is True


def test_onboarding_hides_custom_fields(qapp):
    # First run must not surface custom-field IDs (spec §1).
    dlg = OnboardingDialog(None, cfg=AppConfig())
    assert dlg.creds._show_custom_fields is False
    assert not hasattr(dlg.creds, "btn_forget")


def test_starts_from_existing_config(qapp):
    cfg = AppConfig(base_url="https://a.atlassian.net", deployment="cloud", email="me@x.com")
    dlg = OnboardingDialog(None, cfg=cfg)
    assert dlg.creds.rb_cloud.isChecked()
    assert dlg.creds.ed_url.text() == "https://a.atlassian.net"
    assert not dlg.creds.ed_email.isHidden()  # email row shown for cloud


def test_email_row_hidden_for_server(qapp):
    dlg = OnboardingDialog(None, cfg=AppConfig(deployment="server"))
    dlg.creds.rb_server.setChecked(True)
    assert dlg.creds.ed_email.isHidden()


def test_want_csv_import_flag(qapp):
    dlg = OnboardingDialog(None, cfg=AppConfig())
    assert dlg.want_csv_import is False
    dlg.cb_import_csv.setChecked(True)
    assert dlg.want_csv_import is True


def test_token_readouts(qapp):
    dlg = OnboardingDialog(None, cfg=AppConfig())
    dlg.creds.ed_token.setText("  tok  ")
    assert dlg.token() == "tok"
    assert dlg.raw_token() == "  tok  "
