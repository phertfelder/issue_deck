"""Human-readable *preview* of what redaction will do to an export.

Given the same issues and :class:`ExportConfig` the export pipeline will use,
:func:`redaction_preview` renders a compact before -> after comparison for a small
sample of issues, plus a one-line summary of the enabled redactions. The UI shows
this before writing anything so a user can confirm nothing sensitive leaks.

Pure and Qt-free: it drives the real :func:`prepare_issues`, so the preview can
never diverge from the actual output.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..schema import NormalizedIssue
from .options import ExportConfig
from .transform import prepare_issues, sort_issues

_FIELDS = ("key", "summary", "client")


def _first_line(text: str, limit: int = 120) -> str:
    line = (text or "").splitlines()[0] if text else ""
    return line[:limit] + ("…" if len(line) > limit else "")


def _row(label: str, before: str, after: str) -> list[str]:
    if before == after:
        return [f"- **{label}:** {before or '_(empty)_'} _(unchanged)_"]
    return [f"- **{label}:** {before or '_(empty)_'} → {after or '_(empty)_'}"]


def redaction_preview(
    issues: Sequence[NormalizedIssue],
    config: ExportConfig,
    *,
    limit: int = 5,
) -> str:
    """Return a Markdown before/after preview of redaction for up to ``limit`` issues."""
    config = config.normalized()
    settings = config.redaction_settings()
    labels = settings.labels()
    header = (
        "Redaction enabled: " + ", ".join(labels) + "."
        if labels else "No redaction enabled — the export is unredacted."
    )
    if not issues:
        return header + "\n\n_No issues to preview._\n"

    prepared = prepare_issues(issues, config)
    # prepare_issues sorts *then* redacts in place, so re-running the same sort on
    # the originals lines each "before" up with its redacted "after" by index —
    # matching on key afterwards is impossible once keys are masked.
    originals = sort_issues(issues, config)

    parts = [header, ""]
    shown = min(limit, len(prepared))
    for before, after in list(zip(originals, prepared))[:shown]:
        parts.append(f"### {before.key or '(no key)'}")
        parts.extend(_row("Key", before.key, after.key))
        parts.extend(_row("Summary", _first_line(before.summary), _first_line(after.summary)))
        parts.extend(_row(
            "Assignee", before.assignee.name, after.assignee.name))
        parts.extend(_row("Reporter", before.reporter.name, after.reporter.name))
        if before.client or after.client:
            parts.extend(_row("Client", before.client, after.client))
        parts.extend(_row(
            "Description", _first_line(before.description), _first_line(after.description)))
        parts.append("")
    if len(prepared) > shown:
        parts.append(f"_…and {len(prepared) - shown} more issue(s)._")
    return "\n".join(parts).rstrip() + "\n"
