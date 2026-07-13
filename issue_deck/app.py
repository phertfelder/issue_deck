"""Application entry point: build the Qt app and show the main window."""

from __future__ import annotations

import logging
import os
import sys

from PyQt6.QtWidgets import QApplication

from . import paths
from .logging_utils import configure_logging
from .ui.main_window import MainWindow
from .ui.theme import apply_theme


def main() -> None:
    # Redacting logging is always installed; JIRA_PULLER_DEBUG raises verbosity.
    configure_logging(logging.DEBUG if os.environ.get("JIRA_PULLER_DEBUG") else logging.WARNING)
    # One-time, non-destructive move of legacy ~/.issue_deck data to the native
    # location. Surfaced at WARNING so the (rare) migration notice is always seen.
    migration = paths.migrate_legacy()
    if migration.summary():
        logging.getLogger("issue_deck").warning(migration.summary())
    app = QApplication(sys.argv)
    # Warm-neutral dark theme (Fusion base + QSS). Global, so all windows match.
    apply_theme(app, "dark")
    win = MainWindow()
    win.show()
    win.run_first_run_if_needed()
    sys.exit(app.exec())
