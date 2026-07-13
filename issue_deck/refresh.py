"""Refreshing the working dataset from a re-run source, with a delta preview.

A *refresh* re-materializes issues from some source (the same saved API query, a
newer CSV export, or a pasted JQL) and compares them to the current dataset so
the user can see exactly what changed **before** any destructive replace. This
module holds the pure, Qt-free policy the UI drives:

* :func:`validate_incoming` enforces the hard preconditions — most importantly
  that CSV-sourced issues carry an issue key, since a refresh matches by key and
  a keyless row can't be diffed against anything.
* :class:`RefreshPlan` captures the user's chosen source + apply options.
* :func:`render_delta_report` turns a :class:`~issue_deck.merge.DeltaPreview`
  into a text or CSV report for export.

Nothing here does I/O or touches Qt; see :func:`issue_deck.merge.build_delta`.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Sequence

from .merge import ConflictRule, DeltaCategory, DeltaPreview
from .schema import NormalizedIssue

__all__ = [
    "RefreshSource",
    "RefreshPlan",
    "RefreshValidation",
    "validate_incoming",
    "removed_keys",
    "render_delta_report",
]

# The three ways a refresh can re-materialize issues.
RefreshSource = str  # "api_current" | "api_jql" | "csv"


@dataclass
class RefreshValidation:
    """Outcome of pre-flighting an incoming batch against a refresh.

    ``blocking`` messages must stop the refresh (shown to the user, no delta is
    built). ``warnings`` are advisory and don't block. ``ok`` is the go/no-go.
    """

    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocking


@dataclass
class RefreshPlan:
    """The user's chosen refresh source and how to apply the result."""

    source: RefreshSource = "api_current"
    jql: str = ""                                  # for source == "api_jql"
    csv_path: str = ""                             # for source == "csv"
    apply_mode: str = "replace"                    # "replace" | "merge"
    rule: ConflictRule = ConflictRule.NEWEST_WINS  # merge conflict resolution
    # When False, annotations for issues dropped by a replace are pruned too.
    preserve_annotations: bool = True

    @property
    def is_merge(self) -> bool:
        return self.apply_mode == "merge"


def validate_incoming(
    incoming: Sequence[NormalizedIssue], *, is_csv: bool
) -> RefreshValidation:
    """Pre-flight ``incoming`` before diffing it against the current dataset.

    Blocks when a CSV import yields issues without a key (a refresh matches by
    issue key, so keyless rows can't be compared). Non-blocking warnings cover an
    empty result (a replace would drop everything) and duplicate keys (only the
    first of each is used for matching).
    """
    v = RefreshValidation()
    total = len(incoming)

    if is_csv:
        missing = sum(1 for i in incoming if not i.key.strip())
        if missing:
            v.blocking.append(
                f"{missing} of {total} imported row(s) have no issue key. "
                "A refresh matches issues by their Jira key, so rows without one "
                "can't be compared to the current dataset. Re-import the CSV with "
                "an “Issue key” column mapped, then refresh again."
            )

    if total == 0:
        v.warnings.append(
            "The new data source returned no issues — a replace would remove the "
            "entire current dataset."
        )

    seen: set[str] = set()
    dups: set[str] = set()
    for issue in incoming:
        key = issue.key.strip()
        if not key:
            continue
        if key in seen:
            dups.add(key)
        seen.add(key)
    if dups:
        v.warnings.append(
            f"{len(dups)} duplicate issue key(s) in the new data; only the first "
            "occurrence of each is used for matching."
        )

    return v


def removed_keys(delta: DeltaPreview) -> list[str]:
    """Keys present now but absent from the incoming batch (dropped by a replace)."""
    return [i.key for i in delta.removed_issues if i.key]


def render_delta_report(delta: DeltaPreview, *, fmt: str = "text") -> str:
    """Render a delta preview as a shareable report.

    ``fmt="csv"`` yields a flat ``Key,Category,Summary,Before,After`` sheet (one
    row per change); ``fmt="text"`` yields a grouped, human-readable summary.
    """
    if fmt == "csv":
        return _render_csv(delta)
    if fmt == "text":
        return _render_text(delta)
    raise ValueError(f"Unknown report format: {fmt!r}")


def _render_csv(delta: DeltaPreview) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Key", "Category", "Summary", "Before", "After"])
    for row in delta.rows():
        writer.writerow(
            [row.key, row.category.label, row.summary, row.before, row.after]
        )
    return buf.getvalue()


def _render_text(delta: DeltaPreview) -> str:
    s = delta.summary()
    lines: list[str] = [
        "Refresh delta report",
        "====================",
        "",
        f"New: {s['new']}   Removed: {s['removed']}   "
        f"Carried over: {s['carried_over']}   "
        f"Newly resolved: {s['newly_resolved']}   Reopened: {s['reopened']}",
        "",
    ]
    if not delta.is_destructive and not delta.new_issues:
        lines.append("No changes: the incoming data matches the current dataset.")
        return "\n".join(lines) + "\n"

    rows_by_cat: dict[DeltaCategory, list] = {}
    for row in delta.rows():
        rows_by_cat.setdefault(row.category, []).append(row)

    for category, rows in rows_by_cat.items():
        lines.append(f"{category.label} ({len(rows)})")
        lines.append("-" * len(f"{category.label} ({len(rows)})"))
        for row in rows:
            if row.before or row.after:
                detail = f"{row.before or '—'} -> {row.after or '—'}"
                lines.append(f"  {row.key}: {detail}")
            elif row.summary:
                lines.append(f"  {row.key} — {row.summary}")
            else:
                lines.append(f"  {row.key}")
        lines.append("")
    return "\n".join(lines) + "\n"
