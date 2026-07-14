"""Reusable presentational widgets for the guided query builder (PR 4).

Small, logic-free building blocks the Query tab composes to reach the redesign
spec's guided surface without rewriting its `SearchFilters` plumbing:

* :class:`SegmentedToggle` — a pill segmented control (e.g. *Guided ⇄ Raw*).
* :class:`CollapsibleSection` — a disclosure header + hideable body, used for the
  "Advanced — generated JQL · preview only" drawer.

Neither widget knows anything about queries; they just emit signals the tab
wires to its existing apply/preview/fetch logic.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .theme import (
    DRAWER_HEADER_OBJECT,
    SEGMENTED_BTN_OBJECT,
    SEGMENTED_OBJECT,
)


class SegmentedToggle(QWidget):
    """An exclusive, pill-styled segmented control. Emits :attr:`changed`(index)."""

    changed = pyqtSignal(int)

    def __init__(self, options: list[str], initial: int = 0) -> None:
        super().__init__()
        self.setObjectName(SEGMENTED_OBJECT)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []
        for i, label in enumerate(options):
            btn = QPushButton(label)
            btn.setObjectName(SEGMENTED_BTN_OBJECT)
            btn.setCheckable(True)
            btn.setChecked(i == initial)
            btn.clicked.connect(lambda _=False, idx=i: self.changed.emit(idx))
            self._group.addButton(btn, i)
            self._buttons.append(btn)
            row.addWidget(btn)

    def current_index(self) -> int:
        return self._group.checkedId()

    def set_index(self, index: int) -> None:
        """Check a segment without emitting :attr:`changed` (programmatic sync)."""
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)


class CollapsibleSection(QWidget):
    """A disclosure header (▸/▾ + title) over a body that shows/hides.

    Add body widgets to :attr:`content_layout`. Starts collapsed unless
    ``expanded`` is set. Emits :attr:`toggled`(is_open)."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, expanded: bool = False) -> None:
        super().__init__()
        self._title = title
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QToolButton()
        self._header.setObjectName(DRAWER_HEADER_OBJECT)
        self._header.setCheckable(True)
        self._header.setChecked(expanded)
        self._header.clicked.connect(self._on_clicked)
        outer.addWidget(self._header)

        self._body = QFrame()
        self.content_layout = QVBoxLayout(self._body)
        self.content_layout.setContentsMargins(4, 4, 4, 4)
        outer.addWidget(self._body)

        self._body.setVisible(expanded)
        self._render_header()

    def _render_header(self) -> None:
        arrow = "▾" if self._header.isChecked() else "▸"
        self._header.setText(f"{arrow}  {self._title}")

    def _on_clicked(self, checked: bool) -> None:
        self._body.setVisible(checked)
        self._render_header()
        self.toggled.emit(checked)

    def is_expanded(self) -> bool:
        return self._header.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self._header.setChecked(expanded)
        self._body.setVisible(expanded)
        self._render_header()

    def addWidget(self, widget: QWidget) -> None:  # noqa: N802 - Qt-style convenience
        self.content_layout.addWidget(widget)
