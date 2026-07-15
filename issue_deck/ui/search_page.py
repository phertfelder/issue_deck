"""Search — broad, all-Jira discovery (any project, client, or person).

Deliberately *not* a clone of My Work: where My Work is "your work", Search is
"search all of Jira". It offers searchable pickers (Project / Client / Assignee /
Reporter), a free-text box, STATUS / TYPE / TIME chips, a live plain-English
summary, and a **Search Jira** primary that runs through the same off-thread
:class:`~issue_deck.ui.workers.FetchWorker` and typed error presenter as the
workbench, into its own results table + shared detail panel.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..comments import CommentsMode, CommentsOptions
from ..config import AppConfig
from ..datasource import DataSourceInfo, DataSourceKind
from ..jira_client import JiraClient
from ..jql_helper import decompose, explain
from ..models import FieldFilter, SearchFilters
from ..progress import FetchProgress, Phase
from ..query import estimate_query
from ..schema import IssueCollection
from ..services import value_source_service
from ..store import InMemoryIssueStore
from .detail_panel import IssueDetailPanel
from .filter_bar import ChipRow
from .results_table import ResultsTable
from .theme import PRIMARY_ACTION_OBJECT, ROW_LABEL_OBJECT, SCOPE_SUMMARY_OBJECT
from .workers import FetchWorker

_STATUS_OPTS = [("To Do", "Open"), ("In Progress", "In progress"),
                ("Done", "Done")]
_TYPE_OPTS = [("Bug", "Bug"), ("Task", "Task"), ("Story", "Story"),
              ("Incident", "Incident"), ("Epic", "Epic")]
_TIME_OPTS = [("0", "Any time"), ("1", "Updated today"), ("7", "Last 7 days"),
              ("30", "Last 30 days")]


class SearchPage(QWidget):
    """Broad Jira exploration beyond the current user."""

    def __init__(self, cfg: AppConfig,
                 config_provider: Callable[[], AppConfig],
                 client_provider: Callable[[], JiraClient]) -> None:
        super().__init__()
        self.cfg = cfg
        self._config_provider = config_provider
        self._client_provider = client_provider
        self.store = InMemoryIssueStore()
        self._updated_days = 0
        self._fetch_thread: QThread | None = None
        self._fetch_worker: FetchWorker | None = None
        self._build()
        self._update_summary()

    # ---------------------------------------------------------------- build
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 14)
        root.setSpacing(10)

        title = QLabel("Search all of Jira")
        f = title.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)
        sub = QLabel("Explore beyond your own work — any project, client, or person.")
        sub.setStyleSheet("color: palette(mid); font-size:12px;")
        root.addWidget(sub)

        # --- pickers: Project / Client / Assignee / Reporter ---
        pickers = QGridLayout()
        pickers.setHorizontalSpacing(10)
        self.cmb_project = self._picker("PROJECT", pickers, 0)
        self.cmb_client = self._picker("CLIENT", pickers, 1)
        self.cmb_assignee = self._picker("ASSIGNEE", pickers, 2)
        self.cmb_reporter = self._picker("REPORTER", pickers, 3)
        root.addLayout(pickers)

        self.ed_text = QLineEdit()
        self.ed_text.setPlaceholderText("Text in summary, description or comments…")
        self.ed_text.textChanged.connect(self._update_summary)
        root.addWidget(self.ed_text)

        # --- STATUS / TYPE / TIME chip rows ---
        self._chip_rows: dict[str, ChipRow] = {}
        self._status: set[str] = set()
        self._types: set[str] = set()
        for key, caption, opts in (
            ("status", "STATUS", _STATUS_OPTS),
            ("type", "TYPE", _TYPE_OPTS),
            ("time", "TIME", _TIME_OPTS),
        ):
            crow = ChipRow(caption, opts)
            crow.toggled.connect(lambda v, on, k=key: self._on_chip(k, v, on))
            self._chip_rows[key] = crow
            root.addWidget(crow)

        # --- summary + Search Jira primary ---
        actions = QHBoxLayout()
        self.lbl_summary = QLabel("")
        self.lbl_summary.setObjectName(SCOPE_SUMMARY_OBJECT)
        self.lbl_summary.setWordWrap(True)
        actions.addWidget(self.lbl_summary, 1)
        self.btn_search = QPushButton("Search Jira")
        self.btn_search.setObjectName(PRIMARY_ACTION_OBJECT)
        self.btn_search.clicked.connect(self._search)
        actions.addWidget(self.btn_search)
        root.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        root.addWidget(self.progress)

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color: palette(mid); font-size:12px;")
        root.addWidget(self.lbl_count)

        # --- results + detail ---
        self.table = ResultsTable()
        self.table.setAccessibleName("Search results table")
        self.detail = IssueDetailPanel()
        self.table.issueSelected.connect(self.detail.show_issue)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([720, 360])
        root.addWidget(split, 1)

    def _picker(self, caption: str, grid: QGridLayout, col: int) -> QComboBox:
        label = QLabel(caption)
        label.setObjectName(ROW_LABEL_OBJECT)
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItem("", "")
        combo.setAccessibleName(f"{caption.title()} picker")
        combo.currentTextChanged.connect(self._update_summary)
        grid.addWidget(label, 0, col)
        grid.addWidget(combo, 1, col)
        return combo

    # ------------------------------------------------------------- options
    def on_connected(self, client) -> None:
        """Populate the pickers from the instance's real values (best-effort)."""
        if client is None:
            return
        self._fill(self.cmb_project, self._safe(value_source_service.project_options, client))
        self._fill(self.cmb_assignee, self._safe(value_source_service.user_options, client, ""))
        self._fill(self.cmb_reporter, self._safe(value_source_service.user_options, client, ""))

    @staticmethod
    def _safe(fn, *args):
        try:
            return [o.value for o in fn(*args)]
        except Exception:  # noqa: BLE001 - picker population is best-effort
            return []

    @staticmethod
    def _fill(combo: QComboBox, values: list[str]) -> None:
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("", "")
        for v in values:
            if v:
                combo.addItem(v, v)
        combo.setCurrentText(current)
        combo.blockSignals(False)

    # -------------------------------------------------------------- filters
    def _on_chip(self, key: str, value: str, on: bool) -> None:
        if key == "status":
            self._toggle(self._status, value, on)
        elif key == "type":
            self._toggle(self._types, value, on)
        elif key == "time":
            self._updated_days = int(value) if on else 0
            for val, _ in _TIME_OPTS:
                self._chip_rows["time"].set_checked(val, int(val) == self._updated_days)
        self._update_summary()

    @staticmethod
    def _toggle(bag: set[str], value: str, on: bool) -> None:
        bag.add(value) if on else bag.discard(value)

    def _filters(self) -> SearchFilters:
        field_filters: list[FieldFilter] = []
        assignee = self.cmb_assignee.currentText().strip()
        reporter = self.cmb_reporter.currentText().strip()
        if assignee:
            field_filters.append(FieldFilter(field="assignee", op="=", value=assignee,
                                             label="Assignee"))
        if reporter:
            field_filters.append(FieldFilter(field="reporter", op="=", value=reporter,
                                             label="Reporter"))
        project = self.cmb_project.currentText().strip()
        return SearchFilters(
            assigned_to_me=False,
            projects=[project] if project else [],
            client=self.cmb_client.currentText().strip(),
            text=self.ed_text.text().strip(),
            status_categories=[s for s in ("To Do", "In Progress", "Done")
                               if s in self._status],
            issue_types=[t for t, _ in _TYPE_OPTS if t in self._types],
            updated_days=self._updated_days,
            field_filters=field_filters,
        )

    def _update_summary(self, *_: object) -> None:
        try:
            cfg = self._config_provider()
        except Exception:  # noqa: BLE001
            cfg = self.cfg
        self.lbl_summary.setText("◎  " + explain(decompose(cfg, self._filters())))

    # --------------------------------------------------------------- search
    def _search(self) -> None:
        try:
            client = self._client_provider()
            est = estimate_query(self.cfg, self._filters())
        except Exception as e:  # noqa: BLE001 - surfaced to the user verbatim
            QMessageBox.critical(self, "Error", str(e))
            return
        self.btn_search.setEnabled(False)
        self.progress.show()
        self._last_jql = est.jql
        self._fetch_thread = QThread()
        self._fetch_worker = FetchWorker(
            client, est.jql, self.cfg,
            comments=CommentsOptions(mode=CommentsMode.NONE))
        self._fetch_worker.moveToThread(self._fetch_thread)
        self._fetch_thread.started.connect(self._fetch_worker.run)
        self._fetch_worker.progress.connect(self._on_progress)
        self._fetch_worker.finished.connect(self._on_finished)
        self._fetch_worker.failed.connect(self._on_failed)
        self._fetch_worker.finished.connect(self._fetch_thread.quit)
        self._fetch_worker.failed.connect(self._fetch_thread.quit)
        self._fetch_thread.start()

    def _on_progress(self, p: FetchProgress) -> None:
        if p.phase == Phase.SEARCHING and p.total:
            self.progress.setRange(0, p.total)
            self.progress.setValue(p.fetched)
        else:
            self.progress.setRange(0, 0)

    def _on_finished(self, result) -> None:
        self.btn_search.setEnabled(True)
        self.progress.hide()
        info = DataSourceInfo(
            kind=DataSourceKind.JIRA_API, label="Jira API live search",
            detail=getattr(self, "_last_jql", ""), deployment=self.cfg.deployment)
        self.store.replace(IssueCollection(issues=result.issues), info)
        self.table.populate(self.store.issues)
        self.lbl_count.setText(f"{len(self.store.issues)} issues · Jira API live search")

    def _on_failed(self, exc: Exception) -> None:
        self.btn_search.setEnabled(True)
        self.progress.hide()
        from .error_dialog import show_error
        show_error(self, exc, operation="search")
