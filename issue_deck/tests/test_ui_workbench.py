"""Headless tests for the workbench UI: results table, detail panel, query tab.

Widgets are driven programmatically under the offscreen Qt platform. Modal
dialogs are stubbed and the app dir is redirected to a tmp path so saved views
never touch the real config.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.models import FieldFilter, SavedView, SearchFilters
from issue_deck.schema import (
    JiraComment,
    JiraUser,
    NormalizedIssue,
    SourceMetadata,
)
from issue_deck.ui.detail_panel import IssueDetailPanel
from issue_deck.ui.query_tab import QueryTab
from issue_deck.ui.results_table import (
    _MARK_BLOCKED,
    _MARK_HIGH,
    _MARKER_ROLE,
    ResultsTable,
)


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


# --------------------------------------------------------------------------- #
# Results table
# --------------------------------------------------------------------------- #
def test_table_sort_by_key(qapp):
    t = ResultsTable()
    t.populate([_issue("A-3"), _issue("A-1"), _issue("A-2")])
    t.sortItems(0)  # key column, ascending
    keys = [t.item(r, 0).text() for r in range(t.rowCount())]
    assert keys == ["A-1", "A-2", "A-3"]


def test_table_numeric_points_sort(qapp):
    t = ResultsTable()
    t.set_columns(["key", "story_points"])
    t.populate([_issue("A", story_points=10), _issue("B", story_points=None),
                _issue("C", story_points=5)])
    t.sortItems(1)  # points ascending: None(-inf), 5, 10
    keys = [t.item(r, 0).text() for r in range(t.rowCount())]
    assert keys == ["B", "C", "A"]


def test_table_quick_filter_hides_rows(qapp):
    t = ResultsTable()
    t.populate([_issue("A", summary="login crash"), _issue("B", summary="export feature")])
    assert t.apply_quick_filter("crash") == 1
    assert t.isRowHidden(1) or t.isRowHidden(0)  # one hidden
    assert t.apply_quick_filter("") == 2          # cleared -> all shown


def test_table_high_priority_marker(qapp):
    t = ResultsTable()
    t.populate([_issue("A", priority="High"), _issue("B", priority="Low")])
    # Row 0 (high) carries a left-edge marker flag; row 1 does not. The marker
    # is a narrow bar drawn by the delegate — it must NOT repaint the whole row
    # background (the old full-row tint that killed contrast in dark mode).
    assert t.item(0, 0).data(_MARKER_ROLE) == _MARK_HIGH
    assert not t.item(1, 0).data(_MARKER_ROLE)
    # No per-cell background brush is set on any row (no full-row fill).
    assert t.item(0, 0).background().style() == Qt.BrushStyle.NoBrush
    assert t.item(1, 0).background().style() == Qt.BrushStyle.NoBrush


def test_table_blocked_marker(qapp):
    t = ResultsTable()
    t.populate([_issue("A", status="Blocked")])
    # Blocked outranks priority/staleness for the single left-edge marker.
    assert t.item(0, 0).data(_MARKER_ROLE) == _MARK_BLOCKED
    assert t.item(0, 0).background().style() == Qt.BrushStyle.NoBrush  # no fill


def test_table_stale_marker_tooltip(qapp):
    t = ResultsTable()
    t.populate([_issue("A", updated="2026-01-01T00:00:00+00:00")])  # long ago
    # Stale rows carry an explanatory tooltip.
    assert "Stale" in t.item(0, 0).toolTip()


def test_table_column_toggle(qapp):
    t = ResultsTable()
    t.set_columns(["key", "summary", "priority"])
    assert t.visible_columns() == ["key", "summary", "priority"]
    assert t.columnCount() == 3


def test_table_copy_actions(qapp):
    t = ResultsTable()
    issue = _issue("A-9", summary="hi", url="https://x/browse/A-9")
    t.populate([issue])
    t.copy_key(issue)
    assert QApplication.clipboard().text() == "A-9"
    t.copy_markdown(issue)
    assert "# A-9 — hi" in QApplication.clipboard().text()


def test_table_emits_selected_issue(qapp):
    t = ResultsTable()
    t.populate([_issue("A"), _issue("B")])
    seen = []
    t.issueSelected.connect(lambda i: seen.append(i.key if i else None))
    t.selectRow(1)
    assert seen and seen[-1] == "B"


# --------------------------------------------------------------------------- #
# Detail panel
# --------------------------------------------------------------------------- #
def test_detail_panel_renders_issue(qapp):
    panel = IssueDetailPanel()
    issue = _issue("A-1", summary="Boom", description="it broke", status="Open",
                   comments=[JiraComment(author="Ada", body="looking into it")])
    panel.show_issue(issue)
    html = panel.view.toHtml()
    assert "A-1" in html and "it broke" in html and "Ada" in html
    assert panel.btn_open.isEnabled() is False  # no url


def test_detail_panel_placeholder_when_cleared(qapp):
    panel = IssueDetailPanel()
    panel.show_issue(None)
    assert "Select an issue" in panel.view.toHtml()


# --------------------------------------------------------------------------- #
# Query tab
# --------------------------------------------------------------------------- #
def _tab():
    cfg = AppConfig(base_url="https://x.atlassian.net", deployment="cloud", email="a@b.c")
    return QueryTab(cfg, lambda: cfg, lambda: None)


def test_query_tab_smart_defaults(qapp):
    f = _tab()._filters()
    assert f.assigned_to_me and f.unresolved and f.updated_days == 90


def test_query_tab_preview_shows_jql_fields(qapp):
    tab = _tab()
    tab._preview()
    assert "resolution = Unresolved" in tab.jql_view.toPlainText()
    assert "Fields:" in tab.lbl_fields.text()
    assert tab.lbl_warn.text() == ""  # defaults are not broad


def test_query_tab_broad_search_warns(qapp):
    tab = _tab()
    tab.cb_assigned.setChecked(False)
    tab.sp_updated.setValue(0)
    tab.cb_unresolved.setChecked(False)
    tab._preview()
    assert "⚠" in tab.lbl_warn.text()


def test_query_tab_raw_mode_round_trips(qapp):
    tab = _tab()
    tab.cb_raw.setChecked(True)
    tab.ed_raw.setPlainText("project = ZZZ ORDER BY created ASC")
    f = tab._filters()
    assert f.raw_mode and f.raw_jql == "project = ZZZ ORDER BY created ASC"


def test_query_tab_field_filters_from_table(qapp):
    tab = _tab()
    tab._add_field_filter_row(FieldFilter(field="labels", op="=", value="settlement"))
    ffs = tab._filters().field_filters
    assert ffs == [FieldFilter(field="labels", op="=", value="settlement")]


def test_query_tab_apply_and_read_filters(qapp):
    tab = _tab()
    src = SearchFilters(
        assigned_to_me=False, reported_by_me=True, projects=["ABC", "DEF"],
        status_categories=["Done"], statuses=["Open"], text="crash",
        created_days=30, unresolved=True,
        field_filters=[FieldFilter(field="cf[10050]", op="~", value="Acme")],
    )
    tab._apply_filters(src)
    got = tab._filters()
    assert got.reported_by_me and not got.assigned_to_me
    assert got.projects == ["ABC", "DEF"]
    assert got.status_categories == ["Done"]
    assert got.statuses == ["Open"]
    assert got.text == "crash"
    assert got.created_days == 30
    assert got.field_filters == src.field_filters


def test_query_tab_saved_view_load_applies_columns(qapp):
    tab = _tab()
    view = SavedView(
        name="V", filters=SearchFilters(projects=["ABC"], text="x"),
        visible_columns=["key", "status"], sort_column="key", sort_desc=False)
    tab.views.save(view)
    tab._reload_views_combo()
    tab.cmb_views.setCurrentText("V")
    tab._view_load()
    assert tab.table.visible_columns() == ["key", "status"]
    assert tab._filters().projects == ["ABC"]


def test_query_tab_save_view_via_dialog(qapp, monkeypatch):
    tab = _tab()
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("My View", True)))
    tab._view_save()
    assert "My View" in tab.views.names()
    # Persisted to the isolated app dir.
    assert tab.views.path.exists()


class _CapClient:
    """Minimal client exposing just what fetch_capabilities needs."""

    def __init__(self, fields):
        self._fields = fields

    def fields_raw(self):
        return self._fields


def test_query_tab_watched_enabled_when_supported(qapp):
    tab = _tab()
    tab.on_connected(_CapClient([
        {"id": "watches", "name": "Watchers", "clauseNames": ["watcher"]},
    ]))
    assert tab.cb_watched.isEnabled()


def test_query_tab_watched_disabled_when_unsupported(qapp):
    tab = _tab()
    tab.cb_watched.setChecked(True)
    tab.on_connected(_CapClient([
        {"id": "summary", "name": "Summary", "clauseNames": ["summary"]},
    ]))
    assert not tab.cb_watched.isEnabled()
    assert not tab.cb_watched.isChecked()          # forced off
    assert not tab._filters().watched_by_me         # and never emitted


def test_query_tab_probe_failure_stays_optimistic(qapp):
    tab = _tab()

    class Boom:
        def fields_raw(self):
            raise RuntimeError("network down")

    tab.on_connected(Boom())
    assert tab.cb_watched.isEnabled()               # unchanged on failure


def test_query_tab_unsupported_view_does_not_check_watched(qapp):
    tab = _tab()
    tab.on_connected(_CapClient([
        {"id": "summary", "name": "Summary", "clauseNames": ["summary"]},
    ]))
    tab._apply_filters(SearchFilters(watched_by_me=True))
    assert not tab.cb_watched.isChecked()
    assert not tab._filters().watched_by_me


def test_query_tab_comments_mode_bridge(qapp):
    from issue_deck.comments import CommentsMode
    tab = _tab()
    tab._set_comments_mode(CommentsMode.LATEST)
    tab.sp_latest.setValue(3)
    opts = tab._comments_options()
    assert opts.mode is CommentsMode.LATEST and opts.latest_n == 3
    # 'None' maps back onto the persisted load_comments=False flag.
    tab._set_comments_mode(CommentsMode.NONE)
    assert tab._filters().load_comments is False
    tab._set_comments_mode(CommentsMode.ALL)
    assert tab._filters().load_comments is True


def test_query_tab_cancel_calls_worker(qapp):
    tab = _tab()

    class FakeWorker:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    tab._fetch_worker = FakeWorker()
    tab.btn_cancel.setEnabled(True)
    tab._cancel_fetch()
    assert tab._fetch_worker.cancelled
    assert not tab.btn_cancel.isEnabled()
    assert "Cancel" in tab.status.text()


def test_query_tab_on_cancelled_resets(qapp):
    tab = _tab()
    tab.btn_fetch.setEnabled(False)
    tab._on_cancelled()
    assert tab.btn_fetch.isEnabled()
    assert "cancelled" in tab.status.text().lower()


def test_query_tab_renders_comment_warnings(qapp):
    from issue_deck.services.issue_service import FetchResult, IssueWarning
    tab = _tab()
    tab._last_jql = "assignee = currentUser()"
    tab._pending_commented_days = 0
    result = FetchResult(
        issues=[_issue("K-1")],
        warnings=[IssueWarning("K-1", "Failed to load comments: boom")],
    )
    tab._on_fetched(result)
    assert not tab.warn_row.isHidden()          # explicitly shown (ancestor is offscreen)
    assert not tab.btn_warnings.isHidden()
    assert "comment warning" in tab.status.text()
    assert "K-1: Failed to load comments: boom" in tab._warnings_text()


def test_query_tab_renders_cap_warning(qapp):
    from issue_deck.services.issue_service import FetchResult
    tab = _tab()
    tab._last_jql = "x"
    tab._pending_commented_days = 0
    result = FetchResult(issues=[_issue("K-1")], truncated=True,
                         total_available=500, cap=1)
    tab._on_fetched(result)
    assert not tab.warn_row.isHidden()
    assert "capped" in tab.lbl_fetch_warn.text().lower()


def test_query_tab_fetch_result_populates_store(qapp):
    """The classic fetch flow still works: results land in the store + table."""
    from issue_deck.services.issue_service import FetchResult
    tab = _tab()
    tab._last_jql = "assignee = currentUser()"
    tab._pending_commented_days = 0
    tab._on_fetched(FetchResult(issues=[_issue("K-1"), _issue("K-2")]))
    assert len(tab.store) == 2
    assert tab.store.kind.value == "jira_api"
    assert "Jira API live search" in tab.lbl_source.text()
    assert tab.table.rowCount() == 2


# --------------------------------------------------------------------------- #
# Value-discovery wiring
# --------------------------------------------------------------------------- #
class _OptClient:
    """Client exposing capability + value-source endpoints for hydration."""

    def fields_raw(self):
        return [{"id": "summary", "name": "Summary", "clauseNames": ["summary"]}]

    def statuses(self):
        return [{"name": "Backlog"}, {"name": "Open"}]  # "Open" is already a default

    def issue_types(self):
        return [{"name": "Spike"}]


def _list_texts(lst):
    return [lst.item(i).text() for i in range(lst.count())]


def test_on_connected_hydrates_status_and_type_lists(qapp):
    tab = _tab()
    tab.on_connected(_OptClient())
    statuses = _list_texts(tab.lst_status)
    assert "Backlog" in statuses                 # new value merged in
    assert statuses.count("Open") == 1           # existing default not duplicated
    assert "Spike" in _list_texts(tab.lst_type)


def test_merge_list_items_is_case_insensitive_and_additive(qapp):
    tab = _tab()
    before = len(_list_texts(tab.lst_status))
    tab._merge_list_items(tab.lst_status, ["OPEN", "", "Triage"])
    after = _list_texts(tab.lst_status)
    assert len(after) == before + 1              # only "Triage" is new
    assert "Triage" in after


def test_hydration_survives_client_without_endpoints(qapp):
    # The capability-only client has no statuses()/issue_types(): best-effort,
    # so hydration must not raise and lists stay at their defaults.
    tab = _tab()
    before = _list_texts(tab.lst_status)
    tab.on_connected(_CapClient([{"id": "summary", "clauseNames": ["summary"]}]))
    assert _list_texts(tab.lst_status) == before


def test_discover_values_pins_returned_filters(qapp, monkeypatch):
    tab = _tab()

    class _FakeDiscovery:
        def __init__(self, parent, *, cfg, current_issues, client_provider=None):
            pass

        def exec(self):
            return True

        def pinned_filters(self):
            return [FieldFilter(field="status", op="in", value="Open"),
                    FieldFilter(field="cf[10060]", op="=", value="S1")]

    monkeypatch.setattr("issue_deck.ui.query_tab.ValueDiscoveryDialog", _FakeDiscovery)
    tab._discover_values()
    ffs = tab._filters().field_filters
    assert FieldFilter(field="status", op="in", value="Open") in ffs
    assert FieldFilter(field="cf[10060]", op="=", value="S1") in ffs


# --------------------------------------------------------------------------- #
# Guided ⇄ Raw toggle + Advanced JQL drawer (PR 4)
# --------------------------------------------------------------------------- #
def test_mode_toggle_switches_to_raw(qapp):
    tab = _tab()
    assert tab._body_stack.currentIndex() == 0        # Guided by default
    tab.mode_toggle.changed.emit(1)                   # user picks Raw
    assert tab.cb_raw.isChecked()                     # backing state follows
    assert tab._body_stack.currentIndex() == 1        # body swaps to raw editor


def test_apply_raw_filters_syncs_toggle_and_body(qapp):
    # Programmatic apply (e.g. a Home preset in raw mode) also swaps the body.
    tab = _tab()
    tab._apply_filters(SearchFilters(raw_mode=True, raw_jql="project = ZZZ"))
    assert tab._body_stack.currentIndex() == 1
    assert tab.mode_toggle.current_index() == 1


def test_enter_raw_mode_helper(qapp):
    tab = _tab()
    tab.enter_raw_mode()
    assert tab.cb_raw.isChecked()
    assert tab._body_stack.currentIndex() == 1


def test_advanced_drawer_shows_explain_and_breadth_pill(qapp):
    tab = _tab()
    # Default filters are bounded → a reading, no broad-query pill. (Use
    # isHidden(): the drawer is collapsed, so isVisible() is always False here.)
    tab._preview()
    assert "Find issues" in tab.lbl_explain.text()
    assert tab.lbl_breadth.isHidden() is True
    # Strip every bound → broad-query pill appears.
    tab.cb_assigned.setChecked(False)
    tab.sp_updated.setValue(0)
    tab.cb_unresolved.setChecked(False)
    tab._preview()
    assert tab.lbl_breadth.isHidden() is False


def test_validate_raw_result_handler(qapp):
    # The finished-handler renders the validation message (thread-free).
    from issue_deck.jql_helper import JqlValidation
    tab = _tab()
    tab._on_validated(JqlValidation(True, "Valid JQL — matches at least one issue."))
    assert "Valid JQL" in tab.lbl_validate.text()


def test_validate_raw_empty_is_guarded(qapp):
    tab = _tab()
    tab.enter_raw_mode()
    tab.ed_raw.setPlainText("   ")
    tab._validate_raw()
    assert "Enter JQL" in tab.lbl_validate.text()


def test_discover_values_cancelled_pins_nothing(qapp, monkeypatch):
    tab = _tab()

    class _Cancelled:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return False

        def pinned_filters(self):
            return [FieldFilter(field="status", op="in", value="Open")]

    monkeypatch.setattr("issue_deck.ui.query_tab.ValueDiscoveryDialog", _Cancelled)
    tab._discover_values()
    assert tab._filters().field_filters == []
