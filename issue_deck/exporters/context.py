"""LLM-context blocks and local-note rendering for a single issue.

Two small, pure renderers used by the detail panel (clipboard actions) and the
export pipeline:

* :func:`render_note_block` turns an :class:`~issue_deck.annotations.Annotation`
  into a Markdown block that is **explicitly labelled private / local** so it can
  never be mistaken for data that came from Jira.
* :func:`issue_to_llm_context` produces a compact, self-describing context block
  suitable for pasting straight into an LLM prompt, optionally folding in the
  local note.

Kept Qt-free so both the UI and the exporters can share them.
"""

from __future__ import annotations

from ..annotations import Annotation
from ..markers import issue_warnings
from ..schema import NormalizedIssue

__all__ = ["render_note_block", "issue_to_llm_context", "LOCAL_NOTE_HEADING"]

# The exact wording used everywhere a local note is surfaced, so the "these are
# private and never came from / went to Jira" caveat is impossible to miss.
LOCAL_NOTE_HEADING = "Local notes (private — user-authored, never sent to Jira)"


def render_note_block(annotation: Annotation | None, *, level: int = 3) -> str:
    """Render a local note + tags as a labelled Markdown block (``""`` if empty)."""
    if annotation is None or not annotation.has_content:
        return ""
    hashes = "#" * max(1, level)
    lines = [f"{hashes} 🔒 {LOCAL_NOTE_HEADING}", ""]
    if annotation.tags:
        lines.append(f"- **Tags:** {', '.join(annotation.tags)}")
    if annotation.note.strip():
        if annotation.tags:
            lines.append("")
        lines.append(annotation.note.strip())
    return "\n".join(lines).rstrip() + "\n"


def issue_to_llm_context(
    issue: NormalizedIssue,
    annotation: Annotation | None = None,
    *,
    include_notes: bool = False,
    include_comments: bool = True,
) -> str:
    """A compact, LLM-ready context block for one issue.

    Includes the headline metadata, any attention warnings, the description, and
    (optionally) comments. When ``include_notes`` is set and ``annotation`` has
    content, the private local note is appended under a clearly-labelled section.
    """
    header = f"{issue.key}: {issue.summary}".strip(": ").strip()
    lines = [f"# {header}" if header else "# (untitled issue)", ""]

    meta: list[str] = []
    def add(label: str, value: str) -> None:
        if value:
            meta.append(f"- {label}: {value}")

    add("URL", issue.url)
    status = f"{issue.status} ({issue.status_category})" if issue.status_category else issue.status
    add("Status", status)
    add("Type", issue.issue_type)
    add("Priority", issue.priority)
    add("Severity", issue.severity)
    add("Client", issue.client)
    add("Assignee", issue.assignee.name)
    add("Reporter", issue.reporter.name)
    add("Created", issue.created)
    add("Updated", issue.updated)
    add("Due", issue.due_date)
    add("Story points", "" if issue.story_points is None else str(issue.story_points))
    add("Epic", issue.epic_name or issue.epic_key)
    add("Sprints", ", ".join(issue.sprints))
    add("Fix versions", ", ".join(issue.fix_versions))
    add("Components", ", ".join(issue.components))
    add("Labels", ", ".join(issue.labels))
    warnings = issue_warnings(issue)
    add("Warnings", ", ".join(warnings))
    lines.extend(meta)
    lines.append("")

    lines.append("## Description")
    lines.append(issue.description.strip() or "(none)")

    if include_comments:
        lines.append("")
        lines.append("## Comments")
        if not issue.comments:
            lines.append("(none)")
        for c in issue.comments:
            lines.append(f"- {c.author} — {c.created}:")
            body = (c.body or "").strip()
            lines.append(f"  {body}" if body else "  (empty)")

    if include_notes:
        block = render_note_block(annotation, level=2)
        if block:
            lines.append("")
            lines.append(block.rstrip())

    return "\n".join(lines).rstrip() + "\n"
