"""Field discovery: resolve instance fields into typed :class:`JiraField`."""

from __future__ import annotations

from ..jira_client import JiraClient
from ..models import JiraField


def list_fields(client: JiraClient) -> list[JiraField]:
    return [
        JiraField(id=fid, name=name, custom=fid.startswith("customfield_"))
        for fid, name in client.field_map().items()
    ]


def custom_fields(client: JiraClient) -> list[JiraField]:
    fields = [f for f in list_fields(client) if f.custom]
    return sorted(fields, key=lambda f: f.name.lower())
