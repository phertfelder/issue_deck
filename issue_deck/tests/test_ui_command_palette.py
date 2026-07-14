"""Tests for the Ctrl+K command palette (pure filter + widget + wiring)."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

from issue_deck import constants
from issue_deck.ui.command_palette import Command, CommandPalette, filter_commands
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


def _cmds():
    noop = lambda: None  # noqa: E731
    return [
        Command("Go to Home", "Navigate", noop, keywords="page"),
        Command("Run preset: My open work", "Preset", noop, keywords="assigned unresolved"),
        Command("Map custom fields…", "Action", noop, keywords="field discovery mapping"),
        Command("Open issue PROJ-42", "Issue", noop, keywords="login bug"),
    ]


# --------------------------------------------------------------------------- #
# filter_commands (pure)
# --------------------------------------------------------------------------- #
def test_empty_query_keeps_all_in_order():
    cmds = _cmds()
    assert filter_commands(cmds, "") == cmds
    assert filter_commands(cmds, "   ") == cmds


def test_matches_title_and_ranks_prefix_first():
    cmds = [
        Command("Run preset: Home cleanup", "Preset", lambda: None),
        Command("Go to Home", "Navigate", lambda: None),
    ]
    out = filter_commands(cmds, "go to")
    assert out[0].title == "Go to Home"     # title-prefix ranked ahead of substring


def test_matches_keywords_not_just_title():
    out = filter_commands(_cmds(), "discovery")
    assert len(out) == 1 and out[0].title == "Map custom fields…"


def test_all_tokens_must_match():
    assert filter_commands(_cmds(), "open work") != []          # both in the preset
    assert filter_commands(_cmds(), "open nonexistenttoken") == []


def test_issue_key_search():
    out = filter_commands(_cmds(), "proj-42")
    assert out and out[0].title == "Open issue PROJ-42"


# --------------------------------------------------------------------------- #
# CommandPalette widget
# --------------------------------------------------------------------------- #
def test_palette_runs_selected_on_enter(qapp):
    ran = []
    cmds = [Command("First", "X", lambda: ran.append("first")),
            Command("Second", "X", lambda: ran.append("second"))]
    dlg = CommandPalette(None, cmds)
    dlg.ed.setText("second")
    dlg._run_current()
    assert dlg.chosen is cmds[1]
    dlg.chosen.run()
    assert ran == ["second"]


def test_palette_arrow_moves_selection(qapp):
    dlg = CommandPalette(None, _cmds())
    assert dlg.list.currentRow() == 0
    ev = QKeyEvent(ev_type(), Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    dlg.eventFilter(dlg.ed, ev)
    assert dlg.list.currentRow() == 1


def ev_type():
    from PyQt6.QtCore import QEvent
    return QEvent.Type.KeyPress


# --------------------------------------------------------------------------- #
# MainWindow wiring
# --------------------------------------------------------------------------- #
def test_main_window_builds_expected_commands(qapp):
    win = MainWindow()
    titles = [c.title for c in win._build_commands()]
    assert "Go to Home" in titles
    assert any(t.startswith("Run preset:") for t in titles)
    assert "Map custom fields…" in titles
    assert "Toggle raw JQL" in titles
    assert "Export…" in titles


def test_ctrl_k_opens_palette(qapp, monkeypatch):
    win = MainWindow()
    opened = []
    monkeypatch.setattr(
        "issue_deck.ui.command_palette.CommandPalette",
        lambda *a, **k: opened.append(True) or _StubPalette())
    win._open_command_palette()
    assert opened == [True]


class _StubPalette:
    chosen = None
    def exec(self):
        return 0
