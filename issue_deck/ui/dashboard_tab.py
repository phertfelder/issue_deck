"""Analytics dashboard tab — local metrics over the loaded dataset.

A thin driver over :mod:`issue_deck.analytics`: it pulls the current working
set from a provider callback, builds an :class:`~issue_deck.analytics.AnalyticsReport`,
and renders each section as a small table. Selecting a metric row resolves the
issue keys behind it and populates an embedded drill-down (a reused
:class:`~issue_deck.ui.results_table.ResultsTable` + detail panel), giving
click-through to exactly those issues without another Jira round-trip.

Refresh is driven externally: :class:`~issue_deck.ui.query_tab.QueryTab` emits
``datasetChanged`` on every fetch/import/merge/clear, which the main window wires
to :meth:`refresh`. The dashboard therefore always reflects the whole loaded
dataset and needs no Jira access once data is present.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..analytics import AnalyticsReport, MetricGroup, MetricRow, build_report
from ..annotations import AnnotationStore
from ..exporters import render_analytics_csv, render_analytics_markdown
from ..schema import NormalizedIssue
from .detail_panel import IssueDetailPanel
from .results_table import ResultsTable

_ROW_ROLE = Qt.ItemDataRole.UserRole


class DashboardTab(QWidget):
    """The "Analytics" tab. ``issues_provider`` yields the current dataset."""

    def __init__(
        self,
        issues_provider: Callable[[], list[NormalizedIssue]],
        annotation_store: AnnotationStore | None = None,
    ) -> None:
        super().__init__()
        self._issues_provider = issues_provider
        self._annotation_store = annotation_store
        self._report: AnalyticsReport | None = None
        self._by_key: dict[str, NormalizedIssue] = {}
        self._build()
        self.refresh()

    # ================= build =================
    def _build(self) -> None:
        outer = QVBoxLayout(self)

        # --- top bar: summary + refresh + exports ---
        bar = QHBoxLayout()
        self.lbl_summary = QLabel("No data loaded.")
        self.lbl_summary.setWordWrap(True)
        bar.addWidget(self.lbl_summary, 1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(self.btn_refresh)
        self.btn_export_md = QPushButton("Export summary (Markdown)")
        self.btn_export_md.clicked.connect(self._export_markdown)
        bar.addWidget(self.btn_export_md)
        self.btn_export_csv = QPushButton("Export summary (CSV)")
        self.btn_export_csv.clicked.connect(self._export_csv)
        bar.addWidget(self.btn_export_csv)
        bw = QWidget()
        bw.setLayout(bar)
        outer.addWidget(bw)

        split = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(split, 1)

        # --- metric sections (scrollable) ---
        self._sections_host = QWidget()
        self._sections_layout = QVBoxLayout(self._sections_host)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._sections_host)
        split.addWidget(scroll)

        # --- drill-down: reuse the workbench results table + detail panel ---
        drill = QWidget()
        dl = QVBoxLayout(drill)
        dl.setContentsMargins(0, 0, 0, 0)
        self.lbl_drill = QLabel("Select a metric row to see the issues behind it.")
        self.lbl_drill.setStyleSheet("color: palette(mid);")
        dl.addWidget(self.lbl_drill)
        hsplit = QSplitter(Qt.Orientation.Horizontal)
        self.drill_table = ResultsTable()
        self.drill_detail = IssueDetailPanel(self._annotation_store)
        self.drill_table.issueSelected.connect(self.drill_detail.show_issue)
        hsplit.addWidget(self.drill_table)
        hsplit.addWidget(self.drill_detail)
        hsplit.setSizes([720, 380])
        dl.addWidget(hsplit, 1)
        split.addWidget(drill)
        split.setSizes([430, 330])

        self._tables: list[tuple[MetricGroup, QTableWidget]] = []

    # ================= refresh =================
    def refresh(self) -> None:
        """Recompute the report from the current dataset and re-render."""
        issues = list(self._issues_provider())
        self._by_key = {i.key: i for i in issues if i.key}
        self._report = build_report(issues)
        self._render_summary()
        self._render_sections()
        # A stale drill-down could reference issues no longer present.
        self.drill_table.populate([])
        self.drill_detail.show_issue(None)
        self.lbl_drill.setText("Select a metric row to see the issues behind it.")

    def _render_summary(self) -> None:
        r = self._report
        if r is None or r.total == 0:
            self.lbl_summary.setText(
                "No data loaded — fetch or import issues to see analytics.")
            return
        note = "" if r.comments_loaded else "  ·  comments not loaded"
        self.lbl_summary.setText(
            f"{r.total} issues  ·  {r.open_count} open / {r.done_count} done{note}")

    def _render_sections(self) -> None:
        # Clear any previously-built section widgets.
        while self._sections_layout.count():
            item = self._sections_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._tables = []
        if self._report is None:
            return
        for group in self._report.sections:
            self._sections_layout.addWidget(self._build_section(group))
        self._sections_layout.addStretch(1)

    def _build_section(self, group: MetricGroup) -> QWidget:
        box = QGroupBox(group.title)
        v = QVBoxLayout(box)
        if group.note:
            lbl = QLabel(group.note)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: palette(mid);")
            v.addWidget(lbl)
        if not group.rows:
            self._tables.append((group, self._empty_table()))
            v.addWidget(self._tables[-1][1])
            return box
        table = self._section_table(group)
        self._tables.append((group, table))
        v.addWidget(table)
        return box

    @staticmethod
    def _empty_table() -> QTableWidget:
        t = QTableWidget(0, 1)
        t.setHorizontalHeaderLabels(["Metric"])
        t.setMaximumHeight(0)
        t.hide()
        return t

    def _section_table(self, group: MetricGroup) -> QTableWidget:
        with_points = group.has_points
        headers = ["Metric", "Count", "%"] + (["Points"] if with_points else [])
        total = self._report.total if self._report else 0
        table = QTableWidget(len(group.rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for r, row in enumerate(group.rows):
            metric = QTableWidgetItem(row.label)
            metric.setData(_ROW_ROLE, r)
            table.setItem(r, 0, metric)
            count = QTableWidgetItem(str(row.count))
            count.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(r, 1, count)
            pct = QTableWidgetItem(_pct(row.count, total))
            pct.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(r, 2, pct)
            if with_points:
                pts = QTableWidgetItem(_points_str(row.points))
                pts.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(r, 3, pts)
        self._fit_height(table, len(group.rows))
        table.itemSelectionChanged.connect(
            lambda g=group, t=table: self._on_metric_selected(g, t))
        return table

    @staticmethod
    def _fit_height(table: QTableWidget, rows: int) -> None:
        """Size the table to its content so many sections stack without inner scrollbars."""
        header = table.horizontalHeader()
        header_h = header.height() if header is not None else 24
        row_h = table.verticalHeader().defaultSectionSize() if table.verticalHeader() else 24
        table.setMinimumHeight(header_h + row_h * max(rows, 1) + 8)
        table.setMaximumHeight(header_h + row_h * max(rows, 1) + 8)

    # ================= click-through =================
    def _on_metric_selected(self, group: MetricGroup, table: QTableWidget) -> None:
        sm = table.selectionModel()
        rows = sm.selectedRows() if sm is not None else []
        if not rows:
            return
        item = table.item(rows[0].row(), 0)
        idx = item.data(_ROW_ROLE) if item is not None else None
        if not isinstance(idx, int) or not (0 <= idx < len(group.rows)):
            return
        metric = group.rows[idx]
        self._show_drill(group, metric)

    def _show_drill(self, group: MetricGroup, metric: MetricRow) -> None:
        matched = [self._by_key[k] for k in metric.keys if k in self._by_key]
        self.drill_table.populate(matched)
        self.drill_detail.show_issue(None)
        self.lbl_drill.setText(
            f"{group.title} → {metric.label}: {len(matched)} issue(s)")

    # ================= exports =================
    def _export_markdown(self) -> None:
        if not self._require_report():
            return
        p, _ = QFileDialog.getSaveFileName(
            self, "Export analytics summary", "jira_analytics.md", "Markdown (*.md)")
        if p:
            Path(p).write_text(render_analytics_markdown(self._report), encoding="utf-8")
            QMessageBox.information(self, "Exported", p)

    def _export_csv(self) -> None:
        if not self._require_report():
            return
        p, _ = QFileDialog.getSaveFileName(
            self, "Export analytics summary", "jira_analytics.csv", "CSV (*.csv)")
        if p:
            Path(p).write_text(render_analytics_csv(self._report), encoding="utf-8")
            QMessageBox.information(self, "Exported", p)

    def _require_report(self) -> bool:
        if self._report is None or self._report.total == 0:
            QMessageBox.warning(
                self, "Nothing to export", "Fetch or import some issues first.")
            return False
        return True


def _pct(count: int, total: int) -> str:
    return f"{(100.0 * count / total):.1f}%" if total else "0.0%"


def _points_str(value: float | int | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)
