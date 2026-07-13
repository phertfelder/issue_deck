"""Local analytics over a loaded dataset — pure, Qt-free, and unit-tested.

:func:`build_report` turns a list of :class:`~issue_deck.schema.NormalizedIssue`
into an :class:`AnalyticsReport`: totals, open/done, categorical breakdowns
(status, assignee, reporter, priority, severity, type, component, project, epic,
client), staleness, aging, workload, risk flags, and recent activity. Everything
is computed in-memory, so **no Jira access is needed once a dataset is loaded**
— the same report is produced for API-fetched and CSV-imported issues.

Two design choices make this reusable:

* Every metric is a :class:`MetricRow` carrying the *issue keys* behind it, so a
  UI can offer click-through/drill-down to exactly those issues without recompute.
* Missing fields **degrade gracefully**: an absent categorical value lands in a
  ``(none)`` bucket, story points are simply omitted from workload sums, and
  comment-based activity reports a note instead of raising when comments were
  never loaded.

Semantics deliberately reuse the existing markers/merge helpers so the dashboard
agrees with the results table: "done" is :func:`~issue_deck.merge.is_resolved`,
staleness is days since ``updated`` (matching
:func:`~issue_deck.markers.is_stale`), and high-attention is
:func:`~issue_deck.markers.is_high_priority`.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .comments import latest_comment_dt
from .markers import days_since_update, is_blocked, is_high_priority
from .merge import is_resolved, parse_timestamp
from .schema import NormalizedIssue

__all__ = [
    "MetricRow",
    "MetricGroup",
    "AnalyticsReport",
    "build_report",
    "NONE_LABEL",
]

# Displayed for an issue that is missing a categorical value (blank assignee,
# no component, unset severity, …). Keeps every issue accounted for.
NONE_LABEL = "(none)"
UNASSIGNED_LABEL = "(unassigned)"

# Staleness thresholds (days since last update), per the dashboard spec.
STALE_BUCKETS = (14, 30, 60)
# Risk: an unresolved issue this old (days since creation) is "old".
OLD_UNRESOLVED_DAYS = 90
# Risk: due within this many days counts as "due soon".
DUE_SOON_DAYS = 7
# Recent-activity windows (days).
ACTIVITY_WINDOWS = (1, 7, 14, 30)


# --------------------------------------------------------------------------- #
# Metric primitives
# --------------------------------------------------------------------------- #
@dataclass
class MetricRow:
    """One measured value: a label, its count, and the keys behind it.

    ``keys`` enables click-through — the UI resolves them back to issues. ``count``
    is authoritative (it counts every matching issue); ``keys`` lists the non-empty
    ones. ``points`` is a summed story-point total when meaningful (workload rows),
    otherwise ``None``.
    """

    label: str
    count: int
    keys: list[str] = field(default_factory=list)
    points: float | int | None = None


@dataclass
class MetricGroup:
    """A titled collection of :class:`MetricRow`, e.g. "By assignee".

    ``note`` carries a human explanation when a group is empty or degraded (for
    instance, comment activity when comments were never loaded).
    """

    title: str
    rows: list[MetricRow] = field(default_factory=list)
    note: str = ""

    @property
    def has_points(self) -> bool:
        """True when any row carries a story-point total (drives a Points column)."""
        return any(r.points is not None for r in self.rows)

    def row(self, label: str) -> MetricRow | None:
        """First row whose label matches (case-insensitive); handy in tests/UI."""
        low = label.lower()
        return next((r for r in self.rows if r.label.lower() == low), None)


@dataclass
class AnalyticsReport:
    """The full dashboard: headline scalars plus ordered metric sections."""

    total: int = 0
    open_count: int = 0
    done_count: int = 0
    generated_at: str = ""
    comments_loaded: bool = False
    story_points_available: bool = False
    sections: list[MetricGroup] = field(default_factory=list)

    def section(self, title: str) -> MetricGroup | None:
        """The section with ``title`` (case-insensitive), or ``None``."""
        low = title.lower()
        return next((s for s in self.sections if s.title.lower() == low), None)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _now(now: _dt.datetime | None) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc)


def _days_since(value: str, now: _dt.datetime) -> int | None:
    """Whole days between ``value`` and ``now`` (``None`` if unparseable)."""
    when = parse_timestamp(value)
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return (now - when).days


def _due_delta_days(value: str, now: _dt.datetime) -> int | None:
    """Whole days from ``now`` until ``value`` (negative = overdue; ``None`` if absent)."""
    when = parse_timestamp(value)
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return (when - now).days


def _keys(matched: Sequence[NormalizedIssue]) -> list[str]:
    return [i.key for i in matched if i.key]


def _row(label: str, matched: Sequence[NormalizedIssue]) -> MetricRow:
    return MetricRow(label=label, count=len(matched), keys=_keys(matched))


def _points_sum(matched: Sequence[NormalizedIssue]) -> float | int | None:
    """Sum of story points across ``matched`` (``None`` when none carry points)."""
    vals = [
        i.story_points for i in matched
        if isinstance(i.story_points, (int, float)) and not isinstance(i.story_points, bool)
    ]
    if not vals:
        return None
    total = sum(vals)
    # Keep an integer total looking like one (5 not 5.0) when inputs are whole.
    return int(total) if float(total).is_integer() else total


def _count_by(
    issues: Sequence[NormalizedIssue],
    key_fn: Callable[[NormalizedIssue], str | list[str]],
) -> list[MetricRow]:
    """Group ``issues`` by ``key_fn`` (scalar or multi-valued) into sorted rows.

    A scalar accessor buckets each issue once; a list accessor buckets it under
    every value (so a two-component issue appears in both component rows). Empty
    values collapse to :data:`NONE_LABEL`. Rows are ordered by count desc then
    label.
    """
    buckets: dict[str, list[NormalizedIssue]] = {}
    for issue in issues:
        raw = key_fn(issue)
        values = list(raw) if isinstance(raw, list) else [raw]
        cleaned = [v.strip() for v in values if isinstance(v, str) and v.strip()]
        if not cleaned:
            cleaned = [NONE_LABEL]
        for label in cleaned:
            buckets.setdefault(label, []).append(issue)
    rows = [_row(label, matched) for label, matched in buckets.items()]
    rows.sort(key=lambda r: (-r.count, r.label.lower()))
    return rows


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #
def _open_vs_done(issues: Sequence[NormalizedIssue]) -> tuple[MetricGroup, int, int]:
    done = [i for i in issues if is_resolved(i)]
    opn = [i for i in issues if not is_resolved(i)]
    group = MetricGroup("Open vs done", [_row("Open", opn), _row("Done", done)])
    return group, len(opn), len(done)


def _staleness(issues: Sequence[NormalizedIssue], now: _dt.datetime) -> MetricGroup:
    rows: list[MetricRow] = []
    for threshold in STALE_BUCKETS:
        matched = [
            i for i in issues
            if (d := days_since_update(i, now=now)) is not None and d > threshold
        ]
        rows.append(_row(f"Updated > {threshold} days ago", matched))
    return MetricGroup("Stale issues", rows)


def _created_age(issues: Sequence[NormalizedIssue], now: _dt.datetime) -> MetricGroup:
    buckets: list[tuple[str, Callable[[int], bool]]] = [
        ("≤ 7 days", lambda d: d <= 7),
        ("8–30 days", lambda d: 8 <= d <= 30),
        ("31–90 days", lambda d: 31 <= d <= 90),
        ("> 90 days", lambda d: d > 90),
    ]
    rows: list[MetricRow] = []
    unknown: list[NormalizedIssue] = []
    ages = [(i, _days_since(i.created, now)) for i in issues]
    for label, pred in buckets:
        matched = [i for i, d in ages if d is not None and d >= 0 and pred(d)]
        rows.append(_row(label, matched))
    unknown = [i for i, d in ages if d is None]
    if unknown:
        rows.append(_row(NONE_LABEL, unknown))
    return MetricGroup("Age since created", rows)


def _update_age(issues: Sequence[NormalizedIssue], now: _dt.datetime) -> MetricGroup:
    buckets: list[tuple[str, Callable[[int], bool]]] = [
        ("≤ 1 day", lambda d: d <= 1),
        ("2–7 days", lambda d: 2 <= d <= 7),
        ("8–14 days", lambda d: 8 <= d <= 14),
        ("15–30 days", lambda d: 15 <= d <= 30),
        ("> 30 days", lambda d: d > 30),
    ]
    rows: list[MetricRow] = []
    ages = [(i, days_since_update(i, now=now)) for i in issues]
    for label, pred in buckets:
        matched = [i for i, d in ages if d is not None and d >= 0 and pred(d)]
        rows.append(_row(label, matched))
    unknown = [i for i, d in ages if d is None]
    if unknown:
        rows.append(_row(NONE_LABEL, unknown))
    return MetricGroup("Days since last update", rows)


def _workload(issues: Sequence[NormalizedIssue]) -> MetricGroup:
    """Issue count + story-point total per assignee, plus an unassigned row."""
    buckets: dict[str, list[NormalizedIssue]] = {}
    for issue in issues:
        label = issue.assignee.name.strip() or UNASSIGNED_LABEL
        buckets.setdefault(label, []).append(issue)
    rows: list[MetricRow] = []
    for label, matched in buckets.items():
        row = _row(label, matched)
        row.points = _points_sum(matched)
        rows.append(row)
    # Unassigned sinks to the bottom; the rest sort by count desc then name.
    rows.sort(key=lambda r: (r.label == UNASSIGNED_LABEL, -r.count, r.label.lower()))
    return MetricGroup("Workload by assignee", rows)


def _risk(issues: Sequence[NormalizedIssue], now: _dt.datetime) -> MetricGroup:
    unresolved = [i for i in issues if not is_resolved(i)]

    def stale_high(i: NormalizedIssue) -> bool:
        d = days_since_update(i, now=now)
        return is_high_priority(i) and d is not None and d > STALE_BUCKETS[0]

    due_soon = []
    overdue = []
    for i in unresolved:
        delta = _due_delta_days(i.due_date, now)
        if delta is None:
            continue
        if delta < 0:
            overdue.append(i)
        elif delta <= DUE_SOON_DAYS:
            due_soon.append(i)

    rows = [
        _row(f"High priority/severity & stale (> {STALE_BUCKETS[0]}d)",
             [i for i in issues if stale_high(i)]),
        _row("Blocked", [i for i in issues if is_blocked(i)]),
        _row(f"Old unresolved (created > {OLD_UNRESOLVED_DAYS}d)",
             [i for i in unresolved
              if (d := _days_since(i.created, now)) is not None and d > OLD_UNRESOLVED_DAYS]),
        _row("Missing assignee (unresolved)",
             [i for i in unresolved if not i.assignee.name.strip()]),
        _row("Missing story points (unresolved)",
             [i for i in unresolved if i.story_points is None]),
        _row(f"Due soon (≤ {DUE_SOON_DAYS}d, unresolved)", due_soon),
        _row("Overdue (unresolved)", overdue),
    ]
    return MetricGroup("Risk", rows)


def _updated_activity(issues: Sequence[NormalizedIssue], now: _dt.datetime) -> MetricGroup:
    rows: list[MetricRow] = []
    ages = [(i, days_since_update(i, now=now)) for i in issues]
    for window in ACTIVITY_WINDOWS:
        matched = [i for i, d in ages if d is not None and 0 <= d <= window]
        rows.append(_row(f"Updated in last {window} day(s)", matched))
    return MetricGroup("Recent activity — updated", rows)


def _commented_activity(
    issues: Sequence[NormalizedIssue], now: _dt.datetime, *, comments_loaded: bool
) -> MetricGroup:
    if not comments_loaded:
        return MetricGroup(
            "Recent activity — commented",
            note="Comments were not loaded for this dataset, so comment activity "
                 "is unavailable. Re-fetch with a comments mode other than "
                 "\"No comments\" to populate this section.",
        )
    rows: list[MetricRow] = []
    last: list[tuple[NormalizedIssue, int | None]] = []
    for i in issues:
        dt = latest_comment_dt(i)
        if dt is None:
            last.append((i, None))
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        last.append((i, (now - dt).days))
    for window in ACTIVITY_WINDOWS:
        matched = [i for i, d in last if d is not None and 0 <= d <= window]
        rows.append(_row(f"Commented in last {window} day(s)", matched))
    return MetricGroup("Recent activity — commented", rows)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def build_report(
    issues: Sequence[NormalizedIssue], *, now: _dt.datetime | None = None
) -> AnalyticsReport:
    """Compute the full :class:`AnalyticsReport` for ``issues``.

    Pure and deterministic given ``now`` (injected for testing). Safe on an empty
    dataset (every section renders with zero counts) and on partially-populated
    issues (missing fields collapse into ``(none)`` buckets).
    """
    now = _now(now)
    issues = list(issues)

    open_done, open_count, done_count = _open_vs_done(issues)
    comments_loaded = any(i.comments for i in issues)
    story_points_available = any(i.story_points is not None for i in issues)

    sections = [
        open_done,
        MetricGroup("By status", _count_by(issues, lambda i: i.status)),
        MetricGroup("By issue type", _count_by(issues, lambda i: i.issue_type)),
        MetricGroup("By priority", _count_by(issues, lambda i: i.priority)),
        MetricGroup("By severity", _count_by(issues, lambda i: i.severity)),
        MetricGroup("By assignee", _count_by(issues, lambda i: i.assignee.name)),
        MetricGroup("By reporter", _count_by(issues, lambda i: i.reporter.name)),
        MetricGroup("By project", _count_by(issues, lambda i: i.project_key or i.project_name)),
        MetricGroup("By component", _count_by(issues, lambda i: i.components)),
        MetricGroup("By epic", _count_by(issues, lambda i: i.epic_name or i.epic_key)),
        MetricGroup("By client", _count_by(issues, lambda i: i.client)),
        _staleness(issues, now),
        _created_age(issues, now),
        _update_age(issues, now),
        _workload(issues),
        _risk(issues, now),
        _updated_activity(issues, now),
        _commented_activity(issues, now, comments_loaded=comments_loaded),
    ]

    return AnalyticsReport(
        total=len(issues),
        open_count=open_count,
        done_count=done_count,
        generated_at=now.isoformat(),
        comments_loaded=comments_loaded,
        story_points_available=story_points_available,
        sections=sections,
    )
