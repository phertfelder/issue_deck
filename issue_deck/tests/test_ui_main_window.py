"""Headless smoke tests for the main window menu + first-run guard."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox

from issue_deck import constants
from issue_deck.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def test_menu_has_file_and_help(qapp):
    win = MainWindow()
    menus = [m.title() for m in win.menuBar().findChildren(QMenu)]
    assert any("File" in t for t in menus)
    assert any("Help" in t for t in menus)


def test_connection_sync_does_not_clobber_timeout(qapp):
    # Request timeout is owned solely by the Settings dialog now; folding the
    # Connection tab's widgets into the config must leave it untouched.
    win = MainWindow()
    win.cfg.request_timeout = 123
    win.sync_config()
    assert win.cfg.request_timeout == 123


def test_first_run_short_circuits_when_configured(qapp, monkeypatch):
    win = MainWindow()
    win.cfg.base_url = "https://already.configured"
    called = []
    # If it tried to onboard, it would import and exec OnboardingDialog; assert it doesn't.
    monkeypatch.setattr(
        "issue_deck.ui.onboarding.OnboardingDialog",
        lambda *a, **k: called.append(True))
    win.run_first_run_if_needed()
    assert called == []


def test_first_run_short_circuits_when_onboarded(qapp, monkeypatch):
    win = MainWindow()
    win.cfg.base_url = ""
    win.cfg.onboarded = True
    monkeypatch.setattr(
        "issue_deck.ui.onboarding.OnboardingDialog",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not onboard")))
    win.run_first_run_if_needed()  # must not raise


def test_replay_onboarding_runs_wizard_unconditionally(qapp, monkeypatch):
    # Replay must open onboarding even when already configured/onboarded.
    win = MainWindow()
    win.cfg.base_url = "https://already.configured"
    win.cfg.onboarded = True

    opened = []

    class _Dlg:
        DialogCode = type("DC", (), {"Accepted": 1, "Rejected": 0})
        def __init__(self, *a, **k):
            opened.append(True)
        def exec(self):  # user cancels — no persistence side effects
            return 0
    monkeypatch.setattr("issue_deck.ui.onboarding.OnboardingDialog", _Dlg)
    win._replay_onboarding()
    assert opened == [True]  # reached the wizard despite being already onboarded
