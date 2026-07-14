"""Pure, deterministic transforms applied to issues before they are written.

Everything here operates on :class:`~issue_deck.schema.NormalizedIssue` values
and returns *new* issues (via :func:`dataclasses.replace`) — the store's copies
are never mutated. The pipeline is:

    prepare_issues = sort -> redact (keys/people/clients) -> trim/truncate comments
                     -> truncate/drop descriptions

Redaction is deterministic: the same input list always yields the same
pseudonyms (``Person 1``, ``Client 1``, …), so two export runs of the same data
diff cleanly. Issues are sorted *first* so pseudonym numbering is stable and does
not depend on the store's insertion order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from ..models import JiraComment
from ..redaction import pseudonymize, redact_key, scrub_text
from ..schema import JiraUser, NormalizedIssue
from .options import GROUP_BY_LABELS, NONE_BUCKET, ExportConfig

# Priority rank (lower = more urgent) for the "priority" sort. Unknown priorities
# sort after all known ones but before a truly empty priority.
_PRIORITY_RANK: dict[str, int] = {
    "blocker": 0, "highest": 0, "p0": 0,
    "critical": 1, "p1": 1,
    "high": 2, "major": 2,
    "medium": 3, "normal": 3, "p2": 3,
    "low": 4, "minor": 4, "p3": 4,
    "lowest": 5, "trivial": 5,
}
_UNKNOWN_PRIORITY = 50
_EMPTY_PRIORITY = 99


# --------------------------------------------------------------------------- #
# Sorting
# --------------------------------------------------------------------------- #
def _priority_rank(name: str) -> int:
    if not name:
        return _EMPTY_PRIORITY
    return _PRIORITY_RANK.get(name.strip().lower(), _UNKNOWN_PRIORITY)


def _sort_key(issue: NormalizedIssue, sort_by: str):
    if sort_by == "priority":
        return (_priority_rank(issue.priority), issue.key)
    if sort_by == "key":
        return (issue.key,)
    if sort_by == "status":
        return (issue.status.lower(), issue.key)
    if sort_by == "assignee":
        return (issue.assignee.name.lower(), issue.key)
    # default: "updated"
    return (issue.updated, issue.key)


def sort_issues(issues: Sequence[NormalizedIssue], config: ExportConfig) -> list[NormalizedIssue]:
    ordered = sorted(issues, key=lambda i: _sort_key(i, config.sort_by))
    if config.sort_desc:
        ordered.reverse()
    return ordered


# --------------------------------------------------------------------------- #
# Redaction (deterministic pseudonyms)
# --------------------------------------------------------------------------- #
def _people_names(issues: Sequence[NormalizedIssue]) -> list[str]:
    names: list[str] = []
    for issue in issues:
        names.append(issue.assignee.name)
        names.append(issue.reporter.name)
        names.extend(c.author for c in issue.comments)
    return names


def _redact_user(user: JiraUser, mapping: dict[str, str]) -> JiraUser:
    alias = mapping.get(user.name)
    # Drop account/username/email too — those identify a person just as well.
    return JiraUser(display_name=alias) if alias else JiraUser()


def _apply_redaction(
    issues: Sequence[NormalizedIssue], config: ExportConfig
) -> list[NormalizedIssue]:
    people = pseudonymize(_people_names(issues), "Person") if config.redact_people else {}
    clients = (
        pseudonymize([i.client for i in issues], "Client") if config.redact_clients else {}
    )
    out: list[NormalizedIssue] = []
    for issue in issues:
        changes: dict = {}
        if config.redact_keys:
            changes["key"] = redact_key(issue.key)
            # A browse URL embeds the key, so mask it there too.
            if issue.url and issue.key and issue.key in issue.url:
                changes["url"] = issue.url.replace(issue.key, redact_key(issue.key))
        if config.redact_people:
            changes["assignee"] = _redact_user(issue.assignee, people)
            changes["reporter"] = _redact_user(issue.reporter, people)
            changes["comments"] = [
                replace(c, author=people.get(c.author, "")) for c in issue.comments
            ]
        if config.redact_clients and issue.client:
            changes["client"] = clients.get(issue.client, "")
        out.append(replace(issue, **changes) if changes else issue)
    return out


# --------------------------------------------------------------------------- #
# Comment/description shaping
# --------------------------------------------------------------------------- #
def _truncate(text: str, limit: int) -> str:
    if limit and len(text) > limit:
        return text[:limit].rstrip() + " …[truncated]"
    return text


def _shape_comments(
    comments: list[JiraComment], config: ExportConfig
) -> list[JiraComment]:
    if not config.include_comments:
        return []
    kept = comments
    if config.latest_comments > 0:
        kept = kept[-config.latest_comments:]
    if config.max_comment_chars:
        kept = [replace(c, body=_truncate(c.body, config.max_comment_chars)) for c in kept]
    return list(kept)


def _shape_body(issue: NormalizedIssue, config: ExportConfig) -> NormalizedIssue:
    changes: dict = {}
    if not config.include_descriptions:
        changes["description"] = ""
    elif config.max_description_chars:
        changes["description"] = _truncate(issue.description, config.max_description_chars)
    comments = _shape_comments(issue.comments, config)
    if comments != issue.comments:
        changes["comments"] = comments
    return replace(issue, **changes) if changes else issue


# --------------------------------------------------------------------------- #
# Free-text scrubbing (emails / URLs)
# --------------------------------------------------------------------------- #
def _scrub_issue(issue: NormalizedIssue, config: ExportConfig) -> NormalizedIssue:
    """Scrub emails/URLs from every free-text field of ``issue``.

    Applied to the summary, description and comment bodies — the places arbitrary
    prose (and thus stray addresses/links) can appear. A no-op unless
    ``redact_emails`` or ``redact_urls`` is set.
    """
    if not (config.redact_emails or config.redact_urls):
        return issue
    kw = {"emails": config.redact_emails, "urls": config.redact_urls}
    changes: dict = {
        "summary": scrub_text(issue.summary, **kw),
        "description": scrub_text(issue.description, **kw),
    }
    if issue.comments:
        changes["comments"] = [
            replace(c, body=scrub_text(c.body, **kw)) for c in issue.comments
        ]
    return replace(issue, **changes)


# --------------------------------------------------------------------------- #
# Public pipeline
# --------------------------------------------------------------------------- #
def prepare_issues(
    issues: Sequence[NormalizedIssue], config: ExportConfig
) -> list[NormalizedIssue]:
    """Return export-ready issues: sorted, redacted, and comment/description-shaped."""
    config = config.normalized()
    ordered = sort_issues(issues, config)
    redacted = _apply_redaction(ordered, config)
    return [_scrub_issue(_shape_body(i, config), config) for i in redacted]


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #
def group_value(issue: NormalizedIssue, group_by: str) -> str:
    """The single bucket ``issue`` falls into for ``group_by`` (``NONE_BUCKET`` if empty)."""
    if group_by == "status":
        v = issue.status
    elif group_by == "assignee":
        v = issue.assignee.name
    elif group_by == "priority":
        v = issue.priority
    elif group_by == "severity":
        v = issue.severity
    elif group_by == "component":
        v = issue.components[0] if issue.components else ""
    elif group_by == "project":
        v = issue.project_key or issue.project_name
    elif group_by == "epic":
        v = issue.epic_key or issue.epic_name
    elif group_by == "client":
        v = issue.client
    else:
        v = ""
    return v or NONE_BUCKET


def group_issues(
    issues: Sequence[NormalizedIssue], group_by: str
) -> list[tuple[str, list[NormalizedIssue]]]:
    """Group ``issues`` into ``[(bucket, issues), …]`` preserving input order.

    Buckets appear in first-seen order (input is already sorted), except the
    ``(none)`` bucket which is always emitted last. Returns a single ``("", …)``
    group when ``group_by`` is empty/unknown so callers can render uniformly.
    """
    if group_by not in GROUP_BY_LABELS:
        return [("", list(issues))]
    buckets: dict[str, list[NormalizedIssue]] = {}
    for issue in issues:
        buckets.setdefault(group_value(issue, group_by), []).append(issue)
    none = buckets.pop(NONE_BUCKET, None)
    result = list(buckets.items())
    if none is not None:
        result.append((NONE_BUCKET, none))
    return result
