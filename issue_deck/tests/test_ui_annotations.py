"""Headless UI tests for the rich detail panel and local-annotation wiring."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from issue_deck import constants
from issue_deck.annotations import AnnotationStore
from issue_deck.config import AppConfig
from issue_deck.schema import JiraUser, NormalizedIssue, SourceMetadata
from issue_deck.ui.detail_panel import IssueDetailPanel
from issue_deck.ui.export_dialog import ExportDialog


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))


def _issue(key, **kw):
    kw.setdefault("source", SourceMetadata.for_api("cloud"))
    a = kw.pop("assignee", "")
    return NormalizedIssue(key=key, assignee=JiraUser(display_name=a), **kw)


# --------------------------------------------------------------------------- #
# Rich detail panel
# --------------------------------------------------------------------------- #
def test_panel_shows_warnings_and_custom_fields(qapp):
    panel = IssueDetailPanel()
    issue = _issue(
        "A-1", summary="Boom", status="Blocked", priority="Critical",
        updated="2020-01-01T00:00:00+00:00",
        raw_field_values={"customfield_10050": "Acme Corp"})
    panel.show_issue(issue)
    html = panel.view.toHtml()
    assert "blocked" in html and "high priority" in html and "stale" in html
    assert "Custom fields" in html and "Acme Corp" in html


def test_panel_custom_field_names_resolved(qapp):
    panel = IssueDetailPanel(
        field_names_provider=lambda: {"customfield_10050": "Client"})
    panel.show_issue(_issue("A-1", raw_field_values={"customfield_10050": "Acme"}))
    assert "Client" in panel.view.toHtml()


def test_copy_buttons_populate_clipboard(qapp):
    store = AnnotationStore(path=None)  # uses monkeypatched APP_DIR
    panel = IssueDetailPanel(store)
    issue = _issue("A-1", url="https://x/browse/A-1", summary="Boom")
    panel.show_issue(issue)
    cb = QApplication.clipboard()

    panel._copy_url()
    assert cb.text() == "https://x/browse/A-1"
    panel._copy_key()
    assert cb.text() == "A-1"
    panel._copy_markdown()
    assert "A-1" in cb.text() and "Boom" in cb.text()
    panel._copy_llm_context()
    assert "A-1" in cb.text() and "# A-1" in cb.text()


# --------------------------------------------------------------------------- #
# Annotation editing persists to the store
# --------------------------------------------------------------------------- #
def test_note_saves_on_switch_and_persists(qapp, tmp_path):
    store = AnnotationStore(path=tmp_path / "ann.json")
    panel = IssueDetailPanel(store)
    panel.show_issue(_issue("A-1"))
    panel.ed_note.setPlainText("investigate flakiness")
    # Switching issues flushes the pending note.
    panel.show_issue(_issue("A-2"))
    assert store.get("A-1").note == "investigate flakiness"
    # Reload from disk confirms persistence.
    assert AnnotationStore(path=tmp_path / "ann.json").get("A-1").note == "investigate flakiness"


def test_tag_toggle_persists_immediately(qapp, tmp_path):
    store = AnnotationStore(path=tmp_path / "ann.json")
    panel = IssueDetailPanel(store)
    seen = []
    panel.annotationChanged.connect(seen.append)
    panel.show_issue(_issue("A-1"))
    panel._tag_boxes["blocker"].setChecked(True)
    assert store.get("A-1").tags == ["blocker"]
    assert seen == ["A-1"]


def test_switching_issue_loads_that_issues_annotation(qapp, tmp_path):
    store = AnnotationStore(path=tmp_path / "ann.json")
    store.set("A-2", note="preexisting", tags=["follow up"])
    panel = IssueDetailPanel(store)
    panel.show_issue(_issue("A-1"))
    assert panel.ed_note.toPlainText() == ""
    panel.show_issue(_issue("A-2"))
    assert panel.ed_note.toPlainText() == "preexisting"
    assert panel._tag_boxes["follow up"].isChecked()


# --------------------------------------------------------------------------- #
# Query tab tag filter
# --------------------------------------------------------------------------- #
def test_query_tab_tag_filter_narrows_table(qapp):
    from issue_deck.datasource import DataSourceInfo, DataSourceKind
    from issue_deck.schema import IssueCollection
    from issue_deck.ui.query_tab import QueryTab

    tab = QueryTab(AppConfig(), lambda: AppConfig(), lambda: None)
    info = DataSourceInfo(kind=DataSourceKind.CSV, label="csv", detail="f.csv")
    tab.store.replace(
        IssueCollection(issues=[_issue("A-1"), _issue("A-2"), _issue("A-3")]), info)
    tab._refresh_dataset_views()
    assert tab.table.rowCount() == 3

    tab.annotations.set("A-2", tags=["blocker"])
    idx = tab.cmb_tag.findData("blocker")
    tab.cmb_tag.setCurrentIndex(idx)
    # Only the blocker-tagged issue remains.
    assert tab.table.rowCount() == 1
    assert "tag: blocker" in tab.lbl_count.text()


# --------------------------------------------------------------------------- #
# Export dialog: local-notes toggle gated by redaction
# --------------------------------------------------------------------------- #
def test_export_dialog_local_notes_disabled_by_key_redaction(qapp):
    dlg = ExportDialog()
    dlg.cb_local_notes.setChecked(True)
    dlg.cb_redact_keys.setChecked(True)
    assert not dlg.cb_local_notes.isEnabled()
    assert not dlg.cb_local_notes.isChecked()  # cleared
    # And the resulting config drops notes under redaction.
    assert dlg.config().normalized().include_local_notes is False
