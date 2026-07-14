"""Options-aware Markdown rendering for the export packs.

Distinct from :mod:`issue_deck.exporters.markdown`, which renders the *frozen*
legacy layout for the standalone Markdown export buttons. This renderer honours
an :class:`ExportConfig` (grouping, metadata toggles) and is intentionally
**deterministic** — it never stamps ``datetime.now()`` into the body — so two
runs of the same dataset produce byte-identical Markdown for clean diffing.

It consumes issues that have already been through
:func:`issue_deck.exporters.transform.prepare_issues`, so redaction/truncation/
comment-trimming are already applied; this module only lays them out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..schema import NormalizedIssue
from .options import GROUP_BY_LABELS, ExportConfig
from .transform import group_issues


def render_issue(
    issue: NormalizedIssue, config: ExportConfig, *, note_block: str = ""
) -> str:
    """One issue as a Markdown section (heading level 2, for embedding in a doc).

    ``note_block`` is a pre-rendered, already-labelled local-note block appended
    verbatim; it is only ever passed when local notes are enabled and keys are
    unredacted (see :func:`issue_deck.exporters.context.render_note_block`).
    """
    lines = [f"## {issue.key} — {issue.summary}", ""]
    meta = [
        f"- **URL:** {issue.url}",
        f"- **Status:** {issue.status}  |  **Type:** {issue.issue_type}  |  "
        f"**Priority:** {issue.priority}"
        + (f"  |  **Severity:** {issue.severity}" if issue.severity else ""),
    ]
    if issue.client:
        meta.append(f"- **Client:** {issue.client}")
    if issue.components:
        meta.append(f"- **Components:** {', '.join(issue.components)}")
    if issue.labels:
        meta.append(f"- **Labels:** {', '.join(issue.labels)}")
    meta.append(f"- **Assignee:** {issue.assignee.name}  |  **Reporter:** {issue.reporter.name}")
    meta.append(f"- **Created:** {issue.created}  |  **Updated:** {issue.updated}")
    if config.include_source_metadata:
        origin = issue.source.origin or "?"
        extra = issue.source.deployment or issue.source.source_file_name
        meta.append(f"- **Source:** {origin}" + (f" ({extra})" if extra else ""))
    lines.extend(meta)
    lines.append("")

    if config.include_descriptions:
        lines.append("### Description")
        lines.append(issue.description or "_(none)_")
        lines.append("")

    if config.include_comments:
        lines.append("### Comments")
        if not issue.comments:
            lines.append("_(none)_")
        for c in issue.comments:
            lines.append(f"#### {c.author} — {c.created}")
            lines.append(c.body or "")
            lines.append("")

    if note_block:
        lines.append(note_block.rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_combined(
    issues: Sequence[NormalizedIssue],
    config: ExportConfig,
    *,
    title: str = "Jira issue export",
    intro: str = "",
    notes: Mapping[str, str] | None = None,
) -> str:
    """A single Markdown document for all ``issues``, grouped per ``config.group_by``.

    Deterministic: no timestamp in the body (provenance lives in the manifest).
    """
    parts: list[str] = [f"# {title}", ""]
    parts.append(f"_{len(issues)} issue(s)._")
    if intro:
        parts.append("")
        parts.append(intro)
    parts.append("")

    notes = notes or {}
    for bucket, bucket_issues in group_issues(issues, config.group_by):
        if bucket:
            label = GROUP_BY_LABELS.get(config.group_by, config.group_by.title())
            parts.append(f"# {label}: {bucket} ({len(bucket_issues)})")
            parts.append("")
        parts.append("\n\n---\n\n".join(
            render_issue(i, config, note_block=notes.get(i.key, "")) for i in bucket_issues))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def render_per_ticket(
    issues: Sequence[NormalizedIssue],
    config: ExportConfig,
    *,
    notes: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Return ``[(filename, markdown), …]`` — one ``KEY.md`` per issue.

    A per-ticket file uses a level-1 heading (it is a standalone document), so the
    section renderer's ``##`` is promoted to ``#`` for the top line only.
    """
    notes = notes or {}
    out: list[tuple[str, str]] = []
    seen: dict[str, int] = {}
    for issue in issues:
        body = render_issue(issue, config, note_block=notes.get(issue.key, ""))
        if body.startswith("## "):
            body = body[1:]  # "## KEY" -> "# KEY"
        stem = issue.key or "issue"
        # Guard against duplicate/redacted keys colliding on the filesystem.
        n = seen.get(stem, 0)
        seen[stem] = n + 1
        name = f"{stem}.md" if n == 0 else f"{stem}_{n + 1}.md"
        out.append((_safe_filename(name), body))
    return out


def _safe_filename(name: str) -> str:
    """Make a key safe as a filename (redacted keys contain ``•``, keys can have ``/``)."""
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name) or "issue.md"
