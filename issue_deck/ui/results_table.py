"""Results table: sortable, column-configurable, with markers and row actions.

Beyond rendering issues, the table is the interactive heart of the workbench:

* **Sort** by any visible column (numeric points/dates sort correctly).
* **Toggle** which columns are visible (:meth:`set_columns`).
* **Quick filter** the fetched rows in place (:meth:`apply_quick_filter`).
* **Row actions** via right-click: open URL, copy key, copy Markdown.
* **Markers**: high priority/severity rows are tinted; stale rows (not updated in
  >30 days) are greyed with an explanatory tooltip.
* Emits :attr:`issueSelected` so a detail panel can follow the selection.

All heavy logic (markers, staleness) lives in :mod:`issue_deck.markers`; this
widget only presents it.
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
)

from ..exporters import issue_to_markdown
from ..markers import days_since_update, is_blocked, is_high_priority, is_stale
from ..models import DEFAULT_VISIBLE_COLUMNS, RESULT_COLUMNS
from ..schema import NormalizedIssue
from .theme import Tokens, active_tokens

_LABELS = dict(RESULT_COLUMNS)

# Item-data roles. ``_ISSUE_ROLE`` maps a cell back to its source issue; the
# marker/stale roles carry the presentation flags the delegate reads at paint
# time. Roles ride with items through sorting, so they stay correct after
# re-sorts (unlike a row-indexed side table).
_ISSUE_ROLE = Qt.ItemDataRole.UserRole
_STALE_ROLE = Qt.ItemDataRole.UserRole + 1
_MARKER_ROLE = Qt.ItemDataRole.UserRole + 2

# Left-edge row markers, most urgent first. A marker is a narrow coloured bar on
# the key column — never a full-row background — so text contrast is untouched.
_MARK_BLOCKED = "blocked"
_MARK_HIGH = "high"
_MARK_STALE = "stale"

_MONO_FAMILIES = ["JetBrains Mono", "Cascadia Mono", "Consolas", "monospace"]
_TEXT_PAD = 7        # horizontal text inset inside a cell
_MARKER_W = 3        # width of the left-edge marker bar


def _row_marker(issue: NormalizedIssue) -> str:
    """The single left-edge marker for a row (blocked ≻ high ≻ stale ≻ none)."""
    if is_blocked(issue):
        return _MARK_BLOCKED
    if is_high_priority(issue):
        return _MARK_HIGH
    if is_stale(issue):
        return _MARK_STALE
    return ""


def _marker_color(kind: str, t: Tokens) -> QColor | None:
    if kind == _MARK_BLOCKED:
        return QColor(t.table_blocked)
    if kind == _MARK_HIGH:
        return QColor(t.risk)
    if kind == _MARK_STALE:
        return QColor(t.table_stale)
    return None


def _text_color(col_id: str, *, stale: bool, selected: bool, t: Tokens) -> QColor:
    """Semantic foreground for a cell, from theme tokens.

    Selection wins over everything so a picked row is always readable on its
    high-contrast background. Otherwise: keys read as the accent, summaries are
    the strongest body text, and the remaining metadata columns are a calmer —
    but still ≥4.5:1 — secondary. Stale rows drop the summary to secondary so
    the row reads as de-emphasised without becoming disabled-grey.
    """
    if selected:
        return QColor(t.table_sel_text)
    if col_id == "key":
        return QColor(t.accent)
    if col_id == "summary" and not stale:
        return QColor(t.text)
    return QColor(t.text_secondary)


def _cell_value(issue: NormalizedIssue, col_id: str) -> tuple[str, object]:
    """(display text, sort key) for one issue/column."""
    if col_id == "assignee":
        text = issue.assignee.name
        return text, text.lower()
    if col_id == "story_points":
        v = issue.story_points
        return ("" if v is None else str(v)), (float(v) if v is not None else float("-inf"))
    if col_id == "updated":
        return issue.updated[:10], issue.updated  # ISO sorts chronologically
    text = str(getattr(issue, col_id, ""))
    return text, text.lower()


class _SortItem(QTableWidgetItem):
    """Table item that sorts by an explicit key (numbers/dates, not display text)."""

    def __init__(self, text: str, sort_key: object) -> None:
        super().__init__(text)
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._sort_key = sort_key

    def __lt__(self, other: object) -> bool:
        if isinstance(other, _SortItem):
            try:
                return bool(self._sort_key < other._sort_key)  # type: ignore[operator]
            except TypeError:
                return str(self._sort_key) < str(other._sort_key)
        return NotImplemented  # type: ignore[return-value]


class _ResultsDelegate(QStyledItemDelegate):
    """Paints every results cell from *live* theme tokens.

    Owning the paint keeps colour out of baked ``QBrush`` items, so nothing
    overrides the theme and a runtime theme switch is reflected without a
    repopulate. It also makes selection authoritative: a selected row always
    paints its high-contrast foreground in *every* column, so the per-cell
    semantic colours (accent key, muted metadata) can never leave text
    unreadable against the selected background — the classic custom-brush bug.
    """

    def __init__(self, table: ResultsTable) -> None:
        super().__init__(table)
        self._table = table

    def paint(self, painter, option, index) -> None:  # noqa: D102
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        t = active_tokens()
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        active = bool(opt.state & QStyle.StateFlag.State_Active)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        alt = bool(opt.features & QStyleOptionViewItem.ViewItemFeature.Alternate)

        # ---- background state ----
        if selected:
            bg = t.table_sel if active else t.table_sel_unfocused
        elif hover:
            bg = t.table_hover
        elif alt:
            bg = t.card
        else:
            bg = t.content
        painter.save()
        painter.fillRect(opt.rect, QColor(bg))

        rect = opt.rect
        col_id = self._table.column_id(index.column())
        left_inset = _TEXT_PAD

        # ---- narrow left-edge marker (key column only) ----
        if index.column() == 0:
            mc = _marker_color(index.data(_MARKER_ROLE) or "", t)
            if mc is not None:
                painter.fillRect(
                    QRect(rect.left(), rect.top() + 1, _MARKER_W, rect.height() - 2), mc)
                left_inset = _MARKER_W + _TEXT_PAD

        # ---- text ----
        stale = bool(index.data(_STALE_ROLE))
        marker = index.data(_MARKER_ROLE) or ""
        strong = marker in (_MARK_HIGH, _MARK_BLOCKED)
        font = QFont(opt.font)
        if col_id == "key":
            font.setFamilies(_MONO_FAMILIES)
        if strong and col_id in ("key", "summary"):
            font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(_text_color(col_id, stale=stale, selected=selected, t=t))
        text_rect = rect.adjusted(left_inset, 0, -_TEXT_PAD, 0)
        fm = painter.fontMetrics()
        elided = fm.elidedText(
            str(opt.text), Qt.TextElideMode.ElideRight, text_rect.width())
        align = index.data(Qt.ItemDataRole.TextAlignmentRole)
        flags = int(align) if align is not None else int(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        painter.drawText(text_rect, flags, elided)
        painter.restore()


class ResultsTable(QTableWidget):
    """Interactive issue table. ``populate`` keeps the original issue list."""

    issueSelected = pyqtSignal(object)   # NormalizedIssue | None

    _COMFORTABLE_ROW = 28
    _COMPACT_ROW = 20

    def __init__(self) -> None:
        super().__init__(0, 0)
        self._issues: list[NormalizedIssue] = []
        self._columns: list[str] = list(DEFAULT_VISIBLE_COLUMNS)
        self._compact = False
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)
        # Zebra rows (subtle, token-driven) + a delegate that owns all cell paint.
        self.setAlternatingRowColors(True)
        self.setItemDelegate(_ResultsDelegate(self))
        vh = self.verticalHeader()
        if vh is not None:
            vh.setVisible(False)   # row numbers add noise without meaning here
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.itemSelectionChanged.connect(self._on_selection)
        self._apply_headers()

    # ---- columns ----
    def set_columns(self, columns: list[str]) -> None:
        """Set the visible columns (ids from :data:`RESULT_COLUMNS`) and re-render."""
        self._columns = [c for c in columns if c in _LABELS] or list(DEFAULT_VISIBLE_COLUMNS)
        self._apply_headers()
        self.populate(self._issues)

    def visible_columns(self) -> list[str]:
        return list(self._columns)

    def column_id(self, col: int) -> str:
        """The column id (``key``/``summary``/…) at visual column ``col``."""
        return self._columns[col] if 0 <= col < len(self._columns) else ""

    # ---- density ----
    def set_compact(self, compact: bool) -> None:
        """Toggle comfortable ↔ compact row height for power-user triage."""
        self._compact = compact
        vh = self.verticalHeader()
        if vh is not None:
            vh.setDefaultSectionSize(self._COMPACT_ROW if compact else self._COMFORTABLE_ROW)

    def is_compact(self) -> bool:
        return self._compact

    def _apply_headers(self) -> None:
        self.setColumnCount(len(self._columns))
        self.setHorizontalHeaderLabels([_LABELS[c] for c in self._columns])
        header = self.horizontalHeader()
        if header is not None and "summary" in self._columns:
            header.setSectionResizeMode(
                self._columns.index("summary"), QHeaderView.ResizeMode.Stretch)

    # ---- population ----
    def populate(self, issues: list[NormalizedIssue]) -> None:
        self._issues = issues
        self.setSortingEnabled(False)   # avoid re-sort churn while filling
        self.setRowCount(0)
        for idx, issue in enumerate(issues):
            row = self.rowCount()
            self.insertRow(row)
            stale = is_stale(issue)
            marker = _row_marker(issue)
            tooltip = self._marker_tooltip(issue, marker)
            for col, col_id in enumerate(self._columns):
                text, key = _cell_value(issue, col_id)
                item = _SortItem(text, key)
                item.setData(_ISSUE_ROLE, idx)
                item.setData(_STALE_ROLE, stale)
                item.setData(_MARKER_ROLE, marker)
                if tooltip:
                    item.setToolTip(tooltip)
                self.setItem(row, col, item)
        self.setSortingEnabled(True)

    @staticmethod
    def _marker_tooltip(issue: NormalizedIssue, marker: str) -> str:
        """Explain a row's left-edge marker on hover (empty when unmarked)."""
        if marker == _MARK_STALE:
            days = days_since_update(issue)
            return f"Stale — not updated in {days} days" if days else "Stale"
        if marker == _MARK_BLOCKED:
            return "Blocked"
        if marker == _MARK_HIGH:
            return "High priority / severity"
        return ""

    # ---- selection / detail ----
    def _issue_at(self, row: int) -> NormalizedIssue | None:
        item = self.item(row, 0)
        if item is None:
            return None
        idx = item.data(_ISSUE_ROLE)
        return self._issues[idx] if isinstance(idx, int) and 0 <= idx < len(self._issues) else None

    def selected_issue(self) -> NormalizedIssue | None:
        sm = self.selectionModel()
        rows = sm.selectedRows() if sm is not None else []
        return self._issue_at(rows[0].row()) if rows else None

    def select_key(self, key: str) -> bool:
        """Select (and scroll to) the row for ``key``. Returns True if found."""
        want = key.strip().lower()
        for row in range(self.rowCount()):
            issue = self._issue_at(row)
            if issue is not None and issue.key.lower() == want:
                self.setRowHidden(row, False)
                self.selectRow(row)
                item = self.item(row, 0)
                if item is not None:
                    self.scrollToItem(item)
                return True
        return False

    def _on_selection(self) -> None:
        self.issueSelected.emit(self.selected_issue())

    # ---- quick filter ----
    def _cell_text(self, row: int, col: int) -> str:
        item = self.item(row, col)
        return item.text().lower() if item is not None else ""

    def apply_quick_filter(self, text: str) -> int:
        """Hide rows that don't contain ``text`` in any visible cell. Returns shown count."""
        needle = text.strip().lower()
        shown = 0
        for row in range(self.rowCount()):
            match = not needle or any(
                needle in self._cell_text(row, c) for c in range(self.columnCount())
            )
            self.setRowHidden(row, not match)
            shown += int(match)
        return shown

    # ---- row actions ----
    def _context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        issue = self._issue_at(item.row())
        if issue is None:
            return
        menu = QMenu(self)
        act_open = menu.addAction("Open in browser")
        act_key = menu.addAction("Copy issue key")
        act_md = menu.addAction("Copy Markdown")
        vp = self.viewport()
        chosen = menu.exec(vp.mapToGlobal(pos) if vp is not None else pos)
        if chosen == act_open:
            self.open_issue(issue)
        elif chosen == act_key:
            self.copy_key(issue)
        elif chosen == act_md:
            self.copy_markdown(issue)

    @staticmethod
    def open_issue(issue: NormalizedIssue) -> None:
        if issue.url:
            QDesktopServices.openUrl(QUrl(issue.url))

    @staticmethod
    def _clipboard():
        return QApplication.clipboard()

    def copy_key(self, issue: NormalizedIssue) -> None:
        cb = self._clipboard()
        if cb is not None:
            cb.setText(issue.key)

    def copy_markdown(self, issue: NormalizedIssue) -> None:
        cb = self._clipboard()
        if cb is not None:
            cb.setText(issue_to_markdown(issue))
