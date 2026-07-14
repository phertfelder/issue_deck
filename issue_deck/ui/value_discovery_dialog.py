"""Field-value discovery dialog: turn real data into usable filters.

The user picks a source — the *current dataset* (works for an imported CSV, so
filters can be built without ever hitting Jira) or a *bounded sample* of a Jira
project/board — and the dialog shows each field's value distribution (coverage,
unique/empty counts, top values, examples). From a selected field they pin a
filter; the value picker adapts to cardinality so a high-cardinality field
offers a searchable combo or free text instead of a giant dropdown.

Pinned filters are returned as :class:`~issue_deck.models.FieldFilter`s via
:meth:`ValueDiscoveryDialog.pinned_filters` for the query tab to apply.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import constants
from ..config import AppConfig
from ..field_values import (
    ValueDistribution,
    WidgetKind,
    discoverable_fields,
    distributions_from_issues,
    jql_token,
)
from ..jira_client import JiraClient
from ..models import FieldFilter
from ..schema import NormalizedIssue
from ..services import value_source_service as vss
from .workers import SampleWorker

# Sensible default operator per widget kind when pinning.
_DEFAULT_OP = {
    WidgetKind.CHECK_LIST: "in",
    WidgetKind.SEARCH_COMBO: "=",
    WidgetKind.TEXT: "~",
}
_FIELD_OPS = ["~", "=", "!=", ">=", "<=", "in"]


class ValueDiscoveryDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        *,
        cfg: AppConfig,
        current_issues: list[NormalizedIssue],
        client_provider: Callable[[], JiraClient] | None = None,
        field_names: dict[str, str] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Discover field values")
        self.resize(880, 560)
        self.cfg = cfg
        self._current_issues = list(current_issues)
        self._client_provider = client_provider
        self._field_names = field_names or {}
        self._dists: list[ValueDistribution] = []
        self._selected: ValueDistribution | None = None
        self._pinned: list[FieldFilter] = []
        self._value_widget: QWidget | None = None
        self._op_combo: QComboBox | None = None
        self._sample_thread: QThread | None = None
        self._sample_worker: SampleWorker | None = None

        self._build()

    # ================= build =================
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        # Build the table + picker first: toggling the source radios (below)
        # loads distributions into the table, which must already exist.
        fields_w = self._build_fields_table()
        picker_w = self._build_picker_panel()
        outer.addWidget(self._build_source_group())

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(fields_w)
        split.addWidget(picker_w)
        split.setSizes([500, 360])
        outer.addWidget(split, 1)

        self.lbl_pinned = QLabel("No filters pinned yet.")
        self.lbl_pinned.setStyleSheet("color: palette(mid);")
        outer.addWidget(self.lbl_pinned)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Add pinned filters")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _build_source_group(self) -> QWidget:
        box = QGroupBox("Source")
        v = QVBoxLayout(box)

        row1 = QHBoxLayout()
        self.rb_current = QRadioButton(
            f"Current dataset ({len(self._current_issues)} issues)")
        self.rb_sample = QRadioButton("Sample from Jira")
        self.rb_current.setEnabled(bool(self._current_issues))
        self.rb_sample.setEnabled(self._client_provider is not None)
        self.rb_current.setChecked(bool(self._current_issues))
        self.rb_sample.setChecked(not self._current_issues and self._client_provider is not None)
        self.rb_current.toggled.connect(self._on_source_toggled)
        self.rb_sample.toggled.connect(self._on_source_toggled)
        row1.addWidget(self.rb_current)
        row1.addWidget(self.rb_sample)
        row1.addStretch()
        rw1 = QWidget()
        rw1.setLayout(row1)
        v.addWidget(rw1)

        # Sample controls (project OR raw JQL + size + go).
        self.sample_row = QWidget()
        row2 = QHBoxLayout(self.sample_row)
        row2.addWidget(QLabel("Project:"))
        self.cmb_project = QComboBox()
        self.cmb_project.setEditable(True)
        self.cmb_project.setMinimumWidth(160)
        self.cmb_project.setToolTip("Pick or type a project key. Ignored if raw JQL is set.")
        row2.addWidget(self.cmb_project)
        row2.addWidget(QLabel("or JQL:"))
        self.ed_jql = QLineEdit()
        self.ed_jql.setPlaceholderText("project = ABC AND created >= -90d")
        row2.addWidget(self.ed_jql, 1)
        row2.addWidget(QLabel("Sample size:"))
        self.sp_size = QSpinBox()
        self.sp_size.setRange(1, constants.SAMPLE_SIZE_MAX)
        self.sp_size.setValue(constants.SAMPLE_SIZE_DEFAULT)
        row2.addWidget(self.sp_size)
        self.btn_sample = QPushButton("Sample")
        self.btn_sample.clicked.connect(self._start_sample)
        row2.addWidget(self.btn_sample)
        v.addWidget(self.sample_row)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: palette(mid);")
        v.addWidget(self.lbl_status)

        self._on_source_toggled()
        return box

    def _build_fields_table(self) -> QWidget:
        box = QGroupBox("Fields")
        v = QVBoxLayout(box)
        self.tbl = QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels(
            ["Field", "Coverage", "Unique", "Empty", "Top values", "Examples"])
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.tbl.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tbl.itemSelectionChanged.connect(self._on_field_selected)
        v.addWidget(self.tbl)
        return box

    def _build_picker_panel(self) -> QWidget:
        box = QGroupBox("Pin a filter")
        self._picker_layout = QVBoxLayout(box)
        self.lbl_picker = QLabel("Select a field to build a filter from its values.")
        self.lbl_picker.setWordWrap(True)
        self._picker_layout.addWidget(self.lbl_picker)

        # Operator + container for the value input (rebuilt per selection).
        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("Operator:"))
        self._op_combo = QComboBox()
        self._op_combo.addItems(_FIELD_OPS)
        op_row.addWidget(self._op_combo)
        op_row.addStretch()
        self._op_row_widget = QWidget()
        self._op_row_widget.setLayout(op_row)
        self._op_row_widget.setVisible(False)
        self._picker_layout.addWidget(self._op_row_widget)

        self._value_container = QWidget()
        self._value_container_layout = QVBoxLayout(self._value_container)
        self._value_container_layout.setContentsMargins(0, 0, 0, 0)
        self._picker_layout.addWidget(self._value_container, 1)

        self.btn_pin = QPushButton("Pin as filter")
        self.btn_pin.clicked.connect(self._pin_current)
        self.btn_pin.setEnabled(False)
        self._picker_layout.addWidget(self.btn_pin)
        return box

    # ================= source handling =================
    def _on_source_toggled(self) -> None:
        self.sample_row.setVisible(self.rb_sample.isChecked())
        if self.rb_current.isChecked() and self._current_issues:
            self._load_distributions(self._current_issues)
        elif self.rb_sample.isChecked():
            self._populate_projects()

    def _populate_projects(self) -> None:
        """Best-effort project list for the combo (never fatal)."""
        if self._client_provider is None or self.cmb_project.count() > 0:
            return
        try:
            client = self._client_provider()
            for opt in vss.project_options(client):
                self.cmb_project.addItem(opt.label, opt.value)
        except Exception:  # noqa: BLE001 - populating projects is best-effort
            pass

    def _sample_jql(self) -> str:
        raw = self.ed_jql.text().strip()
        if raw:
            return raw
        # Combo carries the project key as item data; fall back to the typed text.
        key = self.cmb_project.currentData() or self.cmb_project.currentText().strip()
        return f"project = {key} ORDER BY updated DESC" if key else ""

    def _extra_field_ids(self) -> tuple[str, ...]:
        ids = [f for f in (self.cfg.severity_field, self.cfg.client_field) if f]
        return tuple(dict.fromkeys(ids))

    def _start_sample(self) -> None:
        if self._client_provider is None:
            return
        jql = self._sample_jql()
        if not jql:
            QMessageBox.warning(self, "Nothing to sample",
                                "Choose a project or enter a JQL query first.")
            return
        try:
            client = self._client_provider()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Connection error", str(e))
            return
        self.btn_sample.setEnabled(False)
        self.lbl_status.setText("Sampling…")
        self._sample_thread = QThread()
        self._sample_worker = SampleWorker(
            client, self.cfg, jql, self.sp_size.value(), self._extra_field_ids())
        self._sample_worker.moveToThread(self._sample_thread)
        self._sample_thread.started.connect(self._sample_worker.run)
        self._sample_worker.finished.connect(self._on_sampled)
        self._sample_worker.failed.connect(self._on_sample_failed)
        self._sample_worker.finished.connect(self._sample_thread.quit)
        self._sample_worker.failed.connect(self._sample_thread.quit)
        self._sample_thread.start()

    def _on_sampled(self, issues: list[NormalizedIssue]) -> None:
        self.btn_sample.setEnabled(True)
        self.lbl_status.setText(f"Sampled {len(issues)} issue(s).")
        self._load_distributions(issues)

    def _on_sample_failed(self, msg: str) -> None:
        self.btn_sample.setEnabled(True)
        self.lbl_status.setText("")
        QMessageBox.critical(self, "Sample failed", msg)

    # ================= distributions -> table =================
    def _load_distributions(self, issues: list[NormalizedIssue]) -> None:
        fields = discoverable_fields(issues, field_names=self._field_names)
        self._dists = distributions_from_issues(issues, fields)
        self.tbl.setRowCount(0)
        for dist in self._dists:
            self._add_dist_row(dist)
        self._clear_picker()

    def _add_dist_row(self, dist: ValueDistribution) -> None:
        row = self.tbl.rowCount()
        self.tbl.insertRow(row)
        top = ", ".join(f"{vc.value} ({vc.count})" for vc in dist.top_values[:5])
        cells = [
            dist.field_label,
            f"{dist.coverage_pct:g}%",
            str(dist.unique_count),
            str(dist.empty_count),
            top,
            ", ".join(dist.examples),
        ]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if col == 0:
                item.setData(Qt.ItemDataRole.UserRole, dist.field_id)
            self.tbl.setItem(row, col, item)

    # ================= field selection -> value picker =================
    def _on_field_selected(self) -> None:
        model = self.tbl.selectionModel()
        rows = model.selectedRows() if model is not None else []
        if not rows:
            self._clear_picker()
            return
        item = self.tbl.item(rows[0].row(), 0)
        field_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self._selected = next((d for d in self._dists if d.field_id == field_id), None)
        if self._selected is None:
            self._clear_picker()
            return
        self._show_value_picker(self._selected)

    def _clear_picker(self) -> None:
        self._selected = None
        self._reset_value_container()
        self.lbl_picker.setText("Select a field to build a filter from its values.")
        self._op_row_widget.setVisible(False)
        self.btn_pin.setEnabled(False)

    def _reset_value_container(self) -> None:
        while self._value_container_layout.count():
            item = self._value_container_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        self._value_widget = None

    def _show_value_picker(self, dist: ValueDistribution) -> None:
        self._reset_value_container()
        token = jql_token(dist.field_id, self.cfg)
        kind = dist.widget

        if not token:
            self.lbl_picker.setText(
                f"“{dist.field_label}” isn't directly searchable in JQL, so it "
                "can't be pinned as a filter. Its value distribution is shown for "
                "reference only.")
            self._op_row_widget.setVisible(False)
            self.btn_pin.setEnabled(False)
            return

        self.lbl_picker.setText(
            f"“{dist.field_label}” — {dist.unique_count} distinct value(s), "
            f"{dist.coverage_pct:g}% coverage. {self._kind_hint(kind)}")
        self._op_row_widget.setVisible(True)
        if self._op_combo is not None:
            self._op_combo.setCurrentText(_DEFAULT_OP[kind])

        if kind is WidgetKind.CHECK_LIST:
            lst = QListWidget()
            lst.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            for vc in dist.top_values:
                item = QListWidgetItem(f"{vc.value}  ({vc.count})")
                item.setData(Qt.ItemDataRole.UserRole, vc.value)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                lst.addItem(item)
            self._value_widget = lst
            self._value_container_layout.addWidget(lst)
        elif kind is WidgetKind.SEARCH_COMBO:
            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.addItem("")
            for vc in dist.top_values:
                combo.addItem(f"{vc.value}  ({vc.count})", vc.value)
            combo.setCurrentIndex(0)
            self._value_widget = combo
            self._value_container_layout.addWidget(combo)
        else:  # TEXT
            edit = QLineEdit()
            edit.setPlaceholderText("Type a value (field has too many to list)")
            self._value_widget = edit
            self._value_container_layout.addWidget(edit)

        self.btn_pin.setEnabled(True)

    @staticmethod
    def _kind_hint(kind: WidgetKind) -> str:
        return {
            WidgetKind.CHECK_LIST: "Check the values to include.",
            WidgetKind.SEARCH_COMBO: "Pick from the list or type your own value.",
            WidgetKind.TEXT: "High-cardinality field — enter a value to match.",
        }[kind]

    # ================= pinning =================
    def _selected_values(self) -> list[str]:
        w = self._value_widget
        if isinstance(w, QListWidget):
            out: list[str] = []
            for i in range(w.count()):
                item = w.item(i)
                if item is not None and item.checkState() == Qt.CheckState.Checked:
                    out.append(item.data(Qt.ItemDataRole.UserRole))
            return out
        if isinstance(w, QComboBox):
            data = w.currentData()
            text = data if data else w.currentText().strip()
            return [text] if text else []
        if isinstance(w, QLineEdit):
            text = w.text().strip()
            return [text] if text else []
        return []

    def _pin_current(self) -> None:
        if self._selected is None or self._op_combo is None:
            return
        token = jql_token(self._selected.field_id, self.cfg)
        values = self._selected_values()
        if not token or not values:
            QMessageBox.warning(self, "Nothing to pin", "Choose at least one value.")
            return
        op = self._op_combo.currentText()
        value = ", ".join(values) if op == "in" else values[0]
        self._pinned.append(FieldFilter(
            field=token, op=op, value=value, label=self._selected.field_label))
        self._refresh_pinned_label()

    def _refresh_pinned_label(self) -> None:
        if not self._pinned:
            self.lbl_pinned.setText("No filters pinned yet.")
            return
        chips = "  •  ".join(
            f"{ff.label or ff.field} {ff.op} {ff.value}" for ff in self._pinned)
        self.lbl_pinned.setText(f"Pinned ({len(self._pinned)}):  {chips}")

    def pinned_filters(self) -> list[FieldFilter]:
        """The filters the user pinned (empty if none / dialog cancelled)."""
        return list(self._pinned)
