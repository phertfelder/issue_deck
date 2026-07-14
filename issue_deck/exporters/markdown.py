"""Markdown exporters (combined single file + one file per ticket)."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Sequence
from pathlib import Path

from ._compat import ExportableIssue, as_legacy


def issue_to_markdown(issue: ExportableIssue) -> str:
    n = as_legacy(issue)
    lines = [
        f"# {n.key} — {n.summary}",
        "",
        f"- **URL:** {n.url}",
        f"- **Status:** {n.status}  |  **Type:** {n.issuetype}  |  "
        f"**Priority:** {n.priority}" + (f"  |  **Severity:** {n.severity}" if n.severity else ""),
    ]
    if n.client:
        lines.append(f"- **Client:** {n.client}")
    if n.components:
        lines.append(f"- **Components:** {', '.join(n.components)}")
    if n.labels:
        lines.append(f"- **Labels:** {', '.join(n.labels)}")
    lines.append(f"- **Assignee:** {n.assignee}  |  **Reporter:** {n.reporter}")
    lines.append(f"- **Created:** {n.created}  |  **Updated:** {n.updated}")
    lines.append("")
    lines.append("## Description")
    lines.append(n.description or "_(none)_")
    lines.append("")
    lines.append("## Comments")
    if not n.comments:
        lines.append("_(none)_")
    for c in n.comments:
        lines.append(f"### {c.author} — {c.created}")
        lines.append(c.body or "")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_markdown_combined(issues: Sequence[ExportableIssue], path: str) -> None:
    parts = [issue_to_markdown(n) for n in issues]
    header = f"<!-- Exported {len(issues)} Jira issues on {_dt.datetime.now().isoformat()} -->\n\n"
    Path(path).write_text(header + "\n\n---\n\n".join(parts), encoding="utf-8")


def export_markdown_per_ticket(issues: Sequence[ExportableIssue], folder: str) -> None:
    d = Path(folder)
    d.mkdir(parents=True, exist_ok=True)
    for issue in issues:
        n = as_legacy(issue)
        (d / f"{n.key}.md").write_text(issue_to_markdown(n), encoding="utf-8")
