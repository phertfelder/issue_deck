"""The local-only CSV import wizard (UI over :mod:`issue_deck.csv_import`).

Six stacked stages mirror the service pipeline exactly — the wizard is pure
presentation and never parses/infers anything itself:

1. Privacy   — local-only notice + "redact issue keys" toggle.
2. File      — choose/drop a CSV (or paste); shows delimiter, encoding, counts.
3. Mapping   — per-column target dropdowns, pre-filled by auto-detection.
4. Filters   — inferred column types, coverage, cardinality, examples; pin some.
5. Preview   — normalized issues, filter chips, group-by counts, warnings.
6. Commit    — replace vs merge (with conflict rule + delta), save opt-ins.

On accept the caller reads :meth:`build_source`, :attr:`apply_mode`,
:meth:`conflict_rule`, and the ``save_*`` flags; the wizard itself touches
neither the store nor disk, and never retains raw rows beyond the transient
:class:`~issue_deck.csv_import.ParsedCsv`.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..csv_import import (
    CANONICAL_FIELDS,
    GROUP_BY_FIELDS,
    ImportOptions,
    ParsedCsv,
    build_preview,
    build_profile,
    parse_csv,
    profile_columns,
    recommend_filters,
)
from ..datasource import CsvDataSource
from ..merge import ConflictRule, build_delta
from ..schema import CsvImportProfile, FieldMapping, NormalizedIssue
from .merge_dialog import DeltaDialog
from .workers import CsvParseWorker

# Target dropdown options: (label, target, transform). "" target == ignore.
_IGNORE = ("— ignore —", "", "")
_TARGET_OPTIONS: list[tuple[str, str, str]] = [_IGNORE] + [
    (f.label, f.target, f.transform) for f in CANONICAL_FIELDS
]
_TRANSFORM_BY_TARGET = {f.target: f.transform for f in CANONICAL_FIELDS}

# Conflict-rule dropdown, default first.
_RULE_OPTIONS: list[tuple[str, ConflictRule]] = [
    ("Newest updated wins", ConflictRule.NEWEST_WINS),
    ("Jira API wins", ConflictRule.API_WINS),
    ("CSV wins", ConflictRule.CSV_WINS),
    ("Ask me per conflict", ConflictRule.ASK),
]


class CsvImportWizard(QDialog):
    """Multi-stage dialog producing a :class:`CsvDataSource` plus an apply plan."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        current_issues: list[NormalizedIssue] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import CSV")
        self.resize(820, 620)
        self.setAcceptDrops(True)

        self._current_issues = current_issues or []
        self.parsed: ParsedCsv | None = None
        self.profile: CsvImportProfile | None = None
        self.options = ImportOptions()

        self._stack = QStackedWidget()
        self._build_pages()

        self.lbl_step = QLabel()
        self.btn_back = QPushButton("Back")
        self.btn_next = QPushButton("Next")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_back.clicked.connect(self._go_back)
        self.btn_next.clicked.connect(self._go_next)
        self.btn_cancel.clicked.connect(self.reject)

        nav = QHBoxLayout()
        nav.addWidget(self.lbl_step)
        nav.addStretch()
        nav.addWidget(self.btn_cancel)
        nav.addWidget(self.btn_back)
        nav.addWidget(self.btn_next)

        outer = QVBoxLayout(self)
        outer.addWidget(self._stack, 1)
        navw = QWidget()
        navw.setLayout(nav)
        outer.addWidget(navw)
        self._update_nav()

    # ================= page construction =================
    def _build_pages(self) -> None:
        self._stack.addWidget(self._page_privacy())
        self._stack.addWidget(self._page_file())
        self._stack.addWidget(self._page_mapping())
        self._stack.addWidget(self._page_filters())
        self._stack.addWidget(self._page_preview())
        self._stack.addWidget(self._page_commit())

    def _page_privacy(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>Local-only CSV import</h3>"))
        note = QLabel(
            "This import happens entirely on your machine.\n\n"
            "• The CSV file is read locally — nothing is sent to Jira or any server.\n"
            "• Raw CSV rows are never saved. Only normalized issues (and, if you "
            "opt in, a schema-only import profile) are kept.\n"
            "• The uploaded file itself is not persisted."
        )
        note.setWordWrap(True)
        v.addWidget(note)
        self.cb_redact = QCheckBox("Redact issue keys in preview and export")
        v.addWidget(self.cb_redact)
        v.addStretch()
        return w

    def _page_file(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>Choose a CSV file</h3>"))

        row = QHBoxLayout()
        self.btn_browse = QPushButton("Choose file…")
        self.btn_browse.clicked.connect(self._browse)
        row.addWidget(self.btn_browse)
        row.addWidget(QLabel("or drop a .csv onto this window."))
        row.addStretch()
        rw = QWidget()
        rw.setLayout(row)
        v.addWidget(rw)

        v.addWidget(QLabel("…or paste CSV content:"))
        self.ed_paste = QPlainTextEdit()
        self.ed_paste.setPlaceholderText("key,summary,status\nPROJ-1,Example,Open")
        self.ed_paste.setMaximumHeight(120)
        v.addWidget(self.ed_paste)
        self.btn_use_paste = QPushButton("Use pasted content")
        self.btn_use_paste.clicked.connect(self._use_paste)
        v.addWidget(self.btn_use_paste)

        self.lbl_stats = QLabel("No file loaded.")
        self.lbl_stats.setStyleSheet("color: palette(mid);")
        v.addWidget(self.lbl_stats)
        v.addStretch()
        return w

    def _page_mapping(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>Map columns to fields</h3>"))
        self.tbl_map = QTableWidget(0, 3)
        self.tbl_map.setHorizontalHeaderLabels(["CSV column", "Maps to", "Detected type"])
        self.tbl_map.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.tbl_map.horizontalHeader()
        if h is not None:
            h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tbl_map)
        return w

    def _page_filters(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>Suggested filters</h3>"))
        v.addWidget(QLabel("Pin the columns you want available as filters."))
        self.tbl_filters = QTableWidget(0, 6)
        self.tbl_filters.setHorizontalHeaderLabels(
            ["Pin", "Column", "Type", "Coverage", "Unique", "Examples"]
        )
        self.tbl_filters.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.tbl_filters.horizontalHeader()
        if h is not None:
            h.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tbl_filters)
        return w

    def _page_preview(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>Preview</h3>"))
        self.lbl_chips = QLabel()
        self.lbl_chips.setWordWrap(True)
        v.addWidget(self.lbl_chips)

        self.tbl_preview = QTableWidget(0, 5)
        self.tbl_preview.setHorizontalHeaderLabels(
            ["Key", "Summary", "Status", "Assignee", "Points"]
        )
        self.tbl_preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.tbl_preview.horizontalHeader()
        if h is not None:
            h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tbl_preview, 1)

        v.addWidget(QLabel("Group-by preview:"))
        self.lst_groupby = QListWidget()
        self.lst_groupby.setMaximumHeight(110)
        v.addWidget(self.lst_groupby)

        v.addWidget(QLabel("Warnings:"))
        self.lst_warnings = QListWidget()
        self.lst_warnings.setMaximumHeight(110)
        v.addWidget(self.lst_warnings)
        return w

    def _page_commit(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>Add to dataset</h3>"))

        box = QGroupBox("How should these issues be added?")
        bv = QVBoxLayout(box)
        self.rb_replace = QRadioButton("Replace the current dataset")
        self.rb_merge = QRadioButton("Merge into the current dataset")
        self.rb_replace.setChecked(True)
        bv.addWidget(self.rb_replace)
        bv.addWidget(self.rb_merge)

        rulerow = QHBoxLayout()
        rulerow.addWidget(QLabel("On conflict:"))
        self.cmb_rule = QComboBox()
        for label, value in _RULE_OPTIONS:
            self.cmb_rule.addItem(label, value)
        rulerow.addWidget(self.cmb_rule)
        self.btn_delta = QPushButton("Preview changes…")
        self.btn_delta.clicked.connect(self._show_delta)
        rulerow.addWidget(self.btn_delta)
        rulerow.addStretch()
        rr = QWidget()
        rr.setLayout(rulerow)
        bv.addWidget(rr)
        v.addWidget(box)

        self.rb_merge.toggled.connect(self._sync_commit_controls)

        savebox = QGroupBox("Save (optional — schema/normalized data only)")
        sv = QVBoxLayout(savebox)
        self.cb_save_profile = QCheckBox("Save the import profile (columns + mappings)")
        self.cb_save_dataset = QCheckBox("Save the normalized dataset")
        sv.addWidget(self.cb_save_profile)
        sv.addWidget(self.cb_save_dataset)
        v.addWidget(savebox)
        v.addStretch()

        # No current data -> merge is meaningless; steer to replace.
        if not self._current_issues:
            self.rb_merge.setEnabled(False)
        self._sync_commit_controls()
        return w

    # ================= navigation =================
    def _go_back(self) -> None:
        idx = self._stack.currentIndex()
        if idx > 0:
            self._stack.setCurrentIndex(idx - 1)
            self._update_nav()

    def _go_next(self) -> None:
        idx = self._stack.currentIndex()
        if idx == self._stack.count() - 1:
            self._finish()
            return
        if not self._leave_page(idx):
            return
        self._stack.setCurrentIndex(idx + 1)
        self._enter_page(self._stack.currentIndex())
        self._update_nav()

    def _leave_page(self, idx: int) -> bool:
        """Validate/commit state when leaving page ``idx``. Return False to block."""
        if idx == 0:
            self.options.redact_keys = self.cb_redact.isChecked()
        elif idx == 1:
            if self.parsed is None:
                QMessageBox.warning(self, "No CSV", "Choose, drop, or paste a CSV first.")
                return False
        elif idx == 2:
            self._rebuild_profile_from_mapping()
        return True

    def _enter_page(self, idx: int) -> None:
        if idx == 2:
            self._populate_mapping()
        elif idx == 3:
            self._populate_filters()
        elif idx == 4:
            self._populate_preview()

    def _update_nav(self) -> None:
        idx = self._stack.currentIndex()
        last = self._stack.count() - 1
        self.lbl_step.setText(f"Step {idx + 1} of {last + 1}")
        self.btn_back.setEnabled(idx > 0)
        self.btn_next.setText("Finish" if idx == last else "Next")

    # ================= stage 2: file loading =================
    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose CSV", "", "CSV files (*.csv);;All files (*)")
        if path:
            self._load_file(path)

    def _use_paste(self) -> None:
        text = self.ed_paste.toPlainText()
        if not text.strip():
            QMessageBox.warning(self, "Empty", "Paste some CSV content first.")
            return
        # Pasted content is small, so parse it inline.
        self._load(lambda t: parse_csv(t, source_file_name="pasted.csv"), text)

    def _load(self, loader, arg) -> None:
        try:
            parsed = loader(arg)
        except Exception as e:  # noqa: BLE001 - surface any parse/read failure to the user
            QMessageBox.critical(self, "Could not read CSV", str(e))
            return
        self._accept_parsed(parsed)

    def _load_file(self, path: str) -> None:
        """Parse a file off the UI thread with a cancelable progress dialog."""
        dlg = QProgressDialog("Parsing CSV…", "Cancel", 0, 0, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        thread = QThread(self)
        worker = CsvParseWorker(path)
        worker.moveToThread(thread)

        def on_finished(parsed: ParsedCsv) -> None:
            dlg.reset()
            self._accept_parsed(parsed)

        def on_failed(msg: str) -> None:
            dlg.reset()
            QMessageBox.critical(self, "Could not read CSV", msg)

        thread.started.connect(worker.run)
        worker.finished.connect(on_finished)
        worker.failed.connect(on_failed)
        worker.cancelled.connect(dlg.reset)
        for sig in (worker.finished, worker.failed, worker.cancelled):
            sig.connect(thread.quit)
        dlg.canceled.connect(worker.cancel)
        # Keep references alive until the thread finishes.
        self._parse_thread, self._parse_worker = thread, worker
        thread.start()
        dlg.exec()

    def _accept_parsed(self, parsed: ParsedCsv) -> None:
        if not parsed.columns:
            QMessageBox.warning(self, "Empty CSV", "That file has no header row.")
            return
        self.parsed = parsed
        self.profile = build_profile(parsed)
        delim = {",": "comma", ";": "semicolon"}.get(parsed.delimiter, repr(parsed.delimiter))
        self.lbl_stats.setText(
            f"{parsed.source_file_name or 'CSV'} — {parsed.row_count} rows × "
            f"{parsed.column_count} columns · {delim}-delimited · {parsed.encoding}"
        )

    # ---- drag & drop ----
    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:  # noqa: N802 (Qt override)
        if event is None:
            return
        md = event.mimeData()
        if md is not None and md.hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent | None) -> None:  # noqa: N802 (Qt override)
        md = event.mimeData() if event is not None else None
        if md is None or not md.hasUrls():
            return
        path = md.urls()[0].toLocalFile()
        if path:
            self._stack.setCurrentIndex(1)
            self._update_nav()
            self._load_file(path)

    # ================= stage 3: mapping =================
    def _populate_mapping(self) -> None:
        assert self.parsed is not None and self.profile is not None
        current = {m.source: m.target for m in self.profile.mappings}
        cols = self.parsed.columns
        self.tbl_map.setRowCount(len(cols))
        profiles = profile_columns(self.parsed, self.profile)
        col_types = {p.name: p.column_type.value for p in profiles}
        for row, col in enumerate(cols):
            self.tbl_map.setItem(row, 0, QTableWidgetItem(col))
            combo = QComboBox()
            for label, target, _ in _TARGET_OPTIONS:
                combo.addItem(label, target)
            want = current.get(col, "")
            combo.setCurrentIndex(max(0, next(
                (i for i, (_, t, _) in enumerate(_TARGET_OPTIONS) if t == want), 0)))
            self.tbl_map.setCellWidget(row, 1, combo)
            self.tbl_map.setItem(row, 2, QTableWidgetItem(col_types.get(col, "")))

    def _rebuild_profile_from_mapping(self) -> None:
        assert self.parsed is not None
        mappings: list[FieldMapping] = []
        for row in range(self.tbl_map.rowCount()):
            source_item = self.tbl_map.item(row, 0)
            combo = self.tbl_map.cellWidget(row, 1)
            if source_item is None or not isinstance(combo, QComboBox):
                continue
            target = combo.currentData()
            if target:
                mappings.append(FieldMapping(
                    source=source_item.text(),
                    target=target,
                    transform=_TRANSFORM_BY_TARGET.get(target, ""),
                ))
        name = self.profile.name if self.profile else ""
        self.profile = build_profile(self.parsed, mappings, name=name)

    # ================= stage 4: filters =================
    def _populate_filters(self) -> None:
        assert self.parsed is not None and self.profile is not None
        profiles = profile_columns(self.parsed, self.profile)
        recommended = {r.column for r in recommend_filters(profiles)}
        self.tbl_filters.setRowCount(len(profiles))
        self._pin_boxes: dict[str, QCheckBox] = {}
        for row, p in enumerate(profiles):
            cb = QCheckBox()
            cb.setChecked(p.name in recommended)  # pre-pin recommendations
            self._pin_boxes[p.name] = cb
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(6, 0, 0, 0)
            hl.addWidget(cb)
            hl.addStretch()
            self.tbl_filters.setCellWidget(row, 0, holder)
            self.tbl_filters.setItem(row, 1, QTableWidgetItem(p.name))
            self.tbl_filters.setItem(row, 2, QTableWidgetItem(p.column_type.value))
            self.tbl_filters.setItem(row, 3, QTableWidgetItem(f"{p.coverage:.0%}"))
            self.tbl_filters.setItem(row, 4, QTableWidgetItem(str(p.unique_count)))
            self.tbl_filters.setItem(row, 5, QTableWidgetItem(", ".join(p.examples)))

    def _pinned_filters(self) -> list[str]:
        return [name for name, cb in getattr(self, "_pin_boxes", {}).items() if cb.isChecked()]

    # ================= stage 5: preview =================
    def _populate_preview(self) -> None:
        assert self.parsed is not None and self.profile is not None
        self.options.redact_keys = self.cb_redact.isChecked()
        group_fields = list(GROUP_BY_FIELDS) + ["auto"]
        preview = build_preview(
            self.parsed, self.profile, self.options,
            group_by_fields=group_fields, pinned_filters=self._pinned_filters(),
        )
        self.lbl_chips.setText(
            "Filters: " + (" · ".join(preview.filter_chips) if preview.filter_chips else "none")
        )

        self.tbl_preview.setRowCount(len(preview.issues))
        for row, n in enumerate(preview.issues):
            pts = "" if n.story_points is None else str(n.story_points)
            values = [n.key, n.summary, n.status, n.assignee.name, pts]
            for col, val in enumerate(values):
                self.tbl_preview.setItem(row, col, QTableWidgetItem(str(val)))

        self.lst_groupby.clear()
        for name, gb in preview.group_by.items():
            top = ", ".join(f"{k}={v}" for k, v in list(gb.groups.items())[:6])
            self.lst_groupby.addItem(f"{name}: {gb.group_count} group(s) — {top}")

        self.lst_warnings.clear()
        for warning in preview.warnings:
            self.lst_warnings.addItem("⚠ " + warning)
        if not preview.warnings:
            self.lst_warnings.addItem("No warnings.")

    # ================= stage 6: commit =================
    def _sync_commit_controls(self) -> None:
        merging = self.rb_merge.isChecked()
        self.cmb_rule.setEnabled(merging)
        self.btn_delta.setEnabled(bool(self._current_issues))

    def _show_delta(self) -> None:
        """Preview what applying this import will do to the current dataset."""
        source = self.build_source()
        incoming = source.load().issues
        delta = build_delta(self._current_issues, incoming)
        merging = self.rb_merge.isChecked()
        dlg = DeltaDialog(
            delta, self,
            title="Preview import changes",
            allow_conflict_rule=merging,
            rule=self.conflict_rule(),
        )
        if dlg.exec() and merging:
            # Reflect the rule chosen inside the delta dialog back onto the combo.
            self._set_rule(dlg.selected_rule())

    def _set_rule(self, rule: ConflictRule) -> None:
        idx = self.cmb_rule.findData(rule)
        if idx >= 0:
            self.cmb_rule.setCurrentIndex(idx)

    def _finish(self) -> None:
        if self.parsed is None or self.profile is None:
            QMessageBox.warning(self, "Nothing to import", "Load a CSV first.")
            return
        self.options.redact_keys = self.cb_redact.isChecked()
        self.accept()

    # ================= results (read by the caller) =================
    @property
    def apply_mode(self) -> str:
        return "merge" if self.rb_merge.isChecked() else "replace"

    def conflict_rule(self) -> ConflictRule:
        data = self.cmb_rule.currentData()
        return data if isinstance(data, ConflictRule) else ConflictRule.NEWEST_WINS

    @property
    def save_profile_requested(self) -> bool:
        return self.cb_save_profile.isChecked()

    @property
    def save_dataset_requested(self) -> bool:
        return self.cb_save_dataset.isChecked()

    def build_source(self) -> CsvDataSource:
        """The configured CSV data source (parsed + profile + options)."""
        assert self.parsed is not None and self.profile is not None
        self.options.redact_keys = self.cb_redact.isChecked()
        return CsvDataSource(self.parsed, self.profile, self.options)
