"""Client-backed value sources for populating filter dropdowns.

Two complementary ways to get useful filter values off a live instance:

* **Authoritative option lists** — for fields Jira exposes directly (projects,
  statuses, issue types, priorities, users, per-project components/versions) we
  ask the API for the canonical set. See :func:`options_for_field`.
* **Bounded sampling** — for custom fields Jira does not expose options for
  (severity, client, arbitrary ``customfield_*``), we pull a capped sample of
  issues and let :mod:`issue_deck.field_values` derive the value distribution.
  See :func:`sample_issues`.

Every option fetch is best-effort: a permission error or missing endpoint yields
an empty list rather than raising, so the discovery UI degrades to sampling.
All Jira access goes through the injected client; nothing here touches Qt.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..cancellation import CancelToken
from ..config import AppConfig
from ..jira_client import JiraError
from ..schema import NormalizedIssue, SourceMetadata, normalized_from_jira
from .issue_service import search_fields

__all__ = ["FieldOption", "options_for_field", "sample_issues"]


@dataclass
class FieldOption:
    """One selectable value for a filter dropdown."""

    value: str            # the value used in a filter/JQL clause
    label: str = ""       # human label (defaults to ``value``)

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.value


def _opt(value: str, label: str = "") -> FieldOption | None:
    """Build an option, dropping blanks."""
    value = (value or "").strip()
    return FieldOption(value=value, label=(label or value)) if value else None


def _dedup(options: list[FieldOption | None]) -> list[FieldOption]:
    """Drop blanks and later duplicates (by value), preserving order."""
    seen: set[str] = set()
    out: list[FieldOption] = []
    for opt in options:
        if opt is None or opt.value in seen:
            continue
        seen.add(opt.value)
        out.append(opt)
    return out


def _name(raw: dict) -> str:
    return str(raw.get("name") or raw.get("value") or "").strip()


# --------------------------------------------------------------------------- #
# Authoritative option lists
# --------------------------------------------------------------------------- #
def project_options(client) -> list[FieldOption]:
    return _dedup([
        _opt(p.get("key", ""), f"{p.get('key', '')} — {p.get('name', '')}".strip(" —"))
        for p in client.projects()
    ])


def status_options(client) -> list[FieldOption]:
    return _dedup([_opt(_name(s)) for s in client.statuses()])


def issue_type_options(client) -> list[FieldOption]:
    return _dedup([_opt(_name(t)) for t in client.issue_types()])


def priority_options(client) -> list[FieldOption]:
    return _dedup([_opt(_name(p)) for p in client.priorities()])


def user_options(client, query: str) -> list[FieldOption]:
    out: list[FieldOption | None] = []
    for u in client.search_users(query):
        name = str(u.get("displayName") or u.get("name") or "").strip()
        email = str(u.get("emailAddress") or "").strip()
        out.append(_opt(name, f"{name} <{email}>" if email else name))
    return _dedup(out)


def component_options(client, project_key: str) -> list[FieldOption]:
    return _dedup([_opt(_name(c)) for c in client.project_components(project_key)])


def version_options(client, project_key: str) -> list[FieldOption]:
    return _dedup([_opt(_name(v)) for v in client.project_versions(project_key)])


# Field ids that have an authoritative, instance-wide option source.
_GLOBAL_SOURCES = {
    "project_key": project_options,
    "status": status_options,
    "issue_type": issue_type_options,
    "priority": priority_options,
}
# Field ids whose options are scoped to a project.
_PROJECT_SOURCES = {
    "components": component_options,
    "fix_versions": version_options,
}


def options_for_field(
    client,
    field_id: str,
    *,
    project_key: str = "",
) -> list[FieldOption] | None:
    """Authoritative options for ``field_id``, or ``None`` if there's no source.

    ``None`` (as opposed to ``[]``) means "Jira doesn't expose options for this
    field — infer them from a sample instead". Any API error is swallowed to
    ``[]`` so the caller can still fall back to sampling.
    """
    try:
        if field_id in _GLOBAL_SOURCES:
            return _GLOBAL_SOURCES[field_id](client)
        if field_id in _PROJECT_SOURCES:
            return _PROJECT_SOURCES[field_id](client, project_key) if project_key else None
    except JiraError:
        return []
    return None


# --------------------------------------------------------------------------- #
# Bounded sampling
# --------------------------------------------------------------------------- #
def sample_issues(
    client,
    cfg: AppConfig,
    *,
    jql: str,
    max_issues: int,
    extra_field_ids: tuple[str, ...] = (),
    cancel: CancelToken | None = None,
) -> list[NormalizedIssue]:
    """Pull up to ``max_issues`` issues for ``jql`` and normalize them.

    Uses the client's bounded ``max_results`` search (comments are never fetched
    — sampling only needs field values). Only the configured/mapped custom fields
    are retained on each issue; the raw payload is dropped by
    :func:`normalized_from_jira`, preserving the no-raw-blob invariant.
    ``extra_field_ids`` requests and keeps additional custom fields so their
    value distributions can be derived.
    """
    fields = search_fields(cfg)
    fields += [f for f in extra_field_ids if f not in fields]
    outcome = client.search(jql, fields, max_results=max_issues, cancel=cancel)
    source = SourceMetadata.for_api(cfg.deployment)
    return [
        normalized_from_jira(i, cfg, source=source, extra_field_ids=extra_field_ids)
        for i in outcome.issues
    ]
