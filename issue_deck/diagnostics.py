"""Runtime environment info for the About dialog.

Kept Qt-free (PyQt versions are read via a guarded import) so the info can be
unit-tested without constructing any widgets.
"""

from __future__ import annotations

import platform

from . import constants, credentials


def environment_info() -> list[tuple[str, str]]:
    """Ordered ``(label, value)`` pairs describing the running install."""
    return [
        ("Version", constants.APP_VERSION),
        ("Config file", str(constants.CONFIG_PATH)),
        ("Data directory", str(constants.APP_DIR)),
        ("Token storage", _token_storage()),
        ("Python", f"{platform.python_version()} ({platform.python_implementation()})"),
        ("Qt / PyQt6", _qt_versions()),
        ("Platform", platform.platform()),
    ]


def _token_storage() -> str:
    if credentials.keyring_available():
        return "OS keychain (encrypted)"
    return "plaintext file (no keychain — install the 'keyring' extra)"


def _qt_versions() -> str:
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    except Exception:  # pragma: no cover - PyQt is a hard runtime dep
        return "unavailable"
    return f"PyQt {PYQT_VERSION_STR}, Qt {QT_VERSION_STR}"
