"""Row markers for the results table: staleness and high-attention flags.

Pure helpers over :class:`~issue_deck.schema.NormalizedIssue` so the same logic
is unit-testable without Qt and reusable outside the table.
"""

from __future__ import annotations

import datetime as _dt

from . import constants
from .merge import is_resolved, parse_timestamp
from .schema import NormalizedIssue

__all__ = [
    "is_stale",
    "days_since_update",
    "is_high_priority",
    "is_blocked",
    "is_overdue",
    "is_missing_owner",
    "is_missing_estimate",
    "issue_markers",
    "issue_warnings",
]


def days_since_update(issue: NormalizedIssue, *, now: _dt.datetime | None = None) -> int | None:
    """Whole days since ``issue.updated`` (``None`` if the date is unparseable)."""
    when = parse_timestamp(issue.updated)
    if when is None:
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return (now - when).days


def is_stale(
    issue: NormalizedIssue, *, days: int = constants.STALE_DAYS, now: _dt.datetime | None = None
) -> bool:
    """True when the issue hasn't been updated in more than ``days`` days."""
    since = days_since_update(issue, now=now)
    return since is not None and since > days


def is_high_priority(issue: NormalizedIssue) -> bool:
    """True when the issue's priority OR severity reads as high-attention."""
    return (
        issue.priority.strip().lower() in constants.HIGH_PRIORITY_NAMES
        or issue.severity.strip().lower() in constants.HIGH_SEVERITY_NAMES
    )


def is_blocked(issue: NormalizedIssue) -> bool:
    """True when the issue reads as blocked: a "Blocked" status or ``blocked`` label."""
    if "blocked" in issue.status.strip().lower():
        return True
    return any(lbl.strip().lower() == "blocked" for lbl in issue.labels)


def is_overdue(issue: NormalizedIssue, *, now: _dt.datetime | None = None) -> bool:
    """True when an unresolved issue's due date is in the past."""
    if is_resolved(issue):
        return False
    due = parse_timestamp(issue.due_date)
    if due is None:
        return False
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if due.tzinfo is None:
        due = due.replace(tzinfo=_dt.timezone.utc)
    return due < now


def is_missing_owner(issue: NormalizedIssue) -> bool:
    """True when an unresolved issue has no assignee."""
    return not is_resolved(issue) and not issue.assignee.name.strip()


def is_missing_estimate(issue: NormalizedIssue) -> bool:
    """True when an unresolved issue has no story-point estimate."""
    return not is_resolved(issue) and issue.story_points is None


def issue_markers(
    issue: NormalizedIssue, *, now: _dt.datetime | None = None
) -> list[str]:
    """Short marker tokens for an issue, e.g. ``["stale", "high"]`` (may be empty)."""
    marks: list[str] = []
    if is_high_priority(issue):
        marks.append("high")
    if is_stale(issue, now=now):
        marks.append("stale")
    return marks


def issue_warnings(
    issue: NormalizedIssue, *, now: _dt.datetime | None = None
) -> list[str]:
    """Human-readable attention flags for an issue (may be empty).

    Order is stable so callers render deterministically. Ownership/estimate/overdue
    flags apply only to unresolved issues (a closed item needs neither an owner nor
    an estimate); staleness/priority/blocked apply regardless.
    """
    warnings: list[str] = []
    if is_stale(issue, now=now):
        days = days_since_update(issue, now=now)
        warnings.append(f"stale ({days}d)" if days is not None else "stale")
    if is_missing_owner(issue):
        warnings.append("missing owner")
    if is_blocked(issue):
        warnings.append("blocked")
    if is_high_priority(issue):
        warnings.append("high priority")
    if is_missing_estimate(issue):
        warnings.append("missing estimate")
    if is_overdue(issue, now=now):
        warnings.append("overdue")
    return warnings
