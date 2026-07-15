"""Headless tests for the guided filter *bar* (My Work redesign).

Covers the presentational chip widgets and their integration into the query
tab: chips drive the underlying SearchFilters, every active selection becomes a
removable pill, the plain-English scope updates, and editing away from the
loaded filters marks the results dirty until Show tickets re-fetches.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.ui.filter_bar import ActiveChip, ChipRow, FilterChip
from issue_deck.ui.query_tab import QueryTab


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def _tab():
    cfg = AppConfig(base_url="https://x.atlassian.net", deployment="cloud", email="a@b.c")
    return QueryTab(cfg, lambda: cfg, lambda: None)


# --------------------------------------------------------------------------- #
# Presentational widgets
# --------------------------------------------------------------------------- #
def test_chip_row_emits_value_and_state(qapp):
    row = ChipRow("STATUS", [("open", "Open"), ("done", "Done")])
    seen = []
    row.toggled.connect(lambda v, on: seen.append((v, on)))
    row._chips["open"].click()          # checkable → toggles on
    assert seen == [("open", True)]
    row._chips["open"].click()          # toggles off
    assert seen[-1] == ("open", False)


def test_chip_row_set_checked_is_emit_free(qapp):
    row = ChipRow("TYPE", [("Bug", "Bug")])
    seen = []
    row.toggled.connect(lambda v, on: seen.append((v, on)))
    row.set_checked("Bug", True)
    assert seen == []                    # programmatic sync never re-emits
    assert isinstance(row._chips["Bug"], FilterChip)
    assert row._chips["Bug"].isChecked()


def test_active_chip_remove_signal(qapp):
    chip = ActiveChip("Assigned to me")
    fired = []
    chip.removed.connect(lambda: fired.append(True))
    # The ✕ button is the only QToolButton in the chip.
    from PyQt6.QtWidgets import QToolButton
    chip.findChild(QToolButton).click()
    assert fired == [True]


# --------------------------------------------------------------------------- #
# Chips <-> SearchFilters integration
# --------------------------------------------------------------------------- #
def test_status_chip_sets_status_category(qapp):
    tab = _tab()
    tab._on_chip_toggled("status", "In Progress", True)
    assert tab._filters().status_categories == ["In Progress"]
    tab._on_chip_toggled("status", "In Progress", False)
    assert tab._filters().status_categories == []


def test_blocked_chip_selects_blocked_status(qapp):
    tab = _tab()
    tab._on_chip_toggled("status", "__blocked__", True)
    assert "Blocked" in tab._filters().statuses


def test_time_chips_are_single_select(qapp):
    tab = _tab()
    tab._on_chip_toggled("time", "7", True)
    assert tab._filters().updated_days == 7
    tab._on_chip_toggled("time", "30", True)      # picking another replaces it
    assert tab._filters().updated_days == 30


def test_priority_chips_round_trip_to_one_pinned_filter(qapp):
    tab = _tab()
    tab._on_chip_toggled("priority", "High", True)
    tab._on_chip_toggled("priority", "Highest", True)
    prio = [ff for ff in tab._filters().field_filters
            if ff.field.lower() == "priority"]
    assert len(prio) == 1                          # a single 'priority in (...)'
    assert prio[0].op == "in"
    assert prio[0].value == "Highest, High"        # ordered highest→lowest
    assert tab._current_priority_values() == ["Highest", "High"]
    tab._on_chip_toggled("priority", "High", False)
    assert tab._current_priority_values() == ["Highest"]


def test_active_chips_reflect_all_filters(qapp):
    tab = _tab()
    tab._on_chip_toggled("type", "Bug", True)
    labels = [label for label, _ in tab._active_filter_chips()]
    assert "Assigned to me" in labels
    assert "Unresolved" in labels
    assert "Bug" in labels


def test_active_chip_remover_clears_that_filter(qapp):
    tab = _tab()
    tab._on_chip_toggled("type", "Bug", True)
    remover = dict(tab._active_filter_chips())["Bug"]
    remover()
    tab._after_filter_edit()
    assert "Bug" not in tab._filters().issue_types


def test_summary_updates_with_filters(qapp):
    tab = _tab()
    before = tab.lbl_summary.text()
    tab._on_chip_toggled("status", "Done", True)
    after = tab.lbl_summary.text()
    assert before != after
    assert after.startswith("◎")
    assert "Done" in after


def test_clear_all_resets_to_open_tickets(qapp):
    tab = _tab()
    tab._on_chip_toggled("type", "Bug", True)
    tab._on_chip_toggled("status", "Done", True)
    tab._clear_all_filters()
    f = tab._filters()
    assert f.assigned_to_me and f.unresolved
    assert f.issue_types == [] and f.status_categories == []


def test_dirty_state_tracks_applied_filters(qapp):
    tab = _tab()
    # Simulate a committed fetch: applied == current, so not dirty.
    tab._applied_filters = tab._filters()
    tab._refresh_chip_bar()
    assert tab.lbl_dirty.isHidden()
    assert tab.btn_fetch.property("dirty") == "false"
    # Edit a filter → dirty until the next Show tickets.
    tab._on_chip_toggled("type", "Bug", True)
    assert not tab.lbl_dirty.isHidden()
    assert tab.btn_fetch.property("dirty") == "true"


def test_preset_tab_runs_filters(qapp, monkeypatch):
    tab = _tab()
    ran = []
    monkeypatch.setattr(tab, "run_filters", lambda f: ran.append(f))
    tab._run_preset_tab(0)                          # "Open" preset
    assert ran and ran[0].assigned_to_me and ran[0].unresolved


def test_primary_button_is_show_tickets(qapp):
    tab = _tab()
    assert tab.btn_fetch.text() == "Show tickets"
    # Saved-views count is reflected on the header entry.
    assert "Saved views (0)" in tab.btn_saved_views.text()
