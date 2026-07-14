"""Render an :class:`~issue_deck.analytics.AnalyticsReport` to Markdown / CSV.

Pure string builders (no filesystem, no Qt) so they are trivially unit-testable
and reusable by the UI's "Export analytics summary" buttons. Percentages are
relative to the dataset total and rounded to one decimal; a story-point column
appears only for sections that carry point totals (workload).
"""

from __future__ import annotations

import csv
import io

from ..analytics import AnalyticsReport, MetricGroup

__all__ = ["render_analytics_markdown", "render_analytics_csv"]


def _pct(count: int, total: int) -> str:
    return f"{(100.0 * count / total):.1f}%" if total else "0.0%"


def _points_str(value: float | int | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def render_analytics_markdown(report: AnalyticsReport) -> str:
    """A Markdown summary: a metadata header then one table per section."""
    lines: list[str] = ["# Analytics summary", ""]
    lines.append(f"- **Total issues:** {report.total}")
    lines.append(f"- **Open:** {report.open_count}  |  **Done:** {report.done_count}")
    lines.append(f"- **Generated:** {report.generated_at}")
    lines.append(f"- **Comments loaded:** {'yes' if report.comments_loaded else 'no'}")
    lines.append(
        f"- **Story points available:** {'yes' if report.story_points_available else 'no'}"
    )
    lines.append("")

    for group in report.sections:
        lines.append(f"## {group.title}")
        lines.append("")
        if group.note:
            lines.append(f"_{group.note}_")
            lines.append("")
        if not group.rows:
            if not group.note:
                lines.append("_No data._")
                lines.append("")
            continue
        lines.extend(_markdown_table(group, report.total))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _markdown_table(group: MetricGroup, total: int) -> list[str]:
    with_points = group.has_points
    if with_points:
        header = "| Metric | Count | % | Story points |"
        sep = "| --- | ---: | ---: | ---: |"
    else:
        header = "| Metric | Count | % |"
        sep = "| --- | ---: | ---: |"
    out = [header, sep]
    for row in group.rows:
        cells = [row.label, str(row.count), _pct(row.count, total)]
        if with_points:
            cells.append(_points_str(row.points))
        out.append("| " + " | ".join(cells) + " |")
    return out


def render_analytics_csv(report: AnalyticsReport) -> str:
    """A flat CSV: one row per metric with a stable ``section,metric,…`` schema."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["section", "metric", "count", "percent", "story_points"])
    for group in report.sections:
        if not group.rows and group.note:
            writer.writerow([group.title, group.note, "", "", ""])
            continue
        for row in group.rows:
            writer.writerow([
                group.title,
                row.label,
                row.count,
                _pct(row.count, report.total),
                _points_str(row.points),
            ])
    return buf.getvalue()
