"""JQL construction from typed :class:`SearchFilters`.

The builder is additive: with a default :class:`SearchFilters` it emits exactly
the classic ``assignee = currentUser() ORDER BY updated DESC``. Each workbench
field contributes a guarded clause only when active, so simple queries stay
simple and the legacy output is preserved byte-for-byte.

Only structured filter values are quote-escaped; the free-form ``extra`` clause
and full ``raw_jql`` are passed through as written (deliberate power-user escape
hatches).
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .models import FieldFilter, SearchFilters

_STATUS_CATEGORY_JQL = {
    "To Do": "To Do",
    "In Progress": "In Progress",
    "Done": "Done",
}


@dataclass(frozen=True)
class Clause:
    """One structured JQL clause: its rendered ``text`` plus UI metadata.

    ``label`` is a short human name for the clause and ``kind`` groups clauses
    for analysis (``scope``/``project``/``date``/``status``/…). The ORDER BY tail
    is *not* a clause — it's appended once when a full query is rendered.
    """

    text: str
    label: str
    kind: str


def _q(v: str) -> str:
    return '"' + v.replace('"', '\\"') + '"'


def _in_list(values: list[str]) -> str:
    return "(" + ", ".join(_q(v) for v in values) + ")"


def _scope_clause(f: SearchFilters) -> str:
    """The who-clause: assigned/reported/watched. Multiple are OR-ed."""
    terms: list[str] = []
    if f.assigned_to_me:
        terms.append("assignee = currentUser()")
    if f.reported_by_me:
        terms.append("reporter = currentUser()")
    if f.watched_by_me:
        terms.append("watcher = currentUser()")
    if not terms:
        return ""
    if len(terms) == 1:
        return terms[0]
    return "(" + " OR ".join(terms) + ")"


def _field_filter_clause(ff: FieldFilter) -> str:
    field = ff.field.strip()
    value = ff.value.strip()
    if not field or not value:
        return ""
    op = (ff.op or "~").strip()
    if op in ("in", "not in"):
        parts = [p.strip() for p in value.split(",") if p.strip()]
        return f"{field} {op} {_in_list(parts)}" if parts else ""
    return f"{field} {op} {_q(value)}"


ORDER_BY_TAIL = " ORDER BY updated DESC"


def build_clauses(cfg: AppConfig, filters: SearchFilters) -> list[Clause]:
    """Decompose ``filters`` into ordered, labeled :class:`Clause` objects.

    This is the single source of truth for structured clause *text* — both
    :func:`build_jql` and the JQL helper panel build on it, so the wording and
    ordering stay identical. ``raw_mode`` is handled by callers (it bypasses the
    structured builder entirely); this function always builds the structured
    clause list.
    """
    clauses: list[Clause] = []

    scope = _scope_clause(filters)
    if scope:
        clauses.append(Clause(scope, "Scope", "scope"))

    if filters.projects:
        if len(filters.projects) == 1:
            text = f"project = {_q(filters.projects[0])}"
        else:
            text = f"project in {_in_list(filters.projects)}"
        clauses.append(Clause(text, "Project", "project"))

    if filters.status_categories:
        cats = [_STATUS_CATEGORY_JQL.get(c, c) for c in filters.status_categories]
        clauses.append(Clause(f"statusCategory in {_in_list(cats)}",
                              "Status category", "status_category"))

    if filters.statuses:
        clauses.append(Clause(f"status in {_in_list(filters.statuses)}", "Status", "status"))

    if filters.issue_types:
        clauses.append(Clause(f"issuetype in {_in_list(filters.issue_types)}",
                              "Issue type", "type"))

    if filters.sprint:
        clauses.append(Clause(f"sprint = {_q(filters.sprint)}", "Sprint", "sprint"))

    if filters.fix_version:
        clauses.append(Clause(f"fixVersion = {_q(filters.fix_version)}",
                              "Fix version", "version"))

    if filters.severity and cfg.severity_field:
        cf = cfg.severity_field.replace("customfield_", "")
        clauses.append(Clause(f"cf[{cf}] = {_q(filters.severity)}", "Severity", "custom"))

    if filters.client and cfg.client_field:
        cf = cfg.client_field.replace("customfield_", "")
        clauses.append(Clause(f"cf[{cf}] ~ {_q(filters.client)}", "Client", "custom"))

    if filters.text:
        clauses.append(Clause(f"text ~ {_q(filters.text)}", "Text", "text"))

    if filters.unresolved:
        clauses.append(Clause("resolution = Unresolved", "Resolution", "resolution"))

    if filters.updated_days > 0:
        clauses.append(Clause(f"updated >= -{filters.updated_days}d", "Updated", "date"))
    if filters.created_days > 0:
        clauses.append(Clause(f"created >= -{filters.created_days}d", "Created", "date"))
    if filters.resolved_days > 0:
        clauses.append(Clause(f"resolved >= -{filters.resolved_days}d", "Resolved", "date"))
    if filters.due_days > 0:
        clauses.append(Clause(f"duedate <= {filters.due_days}d", "Due", "date"))

    for ff in filters.field_filters:
        clause = _field_filter_clause(ff)
        if clause:
            label = (ff.label or ff.field).strip() or "Field"
            clauses.append(Clause(clause, label, "custom"))

    if filters.extra.strip():
        clauses.append(Clause("(" + filters.extra.strip() + ")", "Extra JQL", "raw"))

    return clauses


def build_jql(cfg: AppConfig, filters: SearchFilters) -> str:
    """Render ``filters`` into a JQL string for ``cfg``.

    In ``raw_mode`` the user's ``raw_jql`` is returned verbatim (no scope, no
    ORDER BY injection) so advanced users keep full control.
    """
    if filters.raw_mode and filters.raw_jql.strip():
        return filters.raw_jql.strip()

    clauses = build_clauses(cfg, filters)
    return " AND ".join(c.text for c in clauses) + ORDER_BY_TAIL
