"""Tests for the About dialog's environment-info helper (Qt-free)."""

from __future__ import annotations

from issue_deck import constants, credentials
from issue_deck.diagnostics import environment_info


def _as_dict():
    return dict(environment_info())


def test_reports_version_and_paths():
    info = _as_dict()
    assert info["Version"] == constants.APP_VERSION
    assert info["Config file"] == str(constants.CONFIG_PATH)
    assert info["Data directory"] == str(constants.APP_DIR)


def test_labels_are_ordered_and_complete():
    labels = [label for label, _ in environment_info()]
    assert labels == [
        "Version", "Config file", "Data directory", "Token storage",
        "Python", "Qt / PyQt6", "Platform",
    ]


def test_token_storage_reflects_keyring(monkeypatch):
    monkeypatch.setattr(credentials, "keyring_available", lambda: True)
    assert "keychain" in _as_dict()["Token storage"]
    monkeypatch.setattr(credentials, "keyring_available", lambda: False)
    assert "plaintext" in _as_dict()["Token storage"]


def test_qt_versions_present():
    # PyQt6 is a hard runtime dependency, so real versions are reported.
    assert "PyQt" in _as_dict()["Qt / PyQt6"]
