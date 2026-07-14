"""Field-mapping modal: map workbench roles to instance custom fields.

Replaces the old "here are your field ids, copy one into a box" QMessageBox with
a guided table: per role (Client, Severity, Story Points, Sprint, Epic) it shows
the best-guess field (name + mono id), a confidence badge, the reason, and an
optional sample value — with an editable combo so a power user can still pick or
type any id. Saving writes the mapped ids to :class:`AppConfig`.

All suggestion logic is Qt-free (``services.field_mapping_service``); this file
only presents it and persists the chosen ids. Sample values are fetched via a
bounded, comment-free :class:`SampleWorker` on demand and are never persisted.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..jira_client import JiraClient
from ..models import JiraField
from ..services.field_mapping_service import ROLES, FieldMappingSuggestion, suggest_all
from ..services.field_service import list_fields
from .workers import SampleWorker

_BAND_COLORS = {"high": "#67c08a", "medium": "#e6a94b", "low": "#7d7566", "none": "#7d7566"}
_SAMPLE_CAP = 200


def _short(text: str, limit: int = 40) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class FieldMappingDialog(QDialog):
    """Modal that maps roles → custom-field ids and saves them to the config."""

    def __init__(self, parent: QWidget | None = None, *, cfg: AppConfig,
                 client_provider: Callable[[], JiraClient]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Map custom fields")
        self.setMinimumWidth(640)
        self.cfg = cfg
        self._client_provider = client_provider
        self._fields: list[JiraField] = []
        self._combos: dict[str, QComboBox] = {}
        self._badges: dict[str, QLabel] = {}
        self._reasons: dict[str, QLabel] = {}
        self._samples: dict[str, QLabel] = {}
        self._sample_thread: QThread | None = None
        self._sample_worker: SampleWorker | None = None

        self._build()
        self._refresh_fields()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(
            "<b>Map your instance's custom fields to workbench roles.</b><br>"
            "Pick the suggested field or choose another — no need to copy ids by hand."))

        body = QWidget()
        self._grid = QGridLayout(body)
        self._grid.setColumnStretch(1, 3)
        self._grid.setColumnStretch(3, 4)
        headers = ["Role", "Field", "Conf.", "Reason / sample"]
        for c, text in enumerate(headers):
            lbl = QLabel(text)
            lbl.setStyleSheet("color: palette(mid); font-weight: 600;")
            self._grid.addWidget(lbl, 0, c)

        for r, role in enumerate(ROLES, start=1):
            self._grid.addWidget(QLabel(f"<b>{role.label}</b>"), r, 0)
            combo = QComboBox()
            combo.setEditable(True)   # power users can type a raw id (advanced)
            combo.setMinimumWidth(240)
            self._combos[role.key] = combo
            self._grid.addWidget(combo, r, 1)
            badge = QLabel("—")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._badges[role.key] = badge
            self._grid.addWidget(badge, r, 2)
            reason = QLabel("")
            reason.setWordWrap(True)
            reason.setStyleSheet("color: palette(mid);")
            self._reasons[role.key] = reason
            sample = QLabel("")
            sample.setStyleSheet("color: palette(mid); font-family: Consolas, monospace;")
            self._samples[role.key] = sample
            cell = QVBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.addWidget(reason)
            cell.addWidget(sample)
            holder = QWidget()
            holder.setLayout(cell)
            self._grid.addWidget(holder, r, 3)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: palette(mid);")
        outer.addWidget(self.lbl_status)

        btn_refresh = QPushButton("Refresh fields")
        btn_refresh.clicked.connect(self._refresh_fields)
        self.btn_sample = QPushButton("Load sample values")
        self.btn_sample.clicked.connect(self._load_samples)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(btn_refresh, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.btn_sample, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ------------------------------------------------------------- field load
    def _refresh_fields(self) -> None:
        try:
            client = self._client_provider()
            self._fields = list_fields(client)
        except Exception as e:  # noqa: BLE001 - degrade to name-only, keep dialog open
            self.lbl_status.setText(f"Couldn't list fields: {e}")
            self._apply_suggestions([])
            return
        self.lbl_status.setText(f"{len(self._fields)} fields available.")
        self._apply_suggestions(suggest_all(self._fields))

    def _apply_suggestions(self, suggestions: list[FieldMappingSuggestion]) -> None:
        by_role = {s.role: s for s in suggestions}
        for role in ROLES:
            combo = self._combos[role.key]
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(none)", "")
            for fld in self._fields:
                combo.addItem(f"{fld.name}  ·  {fld.id}", fld.id)
            # Prefer an existing saved mapping, else the fresh suggestion.
            current = getattr(self.cfg, role.config_attr, "")
            sugg = by_role.get(role.key)
            target = current or (sugg.field_id if sugg else "")
            idx = combo.findData(target) if target else 0
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            if idx < 0 and target:            # a saved id not in the field list
                combo.setEditText(target)
            combo.blockSignals(False)
            self._set_badge(role.key, sugg)

    def _set_badge(self, role_key: str, sugg: FieldMappingSuggestion | None) -> None:
        badge = self._badges[role_key]
        reason = self._reasons[role_key]
        if sugg is None or not sugg.has_suggestion:
            badge.setText("—")
            badge.setStyleSheet(f"color: {_BAND_COLORS['none']};")
            reason.setText(sugg.reason if sugg else "")
            return
        badge.setText(f"{sugg.confidence}{'?' if sugg.ambiguous else ''}")
        badge.setStyleSheet(f"color: {_BAND_COLORS[sugg.band]}; font-weight: 600;")
        reason.setText(sugg.reason)

    # ------------------------------------------------------------- sampling
    def _selected_ids(self) -> dict[str, str]:
        """The field id chosen per role (selected item data or typed text)."""
        out: dict[str, str] = {}
        for role in ROLES:
            combo = self._combos[role.key]
            idx = combo.currentIndex()
            if idx >= 0 and combo.itemText(idx) == combo.currentText():
                out[role.key] = combo.itemData(idx) or ""
            else:                                   # manually typed id
                out[role.key] = combo.currentText().strip()
        return out

    def _load_samples(self) -> None:
        ids = tuple(fid for fid in self._selected_ids().values() if fid)
        if not ids:
            self.lbl_status.setText("Pick at least one field to sample.")
            return
        if QMessageBox.question(
                self, "Load sample values",
                f"Fetch up to {_SAMPLE_CAP} recent issues to show example values? "
                "This runs a bounded query and never loads comments.") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            client = self._client_provider()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Connection error", str(e))
            return
        self.btn_sample.setEnabled(False)
        self.lbl_status.setText("Sampling…")
        self._sample_thread = QThread()
        self._sample_worker = SampleWorker(
            client, self.cfg, "ORDER BY updated DESC", _SAMPLE_CAP, ids)
        self._sample_worker.moveToThread(self._sample_thread)
        self._sample_thread.started.connect(self._sample_worker.run)
        self._sample_worker.finished.connect(self._on_sampled)
        self._sample_worker.failed.connect(self._on_sample_failed)
        self._sample_worker.finished.connect(self._sample_thread.quit)
        self._sample_worker.failed.connect(self._sample_thread.quit)
        self._sample_thread.finished.connect(lambda: self.btn_sample.setEnabled(True))
        self._sample_thread.start()

    def _on_sampled(self, issues) -> None:
        self.lbl_status.setText(f"Sampled {len(issues)} issue(s).")
        samples = self._derive_samples(issues)
        chosen = self._selected_ids()
        for role in ROLES:
            fid = chosen.get(role.key, "")
            self._samples[role.key].setText(
                f"e.g. {samples[fid]}" if fid in samples else "")

    def _on_sample_failed(self, msg: str) -> None:
        # Sampling is best-effort: keep the name-based suggestions on failure.
        self.lbl_status.setText(f"Sampling failed: {msg}")

    @staticmethod
    def _derive_samples(issues) -> dict[str, str]:
        """First non-empty raw value seen per field id across the sample."""
        samples: dict[str, str] = {}
        for iss in issues:
            for fid, val in (getattr(iss, "raw_field_values", {}) or {}).items():
                if fid not in samples and val not in (None, "", [], {}):
                    samples[fid] = _short(str(val))
        return samples

    # ------------------------------------------------------------- save
    def _save(self) -> None:
        for role in ROLES:
            setattr(self.cfg, role.config_attr, self._selected_ids()[role.key])
        self.cfg.save()   # writes config.json only — never a token
        self.accept()
