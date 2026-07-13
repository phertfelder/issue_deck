"""Typed models for issues, comments, fields, filters and export options.

Field order on :class:`JiraIssue` / :class:`JiraComment` is significant:
``dataclasses.asdict`` is used for JSONL export, so this ordering defines the
serialized key order and must not change casually.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class JiraComment:
    author: str = ""
    created: str = ""
    updated: str = ""
    body: str = ""


@dataclass
class JiraIssue:
    key: str = ""
    url: str = ""
    summary: str = ""
    status: str = ""
    issuetype: str = ""
    priority: str = ""
    severity: str = ""
    client: str = ""
    assignee: str = ""
    reporter: str = ""
    created: str = ""
    updated: str = ""
    components: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    description: str = ""
    comments: list[JiraComment] = field(default_factory=list)


@dataclass
class JiraField:
    id: str
    name: str
    custom: bool = False


@dataclass
class FieldFilter:
    """A dynamic clause built from a pinned field (for non-JQL users).

    ``field`` is a JQL field token — a plain name (``"labels"``) or a custom-field
    accessor (``"cf[10050]"``). ``op`` is one of ``= ~ != >= <= in "not in"``. For
    ``in``/``not in`` the value is a comma-separated list. Empty ``field``/``value``
    is a no-op.
    """

    field: str = ""
    op: str = "~"
    value: str = ""
    label: str = ""   # human label for the UI chip (optional)


@dataclass
class SearchFilters:
    """User-selected query filters.

    Backward-compatible: every field defaults so ``SearchFilters()`` still yields
    the classic *assigned-to-me* query. ``commented_days`` is applied client-side
    (after fetch) and is therefore not part of the generated JQL. When
    ``raw_mode`` is set, ``raw_jql`` is used verbatim and the structured fields
    are ignored (the advanced escape hatch)."""

    # --- classic fields (unchanged order/semantics) ---
    statuses: list[str] = field(default_factory=list)
    issue_types: list[str] = field(default_factory=list)
    severity: str = ""
    client: str = ""
    text: str = ""
    updated_days: int = 0
    commented_days: int = 0
    extra: str = ""
    load_comments: bool = True

    # --- workbench additions ---
    assigned_to_me: bool = True          # default scope (matches legacy behavior)
    reported_by_me: bool = False
    watched_by_me: bool = False
    projects: list[str] = field(default_factory=list)
    sprint: str = ""
    fix_version: str = ""
    status_categories: list[str] = field(default_factory=list)  # To Do/In Progress/Done
    created_days: int = 0
    resolved_days: int = 0
    due_days: int = 0                    # due within the next N days (duedate <= Nd)
    unresolved: bool = False             # resolution = Unresolved
    field_filters: list[FieldFilter] = field(default_factory=list)
    raw_mode: bool = False
    raw_jql: str = ""


# Canonical results-table columns: id -> human label. ``id`` matches a
# NormalizedIssue attribute (or a derived value) resolved in the UI/markers.
RESULT_COLUMNS: list[tuple[str, str]] = [
    ("key", "Key"),
    ("summary", "Summary"),
    ("status", "Status"),
    ("issue_type", "Type"),
    ("priority", "Priority"),
    ("severity", "Severity"),
    ("client", "Client"),
    ("assignee", "Assignee"),
    ("story_points", "Points"),
    ("updated", "Updated"),
]
DEFAULT_VISIBLE_COLUMNS: list[str] = [
    "key", "summary", "status", "issue_type", "priority", "assignee", "updated",
]


@dataclass
class SavedView:
    """A named, reusable query + table layout. Contains NO credentials."""

    name: str
    filters: SearchFilters = field(default_factory=SearchFilters)
    sort_column: str = "updated"
    sort_desc: bool = True
    visible_columns: list[str] = field(default_factory=lambda: list(DEFAULT_VISIBLE_COLUMNS))


@dataclass
class ExportOptions:
    fmt: str          # "markdown_combined" | "markdown_per_ticket" | "jsonl" | "csv"
    destination: str  # output file path, or folder for per-ticket markdown
