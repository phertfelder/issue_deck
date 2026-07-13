"""Comment conversion, comment-mode trimming, and the 'recent comment' helper."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .adf import body_to_text
from .models import JiraComment


class CommentsMode(str, Enum):
    """How many comments to keep per issue after fetching."""

    NONE = "none"       # don't fetch comments at all
    LATEST = "latest"   # keep only the newest N
    ALL = "all"         # keep everything (legacy behavior)
    SINCE = "since"     # keep only comments created on/after a date


@dataclass
class CommentsOptions:
    """User's comment-loading choice. Defaults to the legacy 'load everything'."""

    mode: CommentsMode = CommentsMode.ALL
    latest_n: int = 5
    since: str = ""     # ISO date/datetime; used only when mode is SINCE

    @property
    def load(self) -> bool:
        """Whether any comment fetch is needed at all."""
        return self.mode is not CommentsMode.NONE


class _HasComments(Protocol):
    """Anything carrying a mutable ``comments`` list — :class:`JiraIssue` and
    :class:`~issue_deck.schema.NormalizedIssue` both satisfy this, so the
    helpers below work across the legacy and normalized models without importing
    either (which would create an import cycle with ``schema``)."""

    comments: list[JiraComment]


def build_comments(raw_comments: list[dict]) -> list[JiraComment]:
    out: list[JiraComment] = []
    for c in raw_comments:
        author = c.get("author", {}) or {}
        out.append(JiraComment(
            author=author.get("displayName") or author.get("name") or "",
            created=c.get("created", ""),
            updated=c.get("updated", ""),
            body=body_to_text(c.get("body")),
        ))
    return out


def attach_comments(issue: _HasComments, raw_comments: list[dict]) -> None:
    issue.comments = build_comments(raw_comments)


def parse_comment_dt(s: str) -> _dt.datetime | None:
    """Tolerant parse of a Jira/ISO comment timestamp (``None`` if unparseable)."""
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_aware(dt: _dt.datetime) -> _dt.datetime:
    """Assume UTC for naive datetimes so aware/naive values compare cleanly."""
    return dt.replace(tzinfo=_dt.timezone.utc) if dt.tzinfo is None else dt


def latest_comment_dt(issue: _HasComments) -> _dt.datetime | None:
    parsed = [p for p in (parse_comment_dt(c.created) for c in issue.comments) if p]
    return max(parsed) if parsed else None


def trim_comments(
    comments: list[JiraComment], options: CommentsOptions
) -> list[JiraComment]:
    """Reduce a fully-fetched comment list per the selected mode.

    Comments are fetched in full from the API, then trimmed here so the same code
    path serves every mode. Chronological order is preserved in the output.
    """
    if options.mode is CommentsMode.NONE:
        return []
    if options.mode is CommentsMode.ALL:
        return comments
    if options.mode is CommentsMode.SINCE:
        cutoff = parse_comment_dt(options.since)
        if cutoff is None:
            return comments  # unparseable cutoff -> don't silently drop everything
        cutoff = _as_aware(cutoff)
        return [c for c in comments
                if (d := parse_comment_dt(c.created)) and _as_aware(d) >= cutoff]
    # LATEST: keep the newest N by created time, but emit them oldest-first.
    n = max(0, options.latest_n)
    if n == 0:
        return []
    ordered = sorted(
        comments,
        key=lambda c: _as_aware(parse_comment_dt(c.created) or _dt.datetime.min.replace(
            tzinfo=_dt.timezone.utc)),
    )
    return ordered[-n:]
