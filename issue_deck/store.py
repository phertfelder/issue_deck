"""The current working dataset, its provenance, and reusable saved profiles.

* :class:`InMemoryIssueStore` is the single source of truth for the issues the
  user is working with. It never talks to Jira or disk itself — callers hand it
  :class:`~issue_deck.schema.IssueCollection` batches (produced by any
  :class:`~issue_deck.datasource.DataSource`) and it clears / replaces / merges
  them, tracking where each batch came from.
* :class:`ImportSession` records that provenance and answers the UI's question
  "what am I currently looking at?" — Jira API, CSV, or a mix of both.
* :class:`SavedProfile` is a named, persistable recipe for re-creating a data
  source later (API filters, or a CSV import profile). It stores schema and
  query parameters only — never a token, a password, or raw CSV rows.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .config import AppConfig
from .merge import (
    ConflictResolver,
    ConflictRule,
    DeltaPreview,
    MergeResult,
    build_delta,
    merge_collections,
)
from .models import SearchFilters
from .schema import CsvImportProfile, FieldMapping, IssueCollection, NormalizedIssue

if TYPE_CHECKING:  # avoid an import cycle; only needed for type hints
    from .datasource import DataSourceInfo

__all__ = [
    "SessionKind",
    "ImportSession",
    "InMemoryIssueStore",
    "SavedProfile",
]


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #
class SessionKind(str, Enum):
    """What the currently-loaded dataset is composed of."""

    EMPTY = "empty"
    JIRA_API = "jira_api"
    CSV = "csv"
    MIXED = "mixed"

    @property
    def label(self) -> str:
        return {
            SessionKind.EMPTY: "No data loaded",
            SessionKind.JIRA_API: "Jira API live search",
            SessionKind.CSV: "CSV import",
            SessionKind.MIXED: "Mixed/session data",
        }[self]


@dataclass
class ImportSession:
    """Provenance of the current dataset: which sources fed into it, in order."""

    sources: list["DataSourceInfo"] = field(default_factory=list)
    started_at: str = field(default_factory=_now_iso)

    def reset(self) -> None:
        self.sources.clear()

    def record(self, info: "DataSourceInfo", *, replace: bool = False) -> None:
        if replace:
            self.sources = [info]
        else:
            self.sources.append(info)

    @property
    def kind(self) -> SessionKind:
        kinds = {s.kind for s in self.sources}
        if not kinds:
            return SessionKind.EMPTY
        if len(kinds) > 1:
            return SessionKind.MIXED
        # Single kind: map the DataSourceKind value onto the session kind.
        return SessionKind.JIRA_API if next(iter(kinds)).value == "jira_api" else SessionKind.CSV

    def describe(self) -> str:
        """One-line description for the UI's data-source indicator."""
        kind = self.kind
        if kind == SessionKind.EMPTY:
            return kind.label
        if kind == SessionKind.MIXED:
            details = ", ".join(dict.fromkeys(s.label for s in self.sources))
            return f"{kind.label} ({details})"
        latest = self.sources[-1]
        return f"{kind.label}: {latest.detail}" if latest.detail else kind.label


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
class InMemoryIssueStore:
    """Holds the working set of issues plus its provenance.

    All mutation goes through :meth:`clear`, :meth:`replace`, and :meth:`merge`;
    :meth:`preview_delta` is a non-mutating look-ahead for destructive ops. The
    store keeps only normalized issues, so exporters and filters consume it
    without caring whether the data came from the API or a CSV.
    """

    def __init__(self) -> None:
        self._issues: list[NormalizedIssue] = []
        self.session = ImportSession()

    # ---- reads ----
    @property
    def issues(self) -> list[NormalizedIssue]:
        return self._issues

    def __len__(self) -> int:
        return len(self._issues)

    def is_empty(self) -> bool:
        return not self._issues

    def collection(self) -> IssueCollection:
        """Snapshot the current dataset as an :class:`IssueCollection`."""
        return IssueCollection(issues=list(self._issues), generated_at=_now_iso())

    def describe_source(self) -> str:
        return self.session.describe()

    @property
    def kind(self) -> SessionKind:
        return self.session.kind

    # ---- mutations ----
    def clear(self) -> None:
        """Drop all issues and forget the session provenance."""
        self._issues = []
        self.session.reset()

    def replace(
        self, collection: IssueCollection, info: "DataSourceInfo | None" = None
    ) -> None:
        """Swap the dataset for ``collection`` (the destructive path)."""
        self._issues = list(collection.issues)
        if info is not None:
            self.session.record(info, replace=True)
        else:
            self.session.reset()

    def merge(
        self,
        collection: IssueCollection,
        rule: ConflictRule = ConflictRule.NEWEST_WINS,
        info: "DataSourceInfo | None" = None,
        *,
        resolver: ConflictResolver | None = None,
    ) -> MergeResult:
        """Fold ``collection`` into the current dataset (see :func:`merge_collections`)."""
        result = merge_collections(self._issues, collection.issues, rule, resolver=resolver)
        self._issues = result.issues
        if info is not None:
            self.session.record(info)
        return result

    # ---- look-ahead ----
    def preview_delta(self, collection: IssueCollection) -> DeltaPreview:
        """What :meth:`replace` with ``collection`` would change (no mutation)."""
        return build_delta(self._issues, collection.issues)


# --------------------------------------------------------------------------- #
# Saved profile (reusable data-source recipe)
# --------------------------------------------------------------------------- #
@dataclass
class SavedProfile:
    """A named, persistable recipe for re-creating a data source.

    Holds only what's needed to *rebuild* a source — never credentials or data:

    * API profiles keep the :class:`SearchFilters` plus the instance identity
      (base URL + deployment) so the query is reproducible; no email/token.
    * CSV profiles embed a schema-only :class:`CsvImportProfile` (columns +
      mappings), never rows.
    """

    name: str
    kind: str                                    # DataSourceKind value: "jira_api" | "csv"
    created_at: str = field(default_factory=_now_iso)
    # API params
    base_url: str = ""
    deployment: str = ""
    filters: SearchFilters | None = None
    # CSV params
    csv_profile: CsvImportProfile | None = None

    # ---- constructors ----
    @classmethod
    def for_api(cls, name: str, cfg: AppConfig, filters: SearchFilters) -> "SavedProfile":
        return cls(
            name=name,
            kind="jira_api",
            base_url=cfg.base_url,
            deployment=cfg.deployment,
            filters=filters,
        )

    @classmethod
    def for_csv(cls, name: str, profile: CsvImportProfile) -> "SavedProfile":
        return cls(name=name, kind="csv", csv_profile=profile)

    # ---- (de)serialization ----
    def to_dict(self) -> dict:
        data: dict = {
            "name": self.name,
            "kind": self.kind,
            "created_at": self.created_at,
        }
        if self.kind == "jira_api":
            data["base_url"] = self.base_url
            data["deployment"] = self.deployment
            data["filters"] = asdict(self.filters) if self.filters else None
        else:
            data["csv_profile"] = asdict(self.csv_profile) if self.csv_profile else None
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SavedProfile":
        kind = data.get("kind", "jira_api")
        filters = None
        csv_profile = None
        if kind == "jira_api" and data.get("filters"):
            names = set(SearchFilters.__dataclass_fields__)
            filters = SearchFilters(**{k: v for k, v in data["filters"].items() if k in names})
        elif kind == "csv" and data.get("csv_profile"):
            cp = data["csv_profile"]
            csv_profile = CsvImportProfile(
                name=cp.get("name", ""),
                delimiter=cp.get("delimiter", ","),
                columns=list(cp.get("columns", [])),
                mappings=[FieldMapping(**m) for m in cp.get("mappings", [])],
                source_file_name=cp.get("source_file_name", ""),
            )
        return cls(
            name=data.get("name", ""),
            kind=kind,
            created_at=data.get("created_at", _now_iso()),
            base_url=data.get("base_url", ""),
            deployment=data.get("deployment", ""),
            filters=filters,
            csv_profile=csv_profile,
        )

    # ---- persistence (schema/query only) ----
    def save(self, directory: str | Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{_slug(self.name)}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "SavedProfile":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _slug(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z._-]+", "-", (name or "profile").strip()).strip("-")
    return s or "profile"
