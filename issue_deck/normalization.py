"""Convert raw Jira REST issue dicts into typed :class:`JiraIssue` models."""

from __future__ import annotations

from typing import Any

from .adf import body_to_text
from .config import AppConfig
from .models import JiraIssue


def _name_of(v: Any) -> str:
    """Resolve a Jira value that may be a nested dict, a scalar, or None."""
    if isinstance(v, dict):
        return v.get("name") or v.get("displayName") or v.get("value") or ""
    return v or ""


def normalize_issue(issue: dict, cfg: AppConfig) -> JiraIssue:
    f = issue.get("fields", {})

    client_val = _name_of(f.get(cfg.client_field)) if cfg.client_field else ""
    severity_val = _name_of(f.get(cfg.severity_field)) if cfg.severity_field else ""

    return JiraIssue(
        key=issue.get("key", ""),
        url=f"{cfg.base_url.rstrip('/')}/browse/{issue.get('key', '')}",
        summary=f.get("summary", ""),
        status=_name_of(f.get("status")),
        issuetype=_name_of(f.get("issuetype")),
        priority=_name_of(f.get("priority")),
        severity=severity_val,
        client=client_val,
        assignee=_name_of(f.get("assignee")),
        reporter=_name_of(f.get("reporter")),
        created=f.get("created", ""),
        updated=f.get("updated", ""),
        components=[_name_of(c) for c in (f.get("components") or [])],
        labels=f.get("labels", []) or [],
        description=body_to_text(f.get("description")),
        comments=[],
    )
