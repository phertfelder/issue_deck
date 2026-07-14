"""CSV exporter (flat summary columns; nested fields are omitted)."""

from __future__ import annotations

import csv
from collections.abc import Sequence

from ._compat import ExportableIssue, as_legacy

COLUMNS = ["key", "summary", "status", "issuetype", "priority", "severity",
           "client", "assignee", "updated", "url"]


def export_csv(issues: Sequence[ExportableIssue], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for issue in issues:
            n = as_legacy(issue)
            w.writerow({c: getattr(n, c) for c in COLUMNS})
