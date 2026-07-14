"""Tests for PR 6: threaded exports (ExportWorker) + results density toggle."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.schema import JiraUser, NormalizedIssue, SourceMetadata
from issue_deck.ui.query_tab import QueryTab
from issue_deck.ui.results_table import ResultsTable
from issue_deck.ui.workers import ExportWorker


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def _tab():
    cfg = AppConfig(base_url="https://x.atlassian.net", deployment="cloud", email="a@b.c")
    return QueryTab(cfg, lambda: cfg, lambda: None)


# --------------------------------------------------------------------------- #
# ExportWorker
# --------------------------------------------------------------------------- #
def test_export_worker_emits_result_message(qapp):
    worker = ExportWorker(lambda: "wrote /tmp/out.md")
    seen = []
    worker.finished.connect(seen.append)
    worker.run()
    assert seen == ["wrote /tmp/out.md"]


def test_export_worker_reports_failure(qapp):
    def boom():
        raise OSError("disk full")
    worker = ExportWorker(boom)
    failed = []
    worker.failed.connect(failed.append)
    worker.run()
    assert failed == ["disk full"]


# --------------------------------------------------------------------------- #
# Threaded export wiring on the tab
# --------------------------------------------------------------------------- #
def test_run_export_completes_and_reenables_buttons(qapp, tmp_path):
    tab = _tab()
    out = tmp_path / "result.txt"
    tab._run_export(lambda: (out.write_text("ok", encoding="utf-8"), str(out))[1], str(out))
    # Busy immediately: export buttons disabled while the worker runs.
    assert all(not b.isEnabled() for b in tab._export_buttons)
    thread = tab._export_thread
    for _ in range(20000):
        if not thread.isRunning():
            break
        qapp.processEvents()
    qapp.processEvents()
    assert out.read_text(encoding="utf-8") == "ok"
    assert all(b.isEnabled() for b in tab._export_buttons)   # re-enabled after done


def test_export_failure_reenables_buttons(qapp):
    tab = _tab()

    def boom():
        raise RuntimeError("nope")
    tab._run_export(boom, "x")
    thread = tab._export_thread
    for _ in range(20000):
        if not thread.isRunning():
            break
        qapp.processEvents()
    qapp.processEvents()
    assert all(b.isEnabled() for b in tab._export_buttons)


# --------------------------------------------------------------------------- #
# Density toggle
# --------------------------------------------------------------------------- #
def _issue(key):
    return NormalizedIssue(key=key, assignee=JiraUser(display_name="x"),
                           source=SourceMetadata.for_api("cloud"))


def test_results_table_density_toggle(qapp):
    t = ResultsTable()
    t.populate([_issue("A-1"), _issue("A-2")])
    assert t.is_compact() is False
    t.set_compact(True)
    assert t.is_compact() is True
    compact = t.verticalHeader().defaultSectionSize()
    t.set_compact(False)
    comfortable = t.verticalHeader().defaultSectionSize()
    assert compact < comfortable       # compact rows are shorter than comfortable


def test_tab_compact_checkbox_drives_table(qapp):
    tab = _tab()
    tab.cb_compact.setChecked(True)
    assert tab.table.is_compact() is True


# --------------------------------------------------------------------------- #
# Human-readable error dialog wiring (PR 7)
# --------------------------------------------------------------------------- #
def test_fetch_failure_presents_typed_error(qapp, monkeypatch):
    from issue_deck.jira_client import AuthError
    from issue_deck.ui import error_dialog

    shown = {}

    def fake_exec(self):
        shown["title"] = self.windowTitle()
        shown["info"] = self.informativeText()
        return 0
    monkeypatch.setattr("PyQt6.QtWidgets.QMessageBox.exec", fake_exec)

    tab = _tab()
    tab._on_failed(AuthError("401 Unauthorized — token bad"))
    assert shown["title"] == "Jira rejected the credentials."
    assert "Cloud vs Server" in shown["info"]
    # Sanity: the presenter path is what rendered it.
    assert error_dialog.present_error(AuthError("x")).title == shown["title"]
