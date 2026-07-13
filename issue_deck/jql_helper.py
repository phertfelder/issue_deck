"""Deterministic JQL helper: toggleable clauses, plain-English explanations,
broad-query warnings, live validation, and reusable templates.

This is *not* an LLM feature — every output here is a pure function of the input
filters/JQL. It sits on top of :func:`issue_deck.jql.build_clauses` (the single
source of truth for clause text) and adds the human-facing layer the JQL helper
panel needs:

* :func:`decompose` turns typed :class:`SearchFilters` into a list of
  :class:`HelperClause` objects that can be toggled on/off individually.
* :func:`render` / :func:`explain` / :func:`breadth_warnings` recompute the JQL,
  its plain-English reading, and any "this query is very broad" hints from the
  *enabled* clauses.
* :func:`validate_jql` asks Jira to run the query with ``maxResults=1`` and maps
  the typed error hierarchy to a clean message.
* :data:`BUILTIN_TEMPLATES` + :class:`JqlTemplateStore` provide ready-made and
  user-saved starting points.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from . import constants
from .config import AppConfig
from .jira_client import (
    AuthError,
    InvalidJQLError,
    JiraError,
    JiraPermissionError,
)
from .jql import ORDER_BY_TAIL, Clause, build_clauses
from .models import FieldFilter, SearchFilters

__all__ = [
    "HelperClause",
    "decompose",
    "render",
    "explain",
    "missing_bounds",
    "breadth_warnings",
    "JqlValidation",
    "validate_jql",
    "JqlTemplate",
    "BUILTIN_TEMPLATES",
    "JqlTemplateStore",
    "all_templates",
    "filters_to_dict",
    "filters_from_dict",
]

# The four dimensions that keep a query bounded; a query with none of them is
# flagged as "broad" (see the panel spec).
_BOUNDING_KINDS = {
    "project": "a project",
    "scope": "an assignee/reporter",
    "date": "a created/updated date range",
    "status": "a status or status category",
}
# `status_category` counts as the "status" bound.
_KIND_TO_BOUND = {
    "project": "project",
    "scope": "scope",
    "date": "date",
    "status": "status",
    "status_category": "status",
}

_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_REL_DAYS = re.compile(r"-?(\d+)d")


# --------------------------------------------------------------------------- #
# Clause model
# --------------------------------------------------------------------------- #
@dataclass
class HelperClause:
    """A structured clause plus its plain-English reading and enabled state."""

    text: str
    label: str
    kind: str
    explain: str
    enabled: bool = True


def decompose(cfg: AppConfig, filters: SearchFilters) -> list[HelperClause]:
    """Break ``filters`` into individually-toggleable :class:`HelperClause`.

    In raw mode the whole ``raw_jql`` becomes a single (used-verbatim) clause,
    since it can't be safely split into structured parts.
    """
    if filters.raw_mode and filters.raw_jql.strip():
        raw = filters.raw_jql.strip()
        return [HelperClause(raw, "Raw JQL", "raw_jql",
                             "Runs your raw JQL exactly as written.")]
    return [
        HelperClause(c.text, c.label, c.kind, explain_clause(c))
        for c in build_clauses(cfg, filters)
    ]


def render(clauses: list[HelperClause]) -> str:
    """Join the *enabled* clauses into a JQL string (with the ORDER BY tail).

    A lone raw-JQL clause is returned verbatim (it may carry its own ORDER BY).
    """
    enabled = [c for c in clauses if c.enabled]
    if len(enabled) == 1 and enabled[0].kind == "raw_jql":
        return enabled[0].text
    return " AND ".join(c.text for c in enabled) + ORDER_BY_TAIL


# --------------------------------------------------------------------------- #
# Plain-English explanation
# --------------------------------------------------------------------------- #
def _vals(text: str) -> list[str]:
    return [m.group(1) for m in _QUOTED.finditer(text)]


def _prose(items: list[str], conj: str = "or") -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {conj} {items[1]}"
    return ", ".join(items[:-1]) + f" {conj} {items[-1]}"


def _scope_explain(text: str) -> str:
    who: list[str] = []
    if "assignee = currentUser()" in text:
        who.append("you are the assignee")
    if "reporter = currentUser()" in text:
        who.append("you are the reporter")
    if "watcher = currentUser()" in text:
        who.append("you watch it")
    return "where " + _prose(who) if who else "matching your scope"


def explain_clause(clause: Clause | HelperClause) -> str:
    """A short plain-English reading of a single clause."""
    text, kind = clause.text, clause.kind
    vals = _vals(text)
    if kind == "scope":
        return _scope_explain(text)
    if kind == "project":
        noun = "project" if len(vals) <= 1 else "projects"
        return f"in {noun} {_prose(vals)}"
    if kind == "status_category":
        return f"in the {_prose(vals)} status category"
    if kind == "status":
        return f"with status {_prose(vals)}"
    if kind == "type":
        return f"of type {_prose(vals)}"
    if kind == "sprint":
        return f"in sprint {vals[0]}" if vals else "in the selected sprint"
    if kind == "version":
        return f"for fix version {vals[0]}" if vals else "for the selected fix version"
    if kind == "text":
        return f"matching the text “{vals[0]}”" if vals else "matching the text search"
    if kind == "resolution":
        return "that are unresolved"
    if kind == "date":
        m = _REL_DAYS.search(text)
        n = m.group(1) if m else "?"
        if text.startswith("updated"):
            return f"updated in the last {n} days"
        if text.startswith("created"):
            return f"created in the last {n} days"
        if text.startswith("resolved"):
            return f"resolved in the last {n} days"
        if text.startswith("duedate"):
            return f"due within the next {n} days"
        return f"within the last {n} days"
    if kind == "custom":
        label = getattr(clause, "label", "").strip().lower() or "a custom field"
        if vals:
            if " not in " in text or " != " in text:
                return f"excluding {label} {_prose(vals)}"
            return f"with {label} {_prose(vals)}"
        return f"where {text}"
    if kind == "raw":  # the free-form "Extra JQL" clause
        inner = text[1:-1] if text.startswith("(") and text.endswith(")") else text
        return f"also matching: {inner}"
    return text


def explain(clauses: list[HelperClause]) -> str:
    """A one-sentence plain-English reading of the whole enabled query."""
    enabled = [c for c in clauses if c.enabled]
    if len(enabled) == 1 and enabled[0].kind == "raw_jql":
        return "Runs your raw JQL exactly as written."
    if not enabled:
        return ("Find all issues (no filters applied), sorted by last updated, "
                "newest first.")
    body = _prose([c.explain for c in enabled], conj="and")
    return f"Find issues {body}, sorted by last updated, newest first."


# --------------------------------------------------------------------------- #
# Broad-query warnings
# --------------------------------------------------------------------------- #
def missing_bounds(clauses: list[HelperClause]) -> list[str]:
    """Which of the four bounding dimensions are absent from enabled clauses."""
    present = {
        _KIND_TO_BOUND[c.kind]
        for c in clauses
        if c.enabled and c.kind in _KIND_TO_BOUND
    }
    return [_BOUNDING_KINDS[k] for k in _BOUNDING_KINDS if k not in present]


def breadth_warnings(clauses: list[HelperClause]) -> list[str]:
    """Warn when a query is unbounded on *every* narrowing dimension.

    Mirrors the spec: warn when there's no project, no assignee/reporter, no
    created/updated range, and no status/statusCategory. A lone raw-JQL clause
    can't be analysed, so it never warns here.
    """
    enabled = [c for c in clauses if c.enabled]
    if len(enabled) == 1 and enabled[0].kind == "raw_jql":
        return []
    missing = missing_bounds(clauses)
    if len(missing) == len(_BOUNDING_KINDS):
        return [
            "Very broad query: it isn't bounded by a project, assignee/reporter, "
            "date range, or status — this may return a very large result set."
        ]
    return []


# --------------------------------------------------------------------------- #
# Live validation (Jira maxResults=1)
# --------------------------------------------------------------------------- #
@dataclass
class JqlValidation:
    ok: bool
    message: str
    sample_key: str | None = None
    total: int | None = None


def _clean_jql_error(msg: str) -> str:
    detail = msg.strip()
    for prefix in ("Invalid JQL:", "Invalid JQL"):
        if detail.startswith(prefix):
            detail = detail[len(prefix):].lstrip(": ").strip()
            break
    return f"Jira rejected this JQL: {detail}" if detail else "Jira rejected this JQL."


def validate_jql(client, cfg: AppConfig, jql: str) -> JqlValidation:
    """Run ``jql`` against Jira with ``maxResults=1`` and report the result.

    Never raises: the typed :class:`~issue_deck.jira_client.JiraError` hierarchy
    is mapped to a clean, user-facing message.
    """
    if not jql.strip():
        return JqlValidation(False, "Enter a JQL query to validate.")
    try:
        outcome = client.search(jql, ["summary"], max_results=1)
    except InvalidJQLError as e:
        return JqlValidation(False, _clean_jql_error(str(e)))
    except AuthError:
        return JqlValidation(False, "Not authenticated — check your connection settings.")
    except JiraPermissionError:
        return JqlValidation(
            False, "Your account isn't allowed to run this query on this instance.")
    except JiraError as e:
        return JqlValidation(False, f"Couldn't validate against Jira: {e}")
    except Exception as e:  # noqa: BLE001 - never let validation crash the caller
        return JqlValidation(False, f"Couldn't validate against Jira: {e}")

    issues = outcome.issues
    if issues:
        key = issues[0].get("key") if isinstance(issues[0], dict) else None
        suffix = f" (e.g. {key})" if key else ""
        return JqlValidation(True, f"Valid JQL — matches at least one issue{suffix}.",
                             key, outcome.total)
    return JqlValidation(True, "Valid JQL — no issues currently match.", None, outcome.total)


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
@dataclass
class JqlTemplate:
    """A named, reusable starting query. Carries NO credentials."""

    name: str
    description: str = ""
    filters: SearchFilters = field(default_factory=SearchFilters)
    builtin: bool = False

    def clone_filters(self) -> SearchFilters:
        """A deep-ish copy safe to hand to the mutable builder."""
        return replace(
            self.filters,
            statuses=list(self.filters.statuses),
            issue_types=list(self.filters.issue_types),
            projects=list(self.filters.projects),
            status_categories=list(self.filters.status_categories),
            field_filters=[replace(ff) for ff in self.filters.field_filters],
        )


def _t(name: str, description: str, **kw) -> JqlTemplate:
    return JqlTemplate(name=name, description=description,
                       filters=SearchFilters(**kw), builtin=True)


# The 10 deterministic starting points from the panel spec. Placeholders like
# <PROJECT>/<customer>/<version> are meant to be edited before running.
BUILTIN_TEMPLATES: list[JqlTemplate] = [
    _t("My open work",
       "Unresolved issues assigned to you.",
       assigned_to_me=True, unresolved=True),
    _t("My recently updated work",
       "Issues assigned to you that changed in the last 14 days.",
       assigned_to_me=True, updated_days=14),
    _t("High priority stale work",
       "Your unresolved high-priority issues not updated in 30+ days.",
       assigned_to_me=True, unresolved=True,
       field_filters=[FieldFilter(field="priority", op="in",
                                  value="High, Highest", label="Priority")],
       extra="updated <= -30d"),
    _t("Blocked issues",
       "Your issues in a Blocked status or flagged as an impediment.",
       assigned_to_me=True, extra="status = Blocked OR flagged is not EMPTY"),
    _t("Recently resolved",
       "Your issues resolved in the last 14 days.",
       assigned_to_me=True, resolved_days=14),
    _t("Unassigned in project",
       "Open, unassigned issues in a project (edit the project key).",
       assigned_to_me=False, projects=["<PROJECT>"], unresolved=True,
       extra="assignee is EMPTY"),
    _t("Client/customer work",
       "Issues for a specific client (needs the client field configured).",
       assigned_to_me=False, client="<customer>"),
    _t("Sprint work",
       "Your issues in any currently open sprint.",
       assigned_to_me=True, extra="sprint in openSprints()"),
    _t("Release/fix version work",
       "Issues targeting a fix version (edit the version).",
       assigned_to_me=False, fix_version="<version>"),
    _t("Issues changed since last export",
       "Issues updated since a date you set (your last export).",
       assigned_to_me=True, extra='updated >= "2026-01-01"'),
]


def filters_to_dict(f: SearchFilters) -> dict:
    return asdict(f)


def filters_from_dict(data: dict) -> SearchFilters:
    """Rebuild :class:`SearchFilters`, tolerating unknown/absent keys."""
    data = data or {}
    known = set(SearchFilters.__dataclass_fields__)
    ff = [
        FieldFilter(**{k: v for k, v in item.items()
                       if k in FieldFilter.__dataclass_fields__})
        for item in (data.get("field_filters") or [])
    ]
    filters = SearchFilters(
        **{k: v for k, v in data.items() if k in known and k != "field_filters"})
    filters.field_filters = ff
    return filters


def _template_to_dict(t: JqlTemplate) -> dict:
    return {"name": t.name, "description": t.description,
            "filters": filters_to_dict(t.filters)}


def _template_from_dict(data: dict) -> JqlTemplate:
    return JqlTemplate(
        name=data.get("name", ""),
        description=data.get("description", ""),
        filters=filters_from_dict(data.get("filters", {})),
        builtin=False,
    )


class JqlTemplateStore:
    """Persists user-saved templates to ``<app dir>/jql_templates.json``.

    Only custom templates are stored; the built-ins live in code. Names are
    unique and :meth:`save` upserts by name. A corrupt file never crashes — it
    starts empty. Pass an explicit ``path`` to isolate storage (tests).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._templates: dict[str, JqlTemplate] = {}
        self.load()

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else constants.APP_DIR / "jql_templates.json"

    def load(self) -> None:
        self._templates = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for item in data.get("templates", []):
                    t = _template_from_dict(item)
                    if t.name:
                        self._templates[t.name] = t
            except Exception:
                self._templates = {}

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"templates": [_template_to_dict(t) for t in self._templates.values()]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def names(self) -> list[str]:
        return list(self._templates.keys())

    def all(self) -> list[JqlTemplate]:
        return list(self._templates.values())

    def get(self, name: str) -> JqlTemplate | None:
        return self._templates.get(name)

    def __len__(self) -> int:
        return len(self._templates)

    def __contains__(self, name: object) -> bool:
        return name in self._templates

    def save(self, name: str, filters: SearchFilters, description: str = "") -> JqlTemplate:
        name = name.strip()
        if not name:
            raise ValueError("A template needs a name.")
        t = JqlTemplate(name=name, description=description,
                        filters=filters_from_dict(filters_to_dict(filters)))
        self._templates[name] = t
        self._persist()
        return t

    def delete(self, name: str) -> bool:
        if name in self._templates:
            del self._templates[name]
            self._persist()
            return True
        return False


def all_templates(store: JqlTemplateStore | None) -> list[JqlTemplate]:
    """Built-in templates followed by any custom ones from ``store``."""
    out = list(BUILTIN_TEMPLATES)
    if store is not None:
        out.extend(store.all())
    return out
