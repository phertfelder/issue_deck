"""JQL helper panel — deterministic (non-LLM) query assistance.

Lets a user start from a template or the current builder filters, toggle
individual clauses on/off, read the query back in plain English, see a
broad-query warning, validate the JQL against Jira (``maxResults=1``), save
custom templates, and push the result back to the builder's raw-JQL box (so it
fetches and rides along in export packs).
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..config import AppConfig
from ..jql_helper import (
    HelperClause,
    JqlTemplateStore,
    all_templates,
    breadth_warnings,
    decompose,
    explain,
    render,
)
from ..models import SearchFilters
from .workers import ValidateJqlWorker


class JqlHelperDialog(QDialog):
    def __init__(self, parent=None, *, cfg: AppConfig, filters: SearchFilters,
                 client_provider: Callable[[], object],
                 template_store: JqlTemplateStore):
        super().__init__(parent)
        self.setWindowTitle("JQL helper")
        self.resize(640, 620)
        self.cfg = cfg
        self._client_provider = client_provider
        self.store = template_store
        # The SearchFilters currently driving the clause list (from the builder
        # snapshot or an applied template); what "Save as template" persists.
        self._filters = filters
        self._clauses: list[HelperClause] = []
        self._result_jql: str | None = None
        self._val_thread: QThread | None = None
        self._val_worker: ValidateJqlWorker | None = None
        self._build()
        self._reload_templates_combo()
        self._set_clauses(decompose(cfg, filters))

    # ---- result accessor for the caller ----
    def result_jql(self) -> str | None:
        """The JQL the user chose to send back (None unless they clicked Use)."""
        return self._result_jql

    # ================= build =================
    def _build(self) -> None:
        v = QVBoxLayout(self)

        # templates row
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Template:"))
        self.cmb_templates = QComboBox()
        self.cmb_templates.setMinimumWidth(240)
        trow.addWidget(self.cmb_templates, 1)
        btn_apply = QPushButton("Apply")
        btn_apply.clicked.connect(self._apply_template)
        trow.addWidget(btn_apply)
        self.btn_del_template = QPushButton("Delete")
        self.btn_del_template.clicked.connect(self._delete_template)
        trow.addWidget(self.btn_del_template)
        v.addLayout(trow)
        self.lbl_template_desc = QLabel("")
        self.lbl_template_desc.setStyleSheet("color: palette(mid);")
        self.lbl_template_desc.setWordWrap(True)
        v.addWidget(self.lbl_template_desc)
        self.cmb_templates.currentIndexChanged.connect(self._show_template_desc)

        # clauses list (toggleable)
        v.addWidget(QLabel("Clauses (uncheck to drop one from the query):"))
        self.lst_clauses = QListWidget()
        self.lst_clauses.itemChanged.connect(self._on_clause_toggled)
        v.addWidget(self.lst_clauses, 1)

        # generated JQL
        v.addWidget(QLabel("Generated JQL:"))
        self.jql_view = QPlainTextEdit()
        self.jql_view.setReadOnly(True)
        self.jql_view.setMaximumHeight(72)
        v.addWidget(self.jql_view)

        # plain-English explanation
        self.lbl_explain = QLabel("")
        self.lbl_explain.setWordWrap(True)
        self.lbl_explain.setStyleSheet("color: palette(text);")
        v.addWidget(self.lbl_explain)

        # broad-query warning
        self.lbl_warn = QLabel("")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet("color: #c0392b;")
        v.addWidget(self.lbl_warn)

        # validation row
        vrow = QHBoxLayout()
        self.btn_validate = QPushButton("Validate against Jira")
        self.btn_validate.clicked.connect(self._validate)
        vrow.addWidget(self.btn_validate)
        self.lbl_validate = QLabel("")
        self.lbl_validate.setWordWrap(True)
        vrow.addWidget(self.lbl_validate, 1)
        v.addLayout(vrow)

        # actions
        arow = QHBoxLayout()
        btn_save = QPushButton("Save as template…")
        btn_save.clicked.connect(self._save_template)
        arow.addWidget(btn_save)
        arow.addStretch()
        btn_copy = QPushButton("Copy JQL")
        btn_copy.clicked.connect(self._copy_jql)
        arow.addWidget(btn_copy)
        btn_use = QPushButton("Use in builder")
        btn_use.setToolTip("Send this JQL to the builder's raw-JQL box and close.")
        btn_use.clicked.connect(self._use)
        arow.addWidget(btn_use)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        arow.addWidget(btn_close)
        v.addLayout(arow)

    # ================= templates =================
    def _reload_templates_combo(self) -> None:
        self.cmb_templates.blockSignals(True)
        self.cmb_templates.clear()
        for t in all_templates(self.store):
            suffix = "" if t.builtin else "  (custom)"
            self.cmb_templates.addItem(t.name + suffix, t)
        self.cmb_templates.blockSignals(False)
        self._show_template_desc()

    def _current_template(self):
        return self.cmb_templates.currentData()

    def _show_template_desc(self, *_: object) -> None:
        t = self._current_template()
        self.lbl_template_desc.setText(t.description if t else "")
        self.btn_del_template.setEnabled(bool(t and not t.builtin))

    def _apply_template(self) -> None:
        t = self._current_template()
        if t is None:
            return
        self._filters = t.clone_filters()
        self._set_clauses(decompose(self.cfg, self._filters))

    def _save_template(self) -> None:
        name, ok = QInputDialog.getText(self, "Save template", "Template name:")
        if not ok or not name.strip():
            return
        if name.strip() in self.store:
            if QMessageBox.question(
                self, "Overwrite?",
                f"A template named '{name.strip()}' exists. Overwrite it?"
            ) != QMessageBox.StandardButton.Yes:
                return
        try:
            self.store.save(name.strip(), self._filters)
        except ValueError as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return
        self._reload_templates_combo()
        idx = self.cmb_templates.findText(name.strip() + "  (custom)")
        if idx >= 0:
            self.cmb_templates.setCurrentIndex(idx)

    def _delete_template(self) -> None:
        t = self._current_template()
        if t is None or t.builtin:
            return
        if QMessageBox.question(self, "Delete template",
                                f"Delete custom template '{t.name}'?") \
                == QMessageBox.StandardButton.Yes:
            self.store.delete(t.name)
            self._reload_templates_combo()

    # ================= clauses =================
    def _set_clauses(self, clauses: list[HelperClause]) -> None:
        self._clauses = clauses
        self.lst_clauses.blockSignals(True)
        self.lst_clauses.clear()
        for c in clauses:
            item = QListWidgetItem(f"{c.label}:  {c.text}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if c.enabled else Qt.CheckState.Unchecked)
            item.setToolTip(c.explain)
            self.lst_clauses.addItem(item)
        self.lst_clauses.blockSignals(False)
        self._recompute()

    def _on_clause_toggled(self, item: QListWidgetItem) -> None:
        row = self.lst_clauses.row(item)
        if 0 <= row < len(self._clauses):
            self._clauses[row].enabled = item.checkState() == Qt.CheckState.Checked
        self._recompute()

    def _recompute(self) -> None:
        jql = render(self._clauses)
        self.jql_view.setPlainText(jql)
        self.lbl_explain.setText(explain(self._clauses))
        warnings = breadth_warnings(self._clauses)
        self.lbl_warn.setText("\n".join("⚠ " + w for w in warnings))
        # A fresh query invalidates any previous validation result.
        self.lbl_validate.setText("")

    # ================= validation =================
    def _validate(self) -> None:
        jql = self.jql_view.toPlainText().strip()
        if not jql:
            self.lbl_validate.setText("Nothing to validate.")
            return
        try:
            client = self._client_provider()
        except Exception as e:  # noqa: BLE001 - no/invalid connection
            self.lbl_validate.setStyleSheet("color: #c0392b;")
            self.lbl_validate.setText(f"Can't reach Jira: {e}")
            return
        self.btn_validate.setEnabled(False)
        self.lbl_validate.setStyleSheet("color: palette(mid);")
        self.lbl_validate.setText("Validating…")
        self._val_thread = QThread()
        self._val_worker = ValidateJqlWorker(client, self.cfg, jql)
        self._val_worker.moveToThread(self._val_thread)
        self._val_thread.started.connect(self._val_worker.run)
        # The handlers run on the main thread and quit the worker loop directly,
        # so a caller can safely thread.wait() without a queued-quit deadlock.
        self._val_worker.finished.connect(self._on_validated)
        self._val_worker.failed.connect(self._on_validate_failed)
        self._val_thread.finished.connect(self._clear_validation_thread)
        self._val_thread.start()

    def _clear_validation_thread(self) -> None:
        """Drop references once the worker thread's event loop has stopped."""
        self._val_worker = None
        self._val_thread = None

    def _stop_validation_thread(self) -> None:
        """Quit and wait the validation thread so it never outlives the dialog."""
        thread = self._val_thread
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._stop_validation_thread()
        super().closeEvent(event)

    def reject(self) -> None:
        self._stop_validation_thread()
        super().reject()

    def accept(self) -> None:
        self._stop_validation_thread()
        super().accept()

    def _on_validated(self, result) -> None:
        if self._val_thread is not None:
            self._val_thread.quit()
        self.btn_validate.setEnabled(True)
        color = "#27ae60" if result.ok else "#c0392b"
        self.lbl_validate.setStyleSheet(f"color: {color};")
        self.lbl_validate.setText(("✓ " if result.ok else "✗ ") + result.message)

    def _on_validate_failed(self, msg: str) -> None:
        if self._val_thread is not None:
            self._val_thread.quit()
        self.btn_validate.setEnabled(True)
        self.lbl_validate.setStyleSheet("color: #c0392b;")
        self.lbl_validate.setText(f"✗ {msg}")

    # ================= output =================
    def _copy_jql(self) -> None:
        from PyQt6.QtWidgets import QApplication
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.jql_view.toPlainText())

    def _use(self) -> None:
        self._result_jql = self.jql_view.toPlainText().strip()
        self.accept()
