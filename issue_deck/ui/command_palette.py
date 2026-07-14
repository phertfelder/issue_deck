"""Ctrl+K command palette — a cross-cutting launcher overlay.

A small modal with a filter box over a ranked command list: navigate pages, run
presets or saved views, jump to a loaded issue by key, open field mapping / CSV
import / export, or toggle raw JQL. The palette itself owns no app logic — the
main window builds the :class:`Command` list (so presets/views/issues reflect
current state) and runs the chosen command's callback after the overlay closes.

:func:`filter_commands` is a pure ranking function (Qt-free), unit-tested apart
from the widget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QVBoxLayout,
    QWidget,
)


@dataclass
class Command:
    """One palette entry: a label, a category, and what to run."""

    title: str
    category: str
    run: Callable[[], None]
    keywords: str = ""


def filter_commands(commands: list[Command], query: str) -> list[Command]:
    """Rank ``commands`` for ``query`` (all tokens must match title/category/keywords).

    Pure and case-insensitive: exact title, then title-prefix, then title-substring,
    then other-field matches; ties broken alphabetically. Empty query keeps order.
    """
    q = query.strip().lower()
    if not q:
        return list(commands)
    tokens = q.split()
    scored: list[tuple[int, str, Command]] = []
    for cmd in commands:
        title = cmd.title.lower()
        hay = f"{title} {cmd.category.lower()} {cmd.keywords.lower()}"
        if not all(t in hay for t in tokens):
            continue
        if title == q:
            rank = 0
        elif title.startswith(q):
            rank = 1
        elif q in title:
            rank = 2
        else:
            rank = 3
        scored.append((rank, title, cmd))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in scored]


class CommandPalette(QDialog):
    """Modal launcher. On accept, :attr:`chosen` holds the picked command."""

    def __init__(self, parent: QWidget | None, commands: list[Command]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command palette")
        self.setModal(True)
        self.resize(560, 420)
        self._commands = commands
        self._filtered: list[Command] = list(commands)
        self.chosen: Command | None = None

        v = QVBoxLayout(self)
        self.ed = QLineEdit()
        self.ed.setPlaceholderText("Type a command…  (Esc to close)")
        self.ed.textChanged.connect(self._refilter)
        self.ed.returnPressed.connect(self._run_current)
        self.ed.installEventFilter(self)   # route Up/Down/Esc from the input
        v.addWidget(self.ed)

        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _: self._run_current())
        v.addWidget(self.list)

        self._populate(self._filtered)
        self.ed.setFocus()

    # ---- filtering / display ----
    def _refilter(self, text: str) -> None:
        self._filtered = filter_commands(self._commands, text)
        self._populate(self._filtered)

    def _populate(self, commands: list[Command]) -> None:
        self.list.clear()
        for cmd in commands:
            self.list.addItem(f"{cmd.title}    ·  {cmd.category}")
        if commands:
            self.list.setCurrentRow(0)

    # ---- keyboard ----
    def eventFilter(self, obj: object, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if obj is self.ed and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self.list.currentRow()
                row += 1 if key == Qt.Key.Key_Down else -1
                self.list.setCurrentRow(max(0, min(row, self.list.count() - 1)))
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, event)

    def _run_current(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._filtered):
            self.chosen = self._filtered[row]
            self.accept()
