"""Stable, source-agnostic normalized issue schema.

This module defines the canonical shape the rest of the app targets, regardless
of where an issue came from — a live Jira API pull (Cloud *or* Server/DC) or a
CSV import. The older :mod:`issue_deck.models` types (``JiraIssue``,
``JiraComment``) remain the *export* shape and are intentionally left untouched
so existing Markdown/JSONL/CSV exports stay byte-compatible;
:meth:`NormalizedIssue.to_legacy_issue` bridges the two.

Privacy / persistence invariants baked into the types here:

* **No credentials.** Nothing in this module stores an API token, password, or
  PAT. Auth lives in :mod:`issue_deck.credentials` and never touches a model.
* **No raw API blobs.** :class:`NormalizedIssue` does not keep the full raw
  Jira ``fields`` dict. ``raw_field_values`` holds *only* the custom fields we
  explicitly mapped — never the whole issue.
* **No raw CSV rows.** CSV conversion consumes a row transiently and keeps only
  mapped values; the source row dict is never retained. :class:`CsvImportProfile`
  stores schema (column names + mappings), not data.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Iterable

from .adf import body_to_text
from .comments import build_comments
from .config import AppConfig
from .models import JiraComment, JiraIssue, SearchFilters

__all__ = [
    "JiraDeployment",
    "JiraUser",
    "JiraComment",
    "JiraIssue",
    "JiraFieldDefinition",
    "FieldMapping",
    "SourceMetadata",
    "NormalizedIssue",
    "IssueCollection",
    "SearchFilters",
    "CsvImportProfile",
    "ExportManifest",
    "normalized_from_jira",
]


# --------------------------------------------------------------------------- #
# Deployment / user primitives
# --------------------------------------------------------------------------- #
class JiraDeployment(str, Enum):
    """Which Jira flavour an issue was pulled from.

    A ``str`` enum so it serializes as ``"cloud"`` / ``"server"`` without a
    custom encoder and compares equal to the plain strings ``AppConfig`` uses.
    """

    CLOUD = "cloud"
    SERVER = "server"

    @classmethod
    def coerce(cls, value: "JiraDeployment | str | None") -> "JiraDeployment":
        if isinstance(value, cls):
            return value
        if isinstance(value, str) and value.strip().lower() == cls.SERVER.value:
            return cls.SERVER
        return cls.CLOUD


@dataclass
class JiraUser:
    """A person on an issue (assignee/reporter/comment author).

    Cloud identifies users by ``account_id``; Server/DC by ``username`` (the
    ``name``/``key``). ``email`` is often absent (GDPR-restricted on Cloud) and
    is kept only when the API volunteers it — never required.
    """

    display_name: str = ""
    account_id: str = ""   # Cloud
    username: str = ""     # Server/DC (name or key)
    email: str = ""        # optional; frequently absent

    @property
    def name(self) -> str:
        """Best available human label, with sensible fallbacks."""
        return self.display_name or self.username or self.email or ""

    @classmethod
    def from_raw(cls, raw: Any) -> "JiraUser":
        """Build from a Jira user object, a bare name string, or ``None``."""
        if raw is None or raw == "":
            return cls()
        if isinstance(raw, str):
            return cls(display_name=raw)
        if isinstance(raw, dict):
            return cls(
                display_name=raw.get("displayName") or raw.get("name") or raw.get("key") or "",
                account_id=raw.get("accountId", "") or "",
                username=raw.get("name") or raw.get("key") or "",
                email=raw.get("emailAddress", "") or "",
            )
        return cls(display_name=str(raw))


# --------------------------------------------------------------------------- #
# Field metadata / mapping
# --------------------------------------------------------------------------- #
@dataclass
class JiraFieldDefinition:
    """Describes one instance field (from ``/rest/api/{2,3}/field``).

    Richer than :class:`issue_deck.models.JiraField`: it also carries the
    field's schema type so mappers know whether a value is a scalar, an array,
    a user, a number, or a date.
    """

    id: str
    name: str
    custom: bool = False
    schema_type: str = ""   # e.g. "string", "array", "user", "number", "datetime"
    item_type: str = ""     # for arrays: the element type (e.g. "option", "user")

    @classmethod
    def from_field_dict(cls, raw: dict) -> "JiraFieldDefinition":
        fid = raw.get("id", "")
        schema = raw.get("schema", {}) or {}
        return cls(
            id=fid,
            name=raw.get("name", fid),
            custom=bool(raw.get("custom", fid.startswith("customfield_"))),
            schema_type=schema.get("type", ""),
            item_type=schema.get("items", ""),
        )


@dataclass
class FieldMapping:
    """Maps a *source* field/column onto a :class:`NormalizedIssue` attribute.

    ``source`` is a Jira field id (``customfield_10050``) or a CSV column header
    (``"Client"``). ``target`` is a ``NormalizedIssue`` attribute name. ``transform``
    is an optional coercion hint: ``"multi_select" | "number" | "date" | "user" |
    "text"`` (empty means plain text).
    """

    source: str
    target: str
    transform: str = ""


@dataclass
class SourceMetadata:
    """Provenance for a normalized issue. Never contains credentials."""

    origin: str = "api"           # "api" | "csv"
    deployment: str = ""          # "cloud" | "server" (for api origin)
    imported_at: str = ""         # ISO-8601 UTC
    source_file_name: str = ""    # basename only, for csv origin

    @classmethod
    def for_api(
        cls, deployment: "JiraDeployment | str", imported_at: str = ""
    ) -> "SourceMetadata":
        return cls(
            origin="api",
            deployment=JiraDeployment.coerce(deployment).value,
            imported_at=imported_at or _now_iso(),
        )

    @classmethod
    def for_csv(cls, source_file_name: str = "", imported_at: str = "") -> "SourceMetadata":
        return cls(
            origin="csv",
            source_file_name=source_file_name,
            imported_at=imported_at or _now_iso(),
        )


# --------------------------------------------------------------------------- #
# The normalized issue
# --------------------------------------------------------------------------- #
@dataclass
class NormalizedIssue:
    """Canonical, source-agnostic issue.

    Optional/instance-specific fields (status category, epic, sprints, story
    points, …) default to empty and are populated only when the source supplies
    them, so a minimally-populated issue never raises.
    """

    key: str = ""
    url: str = ""
    summary: str = ""
    description: str = ""
    status: str = ""
    status_category: str = ""
    issue_type: str = ""
    priority: str = ""
    severity: str = ""
    client: str = ""
    assignee: JiraUser = field(default_factory=JiraUser)
    reporter: JiraUser = field(default_factory=JiraUser)
    created: str = ""
    updated: str = ""
    resolved: str = ""
    due_date: str = ""
    labels: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    project_key: str = ""
    project_name: str = ""
    epic_key: str = ""
    epic_name: str = ""
    sprints: list[str] = field(default_factory=list)
    fix_versions: list[str] = field(default_factory=list)
    story_points: float | int | None = None
    comments: list[JiraComment] = field(default_factory=list)
    # Mapped custom fields only — NEVER the full raw issue blob.
    raw_field_values: dict[str, Any] = field(default_factory=dict)
    source: SourceMetadata = field(default_factory=SourceMetadata)

    # ---- conversions ---- #
    @classmethod
    def from_jira(cls, issue: dict, cfg: AppConfig, **kwargs: Any) -> "NormalizedIssue":
        """See :func:`normalized_from_jira`."""
        return normalized_from_jira(issue, cfg, **kwargs)

    @classmethod
    def from_csv_row(
        cls,
        row: dict,
        profile: "CsvImportProfile",
        *,
        source: SourceMetadata | None = None,
    ) -> "NormalizedIssue":
        """Build from one CSV row using ``profile``'s mappings.

        The ``row`` is consumed transiently: only mapped values are retained,
        so no raw row is ever persisted on the resulting issue.
        """
        known = _field_names()
        data: dict[str, Any] = {}
        raw_values: dict[str, Any] = {}
        for m in profile.mappings:
            value = row.get(m.source)
            if m.target not in known:
                # Unmapped-to-a-known-attribute columns land in raw_field_values.
                raw_values[m.source] = _apply_transform(value, m.transform)
                continue
            if m.target in _USER_FIELDS:
                data[m.target] = JiraUser.from_raw(value)
            elif m.target in _LIST_FIELDS:
                data[m.target] = _as_list(value)
            elif m.target in _NUMBER_FIELDS:
                data[m.target] = _to_number(value)
            else:
                data[m.target] = "" if value is None else str(value)
        return cls(
            raw_field_values=raw_values,
            source=source or SourceMetadata.for_csv(profile.source_file_name),
            **data,
        )

    def to_legacy_issue(self) -> JiraIssue:
        """Down-convert to the backward-compatible :class:`JiraIssue` export shape."""
        return JiraIssue(
            key=self.key,
            url=self.url,
            summary=self.summary,
            status=self.status,
            issuetype=self.issue_type,
            priority=self.priority,
            severity=self.severity,
            client=self.client,
            assignee=self.assignee.name,
            reporter=self.reporter.name,
            created=self.created,
            updated=self.updated,
            components=list(self.components),
            labels=list(self.labels),
            description=self.description,
            comments=list(self.comments),
        )


@dataclass
class IssueCollection:
    """A batch of normalized issues plus the filters/provenance that produced it."""

    issues: list[NormalizedIssue] = field(default_factory=list)
    filters: SearchFilters | None = None
    generated_at: str = ""

    def __len__(self) -> int:
        return len(self.issues)

    def to_legacy_issues(self) -> list[JiraIssue]:
        return [i.to_legacy_issue() for i in self.issues]


# --------------------------------------------------------------------------- #
# CSV import profile / export manifest
# --------------------------------------------------------------------------- #
@dataclass
class CsvImportProfile:
    """Schema-only description of a CSV import. Holds NO row data.

    Enforces the Phase-6 privacy invariant at the type level: there is simply
    nowhere here to stash raw rows — only column names, the delimiter, and the
    mappings onto normalized fields.
    """

    name: str = ""
    delimiter: str = ","
    columns: list[str] = field(default_factory=list)
    mappings: list[FieldMapping] = field(default_factory=list)
    source_file_name: str = ""   # basename only, for provenance display

    def mapping_for(self, source: str) -> FieldMapping | None:
        return next((m for m in self.mappings if m.source == source), None)


@dataclass
class ExportManifest:
    """Metadata describing one export run (never the issue payload itself)."""

    fmt: str = ""                 # "markdown_combined" | "markdown_per_ticket" | "jsonl" | "csv"
    destination: str = ""
    issue_count: int = 0
    exported_at: str = ""
    origin: str = ""              # "api" | "csv"
    deployment: str = ""          # "cloud" | "server" (api origin)
    includes_comments: bool = False
    # Explicit user opt-in required before any raw API payload is written out.
    includes_raw: bool = False

    @classmethod
    def build(
        cls,
        fmt: str,
        destination: str,
        collection: IssueCollection,
        *,
        exported_at: str = "",
        includes_raw: bool = False,
    ) -> "ExportManifest":
        first = collection.issues[0].source if collection.issues else SourceMetadata()
        return cls(
            fmt=fmt,
            destination=destination,
            issue_count=len(collection),
            exported_at=exported_at or _now_iso(),
            origin=first.origin,
            deployment=first.deployment,
            includes_comments=any(i.comments for i in collection.issues),
            includes_raw=includes_raw,
        )


# --------------------------------------------------------------------------- #
# Raw Jira issue -> NormalizedIssue
# --------------------------------------------------------------------------- #
def normalized_from_jira(
    issue: dict,
    cfg: AppConfig,
    *,
    deployment: JiraDeployment | str | None = None,
    story_points_field: str = "",
    sprint_field: str = "",
    epic_link_field: str = "",
    epic_name_field: str = "",
    extra_field_ids: Iterable[str] = (),
    source: SourceMetadata | None = None,
    imported_at: str = "",
) -> NormalizedIssue:
    """Convert a raw Jira REST issue dict into a :class:`NormalizedIssue`.

    Handles both Cloud (ADF bodies, ``accountId`` users) and Server/DC (wiki/
    plain bodies, ``name`` users). Custom-field ids for client/severity come
    from ``cfg``; the optional per-instance ids (story points, sprint, epic)
    are passed explicitly so this stays a pure function with no field-id guessing.

    Only the custom fields actually consumed are copied into
    ``raw_field_values`` — the full raw issue is never retained.
    """
    f = issue.get("fields", {}) or {}
    dep = JiraDeployment.coerce(deployment if deployment is not None else cfg.deployment)

    # Fall back to the configured/mapped ids when a caller doesn't pass explicit
    # ones, so a fetch automatically honors the field-mapping the user saved.
    story_points_field = story_points_field or getattr(cfg, "story_points_field", "")
    sprint_field = sprint_field or getattr(cfg, "sprint_field", "")
    epic_link_field = epic_link_field or getattr(cfg, "epic_field", "")

    status_raw = f.get("status") or {}
    project_raw = f.get("project") or {}
    parent_raw = f.get("parent") or {}

    epic_key, epic_name = _epic(parent_raw, f, epic_link_field, epic_name_field)

    src = source or SourceMetadata.for_api(dep, imported_at)

    # Collect the mapped custom-field ids and snapshot only those raw values.
    mapped_ids = [
        cfg.client_field, cfg.severity_field, story_points_field,
        sprint_field, epic_link_field, epic_name_field, *extra_field_ids,
    ]
    raw_field_values = {
        fid: _snapshot_value(f.get(fid)) for fid in mapped_ids if fid and fid in f
    }

    return NormalizedIssue(
        key=issue.get("key", ""),
        url=f"{cfg.base_url.rstrip('/')}/browse/{issue.get('key', '')}",
        summary=f.get("summary", "") or "",
        description=body_to_text(f.get("description")),
        status=_name_of(status_raw),
        status_category=_status_category(status_raw),
        issue_type=_name_of(f.get("issuetype")),
        priority=_name_of(f.get("priority")),
        severity=_name_of(f.get(cfg.severity_field)) if cfg.severity_field else "",
        client=_name_of(f.get(cfg.client_field)) if cfg.client_field else "",
        assignee=JiraUser.from_raw(f.get("assignee")),
        reporter=JiraUser.from_raw(f.get("reporter")),
        created=f.get("created", "") or "",
        updated=f.get("updated", "") or "",
        resolved=f.get("resolutiondate", "") or "",
        due_date=f.get("duedate", "") or "",
        labels=list(f.get("labels") or []),
        components=_multi_names(f.get("components")),
        project_key=project_raw.get("key", "") if isinstance(project_raw, dict) else "",
        project_name=project_raw.get("name", "") if isinstance(project_raw, dict) else "",
        epic_key=epic_key,
        epic_name=epic_name,
        sprints=_sprint_names(f.get(sprint_field)) if sprint_field else [],
        fix_versions=_multi_names(f.get("fixVersions")),
        story_points=_to_number(f.get(story_points_field)) if story_points_field else None,
        comments=_inline_comments(f.get("comment")),
        raw_field_values=raw_field_values,
        source=src,
    )


# --------------------------------------------------------------------------- #
# Value helpers
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _name_of(v: Any) -> str:
    """Resolve a Jira value that may be a nested dict, a scalar, or ``None``."""
    if isinstance(v, dict):
        return v.get("name") or v.get("displayName") or v.get("value") or ""
    return v or ""


def _multi_names(v: Any) -> list[str]:
    """Resolve a (possibly multi-select) value to a list of display names."""
    if not v:
        return []
    if isinstance(v, list):
        return [n for n in (_name_of(x) for x in v) if n]
    name = _name_of(v)
    return [name] if name else []


def _as_list(v: Any) -> list[str]:
    """Coerce a CSV cell (or list) to a list of trimmed strings."""
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [p.strip() for p in re.split(r"[;,]", str(v)) if p.strip()]


def _to_number(v: Any) -> float | int | None:
    """Coerce to int/float, or ``None`` when absent/non-numeric."""
    if isinstance(v, bool):  # guard: bool is an int subclass
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return float(s) if ("." in s or "e" in s.lower()) else int(s)
        except ValueError:
            return None
    return None


def _status_category(status_raw: Any) -> str:
    if isinstance(status_raw, dict):
        sc = status_raw.get("statusCategory") or {}
        if isinstance(sc, dict):
            return sc.get("name") or sc.get("key") or ""
    return ""


def _epic(parent_raw: Any, f: dict, epic_link_field: str, epic_name_field: str) -> tuple[str, str]:
    epic_key = ""
    epic_name = ""
    if isinstance(parent_raw, dict) and parent_raw.get("key"):
        epic_key = parent_raw.get("key", "")
        epic_name = (parent_raw.get("fields") or {}).get("summary", "") or ""
    if epic_link_field and f.get(epic_link_field):
        epic_key = _name_of(f.get(epic_link_field)) or epic_key
    if epic_name_field and f.get(epic_name_field):
        epic_name = _name_of(f.get(epic_name_field)) or epic_name
    return epic_key, epic_name


_SPRINT_NAME_RE = re.compile(r"name=([^,\]]+)")


def _sprint_names(v: Any) -> list[str]:
    """Extract sprint names from dicts or the legacy GreenHopper string form."""
    out: list[str] = []
    for item in (v or []):
        if isinstance(item, dict):
            n = item.get("name")
            if n:
                out.append(n)
        elif isinstance(item, str):
            m = _SPRINT_NAME_RE.search(item)
            out.append(m.group(1) if m else item)
    return out


def _snapshot_value(v: Any) -> Any:
    """Normalize a single mapped custom-field value for ``raw_field_values``.

    Multi-selects become name lists, users become their display name, numbers
    stay numeric, everything else resolves to a display string. This is a
    *derived* value, not the raw API node — we never stash nested blobs.
    """
    if isinstance(v, list):
        return _multi_names(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    return _name_of(v)


def _inline_comments(comment_field: Any) -> list[JiraComment]:
    """Extract comments if the issue payload inlined them (``fields.comment``)."""
    if isinstance(comment_field, dict):
        return build_comments(comment_field.get("comments", []) or [])
    return []


# ---- CSV transform + reflection helpers ---- #
_LIST_FIELDS = {"labels", "components", "fix_versions", "sprints"}
_USER_FIELDS = {"assignee", "reporter"}
_NUMBER_FIELDS = {"story_points"}


def _field_names() -> set[str]:
    """NormalizedIssue attributes a mapping may target directly."""
    reserved = {"comments", "raw_field_values", "source"}
    return {fld.name for fld in fields(NormalizedIssue)} - reserved


def _apply_transform(value: Any, transform: str) -> Any:
    if transform == "multi_select":
        return _as_list(value)
    if transform == "number":
        return _to_number(value)
    if transform == "user":
        return JiraUser.from_raw(value)
    # "date" / "text" / "" -> plain string
    return "" if value is None else str(value)
