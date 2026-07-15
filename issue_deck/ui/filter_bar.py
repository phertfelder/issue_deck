"""Presentational widgets for the guided **filter bar** (My Work redesign).

The recognition-over-recall redesign replaces the dense filter *form* with a
guided filter *bar*: every dimension (WHO / STATUS / TIME / TYPE / PRIORITY) is a
labelled row of toggle **chips**, and every active selection also shows as a
removable **pill**. These widgets are logic-free — they render chips and emit
``value``/``checked`` signals that :class:`~issue_deck.ui.query_tab.QueryTab`
translates into its existing ``SearchFilters`` plumbing (the form still lives
behind a "More filters" disclosure, so nothing is lost).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from .theme import (
    ACTIVE_CHIP_CLOSE_OBJECT,
    ACTIVE_CHIP_OBJECT,
    FILTER_CHIP_OBJECT,
    ROW_LABEL_OBJECT,
)


class FilterChip(QToolButton):
    """A checkable toggle chip (e.g. *In progress*, *Bug*, *High*)."""

    def __init__(self, value: str, label: str) -> None:
        super().__init__()
        self.value = value
        self.setObjectName(FILTER_CHIP_OBJECT)
        self.setText(label)
        self.setCheckable(True)
        self.setAutoRaise(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Selection is communicated by fill + weight + border, never colour alone,
        # so the accessible name keeps the role explicit for screen readers.
        self.setAccessibleName(f"{label} filter")


class ChipRow(QWidget):
    """A captioned row of :class:`FilterChip`s for one filter dimension.

    Emits :attr:`toggled` ``(value, checked)`` when the user clicks a chip. The
    owner is the source of truth: it should mutate its model, then call
    :meth:`set_checked` to reconcile the visual state (which never re-emits).
    """

    toggled = pyqtSignal(str, bool)

    def __init__(self, caption: str, options: list[tuple[str, str]]) -> None:
        super().__init__()
        self._chips: dict[str, FilterChip] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)

        label = QLabel(caption)
        label.setObjectName(ROW_LABEL_OBJECT)
        label.setFixedWidth(62)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label)

        for value, text in options:
            chip = FilterChip(value, text)
            chip.clicked.connect(
                lambda checked=False, v=value: self.toggled.emit(v, self._chips[v].isChecked()))
            self._chips[value] = chip
            row.addWidget(chip)
        self._tail = row
        row.addStretch(1)

    def add_trailing(self, widget: QWidget) -> None:
        """Append a control (e.g. *Advanced JQL…*) after the chips, before the stretch."""
        self._tail.insertWidget(self._tail.count() - 1, widget)

    def set_checked(self, value: str, checked: bool) -> None:
        """Reflect model state onto a chip without emitting :attr:`toggled`."""
        chip = self._chips.get(value)
        if chip is not None and chip.isChecked() != checked:
            chip.blockSignals(True)
            chip.setChecked(checked)
            chip.blockSignals(False)

    def values(self) -> list[str]:
        return list(self._chips)


class ActiveChip(QFrame):
    """A removable pill for one active filter: ``label`` + a ✕ button."""

    removed = pyqtSignal()

    def __init__(self, label: str) -> None:
        super().__init__()
        self.setObjectName(ACTIVE_CHIP_OBJECT)
        row = QHBoxLayout(self)
        row.setContentsMargins(11, 2, 4, 2)
        row.setSpacing(4)
        text = QLabel(label)
        text.setStyleSheet("font-size:12px;")
        row.addWidget(text)
        close = QToolButton()
        close.setObjectName(ACTIVE_CHIP_CLOSE_OBJECT)
        close.setText("✕")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setAccessibleName(f"Remove {label} filter")
        close.setToolTip(f"Remove “{label}”")
        close.clicked.connect(self.removed.emit)
        row.addWidget(close)
