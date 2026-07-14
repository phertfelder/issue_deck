"""The "prompt pack": ready-to-run LLM prompts built around the exported issues.

Each prompt file is self-contained — a task instruction followed by a compact,
deterministic digest of the issues — so a user can paste one straight into an
LLM (or attach it alongside the export pack) without any further assembly. The
five prompts cover the recurring reporting jobs a Jira dataset feeds:

    triage_prompt.md          prioritize/categorize the open work
    sprint_summary_prompt.md  summarize progress for a standup/review
    risk_review_prompt.md     surface risks, blockers, and stale items
    release_notes_prompt.md   draft user-facing release notes
    client_status_prompt.md   draft an external client status update

Like the export pack, the digests are deterministic (no timestamps in the body),
and they respect the same redaction/shaping via :func:`prepare_issues`.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..schema import NormalizedIssue
from .options import ExportConfig, ExportContext
from .transform import prepare_issues

PROMPT_FILENAMES = (
    "triage_prompt.md",
    "sprint_summary_prompt.md",
    "risk_review_prompt.md",
    "release_notes_prompt.md",
    "client_status_prompt.md",
)


def _digest_row(issue: NormalizedIssue) -> str:
    cells = [
        issue.key or "—",
        (issue.summary or "").replace("|", "\\|"),
        issue.status or "—",
        issue.priority or "—",
        issue.severity or "—",
        issue.assignee.name or "Unassigned",
        issue.updated or "—",
    ]
    return "| " + " | ".join(cells) + " |"


def issues_digest(issues: Sequence[NormalizedIssue]) -> str:
    """A compact Markdown table of the issues — the shared body of every prompt."""
    header = (
        "| Key | Summary | Status | Priority | Severity | Assignee | Updated |\n"
        "|---|---|---|---|---|---|---|"
    )
    rows = [_digest_row(i) for i in issues]
    return "\n".join([header, *rows])


def _context_line(context: ExportContext) -> str:
    if context.source_type == "csv":
        name = context.csv_source_filename
        src = f"CSV import ({name})" if name else "CSV import"
    else:
        src = "Jira API"
        if context.jql:
            src += f" — `{context.jql}`"
    return f"_Source: {src}. {{count}} issue(s)._"


_PROMPT_TASKS: dict[str, tuple[str, str]] = {
    "triage_prompt.md": (
        "Triage the Jira issues below",
        "You are a delivery lead triaging a backlog. For the issues below:\n"
        "1. Group them into **Now / Next / Later** with a one-line rationale each.\n"
        "2. Flag anything mis-prioritized (e.g. a high-severity item left at low priority).\n"
        "3. Call out duplicates or issues that should be merged.\n"
        "4. List any issue that lacks enough detail to action, and say what's missing.",
    ),
    "sprint_summary_prompt.md": (
        "Summarize this sprint's issues",
        "You are preparing a sprint review. Using the issues below:\n"
        "1. Summarize overall progress in 2-3 sentences.\n"
        "2. Give a breakdown by status (counts + notable items).\n"
        "3. Highlight completed work worth calling out.\n"
        "4. List carry-over / in-progress items and any blockers.",
    ),
    "risk_review_prompt.md": (
        "Review these issues for delivery risk",
        "You are a program manager doing a risk review. From the issues below:\n"
        "1. Identify the top risks and blockers, ordered by impact.\n"
        "2. Flag stale items (not updated recently) and high-severity/high-priority work.\n"
        "3. Note any single points of failure (e.g. one assignee holding many critical items).\n"
        "4. Recommend concrete mitigations for the top 3 risks.",
    ),
    "release_notes_prompt.md": (
        "Draft release notes from these issues",
        "You are a technical writer drafting user-facing release notes. Using the issues below:\n"
        "1. Write a short intro paragraph.\n"
        "2. Group changes into **New**, **Improved**, and **Fixed**.\n"
        "3. Rewrite each entry in clear, user-facing language (no internal jargon or ticket ids).\n"
        "4. Omit purely internal/chore items.",
    ),
    "client_status_prompt.md": (
        "Write a client status update",
        "You are an account manager writing an external client status update. "
        "From the issues below:\n"
        "1. Write a concise, professional summary of progress this period.\n"
        "2. Highlight delivered value in outcome terms, not ticket ids.\n"
        "3. Note what's in progress and expected next.\n"
        "4. Surface any client-facing risks diplomatically, with next steps.\n"
        "Keep it non-technical and reassuring; do not expose internal identifiers.",
    ),
}


def build_prompt_pack(
    issues: Sequence[NormalizedIssue], config: ExportConfig, context: ExportContext
) -> dict[str, str]:
    """Return ``{filename: prompt_text}`` for all five prompts."""
    config = config.normalized()
    prepared = prepare_issues(issues, config)
    digest = issues_digest(prepared)
    context_line = _context_line(context).format(count=len(prepared))
    out: dict[str, str] = {}
    for name, (title, task) in _PROMPT_TASKS.items():
        body = "\n".join([
            f"# {title}",
            "",
            context_line,
            "",
            "## Task",
            "",
            task,
            "",
            "## Issues",
            "",
            digest,
            "",
        ])
        out[name] = body
    return out


def write_prompt_pack(
    issues: Sequence[NormalizedIssue],
    config: ExportConfig,
    context: ExportContext,
    path: str,
) -> None:
    """Build the prompt pack and write it as a deterministic ZIP to ``path``."""
    from pathlib import Path

    from .pack import zip_bytes

    files = {name: text.encode("utf-8") for name, text in
             build_prompt_pack(issues, config, context).items()}
    Path(path).write_bytes(zip_bytes(files))
