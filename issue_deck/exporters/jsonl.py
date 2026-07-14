"""JSONL exporter (one issue object per line).

Uses ``dataclasses.asdict`` so the serialized shape follows the field order of
:class:`JiraIssue` / :class:`JiraComment`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, fields

from ..models import JiraIssue
from ._compat import ExportableIssue, as_legacy

# The frozen top-level key order each JSONL object carries (mirrors JiraIssue).
# Exposed so the export pack can document the schema in ``field_mapping.json``.
_JSONL_ISSUE_KEYS: list[str] = [f.name for f in fields(JiraIssue)]


def export_jsonl(issues: Sequence[ExportableIssue], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for issue in issues:
            fh.write(json.dumps(asdict(as_legacy(issue)), ensure_ascii=False) + "\n")
