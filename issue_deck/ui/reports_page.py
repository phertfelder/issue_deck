"""Reports & export — turn the loaded set into a shareable output.

Folds the former Analytics tab in as the report body and adds a header that
opens the full export builder (Markdown / JSONL / CSV / LLM pack). It owns no
data: the embedded dashboard reads the working dataset, and the export button
delegates to the workbench's existing export dialog.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .theme import PRIMARY_ACTION_OBJECT


class ReportsPage(QWidget):
    """Header (title + export builder entry) over the analytics dashboard."""

    def __init__(self, dashboard: QWidget, on_export: Callable[[], None]) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 14)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Reports & export")
        f = title.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        title.setFont(f)
        header.addWidget(title)
        sub = QLabel("Everything is generated on your machine. Credentials are never included.")
        sub.setStyleSheet("color: palette(mid); font-size:12px;")
        header.addWidget(sub)
        header.addStretch(1)
        self.btn_export = QPushButton("Open export builder…")
        self.btn_export.setObjectName(PRIMARY_ACTION_OBJECT)
        self.btn_export.setToolTip(
            "Configure comments, redaction, grouping and format, then export "
            "Markdown / JSONL / CSV or an LLM pack.")
        self.btn_export.clicked.connect(on_export)
        header.addWidget(self.btn_export)
        root.addLayout(header)

        root.addWidget(dashboard, 1)
