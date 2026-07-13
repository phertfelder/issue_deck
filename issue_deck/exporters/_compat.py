"""Adapter so exporters accept both the normalized and legacy issue models.

Exporters emit a *frozen* on-disk contract (JSONL key order, CSV columns,
Markdown layout) defined by :class:`issue_deck.models.JiraIssue`. The fetch
pipeline now produces :class:`issue_deck.schema.NormalizedIssue`, so exporters
down-convert to the legacy shape first — keeping output byte-identical while
still accepting either type from callers.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from ..models import JiraIssue
from ..schema import NormalizedIssue

# The permissive input type every public exporter accepts. Exporters only read
# their input, so they take a covariant ``Sequence[ExportableIssue]`` — that lets
# callers pass a ``list[NormalizedIssue]`` (or ``list[JiraIssue]``) without a
# variance error.
ExportableIssue = Union[JiraIssue, NormalizedIssue]


def as_legacy(issue: ExportableIssue) -> JiraIssue:
    """Return the backward-compatible :class:`JiraIssue` for an issue of either type."""
    if isinstance(issue, NormalizedIssue):
        return issue.to_legacy_issue()
    return issue


def as_legacy_list(issues: Sequence[ExportableIssue]) -> list[JiraIssue]:
    return [as_legacy(i) for i in issues]
