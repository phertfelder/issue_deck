"""Query & Results tab — the Jira workbench.

Left/top: a filter builder (scope toggles, project/sprint/fixVersion pickers,
status-category and status/type lists, date bounds, pinned field filters, and a
raw-JQL escape hatch) plus saved views and a pre-fetch estimate. Bottom: an
interactive results table with a detail side panel, quick filter, column toggles,
and export. All fetched/imported issues flow through the in-memory store, so the
data-source indicator and exporters stay source-agnostic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..annotations import ANNOTATION_TAGS, AnnotationStore
from ..comments import CommentsMode, CommentsOptions
from ..config import AppConfig
from ..constants import COMMON_ISSUE_TYPES, COMMON_STATUSES, STATUS_CATEGORIES
from ..csv_import import build_profile, read_csv_file
from ..datasource import CsvDataSource, DataSourceInfo, DataSourceKind
from ..exporters import (
    ExportConfig,
    ExportContext,
    export_csv,
    export_jsonl,
    export_markdown_combined,
    export_markdown_per_ticket,
    prepare_issues,
    render_combined,
    render_note_block,
    render_per_ticket,
    write_export_pack,
    write_prompt_pack,
)
from ..jira_client import JiraClient
from ..jql_helper import JqlTemplateStore, decompose, explain
from ..merge import build_delta
from ..models import RESULT_COLUMNS, FieldFilter, SavedView, SearchFilters
from ..progress import FetchProgress, Phase
from ..query import default_filters, estimate_query
from ..refresh import RefreshPlan, removed_keys, validate_incoming
from ..schema import IssueCollection, NormalizedIssue
from ..services import issue_service, value_source_service
from ..services.capability_service import Capabilities, fetch_capabilities
from ..store import InMemoryIssueStore
from ..views import SavedViewStore
from .csv_wizard import CsvImportWizard
from .detail_panel import IssueDetailPanel
from .empty_state import EmptyState
from .jql_helper_dialog import JqlHelperDialog
from .merge_dialog import DeltaDialog
from .query_builder import CollapsibleSection, SegmentedToggle
from .refresh_dialog import RefreshDialog
from .results_table import ResultsTable
from .theme import HOME_PILL_OBJECT, RAW_BANNER_OBJECT
from .value_discovery_dialog import ValueDiscoveryDialog
from .workers import ExportWorker, FetchWorker, ValidateJqlWorker

_FIELD_OPS = ["~", "=", "!=", ">=", "<=", "in"]


class QueryTab(QWidget):
    # Emitted whenever the working dataset changes (fetch/import/merge/clear), so
    # dependents like the analytics dashboard can recompute. See _refresh_dataset_views.
    datasetChanged = pyqtSignal()

    def __init__(self, cfg: AppConfig,
                 config_provider: Callable[[], AppConfig],
                 client_provider: Callable[[], JiraClient]):
        super().__init__()
        self.cfg = cfg
        self._config_provider = config_provider
        self._client_provider = client_provider
        self.store = InMemoryIssueStore()
        self.views = SavedViewStore()
        self.jql_templates = JqlTemplateStore()
        self.annotations = AnnotationStore()
        # Optimistic until a connection probe says otherwise (see on_connected).
        self._capabilities = Capabilities()
        self._last_jql = ""
        self._last_warnings: list = []
        self._fetch_thread: QThread | None = None
        self._fetch_worker: FetchWorker | None = None
        self._export_thread: QThread | None = None
        self._export_worker: ExportWorker | None = None
        self._export_buttons: list[QPushButton] = []
        self._build()
        # First-run smart default: assigned to me, unresolved, updated last 90 days.
        self._apply_filters(default_filters())
        self._reload_views_combo()

    @property
    def issues(self) -> list[NormalizedIssue]:
        """The current working dataset (read-only view of the store)."""
        return self.store.issues

    # ================= public entry points (Home command center) =================
    def run_filters(self, filters: SearchFilters) -> None:
        """Load ``filters`` into the builder and fetch — the preset-run path.

        Thin composition of the existing apply + fetch steps so the Home page can
        launch a preset without reaching into private methods. Fetch keeps its own
        guards (connection check, broad-query warning, typed-error reporting)."""
        self._apply_filters(filters)
        self._fetch()

    def enter_raw_mode(self) -> None:
        """Switch the builder into raw-JQL mode and focus the JQL editor."""
        self.cb_raw.setChecked(True)
        self._toggle_raw(True)
        self.ed_raw.setFocus()

    def reveal_issue(self, key: str) -> bool:
        """Select the loaded issue ``key`` in the results table (command palette)."""
        return self.table.select_key(key)

    # ================= build =================
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(split)

        split.addWidget(self._build_query_panel())
        split.addWidget(self._build_results_panel())
        split.setSizes([340, 520])

    def _build_query_panel(self) -> QWidget:
        filt = QWidget()
        root = QVBoxLayout(filt)

        # --- saved views bar ---
        root.addWidget(self._build_views_bar())

        # --- header: title + Guided/Raw toggle ---
        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Build your query</b>"))
        header.addStretch(1)
        self.mode_toggle = SegmentedToggle(["Guided", "Raw"])
        self.mode_toggle.changed.connect(self._on_mode_changed)
        header.addWidget(self.mode_toggle)
        root.addLayout(header)

        # Backing raw-mode state: kept (but hidden) so _filters/_apply_filters and
        # the existing round-trip tests are unchanged; the toggle drives it.
        self.cb_raw = QCheckBox("Raw JQL advanced mode (overrides the builder)")
        self.cb_raw.setVisible(False)
        self.cb_raw.toggled.connect(self._toggle_raw)
        root.addWidget(self.cb_raw)

        # --- guided body ⇄ raw body ---
        self._body_stack = QStackedWidget()
        self._body_stack.addWidget(self._build_guided_body())
        self._body_stack.addWidget(self._build_raw_body())
        root.addWidget(self._body_stack)

        # --- shared: fetch options, actions, advanced drawer ---
        root.addWidget(self._build_fetch_options())
        root.addWidget(self._build_actions())
        root.addWidget(self._build_advanced_drawer())
        root.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(filt)
        return scroll

    def _build_guided_body(self) -> QWidget:
        """The structured filter controls (hidden while in Raw mode)."""
        body = QWidget()
        fl = QGridLayout(body)
        fl.setContentsMargins(0, 0, 0, 0)
        r = 0

        # --- scope toggles ---
        scope = QHBoxLayout()
        self.cb_assigned = QCheckBox("Assigned to me")
        self.cb_reported = QCheckBox("Reported by me")
        self.cb_watched = QCheckBox("Watched by me")
        self.cb_watched.setToolTip(
            "Filters to issues you watch (watcher = currentUser()). "
            "Disabled on instances where watcher search is turned off.")
        for cb in (self.cb_assigned, self.cb_reported, self.cb_watched):
            cb.toggled.connect(self._refresh_estimate)
            scope.addWidget(cb)
        scope.addStretch()
        sw = QWidget()
        sw.setLayout(scope)
        fl.addWidget(sw, r, 0, 1, 3)
        r += 1

        # --- project / sprint / fix version ---
        psv = QHBoxLayout()
        psv.addWidget(QLabel("Projects:"))
        self.ed_projects = QLineEdit()
        self.ed_projects.setPlaceholderText("ABC, DEF (comma-separated)")
        psv.addWidget(self.ed_projects, 2)
        psv.addWidget(QLabel("Sprint:"))
        self.ed_sprint = QLineEdit()
        psv.addWidget(self.ed_sprint, 1)
        psv.addWidget(QLabel("Fix version:"))
        self.ed_fix_version = QLineEdit()
        psv.addWidget(self.ed_fix_version, 1)
        pw = QWidget()
        pw.setLayout(psv)
        fl.addWidget(pw, r, 0, 1, 3)
        r += 1

        # --- status category ---
        catrow = QHBoxLayout()
        catrow.addWidget(QLabel("Status category:"))
        self._cat_boxes: dict[str, QCheckBox] = {}
        for cat in STATUS_CATEGORIES:
            cb = QCheckBox(cat)
            cb.toggled.connect(self._refresh_estimate)
            self._cat_boxes[cat] = cb
            catrow.addWidget(cb)
        catrow.addStretch()
        cw = QWidget()
        cw.setLayout(catrow)
        fl.addWidget(cw, r, 0, 1, 3)
        r += 1

        # --- status + type lists, and the right-hand column ---
        fl.addWidget(QLabel("Status:"), r, 0)
        fl.addWidget(QLabel("Issue type:"), r, 1)
        r += 1
        self.lst_status = self._multi_list(COMMON_STATUSES)
        self.lst_type = self._multi_list(COMMON_ISSUE_TYPES)
        self.lst_status.itemSelectionChanged.connect(self._refresh_estimate)
        self.lst_type.itemSelectionChanged.connect(self._refresh_estimate)
        fl.addWidget(self.lst_status, r, 0)
        fl.addWidget(self.lst_type, r, 1)
        fl.addWidget(self._build_right_column(), r, 2)
        r += 1

        # --- pinned field filters ---
        fl.addWidget(self._build_field_filters(), r, 0, 1, 3)
        return body

    def _build_raw_body(self) -> QWidget:
        """The raw-JQL editor with a warm warning banner + live validation."""
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        banner = QLabel(
            "⚠ Raw JQL runs verbatim — structured filters are ignored and values "
            "are not escaped for you. Validate before fetching.")
        banner.setObjectName(RAW_BANNER_OBJECT)
        banner.setWordWrap(True)
        v.addWidget(banner)

        self.ed_raw = QPlainTextEdit()
        self.ed_raw.setPlaceholderText(
            "project = ABC AND assignee = currentUser() ORDER BY updated DESC")
        self.ed_raw.setMaximumHeight(90)
        self.ed_raw.setEnabled(False)
        self.ed_raw.textChanged.connect(self._refresh_estimate)
        v.addWidget(self.ed_raw)

        row = QHBoxLayout()
        self.btn_validate = QPushButton("Validate against Jira")
        self.btn_validate.setToolTip(
            "Run this JQL with maxResults=1 to check it against your instance.")
        self.btn_validate.clicked.connect(self._validate_raw)
        row.addWidget(self.btn_validate)
        self.lbl_validate = QLabel("")
        self.lbl_validate.setStyleSheet("color: palette(mid);")
        self.lbl_validate.setWordWrap(True)
        row.addWidget(self.lbl_validate, 1)
        v.addLayout(row)
        return body

    def _build_actions(self) -> QWidget:
        actions = QHBoxLayout()
        self.btn_jql_helper = QPushButton("JQL helper…")
        self.btn_jql_helper.setToolTip(
            "Build JQL from templates or your filters, toggle clauses, read it "
            "in plain English, and validate it against Jira.")
        self.btn_jql_helper.clicked.connect(self._open_jql_helper)
        actions.addWidget(self.btn_jql_helper)
        self.btn_preview = QPushButton("Preview JQL")
        self.btn_preview.clicked.connect(self._preview)
        self.btn_fetch = QPushButton("Fetch")
        self.btn_fetch.clicked.connect(self._fetch)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._cancel_fetch)
        self.btn_cancel.setEnabled(False)
        actions.addStretch()
        actions.addWidget(self.btn_preview)
        actions.addWidget(self.btn_fetch)
        actions.addWidget(self.btn_cancel)
        aw = QWidget()
        aw.setLayout(actions)
        return aw

    def _build_advanced_drawer(self) -> QWidget:
        """Collapsed 'Advanced — generated JQL' drawer: JQL + reading + pill."""
        drawer = CollapsibleSection("Advanced — generated JQL · preview only")
        self.jql_view = QPlainTextEdit()
        self.jql_view.setReadOnly(True)
        self.jql_view.setMaximumHeight(52)
        drawer.addWidget(self.jql_view)
        self.lbl_explain = QLabel("")
        self.lbl_explain.setWordWrap(True)
        drawer.addWidget(self.lbl_explain)
        self.lbl_fields = QLabel("")
        self.lbl_fields.setStyleSheet("color: palette(mid);")
        self.lbl_fields.setWordWrap(True)
        drawer.addWidget(self.lbl_fields)
        self.lbl_breadth = QLabel("Broad query")
        self.lbl_breadth.setObjectName(HOME_PILL_OBJECT)  # reuse the warm pill look
        self.lbl_breadth.setVisible(False)
        drawer.addWidget(self.lbl_breadth)
        self.lbl_warn = QLabel("")
        self.lbl_warn.setStyleSheet("color: #c0392b;")
        self.lbl_warn.setWordWrap(True)
        drawer.addWidget(self.lbl_warn)
        return drawer

    def _build_views_bar(self) -> QWidget:
        row = QHBoxLayout()
        row.addWidget(QLabel("Saved views:"))
        self.cmb_views = QComboBox()
        self.cmb_views.setMinimumWidth(160)
        row.addWidget(self.cmb_views)
        for label, slot in [
            ("Load", self._view_load), ("Save…", self._view_save),
            ("Duplicate…", self._view_duplicate), ("Rename…", self._view_rename),
            ("Delete", self._view_delete),
        ]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        w = QWidget()
        w.setLayout(row)
        return w

    def _build_right_column(self) -> QWidget:
        g = QGridLayout()
        g.addWidget(QLabel("Severity:"), 0, 0)
        self.ed_severity = QLineEdit()
        self.ed_severity.setPlaceholderText("e.g. High / S1")
        g.addWidget(self.ed_severity, 0, 1)
        g.addWidget(QLabel("Client contains:"), 1, 0)
        self.ed_client = QLineEdit()
        g.addWidget(self.ed_client, 1, 1)
        g.addWidget(QLabel("Text search:"), 2, 0)
        self.ed_text = QLineEdit()
        self.ed_text.setPlaceholderText("summary/desc/comments")
        g.addWidget(self.ed_text, 2, 1)

        self.sp_updated = self._spin()
        self.sp_created = self._spin()
        self.sp_resolved = self._spin()
        self.sp_due = self._spin()
        self.sp_commented = self._spin()
        g.addWidget(QLabel("Updated within (days):"), 3, 0)
        g.addWidget(self.sp_updated, 3, 1)
        g.addWidget(QLabel("Created within (days):"), 4, 0)
        g.addWidget(self.sp_created, 4, 1)
        g.addWidget(QLabel("Resolved within (days):"), 5, 0)
        g.addWidget(self.sp_resolved, 5, 1)
        g.addWidget(QLabel("Due within (days):"), 6, 0)
        g.addWidget(self.sp_due, 6, 1)
        g.addWidget(QLabel("Commented within (days):"), 7, 0)
        g.addWidget(self.sp_commented, 7, 1)
        self.cb_unresolved = QCheckBox("Unresolved only")
        g.addWidget(self.cb_unresolved, 8, 0, 1, 2)
        self.ed_extra = QLineEdit()
        self.ed_extra.setPlaceholderText("Extra JQL (optional)")
        g.addWidget(QLabel("Extra JQL:"), 9, 0)
        g.addWidget(self.ed_extra, 9, 1)
        w = QWidget()
        w.setLayout(g)
        return w

    def _build_field_filters(self) -> QWidget:
        box = QGroupBox("Pinned field filters")
        v = QVBoxLayout(box)
        self.tbl_fields = QTableWidget(0, 3)
        self.tbl_fields.setHorizontalHeaderLabels(["Field (or cf[id])", "Op", "Value"])
        self.tbl_fields.setMaximumHeight(120)
        h = self.tbl_fields.horizontalHeader()
        if h is not None:
            h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tbl_fields)
        row = QHBoxLayout()
        btn_add = QPushButton("Add filter")
        btn_add.clicked.connect(lambda: self._add_field_filter_row())
        btn_del = QPushButton("Remove selected")
        btn_del.clicked.connect(self._remove_field_filter_row)
        btn_discover = QPushButton("Discover values…")
        btn_discover.setToolTip(
            "Explore real field values from the current dataset or a Jira sample, "
            "then pin filters from them.")
        btn_discover.clicked.connect(self._discover_values)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addWidget(btn_discover)
        row.addStretch()
        rw = QWidget()
        rw.setLayout(row)
        v.addWidget(rw)
        return box

    def _build_fetch_options(self) -> QWidget:
        box = QGroupBox("Fetch options")
        g = QGridLayout(box)
        g.addWidget(QLabel("Comments:"), 0, 0)
        self.cmb_comments = QComboBox()
        self.cmb_comments.addItem("All comments", CommentsMode.ALL)
        self.cmb_comments.addItem("No comments", CommentsMode.NONE)
        self.cmb_comments.addItem("Latest N", CommentsMode.LATEST)
        self.cmb_comments.addItem("Since date", CommentsMode.SINCE)
        self.cmb_comments.currentIndexChanged.connect(self._sync_comments_widgets)
        g.addWidget(self.cmb_comments, 0, 1)
        self.lbl_latest = QLabel("N:")
        g.addWidget(self.lbl_latest, 0, 2)
        self.sp_latest = QSpinBox()
        self.sp_latest.setRange(1, 999)
        self.sp_latest.setValue(5)
        g.addWidget(self.sp_latest, 0, 3)
        self.lbl_since = QLabel("Since:")
        g.addWidget(self.lbl_since, 0, 4)
        self.ed_since = QLineEdit()
        self.ed_since.setPlaceholderText("YYYY-MM-DD")
        g.addWidget(self.ed_since, 0, 5)

        g.addWidget(QLabel("Max issues (0 = all):"), 1, 0)
        self.sp_max = QSpinBox()
        self.sp_max.setRange(0, 1_000_000)
        self.sp_max.setValue(0)
        g.addWidget(self.sp_max, 1, 1)
        self.cb_fail_comments = QCheckBox("Fail run if comments error")
        g.addWidget(self.cb_fail_comments, 1, 2, 1, 4)
        self.apply_fetch_defaults()
        self._sync_comments_widgets()
        return box

    def apply_fetch_defaults(self) -> None:
        """Initialise the fetch-option widgets from the persisted config defaults.

        Public so the main window can re-apply them after the Settings dialog
        changes them.
        """
        c = self.cfg
        try:
            mode = CommentsMode(c.comments_mode) if c.comments_mode else None
        except ValueError:
            mode = None
        if mode is not None:
            self._set_comments_mode(mode)
        self.sp_latest.setValue(c.comments_latest_n or 5)
        self.ed_since.setText(c.comments_since or "")
        self.sp_max.setValue(c.max_issues or 0)

    def refresh_saved_views(self) -> None:
        """Reload the saved-views picker (e.g. after Settings deletes some)."""
        self._reload_views_combo()

    # ---- export destination helpers (persist the last-used folder) ----
    def _initial_save_path(self, filename: str) -> str:
        base = self.cfg.default_export_folder
        return str(Path(base) / filename) if base else filename

    def _remember_export_dir(self, path: str) -> None:
        """Persist the folder of the last successful export as the new default."""
        p = Path(path)
        folder = str(p if p.is_dir() else p.parent)
        if folder and folder != self.cfg.default_export_folder:
            self.cfg.default_export_folder = folder
            try:
                self.cfg.save()
            except OSError:
                pass

    def _sync_comments_widgets(self, *_: object) -> None:
        mode = self.cmb_comments.currentData()
        is_latest = mode == CommentsMode.LATEST
        is_since = mode == CommentsMode.SINCE
        self.lbl_latest.setVisible(is_latest)
        self.sp_latest.setVisible(is_latest)
        self.lbl_since.setVisible(is_since)
        self.ed_since.setVisible(is_since)

    def _comments_options(self) -> CommentsOptions:
        return CommentsOptions(
            mode=self.cmb_comments.currentData(),
            latest_n=self.sp_latest.value(),
            since=self.ed_since.text().strip(),
        )

    def _set_comments_mode(self, mode: CommentsMode) -> None:
        idx = self.cmb_comments.findData(mode)
        if idx >= 0:
            self.cmb_comments.setCurrentIndex(idx)

    def _build_results_panel(self) -> QWidget:
        res = QWidget()
        rl = QVBoxLayout(res)

        # toolbar: quick filter + columns + source + import/clear
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Quick filter:"))
        self.ed_quick = QLineEdit()
        self.ed_quick.setPlaceholderText("filter fetched rows…")
        self.ed_quick.setAccessibleName("Quick filter")
        self.ed_quick.textChanged.connect(self._quick_filter)
        bar.addWidget(self.ed_quick, 2)
        bar.addWidget(QLabel("Local tag:"))
        self.cmb_tag = QComboBox()
        self.cmb_tag.addItem("(all)", "")
        for tag in ANNOTATION_TAGS:
            self.cmb_tag.addItem(tag, tag)
        self.cmb_tag.currentIndexChanged.connect(self._populate_table)
        bar.addWidget(self.cmb_tag)
        self.btn_columns = QPushButton("Columns ▾")
        self.btn_columns.clicked.connect(self._show_columns_menu)
        bar.addWidget(self.btn_columns)
        self.cb_compact = QCheckBox("Compact")
        self.cb_compact.setToolTip("Tighter rows for fast triage of large result sets.")
        self.cb_compact.toggled.connect(lambda on: self.table.set_compact(on))
        bar.addWidget(self.cb_compact)
        self.lbl_source = QLabel(self.store.describe_source())
        self.lbl_source.setStyleSheet("color: palette(mid);")
        bar.addWidget(self.lbl_source, 1)
        self.btn_import_csv = QPushButton("Import CSV…")
        self.btn_import_csv.clicked.connect(self._import_csv)
        bar.addWidget(self.btn_import_csv)
        self.btn_refresh = QPushButton("Refresh…")
        self.btn_refresh.setToolTip(
            "Re-fetch or re-import your issues and preview exactly what changed "
            "before replacing the current dataset.")
        self.btn_refresh.clicked.connect(self._open_refresh)
        bar.addWidget(self.btn_refresh)
        self.btn_clear = QPushButton("Clear dataset")
        self.btn_clear.clicked.connect(self._clear_dataset)
        bar.addWidget(self.btn_clear)
        bw = QWidget()
        bw.setLayout(bar)
        rl.addWidget(bw)

        # table + detail panel
        self.table = ResultsTable()
        self.table.setAccessibleName("Results table")
        self.detail = IssueDetailPanel(self.annotations, self._panel_field_names)
        self.detail.annotationChanged.connect(self._on_annotation_changed)
        self.table.issueSelected.connect(self.detail.show_issue)
        hsplit = QSplitter(Qt.Orientation.Horizontal)
        hsplit.addWidget(self.table)
        hsplit.addWidget(self.detail)
        hsplit.setSizes([720, 380])
        # A neutral empty state stands in for the table when the dataset is empty.
        self._empty_state = EmptyState("Import CSV…", self._import_csv)
        self._results_stack = QStackedWidget()
        self._results_stack.addWidget(self._empty_state)   # index 0
        self._results_stack.addWidget(hsplit)              # index 1
        rl.addWidget(self._results_stack, 1)

        # exports
        exp = QHBoxLayout()
        self.lbl_count = QLabel("0 issues")
        exp.addWidget(self.lbl_count)
        exp.addStretch()
        for label, slot in [
            ("Export Markdown (combined)", self._export_md_combined),
            ("Export Markdown (per-ticket)", self._export_md_folder),
            ("Export JSONL", self._export_jsonl),
            ("Export CSV", self._export_csv),
        ]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            exp.addWidget(b)
            self._export_buttons.append(b)
        self.btn_export_pack = QPushButton("Export…")
        self.btn_export_pack.setToolTip(
            "Configure an LLM-ready export: comments, redaction, grouping, "
            "ZIP export pack, or a prompt pack.")
        self.btn_export_pack.clicked.connect(self._open_export_dialog)
        exp.addWidget(self.btn_export_pack)
        self._export_buttons.append(self.btn_export_pack)
        ew = QWidget()
        ew.setLayout(exp)
        rl.addWidget(ew)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        rl.addWidget(self.progress)

        # fetch warnings (comment failures / capped results)
        self.warn_row = QWidget()
        wl = QHBoxLayout(self.warn_row)
        wl.setContentsMargins(0, 0, 0, 0)
        self.lbl_fetch_warn = QLabel("")
        self.lbl_fetch_warn.setStyleSheet("color: #c0392b;")
        self.lbl_fetch_warn.setWordWrap(True)
        wl.addWidget(self.lbl_fetch_warn, 1)
        self.btn_warnings = QPushButton("Details…")
        self.btn_warnings.clicked.connect(self._show_warnings)
        wl.addWidget(self.btn_warnings)
        self.btn_export_warnings = QPushButton("Export…")
        self.btn_export_warnings.clicked.connect(self._export_warnings)
        wl.addWidget(self.btn_export_warnings)
        self.warn_row.setVisible(False)
        rl.addWidget(self.warn_row)

        self.status = QLabel("")
        rl.addWidget(self.status)
        self._update_empty_state()   # show the neutral empty state on first launch
        return res

    # ---- small widget factories ----
    @staticmethod
    def _multi_list(items: list[str]) -> QListWidget:
        lst = QListWidget()
        lst.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        lst.setMaximumHeight(120)
        for it in items:
            lst.addItem(QListWidgetItem(it))
        return lst

    @staticmethod
    def _spin() -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(0, 3650)
        sp.setValue(0)
        return sp

    # ================= filters <-> widgets =================
    def _field_filters(self) -> list[FieldFilter]:
        out: list[FieldFilter] = []
        for row in range(self.tbl_fields.rowCount()):
            field_item = self.tbl_fields.item(row, 0)
            op_widget = self.tbl_fields.cellWidget(row, 1)
            value_item = self.tbl_fields.item(row, 2)
            field = field_item.text().strip() if field_item else ""
            value = value_item.text().strip() if value_item else ""
            op = op_widget.currentText() if isinstance(op_widget, QComboBox) else "~"
            if field and value:
                out.append(FieldFilter(field=field, op=op, value=value))
        return out

    def _filters(self) -> SearchFilters:
        return SearchFilters(
            statuses=[i.text() for i in self.lst_status.selectedItems()],
            issue_types=[i.text() for i in self.lst_type.selectedItems()],
            severity=self.ed_severity.text().strip(),
            client=self.ed_client.text().strip(),
            text=self.ed_text.text().strip(),
            updated_days=self.sp_updated.value(),
            commented_days=self.sp_commented.value(),
            extra=self.ed_extra.text(),
            load_comments=self.cmb_comments.currentData() != CommentsMode.NONE,
            assigned_to_me=self.cb_assigned.isChecked(),
            reported_by_me=self.cb_reported.isChecked(),
            # Never emit a watcher clause when the instance doesn't support it,
            # even if a loaded saved view had it checked (isEnabled gates it).
            watched_by_me=self.cb_watched.isChecked() and self.cb_watched.isEnabled(),
            projects=[p.strip() for p in self.ed_projects.text().split(",") if p.strip()],
            sprint=self.ed_sprint.text().strip(),
            fix_version=self.ed_fix_version.text().strip(),
            status_categories=[c for c, cb in self._cat_boxes.items() if cb.isChecked()],
            created_days=self.sp_created.value(),
            resolved_days=self.sp_resolved.value(),
            due_days=self.sp_due.value(),
            unresolved=self.cb_unresolved.isChecked(),
            field_filters=self._field_filters(),
            raw_mode=self.cb_raw.isChecked(),
            raw_jql=self.ed_raw.toPlainText(),
        )

    def _apply_filters(self, f: SearchFilters) -> None:
        self.cb_assigned.setChecked(f.assigned_to_me)
        self.cb_reported.setChecked(f.reported_by_me)
        # Honor a view's watched flag only where the instance supports it.
        self.cb_watched.setChecked(f.watched_by_me and self.cb_watched.isEnabled())
        self.ed_projects.setText(", ".join(f.projects))
        self.ed_sprint.setText(f.sprint)
        self.ed_fix_version.setText(f.fix_version)
        for cat, cb in self._cat_boxes.items():
            cb.setChecked(cat in f.status_categories)
        self._select_list(self.lst_status, f.statuses)
        self._select_list(self.lst_type, f.issue_types)
        self.ed_severity.setText(f.severity)
        self.ed_client.setText(f.client)
        self.ed_text.setText(f.text)
        self.sp_updated.setValue(f.updated_days)
        self.sp_created.setValue(f.created_days)
        self.sp_resolved.setValue(f.resolved_days)
        self.sp_due.setValue(f.due_days)
        self.sp_commented.setValue(f.commented_days)
        self.cb_unresolved.setChecked(f.unresolved)
        self.ed_extra.setText(f.extra)
        # Map the persisted load_comments flag onto the comments-mode combo,
        # preserving a specific mode (Latest/Since) when comments stay enabled.
        if not f.load_comments:
            self._set_comments_mode(CommentsMode.NONE)
        elif self.cmb_comments.currentData() == CommentsMode.NONE:
            self._set_comments_mode(CommentsMode.ALL)
        self.cb_raw.setChecked(f.raw_mode)
        self.ed_raw.setPlainText(f.raw_jql)
        self._set_field_filters(f.field_filters)

    @staticmethod
    def _select_list(lst: QListWidget, wanted: list[str]) -> None:
        want = {w.lower() for w in wanted}
        for i in range(lst.count()):
            item = lst.item(i)
            if item is not None:
                item.setSelected(item.text().lower() in want)

    # ---- pinned field-filter table ----
    def _add_field_filter_row(self, ff: FieldFilter | None = None) -> None:
        row = self.tbl_fields.rowCount()
        self.tbl_fields.insertRow(row)
        self.tbl_fields.setItem(row, 0, QTableWidgetItem(ff.field if ff else ""))
        combo = QComboBox()
        combo.addItems(_FIELD_OPS)
        if ff and ff.op in _FIELD_OPS:
            combo.setCurrentText(ff.op)
        self.tbl_fields.setCellWidget(row, 1, combo)
        self.tbl_fields.setItem(row, 2, QTableWidgetItem(ff.value if ff else ""))

    def _remove_field_filter_row(self) -> None:
        row = self.tbl_fields.currentRow()
        if row >= 0:
            self.tbl_fields.removeRow(row)

    def _set_field_filters(self, filters: list[FieldFilter]) -> None:
        self.tbl_fields.setRowCount(0)
        for ff in filters:
            self._add_field_filter_row(ff)

    def _toggle_raw(self, on: bool) -> None:
        """Sync all raw-mode UI from the backing cb_raw state (single sink)."""
        self.ed_raw.setEnabled(on)
        self._body_stack.setCurrentIndex(1 if on else 0)
        self.mode_toggle.set_index(1 if on else 0)  # emit-free, avoids a loop
        self._refresh_estimate()

    def _on_mode_changed(self, index: int) -> None:
        """Guided/Raw toggle → drive the backing cb_raw (which syncs the rest)."""
        self.cb_raw.setChecked(index == 1)

    def _validate_raw(self) -> None:
        """Validate the raw JQL against Jira off-thread (maxResults=1)."""
        jql = self.ed_raw.toPlainText().strip()
        if not jql:
            self.lbl_validate.setText("Enter JQL to validate.")
            return
        try:
            client = self._client_provider()
        except Exception as e:  # noqa: BLE001 - surfaced to the user verbatim
            QMessageBox.critical(self, "Error", str(e))
            return
        self.lbl_validate.setText("Validating…")
        self.btn_validate.setEnabled(False)
        self._validate_thread = QThread()
        self._validate_worker = ValidateJqlWorker(client, self.cfg, jql)
        self._validate_worker.moveToThread(self._validate_thread)
        self._validate_thread.started.connect(self._validate_worker.run)
        self._validate_worker.finished.connect(self._on_validated)
        self._validate_worker.failed.connect(
            lambda m: self.lbl_validate.setText(m))
        self._validate_worker.finished.connect(self._validate_thread.quit)
        self._validate_worker.failed.connect(self._validate_thread.quit)
        self._validate_thread.finished.connect(
            lambda: self.btn_validate.setEnabled(True))
        self._validate_thread.start()

    def _on_validated(self, validation) -> None:
        self.lbl_validate.setText(validation.message)

    # ---- value discovery ----
    def _discover_values(self) -> None:
        """Open the discovery dialog and pin any filters the user builds."""
        try:
            cfg = self._config_provider()
        except Exception:
            cfg = self.cfg
        dlg = ValueDiscoveryDialog(
            self, cfg=cfg, current_issues=list(self.store.issues),
            client_provider=self._client_provider)
        if dlg.exec():
            for ff in dlg.pinned_filters():
                self._add_field_filter_row(ff)

    # ================= capabilities =================
    def on_connected(self, client) -> None:
        """Probe the connected instance and gate capability-dependent controls.

        Wired to :attr:`ConnectionTab.connected`. Best-effort: a failed probe
        leaves the optimistic defaults untouched so we never disable a feature
        that might work."""
        if client is None:
            return
        try:
            self._apply_capabilities(fetch_capabilities(client))
        except Exception:  # noqa: BLE001 - probing is best-effort, never fatal
            pass
        self._hydrate_option_lists(client)

    def _hydrate_option_lists(self, client) -> None:
        """Augment the status/type pick-lists with the instance's real values.

        Best-effort and additive: discovered values are merged into the built-in
        defaults (never replacing them), and existing selections are preserved.
        A failed probe simply leaves the defaults untouched."""
        try:
            statuses = [o.value for o in value_source_service.status_options(client)]
        except Exception:  # noqa: BLE001
            statuses = []
        try:
            types = [o.value for o in value_source_service.issue_type_options(client)]
        except Exception:  # noqa: BLE001
            types = []
        self._merge_list_items(self.lst_status, statuses)
        self._merge_list_items(self.lst_type, types)

    @staticmethod
    def _merge_list_items(lst: QListWidget, values: list[str]) -> None:
        existing = {
            item.text().lower()
            for i in range(lst.count())
            if (item := lst.item(i)) is not None
        }
        for v in values:
            if v and v.lower() not in existing:
                lst.addItem(QListWidgetItem(v))
                existing.add(v.lower())

    def _apply_capabilities(self, caps: Capabilities) -> None:
        self._capabilities = caps
        supported = caps.watcher_search
        self.cb_watched.setEnabled(supported)
        if supported:
            self.cb_watched.setToolTip(
                "Filters to issues you watch (watcher = currentUser()).")
        else:
            self.cb_watched.setChecked(False)
            self.cb_watched.setToolTip(
                "Watcher search is turned off on this Jira instance, so "
                "“Watched by me” isn't available here.")

    # ================= JQL helper =================
    def _open_jql_helper(self) -> None:
        """Open the deterministic JQL helper; adopt any JQL the user sends back.

        Sending JQL back flips the builder into raw mode with that JQL, so the
        next Fetch uses it verbatim and it rides along in export packs (via
        ``_last_jql``)."""
        try:
            cfg = self._config_provider()
        except Exception:  # noqa: BLE001 - fall back to the last-known config
            cfg = self.cfg
        dlg = JqlHelperDialog(
            self, cfg=cfg, filters=self._filters(),
            client_provider=self._client_provider, template_store=self.jql_templates)
        if not dlg.exec():
            return
        jql = dlg.result_jql()
        if not jql:
            return
        self.cb_raw.setChecked(True)
        self.ed_raw.setPlainText(jql)
        self._last_jql = jql
        self._preview()
        self.status.setText("Adopted JQL from the helper (raw mode).")

    # ================= estimate / preview =================
    def _apply_estimate(self, cfg: AppConfig, filters: SearchFilters):
        """Render the Advanced drawer (JQL, plain-English reading, fields, pill).

        The single place that populates the estimate widgets, shared by live
        refresh, the Preview button, and Fetch. Returns the estimate."""
        est = estimate_query(cfg, filters)
        self.jql_view.setPlainText(est.jql)
        self.lbl_fields.setText("Fields: " + ", ".join(est.fields))
        self.lbl_warn.setText("\n".join("⚠ " + w for w in est.warnings))
        self.lbl_explain.setText(explain(decompose(cfg, filters)))
        self.lbl_breadth.setVisible(bool(est.warnings))
        return est

    def _refresh_estimate(self, *_: object) -> None:
        """Live-update the Advanced drawer as guided/raw inputs change."""
        try:
            cfg = self._config_provider()
        except Exception:  # noqa: BLE001 - fall back to the last-known config
            cfg = self.cfg
        self._apply_estimate(cfg, self._filters())

    def _preview(self) -> None:
        self._refresh_estimate()

    # ================= fetch =================
    def _fetch(self) -> None:
        try:
            client = self._client_provider()   # syncs connection cfg + validates
            filters = self._filters()
            est = self._apply_estimate(self.cfg, filters)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        # Warn before a broad, unbounded search.
        if est.warnings:
            proceed = QMessageBox.question(
                self, "Broad search",
                "\n".join(est.warnings) + "\n\nRun this search anyway?",
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        self._pending_commented_days = filters.commented_days
        self._last_jql = est.jql
        self._run_fetch_worker(client, est.jql, self._on_fetched)

    def _run_fetch_worker(self, client, jql: str, on_finished) -> None:
        """Wire up an off-thread :class:`FetchWorker`, routing to ``on_finished``.

        Shared by the plain Fetch and the Refresh path so both get identical
        progress/cancel/error handling; only the finished-handler differs.
        """
        self.warn_row.setVisible(False)
        self.btn_fetch.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress.show()
        self._fetch_thread = QThread()
        self._fetch_worker = FetchWorker(
            client, jql, self.cfg,
            comments=self._comments_options(),
            max_issues=self.sp_max.value() or None,
            fail_on_comment_error=self.cb_fail_comments.isChecked(),
        )
        self._fetch_worker.moveToThread(self._fetch_thread)
        self._fetch_thread.started.connect(self._fetch_worker.run)
        self._fetch_worker.progress.connect(self._on_progress)
        self._fetch_worker.finished.connect(on_finished)
        self._fetch_worker.failed.connect(self._on_failed)
        self._fetch_worker.cancelled.connect(self._on_cancelled)
        self._fetch_worker.finished.connect(self._fetch_thread.quit)
        self._fetch_worker.failed.connect(self._fetch_thread.quit)
        self._fetch_worker.cancelled.connect(self._fetch_thread.quit)
        self._fetch_thread.start()

    def _cancel_fetch(self) -> None:
        if self._fetch_worker is not None:
            self._fetch_worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.status.setText("Cancelling…")

    def _on_progress(self, p: FetchProgress) -> None:
        self.status.setText(p.describe())
        if p.phase == Phase.SEARCHING and p.total:
            self.progress.setRange(0, p.total)
            self.progress.setValue(p.fetched)
        else:
            self.progress.setRange(0, 0)   # indeterminate for comments/retries

    def _reset_fetch_buttons(self) -> None:
        self.progress.hide()
        self.btn_fetch.setEnabled(True)
        self.btn_cancel.setEnabled(False)

    def _on_failed(self, exc: Exception) -> None:
        self._reset_fetch_buttons()
        from .error_dialog import show_error
        show_error(self, exc, operation="fetch")

    def _on_cancelled(self) -> None:
        self._reset_fetch_buttons()
        self.status.setText("Fetch cancelled.")

    def _on_fetched(self, result) -> None:
        self._reset_fetch_buttons()
        issues = issue_service.filter_commented_within(
            result.issues, getattr(self, "_pending_commented_days", 0))
        info = DataSourceInfo(
            kind=DataSourceKind.JIRA_API,
            label="Jira API live search",
            detail=self._last_jql,
            deployment=self.cfg.deployment,
        )
        self.store.replace(IssueCollection(issues=issues), info)
        self._refresh_dataset_views()
        self._last_warnings = list(result.warnings)
        self._render_fetch_outcome(result, len(issues))

    def _render_fetch_outcome(self, result, shown: int) -> None:
        """Status line + warnings row for a completed fetch."""
        n = len(result.warnings)
        parts = [f"Done. {shown} issues."]
        if result.cap_warning:
            parts.append("⚠ results capped")
        if n:
            parts.append(f"⚠ {n} comment warning(s)")
        self.status.setText("  ".join(parts))

        bits = []
        if result.cap_warning:
            bits.append(result.cap_warning)
        if n:
            bits.append(f"{n} issue(s) had comment problems.")
        self.lbl_fetch_warn.setText("  ".join(bits))
        self.btn_warnings.setVisible(bool(n))
        self.btn_export_warnings.setVisible(bool(n))
        self.warn_row.setVisible(bool(bits))
        if result.truncated:
            QMessageBox.warning(self, "Results capped", result.cap_warning)

    def _warnings_text(self) -> str:
        return "\n".join(f"{w.key}: {w.message}" for w in self._last_warnings)

    def _show_warnings(self) -> None:
        QMessageBox.information(
            self, "Comment load warnings", self._warnings_text() or "No warnings.")

    def _export_warnings(self) -> None:
        if not self._last_warnings:
            return
        p, _ = QFileDialog.getSaveFileName(
            self, "Export warnings", "fetch_warnings.txt", "Text (*.txt)")
        if p:
            Path(p).write_text(self._warnings_text(), encoding="utf-8")
            QMessageBox.information(self, "Exported", p)

    def _clear_dataset(self) -> None:
        self.store.clear()
        self._refresh_dataset_views()
        self.status.setText("Dataset cleared.")

    # ================= refresh (re-run source + delta preview) =================
    def _open_refresh(self) -> None:
        """Refresh the working dataset from a chosen source, previewing the delta.

        A refresh is only meaningful against an existing dataset; the destructive
        replace/merge never runs until the user confirms the delta preview.
        """
        if self.store.is_empty():
            QMessageBox.information(
                self, "Nothing to refresh",
                "Fetch or import a dataset first, then use Refresh to see what "
                "changed before replacing it.")
            return
        try:
            cfg = self._config_provider()
        except Exception:  # noqa: BLE001 - fall back to the last-known config
            cfg = self.cfg
        current_jql = estimate_query(cfg, self._filters()).jql
        dlg = RefreshDialog(
            self, current_jql=current_jql, saved_queries=self._saved_query_jql(cfg))
        if not dlg.exec():
            return
        plan = dlg.plan()
        if plan.source == "csv":
            self._refresh_via_csv(plan)
        else:
            self._refresh_via_api(plan.jql, plan)

    def _saved_query_jql(self, cfg: AppConfig) -> dict[str, str]:
        """Resolve each saved view to its JQL, so refresh can re-run any of them."""
        out: dict[str, str] = {}
        for name in sorted(self.views.names()):
            view = self.views.get(name)
            if view is None:
                continue
            try:
                out[name] = estimate_query(cfg, view.filters).jql
            except Exception:  # noqa: BLE001 - a broken saved view shouldn't block refresh
                continue
        return out

    def _refresh_via_csv(self, plan: RefreshPlan) -> None:
        """Parse the chosen CSV (auto-mapped) and stage a refresh from it."""
        try:
            parsed = read_csv_file(plan.csv_path)
            source = CsvDataSource(parsed, build_profile(parsed))
            incoming = source.load().issues
        except Exception as e:  # noqa: BLE001 - surface any read/parse failure
            QMessageBox.critical(self, "Could not read CSV", str(e))
            return
        self._stage_refresh(incoming, source.describe(), plan, is_csv=True)

    def _refresh_via_api(self, jql: str, plan: RefreshPlan) -> None:
        """Re-run a Jira search off-thread, then stage a refresh from the result."""
        try:
            client = self._client_provider()   # syncs connection cfg + validates
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Error", str(e))
            return
        self._last_jql = jql
        self._pending_commented_days = self._filters().commented_days

        def on_finished(result) -> None:
            self._reset_fetch_buttons()
            issues = issue_service.filter_commented_within(
                result.issues, self._pending_commented_days)
            self._last_warnings = list(result.warnings)
            self._render_fetch_outcome(result, len(issues))
            info = DataSourceInfo(
                kind=DataSourceKind.JIRA_API,
                label="Jira API live search",
                detail=jql,
                deployment=self.cfg.deployment,
            )
            self._stage_refresh(issues, info, plan)

        self._run_fetch_worker(client, jql, on_finished)

    def _stage_refresh(
        self, incoming: list[NormalizedIssue], info: DataSourceInfo,
        plan: RefreshPlan, *, is_csv: bool = False,
    ) -> None:
        """Validate → preview delta → confirm → replace/merge. No blind replace."""
        validation = validate_incoming(incoming, is_csv=is_csv)
        if not validation.ok:
            QMessageBox.critical(self, "Can't refresh", "\n\n".join(validation.blocking))
            return

        delta = build_delta(self.store.issues, incoming)
        merging = plan.apply_mode == "merge"
        dlg = DeltaDialog(
            delta, self, title="Refresh preview",
            allow_conflict_rule=merging, rule=plan.rule,
        )
        # Acceptance criterion: no destructive replace without preview + confirm.
        if not dlg.exec():
            self.status.setText("Refresh cancelled — dataset unchanged.")
            return

        collection = IssueCollection(issues=incoming)
        if merging and not self.store.is_empty():
            result = self.store.merge(collection, dlg.selected_rule(), info)
            msg = (f"Refreshed (merge): +{result.added} new, "
                   f"{result.updated} updated, {result.conflicts} conflict(s).")
        else:
            self.store.replace(collection, info)
            msg = f"Refreshed (replace): {len(incoming)} issue(s) now loaded."
            if not plan.preserve_annotations:
                pruned = self._prune_annotations(removed_keys(delta))
                if pruned:
                    msg += f" Pruned {pruned} annotation(s) for removed issues."
        self._refresh_dataset_views()
        if validation.warnings:
            msg += "  ⚠ " + " ".join(validation.warnings)
        self.status.setText(msg)

    def _prune_annotations(self, keys: list[str]) -> int:
        """Delete local annotations for ``keys``; return how many were removed."""
        return sum(1 for key in keys if self.annotations.delete(key))

    # ================= CSV import =================
    def _import_csv(self) -> None:
        wizard = CsvImportWizard(self, current_issues=list(self.store.issues))
        if not wizard.exec():
            return
        source = wizard.build_source()
        collection = source.load()
        info = source.describe()
        if wizard.apply_mode == "merge" and not self.store.is_empty():
            result = self.store.merge(collection, wizard.conflict_rule(), info)
            msg = (f"Merged CSV: +{result.added} new, "
                   f"{result.updated} updated, {result.conflicts} conflict(s).")
        else:
            self.store.replace(collection, info)
            msg = f"Imported {len(collection)} issue(s) from CSV."
        self._persist_csv_opt_ins(wizard, source)
        self._refresh_dataset_views()
        self.status.setText(msg)

    def _persist_csv_opt_ins(self, wizard: "CsvImportWizard", source) -> None:
        """Honor the wizard's save opt-ins (schema/normalized data only)."""
        if not (wizard.save_profile_requested or wizard.save_dataset_requested):
            return
        from .. import constants
        from ..csv_import import save_dataset, save_profile
        try:
            if wizard.save_profile_requested:
                save_profile(source.profile, constants.APP_DIR / "csv_profiles")
            if wizard.save_dataset_requested:
                save_dataset(self.store.collection(), constants.APP_DIR / "datasets",
                             name=source.profile.name)
        except Exception as e:  # noqa: BLE001 - saving is best-effort, never fatal
            QMessageBox.warning(self, "Save failed", str(e))

    def _refresh_dataset_views(self) -> None:
        """Re-render table, counts, and the data-source indicator from the store."""
        self._populate_table()
        self.lbl_source.setText(self.store.describe_source())
        self.datasetChanged.emit()

    def _visible_issues(self) -> list[NormalizedIssue]:
        """The dataset narrowed to the selected local tag (all issues if none)."""
        tag = self.cmb_tag.currentData()
        if not tag:
            return self.issues
        keys = self.annotations.keys_with_tag(tag)
        return [i for i in self.issues if i.key in keys]

    def _populate_table(self) -> None:
        """Fill the table from the tag-filtered dataset, honoring the quick filter."""
        self.table.populate(self._visible_issues())
        self.table.apply_quick_filter(self.ed_quick.text())
        self._update_count()
        self._update_empty_state()

    def _update_empty_state(self) -> None:
        """Show the neutral empty state (with a contextual reason) when there's no data."""
        if self.issues:
            self._results_stack.setCurrentIndex(1)   # table
            return
        if self._last_jql:
            self._empty_state.set_message(
                "No issues matched",
                "Nothing came back for this query. Widen your filters — remove a "
                "status or date bound — then Fetch again.")
        else:
            self._empty_state.set_message(
                "No issues yet",
                "Build a query above and hit Fetch, or import a CSV export to get started.")
        self._results_stack.setCurrentIndex(0)       # empty state

    def _update_count(self) -> None:
        total = len(self.issues)
        shown = sum(
            1 for r in range(self.table.rowCount()) if not self.table.isRowHidden(r))
        tag = self.cmb_tag.currentData()
        if tag:
            self.lbl_count.setText(f"{shown} of {total} issues (tag: {tag})")
        elif shown != total:
            self.lbl_count.setText(f"{shown} of {total} issues")
        else:
            self.lbl_count.setText(f"{total} issues")

    def _on_annotation_changed(self, _key: str) -> None:
        # A tag change can move an issue in/out of an active tag filter.
        if self.cmb_tag.currentData():
            self._populate_table()

    def _panel_field_names(self) -> dict[str, str]:
        """Custom-field id -> label map for the detail panel's custom-fields table."""
        return {
            fid: name for fid, name in (
                (self.cfg.client_field, "Client"),
                (self.cfg.severity_field, "Severity"),
            ) if fid
        }

    # ================= results interactions =================
    def _quick_filter(self, text: str) -> None:
        self.table.apply_quick_filter(text)
        self._update_count()

    def _show_columns_menu(self) -> None:
        menu = QMenu(self)
        visible = set(self.table.visible_columns())
        for col_id, label in RESULT_COLUMNS:
            act = QAction(label, menu)
            act.setCheckable(True)
            act.setChecked(col_id in visible)
            act.toggled.connect(lambda checked, cid=col_id: self._toggle_column(cid, checked))
            menu.addAction(act)
        menu.exec(self.btn_columns.mapToGlobal(self.btn_columns.rect().bottomLeft()))

    def _toggle_column(self, col_id: str, checked: bool) -> None:
        cols = self.table.visible_columns()
        if checked and col_id not in cols:
            # Preserve canonical order from RESULT_COLUMNS.
            order = [c for c, _ in RESULT_COLUMNS]
            cols = [c for c in order if c in set(cols) | {col_id}]
        elif not checked and col_id in cols:
            cols = [c for c in cols if c != col_id]
        self.table.set_columns(cols)
        self.table.apply_quick_filter(self.ed_quick.text())

    # ================= saved views =================
    def _reload_views_combo(self) -> None:
        self.cmb_views.clear()
        self.cmb_views.addItems(sorted(self.views.names()))

    def _current_sort(self) -> tuple[str, bool]:
        header = self.table.horizontalHeader()
        cols = self.table.visible_columns()
        if header is None or not cols:
            return "updated", True
        section = header.sortIndicatorSection()
        desc = header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
        col = cols[section] if 0 <= section < len(cols) else "updated"
        return col, desc

    def _view_save(self) -> None:
        default = self.cmb_views.currentText()
        name, ok = QInputDialog.getText(self, "Save view", "View name:", text=default)
        if not ok or not name.strip():
            return
        sort_col, sort_desc = self._current_sort()
        view = SavedView(
            name=name.strip(),
            filters=self._filters(),
            sort_column=sort_col,
            sort_desc=sort_desc,
            visible_columns=self.table.visible_columns(),
        )
        self.views.save(view)
        self._reload_views_combo()
        self.cmb_views.setCurrentText(name.strip())
        self.status.setText(f"Saved view '{name.strip()}'.")

    def _view_load(self) -> None:
        name = self.cmb_views.currentText()
        view = self.views.get(name)
        if view is None:
            return
        self._apply_filters(view.filters)
        self.table.set_columns(view.visible_columns)
        cols = self.table.visible_columns()
        if view.sort_column in cols:
            order = Qt.SortOrder.DescendingOrder if view.sort_desc else Qt.SortOrder.AscendingOrder
            self.table.sortItems(cols.index(view.sort_column), order)
        self._preview()
        self.status.setText(f"Loaded view '{name}'.")

    def _view_duplicate(self) -> None:
        name = self.cmb_views.currentText()
        if not name:
            return
        new, ok = QInputDialog.getText(self, "Duplicate view", "New name:", text=f"{name} copy")
        if not ok or not new.strip():
            return
        try:
            self.views.duplicate(name, new.strip())
        except ValueError as e:
            QMessageBox.warning(self, "Duplicate failed", str(e))
            return
        self._reload_views_combo()
        self.cmb_views.setCurrentText(new.strip())

    def _view_rename(self) -> None:
        name = self.cmb_views.currentText()
        if not name:
            return
        new, ok = QInputDialog.getText(self, "Rename view", "New name:", text=name)
        if not ok or not new.strip():
            return
        try:
            self.views.rename(name, new.strip())
        except ValueError as e:
            QMessageBox.warning(self, "Rename failed", str(e))
            return
        self._reload_views_combo()
        self.cmb_views.setCurrentText(new.strip())

    def _view_delete(self) -> None:
        name = self.cmb_views.currentText()
        if not name:
            return
        if QMessageBox.question(self, "Delete view", f"Delete saved view '{name}'?") \
                == QMessageBox.StandardButton.Yes:
            self.views.delete(name)
            self._reload_views_combo()

    # ================= exports =================
    def _guard(self) -> bool:
        if not self.issues:
            QMessageBox.warning(self, "Nothing to export", "Fetch some issues first.")
            return False
        return True

    def _export_md_combined(self) -> None:
        if not self._guard():
            return
        p, _ = QFileDialog.getSaveFileName(
            self, "Save Markdown", self._initial_save_path("jira_export.md"),
            "Markdown (*.md)")
        if p:
            issues = self.issues
            self._run_export(lambda: (export_markdown_combined(issues, p), p)[1], p)

    def _export_md_folder(self) -> None:
        if not self._guard():
            return
        d = QFileDialog.getExistingDirectory(self, "Choose folder", self.cfg.default_export_folder)
        if d:
            issues = self.issues
            self._run_export(
                lambda: (export_markdown_per_ticket(issues, d),
                         f"{len(issues)} files → {d}")[1], d)

    def _export_jsonl(self) -> None:
        if not self._guard():
            return
        p, _ = QFileDialog.getSaveFileName(
            self, "Save JSONL", self._initial_save_path("jira_export.jsonl"),
            "JSONL (*.jsonl)")
        if p:
            issues = self.issues
            self._run_export(lambda: (export_jsonl(issues, p), p)[1], p)

    def _export_csv(self) -> None:
        if not self._guard():
            return
        p, _ = QFileDialog.getSaveFileName(
            self, "Save CSV", self._initial_save_path("jira_export.csv"), "CSV (*.csv)")
        if p:
            issues = self.issues
            self._run_export(lambda: (export_csv(issues, p), p)[1], p)

    # ---- configurable export (packs + option-aware single files) ----
    def _build_export_context(self) -> ExportContext:
        """Assemble provenance for the current dataset (never any credentials)."""
        source_type = {
            "jira_api": "api", "csv": "csv", "mixed": "mixed", "empty": "empty",
        }.get(self.store.kind.value, "api")
        csv_name = next(
            (s.detail for s in reversed(self.store.session.sources)
             if s.kind == DataSourceKind.CSV), "")
        field_mapping = {
            fid: label for fid, label in (
                (self.cfg.client_field, "Client"),
                (self.cfg.severity_field, "Severity"),
            ) if fid
        }
        return ExportContext(
            source_type=source_type,
            deployment=self.cfg.deployment,
            base_url=self.cfg.base_url,
            jql=self._last_jql,
            csv_source_filename=csv_name,
            field_mapping=field_mapping,
            warnings=[f"{w.key}: {w.message}" for w in self._last_warnings],
        )

    def _open_export_dialog(self) -> None:
        if not self._guard():
            return
        from .export_dialog import ExportDialog

        dlg = ExportDialog(self, issues=self.issues, cfg=self.cfg)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        self._dispatch_export(dlg.mode, dlg.config())

    def _notes_map(self, config: ExportConfig) -> dict[str, str] | None:
        """Pre-rendered private-note blocks per issue key, or ``None``.

        Only produced when the export opts into local notes *and* keeps issue keys
        (redaction and private notes are mutually exclusive — see ExportConfig)."""
        if not config.normalized().include_local_notes:
            return None
        out: dict[str, str] = {}
        for issue in self.issues:
            block = render_note_block(self.annotations.get(issue.key))
            if block:
                out[issue.key] = block
        return out

    def _dispatch_export(self, mode: str, config: ExportConfig) -> None:
        try:
            if mode == "markdown_per_ticket":
                self._export_shaped_per_ticket(config)
            elif mode == "zip_pack":
                notes = self._notes_map(config)
                self._export_zip(
                    config,
                    lambda issues, cfg, ctx, p: write_export_pack(
                        issues, cfg, ctx, p, notes=notes),
                    "Save export pack", "jira_export_pack.zip")
            elif mode == "prompt_pack":
                self._export_zip(config, write_prompt_pack, "Save prompt pack",
                                 "jira_prompt_pack.zip")
            else:
                self._export_shaped_single(mode, config)
        except Exception as exc:  # surface a friendly error rather than crash the UI
            QMessageBox.critical(self, "Export failed", str(exc))

    def _export_shaped_per_ticket(self, config: ExportConfig) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose folder", self.cfg.default_export_folder)
        if not d:
            return
        issues, notes = self.issues, self._notes_map(config)

        def job() -> str:
            prepared = prepare_issues(issues, config)
            for name, body in render_per_ticket(prepared, config, notes=notes):
                (Path(d) / name).write_text(body, encoding="utf-8")
            return f"{len(prepared)} files → {d}"

        self._run_export(job, d)

    def _export_shaped_single(self, mode: str, config: ExportConfig) -> None:
        title, filename, filt = {
            "markdown_combined": ("Save Markdown", "jira_export.md", "Markdown (*.md)"),
            "jsonl": ("Save JSONL", "jira_export.jsonl", "JSONL (*.jsonl)"),
            "csv": ("Save CSV", "jira_export.csv", "CSV (*.csv)"),
        }[mode]
        p, _ = QFileDialog.getSaveFileName(self, title, self._initial_save_path(filename), filt)
        if not p:
            return
        issues, notes = self.issues, self._notes_map(config)

        def job() -> str:
            prepared = prepare_issues(issues, config)
            if mode == "markdown_combined":
                Path(p).write_text(
                    render_combined(prepared, config, notes=notes), encoding="utf-8")
            elif mode == "jsonl":
                export_jsonl(prepared, p)
            else:
                export_csv(prepared, p)
            return p

        self._run_export(job, p)

    def _export_zip(self, config: ExportConfig, writer, title: str, default: str) -> None:
        p, _ = QFileDialog.getSaveFileName(
            self, title, self._initial_save_path(default), "ZIP archive (*.zip)")
        if not p:
            return
        # Snapshot everything the writer needs on the UI thread; the write runs off-thread.
        issues, ctx = self.issues, self._build_export_context()
        self._run_export(lambda: (writer(issues, config, ctx, p), p)[1], p)

    # ---- off-thread export runner ----
    def _run_export(self, job, remember_path: str) -> None:
        """Run an export ``job`` (a thunk returning a message) off the UI thread."""
        if self._export_thread is not None and self._export_thread.isRunning():
            QMessageBox.information(self, "Export in progress", "An export is already running.")
            return
        self._remember_export_dir(remember_path)
        self._set_export_busy(True)
        self._export_thread = QThread()
        self._export_worker = ExportWorker(job)
        self._export_worker.moveToThread(self._export_thread)
        self._export_thread.started.connect(self._export_worker.run)
        self._export_worker.finished.connect(self._on_export_done)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.finished.connect(self._export_thread.quit)
        self._export_worker.failed.connect(self._export_thread.quit)
        self._export_thread.start()

    def _set_export_busy(self, busy: bool) -> None:
        for btn in self._export_buttons:
            btn.setEnabled(not busy)
        self.status.setText("Exporting…" if busy else "")

    def _on_export_done(self, message: str) -> None:
        self._set_export_busy(False)
        QMessageBox.information(self, "Exported", message)

    def _on_export_failed(self, message: str) -> None:
        self._set_export_busy(False)
        QMessageBox.critical(self, "Export failed", message)
