"""Headless tests for the JQL helper dialog. Modal prompts are stubbed and the
app dir is redirected so the template store never touches the real config.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.jira_client import InvalidJQLError, SearchOutcome
from issue_deck.jql_helper import JqlTemplateStore
from issue_deck.models import SearchFilters
from issue_deck.ui.jql_helper_dialog import JqlHelperDialog


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


class _FakeClient:
    def __init__(self, *, issues=None, exc=None):
        self._issues = issues or []
        self._exc = exc

    def search(self, jql, fields, *, max_results=None):
        if self._exc is not None:
            raise self._exc
        return SearchOutcome(self._issues, total=len(self._issues), truncated=False)


def _dialog(qapp, *, filters=None, client=None, store=None):
    return JqlHelperDialog(
        None,
        cfg=AppConfig(),
        filters=filters or SearchFilters(assigned_to_me=True, projects=["ABC"]),
        client_provider=lambda: (client if client is not None else _FakeClient()),
        template_store=store if store is not None else JqlTemplateStore(),
    )


def test_dialog_populates_clauses_and_jql(qapp):
    dlg = _dialog(qapp)
    assert dlg.lst_clauses.count() == 2  # scope + project
    jql = dlg.jql_view.toPlainText()
    assert "assignee = currentUser()" in jql
    assert 'project = "ABC"' in jql


def test_toggling_clause_updates_jql(qapp):
    dlg = _dialog(qapp)
    # Uncheck the project clause (second row).
    item = next(dlg.lst_clauses.item(i) for i in range(dlg.lst_clauses.count())
                if "project" in dlg.lst_clauses.item(i).text())
    item.setCheckState(Qt.CheckState.Unchecked)
    assert "project" not in dlg.jql_view.toPlainText()


def test_broad_warning_shows(qapp):
    dlg = _dialog(qapp, filters=SearchFilters(assigned_to_me=False, text="foo"))
    assert "broad" in dlg.lbl_warn.text().lower()


def test_apply_template_repopulates(qapp):
    dlg = _dialog(qapp)
    idx = dlg.cmb_templates.findData(None)  # not found -> -1; select "Blocked issues"
    for i in range(dlg.cmb_templates.count()):
        if dlg.cmb_templates.itemData(i).name == "Blocked issues":
            idx = i
            break
    dlg.cmb_templates.setCurrentIndex(idx)
    dlg._apply_template()
    assert "flagged is not EMPTY" in dlg.jql_view.toPlainText()


def test_use_sets_result_jql_and_accepts(qapp):
    dlg = _dialog(qapp)
    dlg._use()
    assert dlg.result_jql()
    assert "assignee = currentUser()" in dlg.result_jql()


def _run_validation(dlg):
    """Drive the off-thread validation to completion, then join the thread."""
    dlg._validate()
    for _ in range(500):
        QApplication.processEvents()
        if dlg.lbl_validate.text() and "Validating" not in dlg.lbl_validate.text():
            break
    if dlg._val_thread is not None:
        dlg._val_thread.wait()
    QApplication.processEvents()


def test_validate_ok(qapp):
    dlg = _dialog(qapp, client=_FakeClient(issues=[{"key": "ABC-9"}]))
    _run_validation(dlg)
    assert "✓" in dlg.lbl_validate.text()
    assert "ABC-9" in dlg.lbl_validate.text()
    dlg.close()


def test_validate_invalid_jql(qapp):
    dlg = _dialog(qapp, client=_FakeClient(exc=InvalidJQLError("Invalid JQL: bad")))
    _run_validation(dlg)
    assert "✗" in dlg.lbl_validate.text()
    assert "rejected" in dlg.lbl_validate.text()
    dlg.close()


def test_save_custom_template(qapp, monkeypatch):
    store = JqlTemplateStore()
    dlg = _dialog(qapp, store=store)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("My saved", True)))
    dlg._save_template()
    assert "My saved" in store
    # It appears in the combo, flagged custom.
    assert any("My saved" in dlg.cmb_templates.itemText(i)
               for i in range(dlg.cmb_templates.count()))


def test_validate_handles_no_connection(qapp):
    def boom():
        raise RuntimeError("not connected")

    dlg = JqlHelperDialog(
        None, cfg=AppConfig(),
        filters=SearchFilters(assigned_to_me=True),
        client_provider=boom, template_store=JqlTemplateStore())
    dlg._validate()
    assert "Can't reach Jira" in dlg.lbl_validate.text()
