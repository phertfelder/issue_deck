"""Home command center — the task-first landing page.

Answers "what do I do next?" with one-click **preset cards** (each a
:data:`issue_deck.jql_helper.BUILTIN_TEMPLATES` entry, shown by plain-English
description with a live count pill), a *Start something* row, and a *Saved views*
chip row. It owns **no** query logic: card clicks emit a
:class:`~issue_deck.models.SearchFilters` (or a plain signal) that the main
window routes into the existing Query/Results fetch flow.

Live counts come from a bounded, off-thread :class:`CountsWorker` (one
``maxResults=1`` round-trip per preset) — never a synchronous call, and only
when a base URL is configured. Until then (or when a probe can't authenticate)
each pill reads ``—``.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..jira_client import JiraClient
from ..jql_helper import BUILTIN_TEMPLATES, JqlTemplate
from ..query import estimate_query
from ..views import SavedViewStore
from .theme import (
    HOME_CARD_OBJECT,
    HOME_CHIP_OBJECT,
    HOME_PILL_OBJECT,
    HOME_SECTION_OBJECT,
)
from .workers import CountsWorker

# Curated COMMON PULLS, mapped to real BUILTIN_TEMPLATES by name (source of
# truth). Order matches the spec's card grid; any name not found is skipped.
_PRESET_NAMES = [
    "My open work",
    "Blocked issues",
    "High priority stale work",
    "Client/customer work",
    "Issues changed since last export",
]


def _builtin(name: str) -> JqlTemplate | None:
    return next((t for t in BUILTIN_TEMPLATES if t.name == name), None)


def _connection_label(cfg: AppConfig) -> str:
    if not cfg.base_url:
        return "Not connected"
    host = cfg.base_url.split("://", 1)[-1].rstrip("/")
    return f"● {host}"


class ClickableCard(QFrame):
    """A card-styled frame that emits :attr:`clicked` when pressed."""

    clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(HOME_CARD_OBJECT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class HomePage(QWidget):
    """The command-center landing page. Emits intents; runs no queries itself."""

    presetChosen = pyqtSignal(object)       # SearchFilters (already cloned)
    customQueryRequested = pyqtSignal()
    importCsvRequested = pyqtSignal()
    discoverFieldsRequested = pyqtSignal()
    rawJqlRequested = pyqtSignal()
    savedViewChosen = pyqtSignal(str)

    def __init__(self, cfg: AppConfig, views: SavedViewStore,
                 client_provider: Callable[[], JiraClient]) -> None:
        super().__init__()
        self.cfg = cfg
        self._views = views
        self._client_provider = client_provider
        # (template, pill) per preset card, index-aligned with the counts worker.
        self._presets: list[JqlTemplate] = []
        self._pills: list[QLabel] = []
        self._count_thread = None
        self._count_worker = None
        self._build()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        # Centered ~960px column.
        row = QHBoxLayout(body)
        row.addStretch(1)
        col_host = QWidget()
        col_host.setMaximumWidth(980)
        col_host.setMinimumWidth(560)
        row.addWidget(col_host, 6)
        row.addStretch(1)

        col = QVBoxLayout(col_host)
        col.setContentsMargins(30, 28, 30, 28)
        col.setSpacing(18)

        col.addLayout(self._header())
        col.addWidget(self._section_label("COMMON PULLS"))
        col.addWidget(self._common_pulls())
        col.addWidget(self._section_label("START SOMETHING"))
        col.addWidget(self._start_something())
        col.addWidget(self._section_label("SAVED VIEWS"))
        col.addWidget(self._saved_views())
        col.addStretch(1)

    def _header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        greeting = QLabel("IssueDeck")
        f = greeting.font()
        f.setPointSize(f.pointSize() + 7)
        f.setBold(True)
        greeting.setFont(f)
        row.addWidget(greeting)
        row.addStretch(1)
        self.lbl_connection = QLabel(_connection_label(self.cfg))
        self.lbl_connection.setStyleSheet("color: palette(mid);")
        row.addWidget(self.lbl_connection)
        return row

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName(HOME_SECTION_OBJECT)
        return lbl

    def _common_pulls(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(13)

        cards: list[QWidget] = []
        for name in _PRESET_NAMES:
            tpl = _builtin(name)
            if tpl is None:
                continue
            cards.append(self._preset_card(tpl))
        cards.append(self._custom_card())

        for i, card in enumerate(cards):
            grid.addWidget(card, i // 3, i % 3)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        return w

    def _preset_card(self, tpl: JqlTemplate) -> QWidget:
        card = ClickableCard()
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(6)

        top = QHBoxLayout()
        title = QLabel(tpl.name)
        tf = title.font()
        tf.setBold(True)
        title.setFont(tf)
        top.addWidget(title)
        top.addStretch(1)
        pill = QLabel("—")
        pill.setObjectName(HOME_PILL_OBJECT)
        top.addWidget(pill)
        v.addLayout(top)

        desc = QLabel(tpl.description)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: palette(mid);")
        v.addWidget(desc)

        self._presets.append(tpl)
        self._pills.append(pill)
        card.clicked.connect(lambda t=tpl: self.presetChosen.emit(t.clone_filters()))
        return card

    def _custom_card(self) -> QWidget:
        card = ClickableCard()
        card.setStyleSheet("QFrame#%s { border-style: dashed; }" % HOME_CARD_OBJECT)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(6)
        title = QLabel("Build a custom query")
        tf = title.font()
        tf.setBold(True)
        title.setFont(tf)
        v.addWidget(title)
        desc = QLabel("Open the guided builder and compose your own filters.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: palette(mid);")
        v.addWidget(desc)
        card.clicked.connect(self.customQueryRequested.emit)
        return card

    def _start_something(self) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(13)
        row.addWidget(self._action_card(
            "Import Jira CSV", "Build filters from a local CSV export — no Jira "
            "access needed.", self.importCsvRequested.emit))
        row.addWidget(self._action_card(
            "Discover fields", "Map your instance's custom fields from real "
            "sample data.", self.discoverFieldsRequested.emit))
        row.addWidget(self._action_card(
            "Paste raw JQL", "Drop straight into the raw-JQL editor for full "
            "control.", self.rawJqlRequested.emit))
        return w

    def _action_card(self, title: str, desc: str, on_click: Callable[[], None]) -> QWidget:
        card = ClickableCard()
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(6)
        t = QLabel(title)
        tf = t.font()
        tf.setBold(True)
        t.setFont(tf)
        v.addWidget(t)
        d = QLabel(desc)
        d.setWordWrap(True)
        d.setStyleSheet("color: palette(mid);")
        v.addWidget(d)
        card.clicked.connect(on_click)
        return card

    def _saved_views(self) -> QWidget:
        # Persistent host so the chip row can be repopulated after a view is
        # saved elsewhere (see _populate_saved_views / refresh).
        self._views_row = QHBoxLayout()
        self._views_row.setContentsMargins(0, 0, 0, 0)
        self._views_row.setSpacing(8)
        host = QWidget()
        host.setLayout(self._views_row)
        self._populate_saved_views()
        return host

    def _populate_saved_views(self) -> None:
        while self._views_row.count():
            item = self._views_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        names = sorted(self._views.names())
        if not names:
            empty = QLabel("No saved views yet — save one from the Query page.")
            empty.setStyleSheet("color: palette(mid);")
            self._views_row.addWidget(empty)
        else:
            for name in names:
                chip = QPushButton(name)
                chip.setObjectName(HOME_CHIP_OBJECT)
                chip.setCursor(Qt.CursorShape.PointingHandCursor)
                chip.clicked.connect(lambda _=False, n=name: self.savedViewChosen.emit(n))
                self._views_row.addWidget(chip)
        self._views_row.addStretch(1)

    # ------------------------------------------------------------- live counts
    def refresh(self) -> None:
        """Refresh everything that can change while the page is hidden."""
        self._populate_saved_views()
        self.refresh_counts()  # also refreshes the connection chip

    def refresh_connection(self) -> None:
        """Re-render the connection chip from the current config."""
        self.lbl_connection.setText(_connection_label(self.cfg))

    def refresh_counts(self) -> None:
        """Kick off a bounded, off-thread recount of every preset pill.

        No-op (pills reset to ``—``) when unconfigured or a client can't be built,
        so the page never blocks and never fabricates a number."""
        self.refresh_connection()
        if not self.cfg.base_url or not self._presets:
            self._reset_pills()
            return
        try:
            client = self._client_provider()
        except Exception:  # noqa: BLE001 - unconfigured/invalid; leave dashes
            self._reset_pills()
            return
        jqls = [estimate_query(self.cfg, t.filters).jql for t in self._presets]
        self._start_counts(client, jqls)

    def _reset_pills(self) -> None:
        for pill in self._pills:
            pill.setText("—")

    def _start_counts(self, client: JiraClient, jqls: list[str]) -> None:
        from PyQt6.QtCore import QThread

        self._stop_counts()
        self._count_thread = QThread()
        self._count_worker = CountsWorker(client, self.cfg, jqls)
        self._count_worker.moveToThread(self._count_thread)
        self._count_thread.started.connect(self._count_worker.run)
        self._count_worker.countReady.connect(self._on_count)
        self._count_worker.finished.connect(self._count_thread.quit)
        self._count_thread.start()

    def _stop_counts(self) -> None:
        if self._count_worker is not None:
            self._count_worker.cancel()
        if self._count_thread is not None:
            self._count_thread.quit()
            self._count_thread.wait(1500)
        self._count_thread = None
        self._count_worker = None

    def _on_count(self, index: int, total: object) -> None:
        if 0 <= index < len(self._pills):
            self._pills[index].setText("—" if total is None else str(total))
