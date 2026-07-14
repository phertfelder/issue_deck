"""Show a :class:`~issue_deck.error_presenter.PresentedError` to the user.

A thin Qt wrapper: title + plain message + suggested action up front, with the
sanitized raw text tucked behind the message box's expandable *Details* area.
The presentation/mapping itself is Qt-free (``error_presenter``)."""

from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox, QWidget

from ..error_presenter import PresentedError, present_error

_ICONS = {
    "info": QMessageBox.Icon.Information,
    "warning": QMessageBox.Icon.Warning,
    "error": QMessageBox.Icon.Critical,
}


def show_presented_error(parent: QWidget | None, presented: PresentedError) -> None:
    box = QMessageBox(parent)
    box.setIcon(_ICONS.get(presented.severity, QMessageBox.Icon.Critical))
    box.setWindowTitle(presented.title)
    box.setText(presented.title)
    box.setInformativeText(f"{presented.plain_message}\n\n{presented.suggested_action}")
    if presented.details:
        box.setDetailedText(presented.details)   # sanitized; expandable
    box.exec()


def show_error(parent: QWidget | None, exc: Exception, *, operation: str = "") -> None:
    """Present ``exc`` and show it — the one-call path for failure handlers."""
    show_presented_error(parent, present_error(exc, operation=operation))
