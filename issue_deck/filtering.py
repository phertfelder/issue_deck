"""In-memory application of :class:`SearchFilters` to normalized issues.

On the API path, filters become JQL and the server does the work. That doesn't
help a CSV dataset — and it means the same filter can't be re-applied to an
already-fetched set without another round-trip. :func:`apply_filters` closes the
gap: it evaluates a :class:`SearchFilters` against a list of
:class:`~issue_deck.schema.NormalizedIssue` locally, so **the same filters work
identically on API and CSV data**.

Semantics deliberately mirror :mod:`issue_deck.jql`:

* ``statuses`` / ``issue_types`` — membership (case-insensitive).
* ``severity`` — equality (case-insensitive), matching JQL ``=``.
* ``client`` — substring (case-insensitive), matching JQL ``~``.
* ``text`` — substring across summary, description, and comment bodies.
* ``updated_days`` — issue ``updated`` within N days of ``now``.
* ``commented_days`` — latest comment within N days (client-side in both paths).
* ``extra`` — free-form JQL; not evaluable locally, so it is ignored here.
"""

from __future__ import annotations

import datetime as _dt
from typing import Sequence

from .comments import latest_comment_dt
from .merge import is_resolved, parse_timestamp
from .models import SearchFilters
from .schema import NormalizedIssue

__all__ = ["apply_filters", "matches"]


def _within_days(value: str, days: int, now: _dt.datetime) -> bool:
    if days <= 0:
        return True
    when = parse_timestamp(value)
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when >= now - _dt.timedelta(days=days)


def matches(
    issue: NormalizedIssue, filters: SearchFilters, *, now: _dt.datetime | None = None
) -> bool:
    """True when ``issue`` satisfies every active clause of ``filters``."""
    now = now or _dt.datetime.now(_dt.timezone.utc)

    if filters.statuses:
        wanted = {s.lower() for s in filters.statuses}
        if issue.status.lower() not in wanted:
            return False

    if filters.issue_types:
        wanted = {t.lower() for t in filters.issue_types}
        if issue.issue_type.lower() not in wanted:
            return False

    if filters.projects:
        wanted = {p.lower() for p in filters.projects}
        if issue.project_key.lower() not in wanted and issue.project_name.lower() not in wanted:
            return False

    if filters.status_categories:
        wanted = {c.lower() for c in filters.status_categories}
        if issue.status_category.lower() not in wanted:
            return False

    if filters.unresolved and is_resolved(issue):
        return False

    if filters.severity and issue.severity.lower() != filters.severity.lower():
        return False

    if filters.client and filters.client.lower() not in issue.client.lower():
        return False

    if filters.text:
        needle = filters.text.lower()
        haystacks = [issue.summary, issue.description]
        haystacks.extend(c.body for c in issue.comments)
        if not any(needle in (h or "").lower() for h in haystacks):
            return False

    if not _within_days(issue.updated, filters.updated_days, now):
        return False
    if not _within_days(issue.created, filters.created_days, now):
        return False
    if not _within_days(issue.resolved, filters.resolved_days, now):
        return False

    if filters.due_days > 0:
        due = parse_timestamp(issue.due_date)
        if due is None:
            return False
        if due.tzinfo is None:
            due = due.replace(tzinfo=_dt.timezone.utc)
        if due > now + _dt.timedelta(days=filters.due_days):
            return False

    if filters.commented_days > 0:
        lc = latest_comment_dt(issue)
        if lc is None:
            return False
        if lc.tzinfo is None:
            lc = lc.replace(tzinfo=_dt.timezone.utc)
        if lc < now - _dt.timedelta(days=filters.commented_days):
            return False

    return True


def apply_filters(
    issues: Sequence[NormalizedIssue],
    filters: SearchFilters,
    *,
    now: _dt.datetime | None = None,
) -> list[NormalizedIssue]:
    """Return the subset of ``issues`` matching ``filters`` (order preserved)."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return [i for i in issues if matches(i, filters, now=now)]
