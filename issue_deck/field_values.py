"""Field-value discovery: derive value distributions from a set of issues.

Filter dropdowns are only useful if they're populated with values that actually
occur. This module distills a :class:`~issue_deck.schema.NormalizedIssue`
sample into per-field :class:`ValueDistribution`s — coverage, unique/empty
counts, the most common values, and a few concrete examples. Because it consumes
*normalized* issues it is source-agnostic: the exact same code derives values
from a live Jira sample and from an imported CSV, so a user can build filters
from an example CSV without ever hitting Jira.

Everything here is pure (no Qt, no HTTP). The cardinality of a field decides how
its filter should be offered (:class:`WidgetKind`) so a high-cardinality field
never renders as a giant, unhelpful dropdown.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Sequence

from . import constants
from .config import AppConfig
from .schema import NormalizedIssue

__all__ = [
    "WidgetKind",
    "ValueCount",
    "ValueDistribution",
    "DISCOVERABLE_BUILTINS",
    "values_for",
    "distribution_for",
    "distributions_from_issues",
    "custom_field_ids",
    "discoverable_fields",
    "recommend_widget",
    "jql_token",
]


class WidgetKind(str, Enum):
    """How a field's filter should be presented, based on its cardinality."""

    CHECK_LIST = "check_list"      # few distinct values -> pick from a checklist
    SEARCH_COMBO = "search_combo"  # medium -> searchable/editable combo
    TEXT = "text"                  # high-cardinality/free-text -> plain text entry


@dataclass
class ValueCount:
    value: str
    count: int


@dataclass
class ValueDistribution:
    """What one field looked like across a sampled set of issues."""

    field_id: str
    field_label: str
    total: int                       # issues sampled
    non_empty: int                   # issues with at least one value for this field
    empty_count: int
    unique_count: int
    top_values: list[ValueCount] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    @property
    def coverage_pct(self) -> float:
        """Percentage of sampled issues that have a value for this field."""
        if self.total <= 0:
            return 0.0
        return round(100.0 * self.non_empty / self.total, 1)

    @property
    def widget(self) -> WidgetKind:
        return recommend_widget(self.unique_count)


# --------------------------------------------------------------------------- #
# Accessors: NormalizedIssue field id -> list of display values
# --------------------------------------------------------------------------- #
def _one(v: str | None) -> list[str]:
    """Wrap a scalar as a single-value list, dropping blanks."""
    s = ("" if v is None else str(v)).strip()
    return [s] if s else []


def _fmt_number(v: float | int) -> str:
    """Render a story-point-style number without a trailing ``.0``."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# Built-in NormalizedIssue attributes worth discovering values for, with the
# human label shown in the UI. Order is the display order.
DISCOVERABLE_BUILTINS: list[tuple[str, str]] = [
    ("project_key", "Project"),
    ("status", "Status"),
    ("status_category", "Status category"),
    ("issue_type", "Issue type"),
    ("priority", "Priority"),
    ("severity", "Severity"),
    ("client", "Client"),
    ("assignee", "Assignee"),
    ("reporter", "Reporter"),
    ("labels", "Labels"),
    ("components", "Components"),
    ("fix_versions", "Fix versions"),
    ("sprints", "Sprints"),
    ("epic_key", "Epic"),
    ("story_points", "Story points"),
]

_BUILTIN_ACCESSORS: dict[str, Callable[[NormalizedIssue], list[str]]] = {
    "project_key": lambda i: _one(i.project_key),
    "project_name": lambda i: _one(i.project_name),
    "status": lambda i: _one(i.status),
    "status_category": lambda i: _one(i.status_category),
    "issue_type": lambda i: _one(i.issue_type),
    "priority": lambda i: _one(i.priority),
    "severity": lambda i: _one(i.severity),
    "client": lambda i: _one(i.client),
    "assignee": lambda i: _one(i.assignee.name),
    "reporter": lambda i: _one(i.reporter.name),
    "labels": lambda i: [s for s in (str(x).strip() for x in i.labels) if s],
    "components": lambda i: [s for s in (str(x).strip() for x in i.components) if s],
    "fix_versions": lambda i: [s for s in (str(x).strip() for x in i.fix_versions) if s],
    "sprints": lambda i: [s for s in (str(x).strip() for x in i.sprints) if s],
    "epic_key": lambda i: _one(i.epic_key),
    "epic_name": lambda i: _one(i.epic_name),
    "story_points": lambda i: _one(
        None if i.story_points is None else _fmt_number(i.story_points)),
}


def _raw_values(issue: NormalizedIssue, field_id: str) -> list[str]:
    """Values for a mapped custom field, held in ``raw_field_values``."""
    v = issue.raw_field_values.get(field_id)
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [s for s in (str(x).strip() for x in v) if s]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return _one(_fmt_number(v))
    return _one(str(v))


def values_for(issue: NormalizedIssue, field_id: str) -> list[str]:
    """All non-empty display values ``issue`` has for ``field_id``.

    Multi-valued fields (labels, components, …) yield every element; scalar
    fields yield a one-element list, or an empty list when absent. Unknown ids
    fall through to mapped custom-field values in ``raw_field_values``.
    """
    accessor = _BUILTIN_ACCESSORS.get(field_id)
    if accessor is not None:
        return accessor(issue)
    return _raw_values(issue, field_id)


# --------------------------------------------------------------------------- #
# Distribution derivation
# --------------------------------------------------------------------------- #
def recommend_widget(unique_count: int) -> WidgetKind:
    """Pick a filter widget for a field with ``unique_count`` distinct values."""
    if unique_count <= constants.ENUM_MAX_UNIQUE:
        return WidgetKind.CHECK_LIST
    if unique_count <= constants.SEARCH_COMBO_MAX_UNIQUE:
        return WidgetKind.SEARCH_COMBO
    return WidgetKind.TEXT


def distribution_for(
    issues: Sequence[NormalizedIssue],
    field_id: str,
    label: str = "",
    *,
    top_n: int = constants.SAMPLE_TOP_VALUES,
    examples_n: int = constants.SAMPLE_EXAMPLES,
) -> ValueDistribution:
    """Compute the :class:`ValueDistribution` of one field over ``issues``."""
    counter: Counter[str] = Counter()
    seen_order: list[str] = []
    non_empty = 0
    for issue in issues:
        vals = values_for(issue, field_id)
        if not vals:
            continue
        non_empty += 1
        for v in vals:
            if v not in counter:
                seen_order.append(v)
            counter[v] += 1
    total = len(issues)
    return ValueDistribution(
        field_id=field_id,
        field_label=label or field_id,
        total=total,
        non_empty=non_empty,
        empty_count=total - non_empty,
        unique_count=len(counter),
        top_values=[ValueCount(v, c) for v, c in counter.most_common(top_n)],
        examples=seen_order[:examples_n],
    )


def distributions_from_issues(
    issues: Sequence[NormalizedIssue],
    fields: Iterable[tuple[str, str]],
    *,
    include_empty: bool = False,
) -> list[ValueDistribution]:
    """Distributions for every ``(field_id, label)`` in ``fields``.

    Fields with no values in the sample are dropped unless ``include_empty`` —
    an empty field offers nothing to filter on. Results are sorted by coverage
    (descending) so the most-populated fields surface first.
    """
    out: list[ValueDistribution] = []
    for field_id, label in fields:
        dist = distribution_for(issues, field_id, label)
        if dist.non_empty or include_empty:
            out.append(dist)
    out.sort(key=lambda d: (-d.coverage_pct, d.field_label.lower()))
    return out


def custom_field_ids(issues: Sequence[NormalizedIssue]) -> list[str]:
    """Mapped custom-field ids present anywhere in ``issues`` (first-seen order).

    These live in ``raw_field_values`` and are the CSV/severity/client-style
    fields Jira doesn't expose options for, so discovery infers their values
    from the sample itself.
    """
    seen: list[str] = []
    for issue in issues:
        for fid in issue.raw_field_values:
            if fid not in seen:
                seen.append(fid)
    return seen


def discoverable_fields(
    issues: Sequence[NormalizedIssue],
    *,
    field_names: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """The ``(field_id, label)`` pairs worth discovering for ``issues``.

    Always the built-in attributes, plus any mapped custom fields present in the
    sample (labelled via ``field_names`` — an id->name map from ``/field`` — when
    available, else by raw id).
    """
    fields = list(DISCOVERABLE_BUILTINS)
    known = {fid for fid, _ in fields}
    names = field_names or {}
    for fid in custom_field_ids(issues):
        if fid not in known:
            fields.append((fid, names.get(fid, fid)))
    return fields


# --------------------------------------------------------------------------- #
# Field id -> JQL token (for pinning a discovered field as a filter)
# --------------------------------------------------------------------------- #
_BUILTIN_JQL_TOKENS: dict[str, str] = {
    "project_key": "project",
    "status": "status",
    "status_category": "statusCategory",
    "issue_type": "issuetype",
    "priority": "priority",
    "assignee": "assignee",
    "reporter": "reporter",
    "labels": "labels",
    "components": "component",
    "fix_versions": "fixVersion",
    "sprints": "sprint",
}


def _cf_token(field_id: str) -> str:
    """``customfield_10060`` -> ``cf[10060]`` (leave non-numeric ids as-is)."""
    num = field_id.split("_")[-1]
    return f"cf[{num}]" if num.isdigit() else field_id


def jql_token(field_id: str, cfg: AppConfig | None = None) -> str:
    """Best JQL clause token for a discovered ``field_id``.

    ``severity``/``client`` resolve to their configured custom-field accessor;
    raw ``customfield_*`` ids become ``cf[id]``; the rest map to their standard
    JQL names. Returns ``""`` when there is no sensible searchable token (e.g.
    ``story_points`` / ``epic_key`` vary per instance), signalling the UI to
    offer the value list without an auto-pinned clause.
    """
    if field_id == "severity":
        return _cf_token(cfg.severity_field) if cfg and cfg.severity_field else ""
    if field_id == "client":
        return _cf_token(cfg.client_field) if cfg and cfg.client_field else ""
    if field_id.startswith("customfield_"):
        return _cf_token(field_id)
    return _BUILTIN_JQL_TOKENS.get(field_id, "")
