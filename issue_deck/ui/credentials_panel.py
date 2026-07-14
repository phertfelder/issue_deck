"""Reusable credential editor shared by the Connection tab and the Settings dialog.

Owns the connection widgets (URL, deployment, email, token, remember, custom-field
ids) plus Test connection / Discover fields / Forget token. It does **not** own the
request timeout — that belongs to the container — and it never persists on its own;
callers apply it to a config and then call :func:`persist_token`, the single place
that handles the plaintext-storage confirmation.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QWidget,
)

from ..config import AppConfig
from ..credentials import _HAS_KEYRING, PLAINTEXT
from ..jira_client import make_client


def persist_token(cfg: AppConfig, raw_token: str, *, parent: QWidget | None,
                  announce: bool = True) -> bool:
    """Persist ``cfg`` + its token, confirming before any plaintext write.

    ``cfg`` must already have its credential fields applied (incl. ``remember_token``).
    Returns ``False`` iff the user declined a plaintext write — the caller should
    then abort (leave the dialog open). This is the *only* token-write path in the UI.
    """
    from ..constants import CONFIG_PATH

    if cfg.remember_token and raw_token and not _HAS_KEYRING:
        resp = QMessageBox.warning(
            parent, "Store token as plaintext?",
            "No OS keychain is available. If you continue, your token will be "
            "saved as plaintext in a local file (permissions 0600 where "
            "supported). It is never written to config.json, exports, or logs.\n\n"
            "Store the token anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if resp != QMessageBox.StandardButton.Yes:
            return False
    cfg.save()
    backend = cfg.save_token(raw_token)
    if announce:
        note = "\nToken stored as plaintext (0600)." if backend == PLAINTEXT else ""
        QMessageBox.information(parent, "Saved", f"Settings saved to {CONFIG_PATH}{note}")
    return True


class CredentialsPanel(QWidget):
    """The connection-credentials editor, minus the request timeout.

    ``show_custom_fields`` / ``show_forget`` let a caller present a trimmed
    surface — first-run onboarding reuses this exact widget but hides the
    custom-field IDs (spec: no field IDs at first run) and the Forget action.
    """

    # Emitted with a live JiraClient after a successful "Test connection".
    connected = pyqtSignal(object)

    def __init__(self, cfg: AppConfig, *, show_custom_fields: bool = True,
                 show_forget: bool = True) -> None:
        super().__init__()
        self.cfg = cfg
        self._show_custom_fields = show_custom_fields
        self._show_forget = show_forget
        self._build()
        self.load_config(cfg)

    # ---------- build ----------
    def _build(self) -> None:
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        r = 0
        g.addWidget(QLabel("Base URL:"), r, 0)
        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText("https://yourco.atlassian.net  or  https://jira.yourco.com")
        g.addWidget(self.ed_url, r, 1, 1, 3)
        r += 1

        g.addWidget(QLabel("Deployment:"), r, 0)
        self.rb_cloud = QRadioButton("Cloud (API token)")
        self.rb_server = QRadioButton("Server / Data Center (PAT)")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_cloud)
        grp.addButton(self.rb_server)
        row = QHBoxLayout()
        row.addWidget(self.rb_cloud)
        row.addWidget(self.rb_server)
        row.addStretch()
        rw = QWidget()
        rw.setLayout(row)
        g.addWidget(rw, r, 1, 1, 3)
        r += 1
        self.rb_cloud.toggled.connect(self._toggle_email)

        self.lbl_email = QLabel("Email (Cloud only):")
        g.addWidget(self.lbl_email, r, 0)
        self.ed_email = QLineEdit()
        self.ed_email.setPlaceholderText("you@company.com")
        g.addWidget(self.ed_email, r, 1, 1, 3)
        r += 1

        g.addWidget(QLabel("Token:"), r, 0)
        self.ed_token = QLineEdit()
        self.ed_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_token.setPlaceholderText("API token (Cloud) or Personal Access Token (Server)")
        g.addWidget(self.ed_token, r, 1, 1, 2)
        self.cb_remember = QCheckBox(
            "Remember" + (" (keychain)" if _HAS_KEYRING else " (plaintext)"))
        if not _HAS_KEYRING:
            self.cb_remember.setToolTip(
                "No OS keychain is available, so a remembered token is stored as "
                "plaintext (file permissions 0600 where supported). You will be "
                "warned before it is written.")
        g.addWidget(self.cb_remember, r, 3)
        r += 1

        # Keychain badge: where a remembered token would actually be stored.
        self.lbl_keychain = QLabel(
            "🔒 Keychain" if _HAS_KEYRING else "⚠ Plaintext (0600)")
        self.lbl_keychain.setToolTip(
            "Remembered tokens are stored in your OS keychain." if _HAS_KEYRING
            else "No OS keychain is available, so a remembered token is stored "
                 "as a plaintext file (permissions 0600 where supported). It is "
                 "never written to config.json, exports, or logs.")
        g.addWidget(self.lbl_keychain, r, 0)
        # Custom-field widgets are always constructed (so load/apply stay simple)
        # but only shown when this panel is the full editor.
        self.ed_client_field = QLineEdit()
        self.ed_client_field.setPlaceholderText("customfield_10050")
        self.ed_sev_field = QLineEdit()
        self.ed_sev_field.setPlaceholderText("customfield_10060")
        self.btn_discover = QPushButton("Discover fields…")
        self.btn_discover.clicked.connect(self._discover_fields)
        if self._show_forget:
            self.btn_forget = QPushButton("Forget token")
            self.btn_forget.setToolTip(
                "Delete the stored token from the keychain / plaintext file and "
                "clear the field.")
            self.btn_forget.clicked.connect(self._forget_token)
            g.addWidget(self.btn_forget, r, 3)
        r += 1

        if self._show_custom_fields:
            box = QGroupBox("Custom field IDs (your instance — optional)")
            bl = QGridLayout(box)
            bl.addWidget(QLabel("Client field:"), 0, 0)
            bl.addWidget(self.ed_client_field, 0, 1)
            bl.addWidget(QLabel("Severity field:"), 1, 0)
            bl.addWidget(self.ed_sev_field, 1, 1)
            bl.addWidget(self.btn_discover, 0, 2, 2, 1)
            g.addWidget(box, r, 0, 1, 4)
            r += 1

        self.btn_test = QPushButton("Test connection")
        self.btn_test.clicked.connect(self._test_connection)
        g.addWidget(self.btn_test, r, 0)
        r += 1
        g.setRowStretch(r, 1)

    # ---------- cfg <-> ui ----------
    def _toggle_email(self) -> None:
        is_cloud = self.rb_cloud.isChecked()
        self.lbl_email.setVisible(is_cloud)
        self.ed_email.setVisible(is_cloud)

    def load_config(self, cfg: AppConfig) -> None:
        self.ed_url.setText(cfg.base_url)
        (self.rb_cloud if cfg.deployment == "cloud" else self.rb_server).setChecked(True)
        self.ed_email.setText(cfg.email)
        self.ed_client_field.setText(cfg.client_field)
        self.ed_sev_field.setText(cfg.severity_field)
        self.cb_remember.setChecked(cfg.remember_token)
        self.ed_token.setText(cfg.load_token() if cfg.remember_token else "")
        self._toggle_email()

    def apply_to_config(self, cfg: AppConfig) -> AppConfig:
        cfg.base_url = self.ed_url.text().strip()
        cfg.deployment = "cloud" if self.rb_cloud.isChecked() else "server"
        cfg.email = self.ed_email.text().strip()
        # Only fold custom-field IDs when this panel actually edits them, so a
        # trimmed surface (onboarding) never clobbers previously-saved IDs.
        if self._show_custom_fields:
            cfg.client_field = self.ed_client_field.text().strip()
            cfg.severity_field = self.ed_sev_field.text().strip()
        cfg.remember_token = self.cb_remember.isChecked()
        return cfg

    def token(self) -> str:
        return self.ed_token.text().strip()

    def raw_token(self) -> str:
        return self.ed_token.text()

    # ---------- actions ----------
    def _client(self):
        self.apply_to_config(self.cfg)
        return make_client(self.cfg, self.token())

    def _test_connection(self) -> None:
        try:
            client = self._client()
            who = client.whoami()
            name = who.get("displayName") or who.get("name") or "unknown"
            QMessageBox.information(self, "OK", f"Connected as: {name}")
            self.connected.emit(client)
        except Exception as e:  # noqa: BLE001 - surfaced to the user, presented typed
            from .error_dialog import show_error
            show_error(self, e, operation="connection")

    def _discover_fields(self) -> None:
        """Open the guided field-mapping modal (suggests roles → custom fields)."""
        from .field_mapping_dialog import FieldMappingDialog

        self.apply_to_config(self.cfg)  # so the sample query uses current settings
        dlg = FieldMappingDialog(self, cfg=self.cfg, client_provider=self._client)
        if dlg.exec():
            # Reflect any client/severity change the mapping saved back into the panel.
            self.ed_client_field.setText(self.cfg.client_field)
            self.ed_sev_field.setText(self.cfg.severity_field)

    def _forget_token(self) -> None:
        """Delete any stored token and clear the field (does not touch config.json)."""
        self.apply_to_config(self.cfg)
        self.cfg.clear_token()
        self.ed_token.clear()
        self.cb_remember.setChecked(False)
        self.cfg.remember_token = False
        QMessageBox.information(self, "Token forgotten", "The stored token was deleted.")
