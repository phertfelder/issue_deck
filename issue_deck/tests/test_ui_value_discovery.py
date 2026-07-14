"""Headless tests for the value-discovery dialog (current-dataset path)."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QListWidget,
    QMessageBox,
)

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.schema import JiraUser, NormalizedIssue
from issue_deck.ui.value_discovery_dialog import ValueDiscoveryDialog


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _stub_boxes(monkeypatch):
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def _cfg() -> AppConfig:
    return AppConfig(base_url="https://x", deployment="cloud", email="a@b.c",
                     severity_field="customfield_10060", client_field="customfield_10050")


def _issue(**kw) -> NormalizedIssue:
    a = kw.pop("assignee", "")
    return NormalizedIssue(assignee=JiraUser(display_name=a), **kw)


def _dataset() -> list[NormalizedIssue]:
    return [
        _issue(key="A-1", status="Open", issue_type="Bug", severity="S1", story_points=3),
        _issue(key="A-2", status="Open", issue_type="Task", severity="S2", story_points=5),
        _issue(key="A-3", status="Done", issue_type="Bug", severity="S1"),
        _issue(key="A-4", status="", issue_type="Bug"),  # empty status
    ]


def _dialog(qapp, monkeypatch, issues=None, **kw):
    return ValueDiscoveryDialog(None, cfg=_cfg(),
                                current_issues=issues if issues is not None else _dataset(), **kw)


def _select_field(dlg, field_id: str) -> None:
    for row in range(dlg.tbl.rowCount()):
        item = dlg.tbl.item(row, 0)
        if item.data(Qt.ItemDataRole.UserRole) == field_id:
            dlg.tbl.selectRow(row)
            return
    raise AssertionError(f"field {field_id} not in table")


# ---- table population ----
def test_loads_distributions_from_current_dataset(qapp, monkeypatch):
    dlg = _dialog(qapp, monkeypatch)
    field_ids = {dlg.tbl.item(r, 0).data(Qt.ItemDataRole.UserRole)
                 for r in range(dlg.tbl.rowCount())}
    assert "status" in field_ids
    assert "severity" in field_ids
    # client has no values in the sample -> dropped
    assert "client" not in field_ids


def test_sample_source_disabled_without_client(qapp, monkeypatch):
    dlg = _dialog(qapp, monkeypatch)
    assert dlg.rb_sample.isEnabled() is False
    assert dlg.rb_current.isChecked() is True


# ---- check-list pinning (low cardinality) ----
def test_pin_checklist_field_builds_in_filter(qapp, monkeypatch):
    dlg = _dialog(qapp, monkeypatch)
    _select_field(dlg, "status")
    lst = dlg._value_widget
    assert isinstance(lst, QListWidget)
    # Check the "Open" value.
    for i in range(lst.count()):
        if lst.item(i).data(Qt.ItemDataRole.UserRole) == "Open":
            lst.item(i).setCheckState(Qt.CheckState.Checked)
    dlg._op_combo.setCurrentText("in")
    dlg._pin_current()
    pins = dlg.pinned_filters()
    assert len(pins) == 1
    assert pins[0].field == "status"
    assert pins[0].op == "in"
    assert pins[0].value == "Open"


def test_pin_severity_uses_custom_field_token(qapp, monkeypatch):
    dlg = _dialog(qapp, monkeypatch)
    _select_field(dlg, "severity")
    lst = dlg._value_widget
    for i in range(lst.count()):
        lst.item(i).setCheckState(Qt.CheckState.Checked)  # S1 + S2
    dlg._op_combo.setCurrentText("in")
    dlg._pin_current()
    pin = dlg.pinned_filters()[0]
    assert pin.field == "cf[10060]"
    assert "S1" in pin.value and "S2" in pin.value


def test_pin_without_selection_warns_and_skips(qapp, monkeypatch):
    dlg = _dialog(qapp, monkeypatch)
    _select_field(dlg, "status")
    # Nothing checked -> no pin.
    dlg._pin_current()
    assert dlg.pinned_filters() == []


# ---- non-searchable field ----
def test_non_searchable_field_disables_pin(qapp, monkeypatch):
    dlg = _dialog(qapp, monkeypatch)
    _select_field(dlg, "story_points")  # no JQL token
    assert dlg.btn_pin.isEnabled() is False


# ---- cardinality-adaptive widgets ----
def test_medium_cardinality_uses_search_combo(qapp, monkeypatch):
    monkeypatch.setattr(constants, "ENUM_MAX_UNIQUE", 1)
    monkeypatch.setattr(constants, "SEARCH_COMBO_MAX_UNIQUE", 10)
    dlg = _dialog(qapp, monkeypatch)
    _select_field(dlg, "status")  # 2 unique -> combo
    assert isinstance(dlg._value_widget, QComboBox)
    dlg._value_widget.setCurrentIndex(1)  # first real value
    dlg._op_combo.setCurrentText("=")
    dlg._pin_current()
    assert dlg.pinned_filters()[0].op == "="


def test_high_cardinality_uses_text_entry(qapp, monkeypatch):
    monkeypatch.setattr(constants, "ENUM_MAX_UNIQUE", 1)
    monkeypatch.setattr(constants, "SEARCH_COMBO_MAX_UNIQUE", 1)
    dlg = _dialog(qapp, monkeypatch)
    _select_field(dlg, "status")  # 2 unique -> text
    assert isinstance(dlg._value_widget, QLineEdit)
    dlg._value_widget.setText("Open")
    dlg._op_combo.setCurrentText("~")
    dlg._pin_current()
    assert dlg.pinned_filters()[0].value == "Open"


def test_empty_dataset_no_rows(qapp, monkeypatch):
    dlg = _dialog(qapp, monkeypatch, issues=[])
    assert dlg.tbl.rowCount() == 0
    assert dlg.rb_current.isEnabled() is False
