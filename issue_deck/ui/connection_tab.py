"""Connection settings tab: the shared credentials panel + save.

The request timeout lives only in the Settings dialog (File → Settings), so it is
not repeated here — the panel edits credentials, and Save persists them.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ..config import AppConfig
from .credentials_panel import CredentialsPanel, persist_token


class ConnectionTab(QWidget):
    # Re-emitted from the credentials panel after a successful "Test connection",
    # so other tabs can probe the instance (e.g. for capability-based UI gating).
    connected = pyqtSignal(object)

    def __init__(self, cfg: AppConfig):
        super().__init__()
        self.cfg = cfg
        self.panel = CredentialsPanel(cfg)
        self.panel.connected.connect(self.connected)

        v = QVBoxLayout(self)
        v.addWidget(self.panel)

        self.btn_save = QPushButton("Save settings")
        self.btn_save.clicked.connect(self._save)
        save_row = QHBoxLayout()
        save_row.addWidget(self.btn_save)
        save_row.addStretch()
        v.addLayout(save_row)
        v.addStretch(1)

    # ---------- cfg <-> ui (delegates to the shared panel) ----------
    def load_config(self, cfg: AppConfig) -> None:
        self.panel.load_config(cfg)

    def apply_to_config(self, cfg: AppConfig) -> AppConfig:
        return self.panel.apply_to_config(cfg)

    def token(self) -> str:
        return self.panel.token()

    def raw_token(self) -> str:
        return self.panel.raw_token()

    # ---------- actions ----------
    def _save(self) -> None:
        self.apply_to_config(self.cfg)
        persist_token(self.cfg, self.panel.raw_token(), parent=self, announce=True)
