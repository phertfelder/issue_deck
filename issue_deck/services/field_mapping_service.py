"""Deterministic role → custom-field mapping suggestions (Qt-free).

Kills the "copy customfield_10060 by hand" step: given an instance's field
metadata (:class:`~issue_deck.models.JiraField`), infer which field plays each
workbench role — *client*, *severity*, *story points*, *sprint*, *epic* — using
name heuristics only (no LLM, no guessing at values):

* exact normalized name match → high confidence,
* known-synonym match → high/medium,
* token-subset / substring → medium/low,
* generic ("weak") synonyms like *Parent* / *Impact* → capped + flagged,
* two strong candidates for one role → marked ``ambiguous`` (never silently one).

Pure and side-effect-free: it never touches Jira or config. The UI decides what
to persist. Optional per-field ``samples`` are only *displayed*, never stored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import JiraField

__all__ = [
    "FieldRole",
    "FieldMappingSuggestion",
    "ROLES",
    "ROLES_BY_KEY",
    "suggest_role",
    "suggest_all",
]


@dataclass(frozen=True)
class FieldRole:
    """A workbench role, its config attribute, and its name synonyms."""

    key: str
    label: str
    config_attr: str
    strong: tuple[str, ...]
    weak: tuple[str, ...] = ()


# The five roles wired end-to-end (config + fetch + normalization). Team, fix
# version, components and labels are intentionally out of scope for this pass.
ROLES: tuple[FieldRole, ...] = (
    FieldRole("client", "Client", "client_field",
              ("Client", "Customer", "Account", "Tenant", "Customer Name", "Client Name")),
    FieldRole("severity", "Severity", "severity_field",
              ("Severity", "Incident Severity", "Bug Severity", "Support Severity"),
              ("Impact",)),
    FieldRole("story_points", "Story Points", "story_points_field",
              ("Story Points", "Story point estimate", "Story Points estimate"),
              ("Points", "Estimate", "Effort")),
    FieldRole("sprint", "Sprint", "sprint_field",
              ("Sprint", "Sprints")),
    FieldRole("epic", "Epic", "epic_field",
              ("Epic Link", "Epic Name", "Parent Epic"),
              ("Parent",)),
)
ROLES_BY_KEY = {r.key: r for r in ROLES}

_MIN_CONFIDENCE = 50   # below this, report "no matching field"


@dataclass
class FieldMappingSuggestion:
    """The best field guessed for a role, with confidence and rationale."""

    role: str
    role_label: str
    config_attr: str
    field_id: str = ""
    field_name: str = ""
    confidence: int = 0
    reason: str = ""
    ambiguous: bool = False
    sample: str = ""

    @property
    def has_suggestion(self) -> bool:
        return bool(self.field_id)

    @property
    def band(self) -> str:
        """Badge band per the spec: green ≥90 / amber ≥70 / grey below."""
        if not self.field_id:
            return "none"
        if self.confidence >= 90:
            return "high"
        if self.confidence >= 70:
            return "medium"
        return "low"


_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "").strip().lower())


def _score_name(role: FieldRole, name: str) -> tuple[int, str]:
    """Best (score, reason) for a field ``name`` against ``role``'s synonyms."""
    n = _norm(name)
    if not n:
        return 0, ""
    ntok = set(n.split())
    best, reason = 0, ""

    def consider(score: int, why: str) -> None:
        nonlocal best, reason
        if score > best:
            best, reason = score, why

    for i, syn in enumerate(role.strong):
        sn = _norm(syn)
        stok = set(sn.split())
        if n == sn:
            consider(96 if i == 0 else 90,
                     "Exact name match" if i == 0 else f"Matches synonym “{syn}”")
        elif stok and stok <= ntok:
            consider(80, f"Contains “{syn}”")
        elif sn in n or n in sn:
            consider(68, f"Loosely matches “{syn}”")

    for syn in role.weak:
        sn = _norm(syn)
        stok = set(sn.split())
        if n == sn:
            consider(72, f"Generic term “{syn}” — verify")
        elif stok and stok <= ntok:
            consider(60, f"Generic term “{syn}” — verify")
        elif sn in n or n in sn:
            consider(55, f"Loosely matches “{syn}” — verify")

    return best, reason


def suggest_role(
    role: FieldRole,
    fields: list[JiraField],
    samples: dict[str, str] | None = None,
) -> FieldMappingSuggestion:
    """Best field for ``role``, or an empty suggestion when nothing matches."""
    scored = []
    for fld in fields:
        score, why = _score_name(role, fld.name)
        if score > 0:
            scored.append((score, fld, why))
    scored.sort(key=lambda t: (-t[0], t[1].name.lower()))

    empty = FieldMappingSuggestion(
        role=role.key, role_label=role.label, config_attr=role.config_attr,
        reason="No matching field found")
    if not scored or scored[0][0] < _MIN_CONFIDENCE:
        return empty

    top_score, top_field, top_reason = scored[0]
    ambiguous = False
    if len(scored) >= 2:
        second = scored[1][0]
        if (top_score >= 85 and second >= 85) or ((top_score - second) <= 6 and second >= 70):
            ambiguous = True
            top_reason += f" (ambiguous: also “{scored[1][1].name}”)"

    return FieldMappingSuggestion(
        role=role.key, role_label=role.label, config_attr=role.config_attr,
        field_id=top_field.id, field_name=top_field.name,
        confidence=top_score, reason=top_reason, ambiguous=ambiguous,
        sample=(samples or {}).get(top_field.id, ""))


def suggest_all(
    fields: list[JiraField],
    samples: dict[str, str] | None = None,
) -> list[FieldMappingSuggestion]:
    """One suggestion per role, in :data:`ROLES` order."""
    return [suggest_role(role, fields, samples) for role in ROLES]
