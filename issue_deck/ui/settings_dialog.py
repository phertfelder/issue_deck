"""Settings dialog: preference defaults, redaction defaults, and stored data.

Connection *credentials* are no longer edited here — they live in the single
credential surface (the Settings page's Connection card, and first-run
onboarding). This dialog owns everything else (timeout, issue cap, export
folder, comments defaults, redaction defaults, authoring choices), persisted to
:class:`~issue_deck.config.AppConfig`. Saved views and CSV import profiles are
managed (list / delete) against their on-disk stores.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import constants
from ..comments import CommentsMode
from ..config import AppConfig
from ..views import SavedViewStore

_DATA_SOURCE_LABELS = {
    "api": "Jira API first", "csv": "CSV import first", "ask": "Ask every time"}
_AUTHORING_LABELS = {
    "structured": "Structured filters", "raw": "Raw JQL", "last_used": "Remember last used"}
_SCOPE_LABELS = {
    "assigned_to_me": "Assigned to me", "reported_by_me": "Reported by me",
    "project": "Project / team scoped", "recent": "Unresolved / recently updated"}
_COMMENTS_LABELS = {
    CommentsMode.ALL: "All comments", CommentsMode.NONE: "No comments",
    CommentsMode.LATEST: "Latest N", CommentsMode.SINCE: "Since date"}


def _combo(labels: dict) -> QComboBox:
    cmb = QComboBox()
    for value, label in labels.items():
        cmb.addItem(label, value)
    return cmb


def _select(cmb: QComboBox, value: object) -> None:
    idx = cmb.findData(value)
    if idx >= 0:
        cmb.setCurrentIndex(idx)


class SettingsDialog(QDialog):
    """Modal preferences editor. Call :meth:`exec`; on Accept the cfg is saved."""

    def __init__(self, parent: QWidget | None = None, *, cfg: AppConfig,
                 views: SavedViewStore | None = None,
                 profiles_dir: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        self.cfg = cfg
        self._views = views if views is not None else SavedViewStore()
        self._profiles_dir = Path(profiles_dir) if profiles_dir is not None \
            else (constants.APP_DIR / "csv_profiles")

        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._fetch_tab(), "Fetch && comments")
        tabs.addTab(self._redaction_tab(), "Redaction defaults")
        tabs.addTab(self._data_tab(), "Views && profiles")
        outer.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._load()

    # ---------------- tabs ----------------
    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.sp_timeout = QSpinBox()
        self.sp_timeout.setRange(5, 600)
        form.addRow("Request timeout (s):", self.sp_timeout)

        self.sp_cap = QSpinBox()
        self.sp_cap.setRange(0, 1_000_000)
        self.sp_cap.setSpecialValueText("No cap")
        form.addRow("Issue cap (max fetched):", self.sp_cap)

        folder_row = QHBoxLayout()
        self.ed_export_folder = QLineEdit()
        self.ed_export_folder.setPlaceholderText("Remembered export destination")
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse_export_folder)
        folder_row.addWidget(self.ed_export_folder, 1)
        folder_row.addWidget(btn_browse)
        fw = QWidget()
        fw.setLayout(folder_row)
        form.addRow("Default export folder:", fw)

        self.cmb_data_source = _combo(_DATA_SOURCE_LABELS)
        form.addRow("Default data source:", self.cmb_data_source)
        self.cmb_authoring = _combo(_AUTHORING_LABELS)
        form.addRow("Default query authoring mode:", self.cmb_authoring)
        self.cmb_scope = _combo(_SCOPE_LABELS)
        form.addRow("Default query scope:", self.cmb_scope)
        return w

    def _fetch_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.cmb_comments = _combo(_COMMENTS_LABELS)
        form.addRow("Comments mode:", self.cmb_comments)
        self.sp_latest = QSpinBox()
        self.sp_latest.setRange(1, 999)
        form.addRow("Latest N comments:", self.sp_latest)
        self.ed_since = QLineEdit()
        self.ed_since.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Comments since (date):", self.ed_since)
        return w

    def _redaction_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Defaults pre-selected in the export dialog:"))
        self.cb_redact_keys = QCheckBox("Redact issue keys")
        self.cb_redact_people = QCheckBox("Redact people names")
        self.cb_redact_clients = QCheckBox("Redact client names")
        self.cb_redact_emails = QCheckBox("Redact email addresses")
        self.cb_redact_urls = QCheckBox("Redact URLs")
        for cb in (self.cb_redact_keys, self.cb_redact_people, self.cb_redact_clients,
                   self.cb_redact_emails, self.cb_redact_urls):
            lay.addWidget(cb)
        lay.addStretch(1)
        return w

    def _data_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Saved views:"))
        self.lst_views = QListWidget()
        lay.addWidget(self.lst_views, 1)
        vrow = QHBoxLayout()
        btn_rename = QPushButton("Rename…")
        btn_rename.clicked.connect(self._rename_view)
        btn_del_view = QPushButton("Delete")
        btn_del_view.clicked.connect(self._delete_view)
        vrow.addWidget(btn_rename)
        vrow.addWidget(btn_del_view)
        vrow.addStretch(1)
        lay.addLayout(vrow)

        lay.addWidget(QLabel("CSV import profiles:"))
        self.lst_profiles = QListWidget()
        lay.addWidget(self.lst_profiles, 1)
        prow = QHBoxLayout()
        btn_del_profile = QPushButton("Delete")
        btn_del_profile.clicked.connect(self._delete_profile)
        prow.addWidget(btn_del_profile)
        prow.addStretch(1)
        lay.addLayout(prow)
        return w

    # ---------------- load / save ----------------
    def _load(self) -> None:
        c = self.cfg
        self.sp_timeout.setValue(c.request_timeout or 60)
        self.sp_cap.setValue(c.max_issues or 0)
        self.ed_export_folder.setText(c.default_export_folder)
        _select(self.cmb_data_source, c.default_data_source)
        _select(self.cmb_authoring, c.default_query_authoring_mode)
        _select(self.cmb_scope, c.default_query_scope)

        try:
            mode = CommentsMode(c.comments_mode) if c.comments_mode else CommentsMode.ALL
        except ValueError:
            mode = CommentsMode.ALL
        _select(self.cmb_comments, mode)
        self.sp_latest.setValue(c.comments_latest_n or 5)
        self.ed_since.setText(c.comments_since)

        self.cb_redact_keys.setChecked(c.export_redact_keys)
        self.cb_redact_people.setChecked(c.export_redact_people)
        self.cb_redact_clients.setChecked(c.export_redact_clients)
        self.cb_redact_emails.setChecked(c.export_redact_emails)
        self.cb_redact_urls.setChecked(c.export_redact_urls)

        self._refresh_views()
        self._refresh_profiles()

    def apply_to_config(self) -> AppConfig:
        """Fold widget values into the config (does not persist)."""
        c = self.cfg
        c.request_timeout = self.sp_timeout.value()
        c.max_issues = self.sp_cap.value()
        c.default_export_folder = self.ed_export_folder.text().strip()
        c.default_data_source = self.cmb_data_source.currentData()
        c.default_query_authoring_mode = self.cmb_authoring.currentData()
        c.default_query_scope = self.cmb_scope.currentData()
        c.comments_mode = self.cmb_comments.currentData().value
        c.comments_latest_n = self.sp_latest.value()
        c.comments_since = self.ed_since.text().strip()
        c.export_redact_keys = self.cb_redact_keys.isChecked()
        c.export_redact_people = self.cb_redact_people.isChecked()
        c.export_redact_clients = self.cb_redact_clients.isChecked()
        c.export_redact_emails = self.cb_redact_emails.isChecked()
        c.export_redact_urls = self.cb_redact_urls.isChecked()
        return c

    def _on_accept(self) -> None:
        self.apply_to_config()  # preference defaults only; credentials live elsewhere
        self.cfg.save()
        self.accept()

    # ---------------- actions ----------------
    def _browse_export_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Default export folder", self.ed_export_folder.text().strip())
        if d:
            self.ed_export_folder.setText(d)

    def _refresh_views(self) -> None:
        self.lst_views.clear()
        for name in sorted(self._views.names()):
            self.lst_views.addItem(name)

    def _selected_view(self) -> str:
        item = self.lst_views.currentItem()
        return item.text() if item else ""

    def _delete_view(self) -> None:
        name = self._selected_view()
        if not name:
            return
        if QMessageBox.question(self, "Delete view", f"Delete saved view {name!r}?") \
                == QMessageBox.StandardButton.Yes:
            self._views.delete(name)
            self._refresh_views()

    def _rename_view(self) -> None:
        name = self._selected_view()
        if not name:
            return
        new, ok = QInputDialog.getText(self, "Rename view", "New name:", text=name)
        new = new.strip()
        if not ok or not new or new == name:
            return
        try:
            self._views.rename(name, new)
        except ValueError as e:
            QMessageBox.warning(self, "Rename failed", str(e))
            return
        self._refresh_views()

    def _refresh_profiles(self) -> None:
        self.lst_profiles.clear()
        if self._profiles_dir.exists():
            for path in sorted(self._profiles_dir.glob("*.json")):
                self.lst_profiles.addItem(path.name)

    def _delete_profile(self) -> None:
        item = self.lst_profiles.currentItem()
        if not item:
            return
        path = self._profiles_dir / item.text()
        if QMessageBox.question(self, "Delete profile", f"Delete import profile {item.text()!r}?") \
                == QMessageBox.StandardButton.Yes:
            try:
                path.unlink()
            except OSError as e:
                QMessageBox.warning(self, "Delete failed", str(e))
                return
            self._refresh_profiles()
