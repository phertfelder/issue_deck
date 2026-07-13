"""A common abstraction over the two ways issues enter the app.

Both a live Jira API search and a local CSV import ultimately yield the same
thing — a :class:`~issue_deck.schema.IssueCollection` of
:class:`~issue_deck.schema.NormalizedIssue`. This module hides *where* the
issues came from behind a single :class:`DataSource` protocol so everything
downstream (the in-memory store, filters, exporters) is source-agnostic.

* :class:`JiraApiDataSource` wraps the live-search path (client + JQL filters).
* :class:`CsvDataSource` wraps a parsed CSV + import profile.

Neither the protocol nor its implementations retain credentials or raw CSV rows;
they only ever hand back normalized issues.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol, runtime_checkable

from .comments import CommentsMode, CommentsOptions
from .config import AppConfig
from .csv_import import ImportOptions, ParsedCsv, redact_key, to_normalized_issues
from .jira_client import JiraClient
from .jql import build_jql
from .models import SearchFilters
from .schema import CsvImportProfile, IssueCollection
from .services import issue_service

# A simple textual status callback for the data-source layer. The API source
# adapts the service's structured progress into these one-line strings.
StatusFn = Callable[[str], None]

__all__ = [
    "DataSourceKind",
    "DataSourceInfo",
    "DataSource",
    "JiraApiDataSource",
    "CsvDataSource",
]


class DataSourceKind(str, Enum):
    """Where a batch of issues originated. ``str`` enum for painless serializing."""

    JIRA_API = "jira_api"
    CSV = "csv"


@dataclass
class DataSourceInfo:
    """Human-facing provenance for a data source (never secrets).

    ``label`` is a short display string ("Jira API live search", "CSV import");
    ``detail`` is the JQL or file basename the UI can show as a subtitle.
    """

    kind: DataSourceKind
    label: str
    detail: str = ""
    deployment: str = ""          # "cloud" | "server" (api only)
    loaded_at: str = ""

    @property
    def origin(self) -> str:
        """The :class:`SourceMetadata` origin string this source stamps issues with."""
        return "api" if self.kind == DataSourceKind.JIRA_API else "csv"


@runtime_checkable
class DataSource(Protocol):
    """Anything that can produce an :class:`IssueCollection` on demand."""

    def describe(self) -> DataSourceInfo:
        """Return provenance for display/tracking (no I/O)."""
        ...

    def load(self, on_status: StatusFn | None = None) -> IssueCollection:
        """Materialize the issues. May do I/O (HTTP for API, none for CSV)."""
        ...


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Jira API
# --------------------------------------------------------------------------- #
@dataclass
class JiraApiDataSource:
    """Live Jira search: turns :class:`SearchFilters` into JQL and fetches.

    The heavy lifting stays in :mod:`issue_deck.services.issue_service`; this is
    the thin adapter that presents it as a :class:`DataSource`.
    """

    client: JiraClient
    cfg: AppConfig
    filters: SearchFilters = field(default_factory=SearchFilters)

    def jql(self) -> str:
        return build_jql(self.cfg, self.filters)

    def describe(self) -> DataSourceInfo:
        return DataSourceInfo(
            kind=DataSourceKind.JIRA_API,
            label="Jira API live search",
            detail=self.jql(),
            deployment=self.cfg.deployment,
        )

    def load(self, on_status: StatusFn | None = None) -> IssueCollection:
        jql = self.jql()
        # Adapt the service's structured progress into one-line status strings.
        on_progress = (lambda p: on_status(p.describe())) if on_status else None
        comments = CommentsOptions(
            mode=CommentsMode.ALL if self.filters.load_comments else CommentsMode.NONE)
        result = issue_service.fetch_issues(
            self.client, jql, self.cfg, comments=comments, on_progress=on_progress,
        )
        issues = issue_service.filter_commented_within(
            result.issues, self.filters.commented_days)
        return IssueCollection(
            issues=issues, filters=self.filters, generated_at=_now_iso()
        )


# --------------------------------------------------------------------------- #
# CSV import
# --------------------------------------------------------------------------- #
@dataclass
class CsvDataSource:
    """A parsed CSV + import profile presented as a :class:`DataSource`.

    Holds the (transient) :class:`ParsedCsv` only until :meth:`load` derives
    normalized issues; nothing here is persisted. ``options.redact_keys`` masks
    issue keys in the produced issues, mirroring the import wizard's preview.
    """

    parsed: ParsedCsv
    profile: CsvImportProfile
    options: ImportOptions = field(default_factory=ImportOptions)

    def describe(self) -> DataSourceInfo:
        name = self.profile.source_file_name or self.profile.name or "CSV"
        return DataSourceInfo(
            kind=DataSourceKind.CSV,
            label="CSV import",
            detail=name,
        )

    def load(self, on_status: StatusFn | None = None) -> IssueCollection:
        if on_status:
            on_status(f"Importing {self.parsed.row_count} row(s) from CSV…")
        issues = to_normalized_issues(self.parsed, self.profile)
        if self.options.redact_keys:
            from dataclasses import replace

            issues = [replace(i, key=redact_key(i.key)) for i in issues]
        return IssueCollection(issues=issues, generated_at=_now_iso())
