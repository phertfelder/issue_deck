"""Merging datasets and previewing what a replace/refresh would change.

Two independent-but-related concerns live here, both keyed on the issue key:

* :func:`merge_collections` folds an *incoming* batch into an existing one,
  resolving key collisions by a :class:`ConflictRule` (newest-updated wins, API
  wins, CSV wins, or defer to the user via a callback).
* :func:`build_delta` diffs the *current* dataset against an incoming one and
  reports the changes (new / removed / carried-over / newly-resolved issues plus
  per-field status/assignee/estimate/priority/severity changes) so the user can
  see the impact **before** a destructive replace or refresh.

Everything here is pure and Qt-free; the store and UI drive it.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Sequence

from .schema import NormalizedIssue

__all__ = [
    "ConflictRule",
    "ConflictResolver",
    "MergeResult",
    "merge_collections",
    "FieldChange",
    "DeltaCategory",
    "DeltaRow",
    "DeltaPreview",
    "build_delta",
    "is_resolved",
    "parse_timestamp",
]


class ConflictRule(str, Enum):
    """How to resolve two issues that share the same key during a merge."""

    NEWEST_WINS = "newest"   # larger ``updated`` timestamp wins (default)
    API_WINS = "api"         # prefer the API-origin issue
    CSV_WINS = "csv"         # prefer the CSV-origin issue
    ASK = "ask"              # defer to a user-supplied callback


# Called with (existing, incoming) -> the issue to keep. Only used for ASK.
ConflictResolver = Callable[[NormalizedIssue, NormalizedIssue], NormalizedIssue]


# --------------------------------------------------------------------------- #
# Date / status helpers
# --------------------------------------------------------------------------- #
def _parse_dt(s: str) -> _dt.datetime | None:
    """Parse a Jira/ISO timestamp; return ``None`` if unrecognized.

    Handles the trailing-``Z`` form and the ``+0000`` offset (no colon) Jira
    emits, which :meth:`datetime.fromisoformat` rejects on older Pythons.
    """
    if not s:
        return None
    text = s.strip().replace("Z", "+00:00")
    # Jira's "+0000" -> "+00:00" so fromisoformat accepts the offset.
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]
    try:
        return _dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            return _dt.datetime.fromisoformat(text[:10])  # bare date
        except ValueError:
            return None


def parse_timestamp(s: str) -> _dt.datetime | None:
    """Public alias for the tolerant Jira/ISO timestamp parser."""
    return _parse_dt(s)


def is_resolved(issue: NormalizedIssue) -> bool:
    """True when an issue is completed: a resolution date or a Done category."""
    return bool(issue.resolved) or issue.status_category.strip().lower() == "done"


def _newer(a: NormalizedIssue, b: NormalizedIssue) -> NormalizedIssue:
    """Return whichever issue has the later ``updated`` time (ties -> ``b``)."""
    da, db = _parse_dt(a.updated), _parse_dt(b.updated)
    if da is None and db is None:
        return b
    if da is None:
        return b
    if db is None:
        return a
    return a if da > db else b


# --------------------------------------------------------------------------- #
# Merge
# --------------------------------------------------------------------------- #
@dataclass
class MergeResult:
    """Outcome of :func:`merge_collections`."""

    issues: list[NormalizedIssue]
    added: int = 0
    updated: int = 0        # existing key whose value was replaced by the incoming
    unchanged: int = 0      # existing key where the existing value was kept
    conflicts: int = 0      # existing keys seen in the incoming batch

    def __len__(self) -> int:
        return len(self.issues)


def _resolve(
    rule: ConflictRule,
    existing: NormalizedIssue,
    incoming: NormalizedIssue,
    resolver: ConflictResolver | None,
) -> NormalizedIssue:
    if rule == ConflictRule.API_WINS:
        if incoming.source.origin == "api":
            return incoming
        if existing.source.origin == "api":
            return existing
        return incoming
    if rule == ConflictRule.CSV_WINS:
        if incoming.source.origin == "csv":
            return incoming
        if existing.source.origin == "csv":
            return existing
        return incoming
    if rule == ConflictRule.ASK:
        if resolver is not None:
            return resolver(existing, incoming)
        return _newer(existing, incoming)  # sensible default if no resolver wired
    return _newer(existing, incoming)  # NEWEST_WINS


def merge_collections(
    base: Sequence[NormalizedIssue],
    incoming: Sequence[NormalizedIssue],
    rule: ConflictRule = ConflictRule.NEWEST_WINS,
    *,
    resolver: ConflictResolver | None = None,
) -> MergeResult:
    """Fold ``incoming`` into ``base``, resolving key collisions by ``rule``.

    Order is stable: ``base`` order is preserved (a winning incoming value
    replaces in place), and genuinely-new issues are appended in ``incoming``
    order. Issues with an empty key can't be matched, so they are always
    appended. When ``rule`` is :attr:`ConflictRule.ASK`, ``resolver`` decides
    each collision (falling back to newest-wins if omitted).
    """
    merged = list(base)
    index: dict[str, int] = {}
    for i, issue in enumerate(merged):
        if issue.key:
            index.setdefault(issue.key, i)

    result = MergeResult(issues=merged)
    for inc in incoming:
        if inc.key and inc.key in index:
            result.conflicts += 1
            pos = index[inc.key]
            winner = _resolve(rule, merged[pos], inc, resolver)
            if winner is merged[pos] or winner == merged[pos]:
                result.unchanged += 1
            else:
                merged[pos] = winner
                result.updated += 1
        else:
            if inc.key:
                index[inc.key] = len(merged)
            merged.append(inc)
            result.added += 1
    return result


# --------------------------------------------------------------------------- #
# Delta preview
# --------------------------------------------------------------------------- #
@dataclass
class FieldChange:
    """A single before/after change for one issue and one field."""

    key: str
    field: str
    before: str
    after: str


class DeltaCategory(str, Enum):
    """One bucket of a delta preview — a filterable/exportable change kind.

    ``str`` enum so it serializes and compares as its value. The order here is
    the canonical display/report order.
    """

    NEW = "new"
    REMOVED = "removed"
    UNCHANGED = "unchanged"
    NEWLY_RESOLVED = "newly_resolved"
    REOPENED = "reopened"
    STATUS = "status"
    ASSIGNEE = "assignee"
    REPORTER = "reporter"
    PRIORITY = "priority"
    SEVERITY = "severity"
    STORY_POINTS = "story_points"
    SUMMARY = "summary"
    COMPONENTS = "components"
    LABELS = "labels"
    PROJECT = "project"
    EPIC = "epic"
    UPDATED = "updated"
    COMMENTS = "comments"

    @property
    def label(self) -> str:
        return {
            DeltaCategory.NEW: "New issues",
            DeltaCategory.REMOVED: "Removed issues",
            DeltaCategory.UNCHANGED: "Carried over (unchanged)",
            DeltaCategory.NEWLY_RESOLVED: "Done/resolved since import",
            DeltaCategory.REOPENED: "Reopened",
            DeltaCategory.STATUS: "Status changed",
            DeltaCategory.ASSIGNEE: "Assignee changed",
            DeltaCategory.REPORTER: "Reporter changed",
            DeltaCategory.PRIORITY: "Priority changed",
            DeltaCategory.SEVERITY: "Severity changed",
            DeltaCategory.STORY_POINTS: "Story points changed",
            DeltaCategory.SUMMARY: "Summary changed",
            DeltaCategory.COMPONENTS: "Components changed",
            DeltaCategory.LABELS: "Labels changed",
            DeltaCategory.PROJECT: "Project changed",
            DeltaCategory.EPIC: "Epic changed",
            DeltaCategory.UPDATED: "Updated timestamp changed",
            DeltaCategory.COMMENTS: "Comment count changed",
        }[self]


@dataclass
class DeltaRow:
    """One flattened row of a :class:`DeltaPreview` for tables/reports.

    ``before``/``after`` are empty for whole-issue categories (new/removed/
    unchanged/newly-resolved/reopened) and populated for per-field changes.
    """

    key: str
    category: DeltaCategory
    summary: str = ""
    before: str = ""
    after: str = ""


@dataclass
class DeltaPreview:
    """What replacing/refreshing the current dataset with ``incoming`` would do.

    Keyed by issue key. ``carried_over`` is every key present in both; the
    change lists are subsets of it (only keys whose field actually differs).
    ``newly_resolved`` are carried-over issues that flipped to a completed state;
    ``reopened`` are the reverse (were done, now aren't).
    """

    new_issues: list[NormalizedIssue] = field(default_factory=list)
    removed_issues: list[NormalizedIssue] = field(default_factory=list)
    carried_over: list[str] = field(default_factory=list)
    newly_resolved: list[str] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)
    status_changes: list[FieldChange] = field(default_factory=list)
    assignee_changes: list[FieldChange] = field(default_factory=list)
    reporter_changes: list[FieldChange] = field(default_factory=list)
    estimate_changes: list[FieldChange] = field(default_factory=list)
    priority_changes: list[FieldChange] = field(default_factory=list)
    severity_changes: list[FieldChange] = field(default_factory=list)
    summary_changes: list[FieldChange] = field(default_factory=list)
    component_changes: list[FieldChange] = field(default_factory=list)
    label_changes: list[FieldChange] = field(default_factory=list)
    project_changes: list[FieldChange] = field(default_factory=list)
    epic_changes: list[FieldChange] = field(default_factory=list)
    updated_changes: list[FieldChange] = field(default_factory=list)
    comment_count_changes: list[FieldChange] = field(default_factory=list)

    # Per-field change lists, paired with their category, in canonical order.
    def _field_change_groups(self) -> list[tuple[DeltaCategory, list[FieldChange]]]:
        return [
            (DeltaCategory.STATUS, self.status_changes),
            (DeltaCategory.ASSIGNEE, self.assignee_changes),
            (DeltaCategory.REPORTER, self.reporter_changes),
            (DeltaCategory.PRIORITY, self.priority_changes),
            (DeltaCategory.SEVERITY, self.severity_changes),
            (DeltaCategory.STORY_POINTS, self.estimate_changes),
            (DeltaCategory.SUMMARY, self.summary_changes),
            (DeltaCategory.COMPONENTS, self.component_changes),
            (DeltaCategory.LABELS, self.label_changes),
            (DeltaCategory.PROJECT, self.project_changes),
            (DeltaCategory.EPIC, self.epic_changes),
            (DeltaCategory.UPDATED, self.updated_changes),
            (DeltaCategory.COMMENTS, self.comment_count_changes),
        ]

    @property
    def changed_keys(self) -> set[str]:
        """Every carried-over key that changed in *any* per-field or lifecycle way."""
        keys = set(self.newly_resolved) | set(self.reopened)
        for _cat, changes in self._field_change_groups():
            keys.update(c.key for c in changes)
        return keys

    @property
    def unchanged_keys(self) -> list[str]:
        """Carried-over keys with no field or lifecycle change at all."""
        changed = self.changed_keys
        return [k for k in self.carried_over if k not in changed]

    @property
    def is_destructive(self) -> bool:
        """True when the change would drop or alter existing issues."""
        if self.removed_issues or self.reopened:
            return True
        return any(changes for _cat, changes in self._field_change_groups())

    def counts(self) -> dict[DeltaCategory, int]:
        """Row count per category (only categories with at least one row appear)."""
        out: dict[DeltaCategory, int] = {}
        for cat, n in (
            (DeltaCategory.NEW, len(self.new_issues)),
            (DeltaCategory.REMOVED, len(self.removed_issues)),
            (DeltaCategory.UNCHANGED, len(self.unchanged_keys)),
            (DeltaCategory.NEWLY_RESOLVED, len(self.newly_resolved)),
            (DeltaCategory.REOPENED, len(self.reopened)),
        ):
            if n:
                out[cat] = n
        for cat, changes in self._field_change_groups():
            if changes:
                out[cat] = len(changes)
        return out

    def rows(self) -> list["DeltaRow"]:
        """Flatten the whole delta into categorized rows (canonical order)."""
        rows: list[DeltaRow] = []
        for issue in self.new_issues:
            rows.append(DeltaRow(issue.key, DeltaCategory.NEW, issue.summary))
        for issue in self.removed_issues:
            rows.append(DeltaRow(issue.key, DeltaCategory.REMOVED, issue.summary))
        for key in self.unchanged_keys:
            rows.append(DeltaRow(key, DeltaCategory.UNCHANGED))
        for key in self.newly_resolved:
            rows.append(DeltaRow(key, DeltaCategory.NEWLY_RESOLVED))
        for key in self.reopened:
            rows.append(DeltaRow(key, DeltaCategory.REOPENED))
        for cat, changes in self._field_change_groups():
            for c in changes:
                rows.append(DeltaRow(c.key, cat, "", c.before, c.after))
        return rows

    def summary(self) -> dict[str, int]:
        """Compact counts, handy for a one-line UI banner."""
        return {
            "new": len(self.new_issues),
            "removed": len(self.removed_issues),
            "carried_over": len(self.carried_over),
            "newly_resolved": len(self.newly_resolved),
            "reopened": len(self.reopened),
            "status_changes": len(self.status_changes),
            "assignee_changes": len(self.assignee_changes),
            "reporter_changes": len(self.reporter_changes),
            "estimate_changes": len(self.estimate_changes),
            "priority_changes": len(self.priority_changes),
            "severity_changes": len(self.severity_changes),
            "summary_changes": len(self.summary_changes),
            "component_changes": len(self.component_changes),
            "label_changes": len(self.label_changes),
            "project_changes": len(self.project_changes),
            "epic_changes": len(self.epic_changes),
            "updated_changes": len(self.updated_changes),
            "comment_count_changes": len(self.comment_count_changes),
        }


def _estimate_str(v: float | int | None) -> str:
    return "" if v is None else str(v)


def _join(values: Sequence[str]) -> str:
    """Render a multi-valued field for display (empty -> em dash handled by UI)."""
    return ", ".join(values)


def _project_label(issue: NormalizedIssue) -> str:
    if issue.project_key and issue.project_name:
        return f"{issue.project_key} ({issue.project_name})"
    return issue.project_key or issue.project_name


def _epic_label(issue: NormalizedIssue) -> str:
    if issue.epic_key and issue.epic_name:
        return f"{issue.epic_key} ({issue.epic_name})"
    return issue.epic_key or issue.epic_name


def _by_key(issues: Iterable[NormalizedIssue]) -> dict[str, NormalizedIssue]:
    """Index issues by key (first occurrence wins; blank keys skipped)."""
    out: dict[str, NormalizedIssue] = {}
    for issue in issues:
        if issue.key:
            out.setdefault(issue.key, issue)
    return out


def build_delta(
    current: Sequence[NormalizedIssue], incoming: Sequence[NormalizedIssue]
) -> DeltaPreview:
    """Diff ``current`` against ``incoming`` (both keyed by issue key).

    Intended to be shown before a destructive replace/refresh so the user knows
    exactly what will appear, disappear, or change.
    """
    cur = _by_key(current)
    inc = _by_key(incoming)
    delta = DeltaPreview()

    for key, issue in inc.items():
        if key not in cur:
            delta.new_issues.append(issue)
    for key, issue in cur.items():
        if key not in inc:
            delta.removed_issues.append(issue)

    for key in cur:
        if key not in inc:
            continue
        before, after = cur[key], inc[key]
        delta.carried_over.append(key)

        before_done, after_done = is_resolved(before), is_resolved(after)
        if not before_done and after_done:
            delta.newly_resolved.append(key)
        elif before_done and not after_done:
            delta.reopened.append(key)

        if before.status != after.status:
            delta.status_changes.append(FieldChange(key, "status", before.status, after.status))
        if before.assignee.name != after.assignee.name:
            delta.assignee_changes.append(
                FieldChange(key, "assignee", before.assignee.name, after.assignee.name)
            )
        if before.reporter.name != after.reporter.name:
            delta.reporter_changes.append(
                FieldChange(key, "reporter", before.reporter.name, after.reporter.name)
            )
        if before.story_points != after.story_points:
            delta.estimate_changes.append(
                FieldChange(
                    key, "story_points",
                    _estimate_str(before.story_points), _estimate_str(after.story_points),
                )
            )
        if before.priority != after.priority:
            delta.priority_changes.append(
                FieldChange(key, "priority", before.priority, after.priority)
            )
        if before.severity != after.severity:
            delta.severity_changes.append(
                FieldChange(key, "severity", before.severity, after.severity)
            )
        if before.summary != after.summary:
            delta.summary_changes.append(
                FieldChange(key, "summary", before.summary, after.summary)
            )
        # Multi-valued fields: compare as sets (order-insensitive), show as text.
        if set(before.components) != set(after.components):
            delta.component_changes.append(
                FieldChange(key, "components", _join(before.components), _join(after.components))
            )
        if set(before.labels) != set(after.labels):
            delta.label_changes.append(
                FieldChange(key, "labels", _join(before.labels), _join(after.labels))
            )
        if (before.project_key, before.project_name) != (after.project_key, after.project_name):
            delta.project_changes.append(
                FieldChange(key, "project", _project_label(before), _project_label(after))
            )
        if (before.epic_key, before.epic_name) != (after.epic_key, after.epic_name):
            delta.epic_changes.append(
                FieldChange(key, "epic", _epic_label(before), _epic_label(after))
            )
        if before.updated != after.updated and (before.updated or after.updated):
            delta.updated_changes.append(
                FieldChange(key, "updated", before.updated, after.updated)
            )
        # Comment count only when at least one side actually carries comments,
        # so unloaded comments (empty lists on both) never register as a change.
        if (before.comments or after.comments) and len(before.comments) != len(after.comments):
            delta.comment_count_changes.append(
                FieldChange(
                    key, "comments", str(len(before.comments)), str(len(after.comments))
                )
            )

    return delta
