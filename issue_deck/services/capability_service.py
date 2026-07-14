"""Instance capability detection: which optional JQL clauses are available.

Some clauses only work on instances that enable the underlying feature. The
prime example is ``watcher = currentUser()`` — it requires issue *watching* to be
turned on; on instances where it is disabled the clause is not searchable and a
query using it errors out. The workbench uses this to gate the "Watched by me"
toggle ("where supported"), rather than letting a fetch fail.

Detection is split so the decision logic stays pure and unit-testable:
:func:`detect_capabilities` inspects the raw ``/field`` descriptors (no HTTP),
while :func:`fetch_capabilities` wires it to a live client.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..jira_client import JiraClient

__all__ = ["Capabilities", "detect_capabilities", "fetch_capabilities"]


@dataclass
class Capabilities:
    """Optional query features available on the connected instance.

    Defaults are *optimistic* (everything supported) so behaviour is unchanged
    until a probe positively determines a feature is missing — we never disable
    a working feature just because we couldn't ask."""

    watcher_search: bool = True


def _clause_names(field: dict) -> list[str]:
    return [str(n).lower() for n in (field.get("clauseNames") or [])]


def detect_capabilities(fields: list[dict]) -> Capabilities:
    """Infer capabilities from ``/rest/api/*/field`` descriptors (pure).

    A clause is "supported" when a field exposes it under ``clauseNames`` and is
    still ``searchable``. If *no* field exposes ``clauseNames`` at all (an older
    API that omits them), we can't tell, so we stay optimistic rather than risk
    a false negative that hides a working toggle.
    """
    if not any(f.get("clauseNames") for f in fields):
        return Capabilities()  # API doesn't expose clause names -> assume supported

    watcher = any(
        "watcher" in _clause_names(f) and f.get("searchable", True)
        for f in fields
    )
    return Capabilities(watcher_search=watcher)


def fetch_capabilities(client: JiraClient) -> Capabilities:
    """Probe the live instance for capabilities (one ``/field`` request)."""
    return detect_capabilities(client.fields_raw())
