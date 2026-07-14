"""First-run onboarding wizard.

A lightweight two-page flow: (1) pick deployment + enter/test credentials, and
(2) choose the orthogonal default authoring axes and optionally kick off a CSV
import to build filters. Reads out via :meth:`apply_to_config` / :meth:`raw_token`
/ :attr:`want_csv_import`; the caller persists and wires the result.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from .credentials_panel import CredentialsPanel
from .settings_dialog import (
    _AUTHORING_LABELS,
    _DATA_SOURCE_LABELS,
    _SCOPE_LABELS,
    _combo,
    _select,
)


class OnboardingDialog(QDialog):
    """Modal first-run setup. Accepted → caller applies to config and saves."""

    def __init__(self, parent: QWidget | None = None, *, cfg: AppConfig) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to IssueDeck")
        self.setMinimumWidth(520)
        self.cfg = cfg

        outer = QVBoxLayout(self)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._connection_page())
        self._stack.addWidget(self._defaults_page())
        outer.addWidget(self._stack, 1)

        nav = QHBoxLayout()
        self.btn_back = QPushButton("Back")
        self.btn_back.clicked.connect(self._back)
        self.btn_next = QPushButton("Next")
        self.btn_next.clicked.connect(self._next)
        self.btn_skip = QPushButton("Skip")
        self.btn_skip.clicked.connect(self.reject)
        nav.addWidget(self.btn_skip)
        nav.addStretch(1)
        nav.addWidget(self.btn_back)
        nav.addWidget(self.btn_next)
        outer.addLayout(nav)

        self._load()
        self._update_nav()

    # ---------------- pages ----------------
    def _connection_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "<b>Connect to Jira</b><br>Pick your deployment, then enter your "
            "credentials. Everything stays on this machine."))
        # Reuse the one credential editor, trimmed for first run: no custom-field
        # IDs (you discover those later) and no Forget action.
        self.creds = CredentialsPanel(
            self.cfg, show_custom_fields=False, show_forget=False)
        v.addWidget(self.creds)
        v.addStretch(1)
        return w

    def _defaults_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "<b>Defaults</b><br>These are separate on purpose — they never get "
            "conflated in query construction. You can change them later in Settings."))
        form = QFormLayout()
        self.cmb_data_source = _combo(_DATA_SOURCE_LABELS)
        form.addRow("Start from:", self.cmb_data_source)
        self.cmb_authoring = _combo(_AUTHORING_LABELS)
        form.addRow("Query authoring mode:", self.cmb_authoring)
        self.cmb_scope = _combo(_SCOPE_LABELS)
        form.addRow("Default query scope:", self.cmb_scope)
        v.addLayout(form)

        self.cb_import_csv = QCheckBox(
            "Import a CSV now to build filters from real data (optional)")
        v.addWidget(self.cb_import_csv)
        v.addStretch(1)
        return w

    # ---------------- nav ----------------
    def _back(self) -> None:
        self._stack.setCurrentIndex(max(0, self._stack.currentIndex() - 1))
        self._update_nav()

    def _next(self) -> None:
        if self._stack.currentIndex() < self._stack.count() - 1:
            self._stack.setCurrentIndex(self._stack.currentIndex() + 1)
            self._update_nav()
        else:
            self.accept()

    def _update_nav(self) -> None:
        last = self._stack.currentIndex() == self._stack.count() - 1
        self.btn_back.setEnabled(self._stack.currentIndex() > 0)
        self.btn_next.setText("Finish" if last else "Next")

    # ---------------- actions ----------------
    def _load(self) -> None:
        # Credentials load themselves in CredentialsPanel.__init__; only the
        # default-axis combos are owned here.
        c = self.cfg
        _select(self.cmb_data_source, c.default_data_source)
        _select(self.cmb_authoring, c.default_query_authoring_mode)
        _select(self.cmb_scope, c.default_query_scope)

    # ---------------- read-out ----------------
    @property
    def want_csv_import(self) -> bool:
        return self.cb_import_csv.isChecked()

    def token(self) -> str:
        return self.creds.token()

    def raw_token(self) -> str:
        return self.creds.raw_token()

    def apply_to_config(self) -> AppConfig:
        """Fold widget values into the config and mark onboarding complete."""
        c = self.cfg
        self.creds.apply_to_config(c)  # base_url, deployment, email, remember
        c.default_data_source = self.cmb_data_source.currentData()
        c.default_query_authoring_mode = self.cmb_authoring.currentData()
        c.default_query_scope = self.cmb_scope.currentData()
        c.onboarded = True
        return c
