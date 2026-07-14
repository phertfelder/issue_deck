"""Refresh dialog: pick where to re-materialize the dataset from, then apply.

This dialog only *collects a plan* — it does no I/O. The user chooses a source
(re-run a Jira query — the current builder state or any saved view — import a
newer CSV, or paste/edit JQL), how to apply the result (replace vs merge +
conflict rule), and whether to keep local annotations for issues that disappear.
:class:`~issue_deck.ui.query_tab.QueryTab` reads :meth:`plan` and executes it:
fetch/parse → validate → delta preview → confirm → replace/merge. The destructive
step never happens here.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..merge import ConflictRule
from ..refresh import RefreshPlan

# Conflict-rule dropdown, default first (mirrors the CSV wizard's ordering).
_RULE_OPTIONS: list[tuple[str, ConflictRule]] = [
    ("Newest updated wins", ConflictRule.NEWEST_WINS),
    ("Jira API wins", ConflictRule.API_WINS),
    ("CSV wins", ConflictRule.CSV_WINS),
    ("Ask me per conflict", ConflictRule.ASK),
]


class RefreshDialog(QDialog):
    """Collect a :class:`RefreshPlan`: source + apply mode + options."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        current_jql: str = "",
        saved_queries: dict[str, str] | None = None,
        api_available: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Refresh dataset")
        self.resize(560, 500)
        self._current_jql = current_jql
        # name -> resolved JQL for each saved view the caller offers.
        self._saved_queries = dict(saved_queries or {})
        self._api_available = api_available
        self._csv_path = ""

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "Re-fetch or re-import your issues and preview what changed before "
            "replacing the current dataset."))
        v.addWidget(self._source_box())
        v.addWidget(self._apply_box())
        v.addWidget(self._options_box())
        v.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText("Preview changes…")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

        self._sync()

    # ---- source ----
    def _source_box(self) -> QGroupBox:
        box = QGroupBox("Refresh from")
        bv = QVBoxLayout(box)

        self.rb_api_current = QRadioButton("Re-run a Jira query")
        self.rb_csv = QRadioButton("Import a newer CSV export…")
        self.rb_jql = QRadioButton("Paste / edit JQL and run it")

        # API: re-run the current builder query or any saved view's query.
        bv.addWidget(self.rb_api_current)
        qrow = QHBoxLayout()
        qrow.addWidget(QLabel("Query:"))
        self.cmb_query = QComboBox()
        self.cmb_query.addItem("Current query builder", self._current_jql)
        for name in self._saved_queries:
            self.cmb_query.addItem(f"Saved view: {name}", self._saved_queries[name])
        self.cmb_query.currentIndexChanged.connect(self._on_query_selected)
        qrow.addWidget(self.cmb_query, 1)
        qw = QWidget()
        qw.setLayout(qrow)
        bv.addWidget(qw)

        # CSV file chooser row.
        bv.addWidget(self.rb_csv)
        csvrow = QHBoxLayout()
        self.btn_choose_csv = QPushButton("Choose CSV file…")
        self.btn_choose_csv.clicked.connect(self._choose_csv)
        self.lbl_csv = QLabel("No file chosen.")
        self.lbl_csv.setStyleSheet("color: palette(mid);")
        csvrow.addWidget(self.btn_choose_csv)
        csvrow.addWidget(self.lbl_csv, 1)
        cw = QWidget()
        cw.setLayout(csvrow)
        bv.addWidget(cw)

        # JQL box: editable in paste mode, a read-only preview of the selected
        # query otherwise (prefilled with the current query for convenience).
        bv.addWidget(self.rb_jql)
        self.ed_jql = QPlainTextEdit()
        self.ed_jql.setPlaceholderText(
            "project = ABC AND assignee = currentUser() ORDER BY updated DESC")
        self.ed_jql.setPlainText(self._current_jql)
        self.ed_jql.setMaximumHeight(72)
        bv.addWidget(self.ed_jql)

        self.rb_api_current.setChecked(self._api_available)
        if not self._api_available:
            self.rb_api_current.setEnabled(False)
            self.rb_jql.setEnabled(False)
            self.cmb_query.setEnabled(False)
            self.rb_csv.setChecked(True)
        for rb in (self.rb_api_current, self.rb_csv, self.rb_jql):
            rb.toggled.connect(self._sync)
        return box

    def _on_query_selected(self, *_: object) -> None:
        # Mirror the chosen saved/current query in the read-only preview box.
        if self.rb_api_current.isChecked():
            self.ed_jql.setPlainText(self.cmb_query.currentData() or "")

    # ---- apply mode ----
    def _apply_box(self) -> QGroupBox:
        box = QGroupBox("Apply the result by")
        bv = QVBoxLayout(box)
        self.rb_replace = QRadioButton("Replace the current dataset")
        self.rb_merge = QRadioButton("Merge into the current dataset")
        self.rb_replace.setChecked(True)
        self.rb_merge.toggled.connect(self._sync)
        bv.addWidget(self.rb_replace)
        bv.addWidget(self.rb_merge)

        rulerow = QHBoxLayout()
        rulerow.addWidget(QLabel("On conflict:"))
        self.cmb_rule = QComboBox()
        for label, value in _RULE_OPTIONS:
            self.cmb_rule.addItem(label, value)
        rulerow.addWidget(self.cmb_rule)
        rulerow.addStretch()
        rr = QWidget()
        rr.setLayout(rulerow)
        bv.addWidget(rr)
        return box

    # ---- options ----
    def _options_box(self) -> QGroupBox:
        box = QGroupBox("Options")
        bv = QVBoxLayout(box)
        self.cb_preserve = QCheckBox(
            "Keep local annotations for issues no longer present")
        self.cb_preserve.setChecked(True)
        self.cb_preserve.setToolTip(
            "Local notes/tags are stored separately and always survive a merge. "
            "On a replace, unchecking this also deletes annotations for issues "
            "that disappear from the dataset.")
        bv.addWidget(self.cb_preserve)
        return box

    # ---- interactions ----
    def _choose_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose CSV", "", "CSV files (*.csv);;All files (*)")
        if path:
            self._csv_path = path
            self.lbl_csv.setText(Path(path).name)

    def _sync(self, *_: object) -> None:
        is_csv = self.rb_csv.isChecked()
        is_jql = self.rb_jql.isChecked()
        is_api = self.rb_api_current.isChecked()
        self.btn_choose_csv.setEnabled(is_csv)
        self.lbl_csv.setEnabled(is_csv)
        self.cmb_query.setEnabled(is_api)
        # JQL is editable only in paste mode; shown read-only as a preview of the
        # selected saved/current query otherwise, and irrelevant for CSV.
        self.ed_jql.setEnabled(not is_csv)
        self.ed_jql.setReadOnly(not is_jql)
        if is_api:
            self.ed_jql.setPlainText(self.cmb_query.currentData() or "")
        self.cmb_rule.setEnabled(self.rb_merge.isChecked())

    def _on_accept(self) -> None:
        if self.rb_csv.isChecked() and not self._csv_path:
            QMessageBox.warning(self, "No file", "Choose a CSV file to import first.")
            return
        if not self.rb_csv.isChecked() and not self._effective_jql():
            QMessageBox.warning(self, "No JQL", "Choose or enter a JQL query to run first.")
            return
        self.accept()

    # ---- result ----
    def source(self) -> str:
        if self.rb_csv.isChecked():
            return "csv"
        if self.rb_jql.isChecked():
            return "api_jql"
        return "api_current"

    def _effective_jql(self) -> str:
        """The JQL that will run: the pasted text, or the selected saved/current query."""
        if self.rb_jql.isChecked():
            return self.ed_jql.toPlainText().strip()
        return (self.cmb_query.currentData() or "").strip()

    @property
    def apply_mode(self) -> str:
        return "merge" if self.rb_merge.isChecked() else "replace"

    def conflict_rule(self) -> ConflictRule:
        data = self.cmb_rule.currentData()
        return data if isinstance(data, ConflictRule) else ConflictRule.NEWEST_WINS

    def plan(self) -> RefreshPlan:
        """The collected plan the caller executes."""
        return RefreshPlan(
            source=self.source(),
            jql=self._effective_jql(),
            csv_path=self._csv_path,
            apply_mode=self.apply_mode,
            rule=self.conflict_rule(),
            preserve_annotations=self.cb_preserve.isChecked(),
        )
