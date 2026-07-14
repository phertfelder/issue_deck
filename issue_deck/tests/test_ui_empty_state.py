"""Tests for PR 9: empty-results state + focus rings / accessible names."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.datasource import DataSourceInfo, DataSourceKind
from issue_deck.schema import IssueCollection, JiraUser, NormalizedIssue, SourceMetadata
from issue_deck.ui import theme
from issue_deck.ui.empty_state import EmptyState
from issue_deck.ui.nav_rail import NavRail
from issue_deck.ui.query_tab import QueryTab


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


def _issue(key):
    return NormalizedIssue(key=key, assignee=JiraUser(display_name="x"),
                           source=SourceMetadata.for_api("cloud"))


# --------------------------------------------------------------------------- #
# EmptyState widget
# --------------------------------------------------------------------------- #
def test_empty_state_sets_message_and_cta(qapp):
    ran = []
    es = EmptyState("Import CSV…", lambda: ran.append(True))
    es.set_message("No issues yet", "Do something.")
    assert es._title.text() == "No issues yet"
    assert es._message.text() == "Do something."
    es._cta.click()
    assert ran == [True]


# --------------------------------------------------------------------------- #
# Empty state wired into the results area
# --------------------------------------------------------------------------- #
def test_empty_state_shown_on_first_launch(qapp):
    tab = _tab()
    assert tab._results_stack.currentIndex() == 0             # empty state, not table
    assert "No issues yet" in tab._empty_state._title.text()


def test_table_shown_once_data_arrives(qapp):
    tab = _tab()
    tab.store.replace(
        IssueCollection(issues=[_issue("K-1")]),
        DataSourceInfo(kind=DataSourceKind.JIRA_API, label="api", detail="x",
                       deployment="cloud"))
    tab._refresh_dataset_views()
    assert tab._results_stack.currentIndex() == 1             # table

    tab._clear_dataset()
    assert tab._results_stack.currentIndex() == 0             # back to empty state


def test_empty_state_message_is_contextual_after_a_query(qapp):
    tab = _tab()
    tab._last_jql = "assignee = currentUser()"               # a fetch happened
    tab._refresh_dataset_views()                              # ...that returned nothing
    assert tab._results_stack.currentIndex() == 0
    assert "No issues matched" in tab._empty_state._title.text()
    assert "Widen your filters" in tab._empty_state._message.text()


# --------------------------------------------------------------------------- #
# Focus rings + accessible names
# --------------------------------------------------------------------------- #
def test_qss_has_focus_rings():
    qss = theme.build_qss(theme.DARK)
    assert ":focus" in qss
    assert "QListWidget:focus" in qss


def test_nav_buttons_have_accessible_names(qapp):
    rail = NavRail()
    btn = rail.add_item("Query", 1)
    assert "Query" in btn.accessibleName()


def test_results_table_and_quick_filter_have_accessible_names(qapp):
    tab = _tab()
    assert tab.table.accessibleName() == "Results table"
    assert tab.ed_quick.accessibleName() == "Quick filter"
