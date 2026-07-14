"""Issue fetching orchestration: search -> normalize -> attach comments.

Pure business logic with no Qt/HTTP-transport concerns of its own; the UI wraps
this in a worker thread and the client performs the actual requests. This layer
owns the *policy* around a fetch: emitting a structured :class:`FetchProgress`
stream, honouring a :class:`CancelToken`, applying a max-issue cap, trimming
comments per the selected mode, and collecting per-issue comment failures as
visible warnings instead of swallowing them.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Callable

from ..cancellation import CancelledError, CancelToken
from ..comments import CommentsOptions, attach_comments, latest_comment_dt, trim_comments
from ..config import AppConfig
from ..constants import BASE_SEARCH_FIELDS
from ..jira_client import JiraClient
from ..progress import FetchProgress, Phase, RetryEvent
from ..schema import NormalizedIssue, SourceMetadata, normalized_from_jira

ProgressFn = Callable[[FetchProgress], None]


@dataclass
class IssueWarning:
    """A non-fatal problem tied to one issue (e.g. its comments failed to load)."""

    key: str
    message: str


@dataclass
class FetchResult:
    """Everything a completed fetch produced, including caveats to surface."""

    issues: list[NormalizedIssue]
    warnings: list[IssueWarning] = field(default_factory=list)
    truncated: bool = False              # a max-issue cap hid further results
    total_available: int | None = None   # server-reported total, when known
    cap: int | None = None               # the cap that was applied, if any

    @property
    def cap_warning(self) -> str:
        """A human warning when results were capped, else ''."""
        if not self.truncated:
            return ""
        of = f" of {self.total_available}" if self.total_available else ""
        return (f"Results capped at {self.cap}{of}. More issues match — raise or "
                f"clear the cap, or narrow the query, to see them all.")


def search_fields(cfg: AppConfig) -> list[str]:
    fields = list(BASE_SEARCH_FIELDS)
    # Append configured/mapped custom-field ids (dedup, drop blanks/'parent'
    # which is already requested as a base field).
    mapped = [
        cfg.client_field, cfg.severity_field,
        getattr(cfg, "story_points_field", ""),
        getattr(cfg, "sprint_field", ""),
        getattr(cfg, "epic_field", ""),
    ]
    for fid in mapped:
        fid = (fid or "").strip()
        if fid and fid != "parent" and fid not in fields:
            fields.append(fid)
    return fields


def fetch_issues(
    client: JiraClient,
    jql: str,
    cfg: AppConfig,
    *,
    comments: CommentsOptions | None = None,
    cancel: CancelToken | None = None,
    on_progress: ProgressFn | None = None,
    max_issues: int | None = None,
    fail_on_comment_error: bool = False,
) -> FetchResult:
    """Run a full fetch. Raises :class:`CancelledError` if cancelled mid-flight."""
    comments = comments or CommentsOptions()
    token = cancel or CancelToken()
    warnings: list[IssueWarning] = []
    fetched = 0
    total: int | None = None

    def emit(phase: Phase, **kw) -> None:
        if on_progress is not None:
            on_progress(FetchProgress(phase=phase, fetched=fetched, total=total, **kw))

    def search_progress(f: int, t: int | None) -> None:
        nonlocal fetched, total
        fetched, total = f, t
        emit(Phase.SEARCHING)

    def search_retry(ev: RetryEvent) -> None:
        emit(Phase.RETRYING, message=ev.message())

    emit(Phase.SEARCHING)
    outcome = client.search(
        jql, search_fields(cfg), on_progress=search_progress, cancel=token,
        on_retry=search_retry, max_results=max_issues,
    )
    token.raise_if_cancelled()

    source = SourceMetadata.for_api(cfg.deployment)
    issues = [normalized_from_jira(i, cfg, source=source) for i in outcome.issues]
    fetched = len(issues)
    total = outcome.total

    if comments.load:
        count = len(issues)
        for idx, issue in enumerate(issues, 1):
            token.raise_if_cancelled()
            emit(Phase.LOADING_COMMENTS, current_key=issue.key,
                 message=f"Loading comments {idx}/{count}")

            def comment_retry(ev: RetryEvent, key: str = issue.key) -> None:
                emit(Phase.RETRYING, current_key=key, message=ev.message())

            try:
                raw_comments = client.get_comments(
                    issue.key, cancel=token, on_retry=comment_retry)
                attach_comments(issue, raw_comments)
                issue.comments = trim_comments(issue.comments, comments)
            except CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - one issue's comments, not the run
                if fail_on_comment_error:
                    raise
                warnings.append(IssueWarning(
                    issue.key, f"Failed to load comments: {e}"))

    emit(Phase.DONE)
    return FetchResult(
        issues=issues, warnings=warnings, truncated=outcome.truncated,
        total_available=outcome.total, cap=max_issues,
    )


def filter_commented_within(issues: list[NormalizedIssue], days: int) -> list[NormalizedIssue]:
    """Keep only issues whose latest comment is within ``days`` (0 = disabled)."""
    if days <= 0:
        return issues
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    return [n for n in issues if (lc := latest_comment_dt(n)) and lc >= cutoff]
