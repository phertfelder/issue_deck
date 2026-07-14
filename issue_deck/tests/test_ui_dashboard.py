"""Headless tests for the analytics dashboard tab.

Driven under the offscreen Qt platform; modal dialogs are stubbed. The tab reads
its dataset from a provider callback, so tests just swap the backing list and
call refresh — no store or Jira needed.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from issue_deck.schema import JiraUser, NormalizedIssue
from issue_deck.ui.dashboard_tab import DashboardTab


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _stub_dialogs(monkeypatch):
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def _issue(key, *, assignee="", **kw):
    return NormalizedIssue(key=key, assignee=JiraUser(display_name=assignee), **kw)


def _find_table(tab: DashboardTab, section_title: str):
    return next(t for g, t in tab._tables if g.title == section_title)


def test_dashboard_populates_and_summarizes(qapp):
    data = [
        _issue("A", status="Open", assignee="Alice"),
        _issue("B", status="Open", assignee="Bob"),
        _issue("C", status="Done", status_category="Done"),
    ]
    tab = DashboardTab(lambda: data)
    assert "3 issues" in tab.lbl_summary.text()
    assert "1 done" in tab.lbl_summary.text()
    status_tbl = _find_table(tab, "By status")
    # Two status buckets (Open, Done).
    labels = {status_tbl.item(r, 0).text() for r in range(status_tbl.rowCount())}
    assert labels == {"Open", "Done"}


def test_dashboard_empty_state(qapp):
    tab = DashboardTab(lambda: [])
    assert "No data loaded" in tab.lbl_summary.text()


def test_dashboard_refresh_reflects_new_data(qapp):
    data: list[NormalizedIssue] = []
    tab = DashboardTab(lambda: data)
    assert "No data loaded" in tab.lbl_summary.text()
    data.append(_issue("A", status="Open"))
    tab.refresh()
    assert "1 issues" in tab.lbl_summary.text()


def test_metric_click_through_drills_down(qapp):
    data = [
        _issue("A", status="Open", assignee="Alice"),
        _issue("B", status="Open", assignee="Alice"),
        _issue("C", status="Done", status_category="Done", assignee="Bob"),
    ]
    tab = DashboardTab(lambda: data)
    tbl = _find_table(tab, "By assignee")
    # Find and select the "Alice" row (2 issues).
    alice_row = next(
        r for r in range(tbl.rowCount()) if tbl.item(r, 0).text() == "Alice")
    tbl.selectRow(alice_row)
    assert tab.drill_table.rowCount() == 2
    assert "Alice" in tab.lbl_drill.text()
    # Selecting Bob (1 issue) re-targets the drill-down.
    tbl2 = _find_table(tab, "By assignee")
    bob_row = next(r for r in range(tbl2.rowCount()) if tbl2.item(r, 0).text() == "Bob")
    tbl2.selectRow(bob_row)
    assert tab.drill_table.rowCount() == 1


def test_export_summary_writes_files(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    data = [_issue("A", status="Open", assignee="Alice", story_points=3)]
    tab = DashboardTab(lambda: data)

    md_path = tmp_path / "out.md"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(md_path), "Markdown (*.md)")))
    tab._export_markdown()
    assert md_path.exists() and "# Analytics summary" in md_path.read_text(encoding="utf-8")

    csv_path = tmp_path / "out.csv"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(csv_path), "CSV (*.csv)")))
    tab._export_csv()
    assert csv_path.exists() and "section,metric,count" in csv_path.read_text(encoding="utf-8")
