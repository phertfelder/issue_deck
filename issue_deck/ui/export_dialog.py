"""Export options dialog: pick a mode and shape the output.

Collects an :class:`~issue_deck.exporters.ExportConfig` plus the chosen export
*mode*, so the query tab can dispatch to the right writer. Qt-only glue — all the
actual export logic lives in the Qt-free :mod:`issue_deck.exporters` package.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..exporters import ExportConfig, redaction_preview
from ..exporters.options import GROUP_BY_LABELS, SORT_FIELDS
from ..schema import NormalizedIssue

# (mode key, human label). The four single-file modes reuse the legacy writers;
# the two pack modes use the LLM export/prompt packs.
EXPORT_MODES: list[tuple[str, str]] = [
    ("markdown_combined", "Single combined Markdown (.md)"),
    ("markdown_per_ticket", "Per-ticket Markdown (folder)"),
    ("jsonl", "JSONL (.jsonl)"),
    ("csv", "CSV (.csv)"),
    ("zip_pack", "LLM export pack (.zip)"),
    ("prompt_pack", "Prompt pack (.zip)"),
]


class ExportDialog(QDialog):
    """Modal export configurator. Read :attr:`mode` / :meth:`config` after ``exec()``."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        issues: Sequence[NormalizedIssue] = (),
        cfg: AppConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export…")
        self.setMinimumWidth(460)
        self._issues = list(issues)
        self._cfg = cfg
        self._build()
        if cfg is not None:
            self._apply_defaults(cfg)

    # ---- build ----
    def _build(self) -> None:
        outer = QVBoxLayout(self)

        # Mode
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Export as:"))
        self.cmb_mode = QComboBox()
        for key, label in EXPORT_MODES:
            self.cmb_mode.addItem(label, key)
        mode_row.addWidget(self.cmb_mode, 1)
        outer.addLayout(mode_row)

        outer.addWidget(self._content_group())
        outer.addWidget(self._redaction_group())
        outer.addWidget(self._organize_group())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _content_group(self) -> QGroupBox:
        box = QGroupBox("Content")
        form = QFormLayout(box)
        self.cb_comments = QCheckBox("Include comments")
        self.cb_comments.setChecked(True)
        form.addRow(self.cb_comments)

        self.sp_latest = QSpinBox()
        self.sp_latest.setRange(0, 999)
        self.sp_latest.setSpecialValueText("All")
        form.addRow("Latest N comments:", self.sp_latest)

        self.cb_descriptions = QCheckBox("Include descriptions")
        self.cb_descriptions.setChecked(True)
        form.addRow(self.cb_descriptions)

        self.sp_desc_chars = QSpinBox()
        self.sp_desc_chars.setRange(0, 100000)
        self.sp_desc_chars.setSpecialValueText("No limit")
        form.addRow("Truncate descriptions (chars):", self.sp_desc_chars)

        self.sp_comment_chars = QSpinBox()
        self.sp_comment_chars.setRange(0, 100000)
        self.sp_comment_chars.setSpecialValueText("No limit")
        form.addRow("Truncate comments (chars):", self.sp_comment_chars)

        self.cb_source_meta = QCheckBox("Include source metadata")
        self.cb_source_meta.setChecked(True)
        form.addRow(self.cb_source_meta)

        self.cb_query_meta = QCheckBox("Include filter/query metadata (manifest)")
        self.cb_query_meta.setChecked(True)
        form.addRow(self.cb_query_meta)

        self.cb_local_notes = QCheckBox("Include local notes (private — never sent to Jira)")
        self.cb_local_notes.setToolTip(
            "Fold your private per-issue notes/tags into the Markdown output, "
            "clearly labelled as local. Unavailable when redacting issue keys.")
        form.addRow(self.cb_local_notes)
        return box

    def _redaction_group(self) -> QGroupBox:
        box = QGroupBox("Redaction")
        lay = QVBoxLayout(box)
        self.cb_redact_keys = QCheckBox("Redact issue keys")
        self.cb_redact_people = QCheckBox("Redact people names")
        self.cb_redact_clients = QCheckBox("Redact client names")
        self.cb_redact_emails = QCheckBox("Redact email addresses")
        self.cb_redact_urls = QCheckBox("Redact URLs")
        for cb in (self.cb_redact_keys, self.cb_redact_people, self.cb_redact_clients,
                   self.cb_redact_emails, self.cb_redact_urls):
            lay.addWidget(cb)

        self.btn_preview = QPushButton("Preview redaction…")
        self.btn_preview.setToolTip(
            "Show a before/after sample of how the current redaction settings "
            "affect your issues, before exporting.")
        self.btn_preview.clicked.connect(self._preview_redaction)
        lay.addWidget(self.btn_preview)

        # Private notes and a share-safe redacted export are contradictory: turning
        # on key redaction greys out (and clears) the local-notes option.
        self.cb_redact_keys.toggled.connect(self._sync_local_notes_enabled)
        return box

    def _preview_redaction(self) -> None:
        from PyQt6.QtWidgets import QPlainTextEdit

        text = redaction_preview(self._issues, self.config())
        dlg = QDialog(self)
        dlg.setWindowTitle("Redaction preview")
        dlg.setMinimumSize(560, 420)
        lay = QVBoxLayout(dlg)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        lay.addWidget(view)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()

    def _organize_group(self) -> QGroupBox:
        box = QGroupBox("Organize")
        form = QFormLayout(box)
        self.cmb_group = QComboBox()
        self.cmb_group.addItem("None", "")
        for key, label in GROUP_BY_LABELS.items():
            self.cmb_group.addItem(label, key)
        form.addRow("Group by:", self.cmb_group)

        self.cmb_sort = QComboBox()
        for key in SORT_FIELDS:
            self.cmb_sort.addItem(key.title(), key)
        form.addRow("Sort by:", self.cmb_sort)

        self.cb_sort_desc = QCheckBox("Descending")
        self.cb_sort_desc.setChecked(True)
        form.addRow("", self.cb_sort_desc)
        return box

    def _apply_defaults(self, cfg: AppConfig) -> None:
        """Pre-select the redaction defaults configured in Settings."""
        self.cb_redact_keys.setChecked(cfg.export_redact_keys)
        self.cb_redact_people.setChecked(cfg.export_redact_people)
        self.cb_redact_clients.setChecked(cfg.export_redact_clients)
        self.cb_redact_emails.setChecked(cfg.export_redact_emails)
        self.cb_redact_urls.setChecked(cfg.export_redact_urls)
        self._sync_local_notes_enabled()

    def _sync_local_notes_enabled(self, *_: object) -> None:
        redacting = self.cb_redact_keys.isChecked()
        self.cb_local_notes.setEnabled(not redacting)
        if redacting:
            self.cb_local_notes.setChecked(False)

    # ---- read-out ----
    @property
    def mode(self) -> str:
        return self.cmb_mode.currentData()

    def config(self) -> ExportConfig:
        return ExportConfig(
            include_comments=self.cb_comments.isChecked(),
            latest_comments=self.sp_latest.value(),
            include_descriptions=self.cb_descriptions.isChecked(),
            redact_keys=self.cb_redact_keys.isChecked(),
            redact_people=self.cb_redact_people.isChecked(),
            redact_clients=self.cb_redact_clients.isChecked(),
            redact_emails=self.cb_redact_emails.isChecked(),
            redact_urls=self.cb_redact_urls.isChecked(),
            max_description_chars=self.sp_desc_chars.value(),
            max_comment_chars=self.sp_comment_chars.value(),
            include_source_metadata=self.cb_source_meta.isChecked(),
            include_query_metadata=self.cb_query_meta.isChecked(),
            include_local_notes=self.cb_local_notes.isChecked(),
            group_by=self.cmb_group.currentData(),
            sort_by=self.cmb_sort.currentData(),
            sort_desc=self.cb_sort_desc.isChecked(),
        )
