"""Delta / merge dialog: show what a replace, refresh, or merge would change.

Wraps a :class:`~issue_deck.merge.DeltaPreview` in a single, category-filterable
table (plus a summary banner) so the user can review the impact **before** a
destructive operation, filter it down to one change kind, and export a report.
When opened for a merge (``allow_conflict_rule=True``) it also exposes the
:class:`~issue_deck.merge.ConflictRule` selector and reports the chosen rule.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..merge import ConflictRule, DeltaCategory, DeltaPreview
from ..refresh import render_delta_report

# Radio label <-> rule, in display order (default first).
_RULES: list[tuple[str, ConflictRule]] = [
    ("Newest updated wins (default)", ConflictRule.NEWEST_WINS),
    ("Jira API wins", ConflictRule.API_WINS),
    ("CSV wins", ConflictRule.CSV_WINS),
    ("Ask me per conflict", ConflictRule.ASK),
]

# Sentinel combo data meaning "don't filter".
_ALL = "__all__"


class DeltaDialog(QDialog):
    """Non-destructive preview of a replace/refresh/merge, with optional rule."""

    def __init__(
        self,
        delta: DeltaPreview,
        parent: QWidget | None = None,
        *,
        title: str = "Preview changes",
        allow_conflict_rule: bool = False,
        rule: ConflictRule = ConflictRule.NEWEST_WINS,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(680, 520)
        self._delta = delta
        self._rows = delta.rows()
        self._rule_buttons: dict[ConflictRule, QRadioButton] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(self._summary_label(delta))
        layout.addWidget(self._filter_bar(delta))
        self._table = self._build_table()
        layout.addWidget(self._table, 1)
        self._populate(_ALL)

        if allow_conflict_rule:
            layout.addWidget(self._rule_box(rule))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText("Confirm")
        self.btn_export = buttons.addButton(
            "Export report…", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.btn_export.clicked.connect(self._export_report)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ---- building blocks ----
    def _summary_label(self, delta: DeltaPreview) -> QLabel:
        s = delta.summary()
        text = (
            f"<b>{s['new']}</b> new · <b>{s['removed']}</b> removed · "
            f"<b>{s['carried_over']}</b> carried over · "
            f"<b>{s['newly_resolved']}</b> newly resolved · "
            f"<b>{s['reopened']}</b> reopened"
        )
        if delta.is_destructive:
            text += "  —  <span style='color:#c0392b;'>this will alter existing data</span>"
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        return lbl

    def _filter_bar(self, delta: DeltaPreview) -> QWidget:
        row = QHBoxLayout()
        row.addWidget(QLabel("Show category:"))
        self.cmb_category = QComboBox()
        self.cmb_category.addItem(f"All changes ({len(self._rows)})", _ALL)
        for category, n in delta.counts().items():
            self.cmb_category.addItem(f"{category.label} ({n})", category.value)
        self.cmb_category.currentIndexChanged.connect(
            lambda _i: self._populate(self.cmb_category.currentData())
        )
        row.addWidget(self.cmb_category)
        row.addStretch()
        w = QWidget()
        w.setLayout(row)
        return w

    def _build_table(self) -> QTableWidget:
        t = QTableWidget(0, 4)
        t.setHorizontalHeaderLabels(["Key", "Category", "Before", "After"])
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = t.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        return t

    def _populate(self, category: str) -> None:
        rows = (
            self._rows if category == _ALL
            else [r for r in self._rows if r.category.value == category]
        )
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            # New/removed rows carry a summary instead of before/after values.
            before = r.before or (r.summary if r.category == DeltaCategory.NEW else "—")
            after = r.after or (r.summary if r.category == DeltaCategory.REMOVED else "—")
            values = (r.key or "(no key)", r.category.label, before, after)
            for col, val in enumerate(values):
                self._table.setItem(i, col, QTableWidgetItem(str(val)))

    def _rule_box(self, rule: ConflictRule) -> QGroupBox:
        box = QGroupBox("On conflict (same issue key in both)")
        v = QVBoxLayout(box)
        group = QButtonGroup(self)
        for label, value in _RULES:
            rb = QRadioButton(label)
            rb.setChecked(value == rule)
            group.addButton(rb)
            v.addWidget(rb)
            self._rule_buttons[value] = rb
        return box

    # ---- export ----
    def _export_report(self) -> None:
        path, selected = QFileDialog.getSaveFileName(
            self, "Export delta report", "refresh_delta.csv",
            "CSV (*.csv);;Text (*.txt)",
        )
        if not path:
            return
        fmt = "text" if (path.lower().endswith(".txt") or "Text" in selected) else "csv"
        try:
            Path(path).write_text(render_delta_report(self._delta, fmt=fmt), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - surface, don't crash the dialog
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Exported", path)

    # ---- result ----
    def selected_rule(self) -> ConflictRule:
        """The conflict rule chosen (default when the selector was hidden)."""
        for value, rb in self._rule_buttons.items():
            if rb.isChecked():
                return value
        return ConflictRule.NEWEST_WINS
