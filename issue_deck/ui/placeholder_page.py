"""Minimal placeholder pages for shell nav items not yet built out.

Used by the navigation shell (PR 1) for *Home* and *Exports*, which the spec
turns into full command-center / report-builder pages in later PRs. These are
intentionally thin: a centered title + explanatory line, no behavior.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .theme import PLACEHOLDER_OBJECT


class PlaceholderPage(QWidget):
    """A centered title + description shown for not-yet-implemented pages."""

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.setObjectName(PLACEHOLDER_OBJECT)

        lbl_title = QLabel(title)
        f = lbl_title.font()
        f.setPointSize(f.pointSize() + 6)
        f.setBold(True)
        lbl_title.setFont(f)
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_msg = QLabel(message)
        lbl_msg.setWordWrap(True)
        lbl_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_msg.setStyleSheet("color: palette(mid);")
        lbl_msg.setMaximumWidth(520)

        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(lbl_title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(10)
        layout.addWidget(lbl_msg, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(2)
