"""Headless tests for the single credential surface.

After the PR 2 consolidation, credentials are edited by exactly one widget —
:class:`CredentialsPanel` — used full-strength on the Settings page's
:class:`ConnectionTab` and in a trimmed form during first-run onboarding. These
tests cover the panel directly (forget, edit, compact mode) and the ConnectionTab
save/persist path (including the plaintext-decline guard) that used to be
exercised through the Settings dialog.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.ui.connection_tab import ConnectionTab
from issue_deck.ui.credentials_panel import CredentialsPanel


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


# --------------------------------------------------------------------------- #
# CredentialsPanel — the one shared editor
# --------------------------------------------------------------------------- #
def test_full_panel_shows_custom_fields_and_forget(qapp):
    panel = CredentialsPanel(AppConfig())
    assert panel._show_custom_fields is True
    assert hasattr(panel, "btn_forget")


def test_compact_panel_hides_custom_fields_and_forget(qapp):
    # Onboarding uses this trimmed form: no field IDs, no Forget action.
    panel = CredentialsPanel(AppConfig(), show_custom_fields=False, show_forget=False)
    assert panel._show_custom_fields is False
    assert not hasattr(panel, "btn_forget")


def test_compact_panel_preserves_existing_custom_fields(qapp):
    # A trimmed panel must not wipe custom-field IDs already in the config.
    cfg = AppConfig(client_field="customfield_999", severity_field="customfield_888")
    panel = CredentialsPanel(cfg, show_custom_fields=False)
    panel.ed_url.setText("https://x.atlassian.net")
    panel.apply_to_config(cfg)
    assert cfg.client_field == "customfield_999"
    assert cfg.severity_field == "customfield_888"


def test_keychain_badge_reflects_backend(qapp, monkeypatch):
    panel = CredentialsPanel(AppConfig())
    assert "Keychain" in panel.lbl_keychain.text() or "Plaintext" in panel.lbl_keychain.text()


def test_forget_token_clears(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("issue_deck.credentials._HAS_KEYRING", False)
    (tmp_path / "token.txt").write_text("secret", encoding="utf-8")
    cfg = AppConfig(base_url="https://x", remember_token=True)
    panel = CredentialsPanel(cfg)
    panel._forget_token()
    assert not (tmp_path / "token.txt").exists()
    assert cfg.remember_token is False


# --------------------------------------------------------------------------- #
# ConnectionTab — the Settings-page save path
# --------------------------------------------------------------------------- #
def test_connection_tab_saves_credentials(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("issue_deck.ui.credentials_panel._HAS_KEYRING", False)
    import json

    cfg = AppConfig()
    tab = ConnectionTab(cfg)
    tab.panel.ed_url.setText("https://edited.atlassian.net")
    tab.panel.rb_cloud.setChecked(True)
    tab.panel.ed_email.setText("me@edited.com")
    tab.panel.ed_client_field.setText("customfield_12345")
    tab._save()

    assert cfg.base_url == "https://edited.atlassian.net"
    assert cfg.email == "me@edited.com"
    assert cfg.client_field == "customfield_12345"
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["base_url"] == "https://edited.atlassian.net"
    assert "token" not in saved


def test_connection_tab_plaintext_decline_does_not_write_token(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("issue_deck.ui.credentials_panel._HAS_KEYRING", False)
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    cfg = AppConfig(base_url="https://x")
    tab = ConnectionTab(cfg)
    tab.panel.cb_remember.setChecked(True)
    tab.panel.ed_token.setText("plain-secret")
    tab._save()
    assert not (tmp_path / "token.txt").exists()  # declined plaintext → nothing written
