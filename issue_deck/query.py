"""Query planning helpers: smart defaults, JQL/field estimate, breadth warnings.

Sits between the UI and the raw :func:`issue_deck.jql.build_jql` builder so the
workbench can show *exactly* what it will send — the JQL, the field list, and any
"this looks broad" warnings — before a fetch, and so first-run gets a sensible
starting query.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import AppConfig
from .jql import build_jql
from .models import SearchFilters
from .services.issue_service import search_fields

__all__ = ["QueryEstimate", "default_filters", "breadth_warnings", "estimate_query"]


def default_filters() -> SearchFilters:
    """First-run smart default: assigned to me, unresolved, updated in 90 days."""
    return SearchFilters(
        assigned_to_me=True,
        unresolved=True,
        updated_days=90,
    )


def _has_date_bound(f: SearchFilters) -> bool:
    return bool(f.updated_days or f.created_days or f.resolved_days or f.due_days)


def _has_scope(f: SearchFilters) -> bool:
    return bool(f.assigned_to_me or f.reported_by_me or f.watched_by_me)


def _has_narrowing(f: SearchFilters) -> bool:
    """Any clause likely to bound the result set to a manageable size."""
    return bool(
        f.projects or f.statuses or f.status_categories or f.issue_types
        or f.text or f.sprint or f.fix_version or f.field_filters
        or f.severity or f.client or f.unresolved
    )


def breadth_warnings(filters: SearchFilters) -> list[str]:
    """Warn about searches that may return an unbounded/huge result set."""
    warnings: list[str] = []
    if filters.raw_mode:
        if not filters.raw_jql.strip():
            warnings.append("Raw JQL mode is on but the JQL is empty.")
        return warnings

    # A search is "broad" only when nothing bounds it: no user scope, no date
    # window, and no narrowing clause (project/status/type/text/…).
    if not _has_scope(filters) and not _has_date_bound(filters) and not _has_narrowing(filters):
        warnings.append(
            "Very broad search: no assignee/reporter scope, project, date bound, "
            "or status filter — this may return a very large result set."
        )
    return warnings


@dataclass
class QueryEstimate:
    """What a fetch will send: the JQL, the requested fields, and any warnings."""

    jql: str
    fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_broad(self) -> bool:
        return bool(self.warnings)


def estimate_query(cfg: AppConfig, filters: SearchFilters) -> QueryEstimate:
    """Preview the JQL + field list + breadth warnings without fetching."""
    return QueryEstimate(
        jql=build_jql(cfg, filters),
        fields=search_fields(cfg),
        warnings=breadth_warnings(filters),
    )
