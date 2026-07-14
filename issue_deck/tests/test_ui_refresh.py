"""Headless tests for the Refresh dialog and QueryTab's refresh flow.

Dialogs are driven programmatically under the offscreen Qt platform. Modal
statics are stubbed and the app dir is redirected so annotations/views never
touch the real config. See test_ui_workbench.py for the shared pattern.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.datasource import DataSourceInfo, DataSourceKind
from issue_deck.merge import ConflictRule, build_delta
from issue_deck.schema import IssueCollection, JiraUser, NormalizedIssue, SourceMetadata
from issue_deck.ui.merge_dialog import DeltaDialog
from issue_deck.ui.query_tab import QueryTab
from issue_deck.ui.refresh_dialog import RefreshDialog

API_INFO = DataSourceInfo(DataSourceKind.JIRA_API, "Jira API live search", "jql", "cloud")


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


def _issue(key, **kw):
    kw.setdefault("source", SourceMetadata.for_api("cloud"))
    a = kw.pop("assignee", "")
    return NormalizedIssue(key=key, assignee=JiraUser(display_name=a), **kw)


def _coll(*issues):
    return IssueCollection(issues=list(issues))


# --------------------------------------------------------------------------- #
# RefreshDialog
# --------------------------------------------------------------------------- #
def test_refresh_dialog_defaults_to_api_replace(qapp):
    dlg = RefreshDialog(current_jql="project = ABC ORDER BY updated DESC")
    plan = dlg.plan()
    assert plan.source == "api_current"
    assert plan.apply_mode == "replace"
    assert plan.preserve_annotations is True
    assert plan.jql == "project = ABC ORDER BY updated DESC"


def test_refresh_dialog_jql_and_merge_mode(qapp):
    dlg = RefreshDialog(current_jql="")
    dlg.rb_jql.setChecked(True)
    dlg.ed_jql.setPlainText("assignee = currentUser()")
    dlg.rb_merge.setChecked(True)
    dlg.cmb_rule.setCurrentIndex(dlg.cmb_rule.findData(ConflictRule.CSV_WINS))
    plan = dlg.plan()
    assert plan.source == "api_jql"
    assert plan.jql == "assignee = currentUser()"
    assert plan.is_merge
    assert plan.rule == ConflictRule.CSV_WINS


def test_refresh_dialog_csv_needs_a_file(qapp):
    dlg = RefreshDialog()
    dlg.rb_csv.setChecked(True)
    dlg._on_accept()          # no file chosen -> blocked (dialog not accepted)
    assert dlg.result() != int(dlg.DialogCode.Accepted)
    dlg._csv_path = "/tmp/newer.csv"
    dlg._on_accept()
    assert dlg.source() == "csv"


def test_refresh_dialog_disables_api_when_unavailable(qapp):
    dlg = RefreshDialog(api_available=False)
    assert not dlg.rb_api_current.isEnabled()
    assert dlg.rb_csv.isChecked()
    assert dlg.source() == "csv"


def test_refresh_dialog_offers_saved_views_and_runs_selected_jql(qapp):
    saved = {"My bugs": "type = Bug ORDER BY updated DESC",
             "Sprint": "sprint in openSprints()"}
    dlg = RefreshDialog(current_jql="assignee = currentUser()", saved_queries=saved)
    # Combo: current builder + each saved view.
    labels = [dlg.cmb_query.itemText(i) for i in range(dlg.cmb_query.count())]
    assert labels[0] == "Current query builder"
    assert "Saved view: My bugs" in labels
    # Selecting a saved view makes its JQL the one that will run.
    dlg.cmb_query.setCurrentIndex(dlg.cmb_query.findText("Saved view: My bugs"))
    plan = dlg.plan()
    assert plan.source == "api_current"
    assert plan.jql == "type = Bug ORDER BY updated DESC"
    # The read-only preview mirrors the selection.
    assert dlg.ed_jql.toPlainText() == "type = Bug ORDER BY updated DESC"


def test_query_tab_refresh_exposes_saved_views(qapp):
    from issue_deck.models import SavedView, SearchFilters

    tab = _tab(qapp)
    tab.views.save(SavedView(name="Only bugs", filters=SearchFilters(issue_types=["Bug"])))
    mapping = tab._saved_query_jql(tab.cfg)
    assert "Only bugs" in mapping
    assert "Bug" in mapping["Only bugs"]


# --------------------------------------------------------------------------- #
# DeltaDialog category filtering
# --------------------------------------------------------------------------- #
def test_delta_dialog_category_filter_narrows_rows(qapp):
    current = [_issue("A", status="Open"), _issue("B")]
    incoming = [_issue("A", status="Done"), _issue("C")]
    dlg = DeltaDialog(build_delta(current, incoming))
    all_rows = dlg._table.rowCount()          # new + removed + status
    idx = dlg.cmb_category.findData("status")
    dlg.cmb_category.setCurrentIndex(idx)
    assert dlg._table.rowCount() == 1         # only the status change
    assert dlg._table.rowCount() < all_rows


# --------------------------------------------------------------------------- #
# QueryTab refresh flow
# --------------------------------------------------------------------------- #
def _tab(qapp):
    cfg = AppConfig(base_url="https://x.atlassian.net", deployment="cloud", email="a@b.c")
    return QueryTab(cfg, lambda: cfg, lambda: None)


def _accept_delta(monkeypatch, accept=True):
    monkeypatch.setattr(DeltaDialog, "exec", lambda self: 1 if accept else 0)


def test_refresh_replace_swaps_dataset_after_confirm(qapp, monkeypatch):
    tab = _tab(qapp)
    tab.store.replace(_coll(_issue("A", status="Open"), _issue("B")), API_INFO)
    _accept_delta(monkeypatch, accept=True)

    incoming = [_issue("A", status="Done"), _issue("C")]
    tab._stage_refresh(incoming, API_INFO, _plan())
    assert {i.key for i in tab.store.issues} == {"A", "C"}   # B removed, C added
    assert tab.store.issues[0].status == "Done"


def test_refresh_cancel_leaves_dataset_untouched(qapp, monkeypatch):
    tab = _tab(qapp)
    tab.store.replace(_coll(_issue("A"), _issue("B")), API_INFO)
    _accept_delta(monkeypatch, accept=False)   # user cancels the preview

    tab._stage_refresh([_issue("A")], API_INFO, _plan())
    assert {i.key for i in tab.store.issues} == {"A", "B"}   # unchanged
    assert "cancelled" in tab.status.text().lower()


def test_refresh_merge_keeps_existing_and_adds_new(qapp, monkeypatch):
    tab = _tab(qapp)
    tab.store.replace(_coll(_issue("A")), API_INFO)
    _accept_delta(monkeypatch, accept=True)

    tab._stage_refresh([_issue("B")], API_INFO, _plan(apply_mode="merge"))
    assert {i.key for i in tab.store.issues} == {"A", "B"}


def test_refresh_csv_missing_key_is_blocked(qapp, monkeypatch):
    tab = _tab(qapp)
    tab.store.replace(_coll(_issue("A")), API_INFO)
    seen = {}
    monkeypatch.setattr(
        QMessageBox, "critical",
        staticmethod(lambda *a, **k: seen.setdefault("msg", a)))
    # exec must never be reached because validation blocks first.
    monkeypatch.setattr(DeltaDialog, "exec",
                        lambda self: (_ for _ in ()).throw(AssertionError("shown")))

    tab._stage_refresh([_issue("")], API_INFO, _plan(), is_csv=True)
    assert seen                                   # a critical dialog was raised
    assert {i.key for i in tab.store.issues} == {"A"}   # dataset untouched


def test_refresh_replace_prunes_annotations_when_opted_out(qapp, monkeypatch):
    tab = _tab(qapp)
    tab.store.replace(_coll(_issue("A"), _issue("B")), API_INFO)
    tab.annotations.set("B", note="worth keeping?")
    assert "B" in tab.annotations
    _accept_delta(monkeypatch, accept=True)

    # Replace with only A -> B is removed; preserve=False prunes B's annotation.
    tab._stage_refresh([_issue("A")], API_INFO, _plan(preserve_annotations=False))
    assert "B" not in tab.annotations


def test_refresh_replace_keeps_annotations_by_default(qapp, monkeypatch):
    tab = _tab(qapp)
    tab.store.replace(_coll(_issue("A"), _issue("B")), API_INFO)
    tab.annotations.set("B", note="keep me")
    _accept_delta(monkeypatch, accept=True)

    tab._stage_refresh([_issue("A")], API_INFO, _plan(preserve_annotations=True))
    assert "B" in tab.annotations               # annotation survives the refresh


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _plan(**kw):
    from issue_deck.refresh import RefreshPlan

    return RefreshPlan(**kw)
