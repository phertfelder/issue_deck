"""A neutral empty-state panel for the results area.

Shown in place of the table when the working dataset is empty: a title, a plain
reason, and one call-to-action. The message is set contextually by the caller
(nothing loaded yet vs. a query that matched nothing)."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .theme import PLACEHOLDER_OBJECT


class EmptyState(QWidget):
    """Centered title + message + a single CTA button."""

    def __init__(self, cta_label: str, on_cta: Callable[[], None]) -> None:
        super().__init__()
        self.setObjectName(PLACEHOLDER_OBJECT)

        self._title = QLabel("")
        f = self._title.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        self._title.setFont(f)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._message = QLabel("")
        self._message.setWordWrap(True)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setMaximumWidth(460)
        self._message.setStyleSheet("color: palette(mid);")

        self._cta = QPushButton(cta_label)
        self._cta.setAccessibleName(cta_label)
        self._cta.clicked.connect(on_cta)

        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(self._title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(8)
        layout.addWidget(self._message, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(14)
        layout.addWidget(self._cta, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(2)

    def set_message(self, title: str, message: str) -> None:
        self._title.setText(title)
        self._message.setText(message)
