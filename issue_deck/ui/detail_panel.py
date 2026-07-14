"""Issue detail side panel: full metadata, warnings, description, comments,
mapped custom fields, clipboard actions, and local (private) annotations.

The panel follows the results-table selection (via ``issueSelected``) and renders
the current issue as read-only rich text. When an
:class:`~issue_deck.annotations.AnnotationStore` is supplied it also exposes an
editor for the issue's **private local note and tags** — stored entirely locally,
keyed by issue key, and never written back to Jira. Editing an annotation never
touches the issue object, so fetched/imported data is never mutated.
"""

from __future__ import annotations

from html import escape
from typing import Any, Callable

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..annotations import ANNOTATION_TAGS, AnnotationStore
from ..exporters import issue_to_llm_context, issue_to_markdown
from ..markers import issue_warnings
from ..schema import NormalizedIssue

# Colour a warning by its kind so the panel reads at a glance.
_WARN_COLORS = {
    "high priority": "#c0392b",
    "blocked": "#c0392b",
    "overdue": "#c0392b",
    "missing owner": "#d35400",
    "missing estimate": "#d35400",
    "stale": "#7f8c8d",
}


def _warn_color(label: str) -> str:
    for kind, color in _WARN_COLORS.items():
        if label.startswith(kind):
            return color
    return "#7f8c8d"


def _text(value: str) -> str:
    """Escape and preserve line breaks for embedding in the HTML view."""
    return escape(value).replace("\n", "<br>")


def _render(issue: NormalizedIssue, field_names: dict[str, str]) -> str:
    warnings = issue_warnings(issue)
    warn_line = "&nbsp;&nbsp;".join(
        f"<span style='color:{_warn_color(w)};'>● {escape(w)}</span>" for w in warnings
    )

    def row(label: str, value: str) -> str:
        return (f"<tr><td valign='top'><b>{escape(label)}</b></td>"
                f"<td>{_text(value)}</td></tr>") if value else ""

    status = f"{issue.status} ({issue.status_category})" if issue.status_category else issue.status
    meta = "".join([
        row("Status", status),
        row("Type", issue.issue_type),
        row("Priority", issue.priority),
        row("Severity", issue.severity),
        row("Client", issue.client),
        row("Assignee", issue.assignee.name),
        row("Reporter", issue.reporter.name),
        row("Project", issue.project_name or issue.project_key),
        row("Epic", issue.epic_name or issue.epic_key),
        row("Sprint", ", ".join(issue.sprints)),
        row("Fix versions", ", ".join(issue.fix_versions)),
        row("Components", ", ".join(issue.components)),
        row("Labels", ", ".join(issue.labels)),
        row("Story points", "" if issue.story_points is None else str(issue.story_points)),
        row("Created", issue.created),
        row("Updated", issue.updated),
        row("Resolved", issue.resolved),
        row("Due", issue.due_date),
    ])

    parts = [
        f"<h2>{escape(issue.key)} — {escape(issue.summary)}</h2>",
        f"<p>{warn_line}</p>" if warn_line else "",
        f"<p><a href='{escape(issue.url)}'>{escape(issue.url)}</a></p>" if issue.url else "",
        f"<table cellspacing='4'>{meta}</table>",
        _custom_fields_table(issue, field_names),
        "<h3>Description</h3>",
        f"<p>{_text(issue.description) or '<i>(none)</i>'}</p>",
        "<h3>Comments</h3>",
    ]
    if not issue.comments:
        parts.append("<p><i>(none)</i></p>")
    for c in issue.comments:
        parts.append(
            f"<p><b>{escape(c.author)}</b> "
            f"<span style='color:#7f8c8d;'>{escape(c.created)}</span><br>"
            f"{_text(c.body)}</p>"
        )
    return "".join(p for p in parts if p)


def _custom_fields_table(issue: NormalizedIssue, field_names: dict[str, str]) -> str:
    """Render the mapped custom fields (``raw_field_values``) as a small table."""
    if not issue.raw_field_values:
        return ""
    rows = []
    for fid, value in issue.raw_field_values.items():
        label = field_names.get(fid, fid)
        rows.append(
            f"<tr><td valign='top'><b>{escape(str(label))}</b></td>"
            f"<td>{_text(_fmt_value(value))}</td></tr>")
    return "<h3>Custom fields</h3><table cellspacing='4'>" + "".join(rows) + "</table>"


def _fmt_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return "" if value is None else str(value)


class IssueDetailPanel(QWidget):
    """Read-only issue detail plus an optional local-annotation editor."""

    _PLACEHOLDER = "<p style='color:#7f8c8d;'>Select an issue to see its detail.</p>"

    # Emitted (with the issue key) whenever a local annotation is saved, so a host
    # can refresh anything that depends on annotations (e.g. a tag filter).
    annotationChanged = pyqtSignal(str)

    def __init__(
        self,
        annotation_store: AnnotationStore | None = None,
        field_names_provider: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        super().__init__()
        self._issue: NormalizedIssue | None = None
        self._store = annotation_store
        self._field_names_provider = field_names_provider
        self._note_dirty = False
        self._tag_boxes: dict[str, QCheckBox] = {}
        self._build()

    # ---- build ----
    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setHtml(self._PLACEHOLDER)
        v.addWidget(self.view, 1)

        v.addWidget(self._build_actions())
        if self._store is not None:
            v.addWidget(self._build_annotations())

    def _build_actions(self) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        self._buttons: list[QPushButton] = []
        for label, slot in [
            ("Open in browser", self._open),
            ("Copy URL", self._copy_url),
            ("Copy key", self._copy_key),
            ("Copy Markdown", self._copy_markdown),
            ("Copy LLM context", self._copy_llm_context),
        ]:
            b = QPushButton(label)
            b.setEnabled(False)
            b.clicked.connect(slot)
            row.addWidget(b)
            self._buttons.append(b)
        # The "Open in browser" button keeps its historical attribute name.
        self.btn_open = self._buttons[0]
        row.addStretch()
        return w

    def _build_annotations(self) -> QWidget:
        box = QGroupBox("Local notes (private — never sent to Jira)")
        g = QVBoxLayout(box)
        self.ed_note = QPlainTextEdit()
        self.ed_note.setPlaceholderText("Private notes for this issue…")
        self.ed_note.setMaximumHeight(90)
        self.ed_note.setEnabled(False)
        self.ed_note.textChanged.connect(self._on_note_edited)
        g.addWidget(self.ed_note)

        tags_row = QGridLayout()
        for i, tag in enumerate(ANNOTATION_TAGS):
            cb = QCheckBox(tag)
            cb.setEnabled(False)
            cb.toggled.connect(self._on_tag_toggled)
            self._tag_boxes[tag] = cb
            tags_row.addWidget(cb, i // 3, i % 3)
        tw = QWidget()
        tw.setLayout(tags_row)
        g.addWidget(tw)

        save_row = QHBoxLayout()
        self.btn_save_note = QPushButton("Save note")
        self.btn_save_note.setEnabled(False)
        self.btn_save_note.clicked.connect(self._save_annotation)
        save_row.addWidget(self.btn_save_note)
        self.lbl_note_status = QLabel("")
        self.lbl_note_status.setStyleSheet("color: palette(mid);")
        save_row.addWidget(self.lbl_note_status)
        save_row.addStretch()
        sw = QWidget()
        sw.setLayout(save_row)
        g.addWidget(sw)
        return box

    # ---- selection ----
    def show_issue(self, issue: NormalizedIssue | None) -> None:
        # Persist any pending edits for the outgoing issue before switching.
        self._flush_annotation()
        self._issue = issue
        for b in getattr(self, "_buttons", []):
            b.setEnabled(bool(issue))
        self._buttons[0].setEnabled(bool(issue and issue.url))  # Open needs a URL
        self.view.setHtml(_render(issue, self._field_names()) if issue else self._PLACEHOLDER)
        if self._store is not None:
            self._load_annotation(issue)

    def _field_names(self) -> dict[str, str]:
        if self._field_names_provider is None:
            return {}
        try:
            return self._field_names_provider() or {}
        except Exception:  # noqa: BLE001 - name resolution is best-effort
            return {}

    # ---- annotations ----
    def _load_annotation(self, issue: NormalizedIssue | None) -> None:
        ann = self._store.get_or_empty(issue.key) if (issue and issue.key) else None
        editable = bool(issue and issue.key)
        # Block signals so populating the widgets doesn't mark them dirty.
        self.ed_note.blockSignals(True)
        self.ed_note.setPlainText(ann.note if ann else "")
        self.ed_note.setEnabled(editable)
        self.ed_note.blockSignals(False)
        active = set(ann.tags) if ann else set()
        for tag, cb in self._tag_boxes.items():
            cb.blockSignals(True)
            cb.setChecked(tag in active)
            cb.setEnabled(editable)
            cb.blockSignals(False)
        self.btn_save_note.setEnabled(editable)
        self._note_dirty = False
        self.lbl_note_status.setText("")

    def _on_note_edited(self) -> None:
        self._note_dirty = True

    def _on_tag_toggled(self, _checked: bool) -> None:
        # Tag changes persist immediately (also flushing any note edit).
        self._save_annotation()

    def _save_annotation(self) -> None:
        if self._store is None or self._issue is None or not self._issue.key:
            return
        tags = [t for t, cb in self._tag_boxes.items() if cb.isChecked()]
        self._store.set(self._issue.key, note=self.ed_note.toPlainText(), tags=tags)
        self._note_dirty = False
        self.lbl_note_status.setText("Saved.")
        self.annotationChanged.emit(self._issue.key)

    def _flush_annotation(self) -> None:
        if self._store is not None and self._issue is not None and self._note_dirty:
            self._save_annotation()

    # ---- clipboard / browser actions ----
    def _open(self) -> None:
        if self._issue and self._issue.url:
            QDesktopServices.openUrl(QUrl(self._issue.url))

    @staticmethod
    def _to_clipboard(text: str) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(text)

    def _copy_url(self) -> None:
        if self._issue:
            self._to_clipboard(self._issue.url)

    def _copy_key(self) -> None:
        if self._issue:
            self._to_clipboard(self._issue.key)

    def _copy_markdown(self) -> None:
        if self._issue:
            self._to_clipboard(issue_to_markdown(self._issue))

    def _copy_llm_context(self) -> None:
        if not self._issue:
            return
        ann = self._store.get(self._issue.key) if self._store else None
        self._to_clipboard(
            issue_to_llm_context(
                self._issue, ann, include_notes=bool(ann and ann.has_content)))
