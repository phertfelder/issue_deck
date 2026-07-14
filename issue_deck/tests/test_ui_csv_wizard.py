"""Headless tests for the CSV import wizard and the delta/merge dialog.

The dialogs are driven programmatically (stepping pages, reading widget state)
rather than via real clicks, so they run under the offscreen Qt platform without
a display. A single module-scoped QApplication is shared.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QComboBox, QMessageBox

from issue_deck.merge import ConflictRule, build_delta
from issue_deck.schema import JiraUser, NormalizedIssue, SourceMetadata
from issue_deck.ui.csv_wizard import CsvImportWizard
from issue_deck.ui.merge_dialog import DeltaDialog

CSV = (
    "Issue key,Summary,Status,Assignee,Story Points\n"
    "PROJ-1,Login fails,Open,Ada Lovelace,3\n"
    "PROJ-2,Add export,Done,Grace Hopper,5\n"
    "PROJ-3,Fix typo,Open,Ada Lovelace,1\n"
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Stub the blocking QMessageBox statics so validation paths never hang."""
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def _issue(key, **kw):
    kw.setdefault("source", SourceMetadata.for_api("cloud"))
    if "assignee" in kw and isinstance(kw["assignee"], str):
        kw["assignee"] = JiraUser(display_name=kw["assignee"])
    return NormalizedIssue(key=key, **kw)


def _advance_to_last(wiz: CsvImportWizard) -> None:
    # Load first, then step through every page's leave/enter hooks.
    from issue_deck.csv_import import parse_csv

    wiz.parsed = parse_csv(CSV, source_file_name="s.csv")
    from issue_deck.csv_import import build_profile

    wiz.profile = build_profile(wiz.parsed)
    wiz._stack.setCurrentIndex(1)  # file page (already "loaded")
    while wiz._stack.currentIndex() < wiz._stack.count() - 1:
        wiz._go_next()


# --------------------------------------------------------------------------- #
# Wizard
# --------------------------------------------------------------------------- #
def test_wizard_paste_loads_and_reports_stats(qapp):
    wiz = CsvImportWizard()
    wiz.ed_paste.setPlainText(CSV)
    wiz._use_paste()
    assert wiz.parsed is not None
    assert wiz.parsed.row_count == 3
    assert "3 rows" in wiz.lbl_stats.text()
    assert "comma" in wiz.lbl_stats.text()


def test_wizard_file_page_blocks_next_without_csv(qapp):
    wiz = CsvImportWizard()
    wiz._stack.setCurrentIndex(1)
    wiz._go_next()  # no CSV loaded -> should not advance
    assert wiz._stack.currentIndex() == 1


def test_wizard_mapping_auto_detects_targets(qapp):
    wiz = CsvImportWizard()
    wiz.ed_paste.setPlainText(CSV)
    wiz._use_paste()
    wiz._stack.setCurrentIndex(2)
    wiz._populate_mapping()
    targets = {}
    for row in range(wiz.tbl_map.rowCount()):
        col = wiz.tbl_map.item(row, 0).text()
        combo = wiz.tbl_map.cellWidget(row, 1)
        assert isinstance(combo, QComboBox)
        targets[col] = combo.currentData()
    assert targets["Issue key"] == "key"
    assert targets["Story Points"] == "story_points"
    assert targets["Assignee"] == "assignee"


def test_wizard_preview_and_finish_builds_source(qapp):
    wiz = CsvImportWizard()
    _advance_to_last(wiz)
    # Preview page populated some rows before we reached commit.
    assert wiz.tbl_preview.rowCount() == 3
    wiz.cb_redact.setChecked(True)
    src = wiz.build_source()
    coll = src.load()
    assert len(coll) == 3
    assert coll.issues[0].key == "PROJ-•"  # redaction honored
    assert wiz.apply_mode == "replace"


def test_wizard_merge_disabled_without_current_data(qapp):
    wiz = CsvImportWizard(current_issues=[])
    _advance_to_last(wiz)
    assert not wiz.rb_merge.isEnabled()
    assert wiz.apply_mode == "replace"


def test_wizard_merge_mode_reports_rule(qapp):
    wiz = CsvImportWizard(current_issues=[_issue("PROJ-1")])
    _advance_to_last(wiz)
    wiz.rb_merge.setChecked(True)
    idx = wiz.cmb_rule.findData(ConflictRule.CSV_WINS)
    wiz.cmb_rule.setCurrentIndex(idx)
    assert wiz.apply_mode == "merge"
    assert wiz.conflict_rule() == ConflictRule.CSV_WINS


# --------------------------------------------------------------------------- #
# Delta dialog
# --------------------------------------------------------------------------- #
def test_delta_dialog_tabs_and_default_rule(qapp):
    current = [_issue("A", status="Open"), _issue("B")]
    incoming = [_issue("A", status="Done"), _issue("C")]
    delta = build_delta(current, incoming)
    dlg = DeltaDialog(delta)
    # No conflict-rule box requested -> selected_rule falls back to newest.
    assert dlg.selected_rule() == ConflictRule.NEWEST_WINS


def test_delta_dialog_conflict_rule_selection(qapp):
    delta = build_delta([_issue("A")], [_issue("A", status="Done")])
    dlg = DeltaDialog(delta, allow_conflict_rule=True, rule=ConflictRule.API_WINS)
    assert dlg.selected_rule() == ConflictRule.API_WINS
    dlg._rule_buttons[ConflictRule.CSV_WINS].setChecked(True)
    assert dlg.selected_rule() == ConflictRule.CSV_WINS


# --------------------------------------------------------------------------- #
# QueryTab integration: Import CSV… routes through the store
# --------------------------------------------------------------------------- #
def test_query_tab_import_csv_populates_store(qapp, monkeypatch):
    from issue_deck.config import AppConfig
    from issue_deck.ui import csv_wizard
    from issue_deck.ui.query_tab import QueryTab

    cfg = AppConfig(base_url="https://x.atlassian.net", deployment="cloud", email="a@b.c")
    tab = QueryTab(cfg, lambda: cfg, lambda: None)

    # Drive the wizard non-interactively: load the CSV and accept.
    def fake_exec(self):
        self.ed_paste.setPlainText(CSV)
        self._use_paste()
        return 1  # QDialog.Accepted

    monkeypatch.setattr(csv_wizard.CsvImportWizard, "exec", fake_exec)

    tab._import_csv()
    assert len(tab.store) == 3
    assert tab.store.kind.value == "csv"
    assert "CSV import" in tab.lbl_source.text()
    assert "3 issues" == tab.lbl_count.text()
