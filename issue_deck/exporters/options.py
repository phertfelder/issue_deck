"""Rich export options and provenance context for the LLM-ready export packs.

:class:`ExportConfig` is the *what to include / how to shape it* knob set the UI
collects (comments, descriptions, redaction, truncation, grouping, sorting).
:class:`ExportContext` is the *where did this come from* provenance the manifest
records (source type, Jira host, JQL, CSV filename, field mapping, warnings).

Both are plain Qt-free dataclasses so the whole export layer stays testable and
importable without a display, in keeping with the project's layering rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..constants import APP_VERSION
from ..redaction import RedactionSettings

# Group-by dimension -> the human label used in section headings. The values are
# resolved against a NormalizedIssue in :mod:`transform`.
GROUP_BY_LABELS: dict[str, str] = {
    "status": "Status",
    "assignee": "Assignee",
    "priority": "Priority",
    "severity": "Severity",
    "component": "Component",
    "project": "Project",
    "epic": "Epic",
    "client": "Client",
}

# Allowed sort keys (resolved in :mod:`transform`). "priority" uses a rank table.
SORT_FIELDS: tuple[str, ...] = ("updated", "priority", "key", "status", "assignee")

# The value used for a missing group/redaction bucket.
NONE_BUCKET = "(none)"


@dataclass
class ExportConfig:
    """User choices that shape the *content* of an export.

    Every field defaults so ``ExportConfig()`` yields a faithful, unredacted,
    comment-inclusive export — callers only flip what they want to change.
    """

    # --- content toggles ---
    include_comments: bool = True
    latest_comments: int = 0          # 0 = all comments; N = only the latest N
    include_descriptions: bool = True

    # --- redaction ---
    redact_keys: bool = False
    redact_people: bool = False
    redact_clients: bool = False
    redact_emails: bool = False      # scrub email addresses out of free text
    redact_urls: bool = False        # scrub http(s) URLs out of free text

    # --- truncation (0 = no limit) ---
    max_description_chars: int = 0
    max_comment_chars: int = 0

    # --- metadata ---
    include_source_metadata: bool = True
    include_query_metadata: bool = True

    # --- local annotations (private; never from/to Jira) ---
    # Only honoured when issue keys are NOT redacted — private notes and a
    # share-safe redacted export are contradictory, so redaction wins.
    include_local_notes: bool = False

    # --- organization ---
    group_by: str = ""                # "" or a key of GROUP_BY_LABELS
    sort_by: str = "updated"          # a member of SORT_FIELDS
    sort_desc: bool = True

    def normalized(self) -> "ExportConfig":
        """Return a copy with out-of-range/unknown values coerced to safe defaults."""
        group_by = self.group_by if self.group_by in GROUP_BY_LABELS else ""
        sort_by = self.sort_by if self.sort_by in SORT_FIELDS else "updated"
        return ExportConfig(
            include_comments=self.include_comments,
            latest_comments=max(0, int(self.latest_comments)),
            include_descriptions=self.include_descriptions,
            redact_keys=self.redact_keys,
            redact_people=self.redact_people,
            redact_clients=self.redact_clients,
            redact_emails=self.redact_emails,
            redact_urls=self.redact_urls,
            max_description_chars=max(0, int(self.max_description_chars)),
            max_comment_chars=max(0, int(self.max_comment_chars)),
            include_source_metadata=self.include_source_metadata,
            include_query_metadata=self.include_query_metadata,
            # A redacted export never carries private local notes.
            include_local_notes=self.include_local_notes and not self.redact_keys,
            group_by=group_by,
            sort_by=sort_by,
            sort_desc=self.sort_desc,
        )

    def redaction_settings(self) -> RedactionSettings:
        """Map this export config onto the shared :class:`RedactionSettings`.

        ``comments`` / ``descriptions`` are considered *redacted* (dropped) when
        the corresponding include toggle is off — that is the export layer's way
        of omitting them entirely.
        """
        return RedactionSettings(
            keys=self.redact_keys,
            people=self.redact_people,
            emails=self.redact_emails,
            clients=self.redact_clients,
            urls=self.redact_urls,
            comments=not self.include_comments,
            descriptions=not self.include_descriptions,
        )

    def redaction_summary(self) -> dict[str, object]:
        """Serializable description of the redaction/truncation applied."""
        summary: dict[str, object] = dict(self.redaction_settings().describe())
        summary.update({
            "descriptions_truncated_at": self.max_description_chars,
            "comments_truncated_at": self.max_comment_chars,
        })
        return summary

    def options_summary(self) -> dict[str, object]:
        """Serializable description of the content/organization options."""
        return {
            "include_comments": self.include_comments,
            "latest_comments": self.latest_comments,
            "include_descriptions": self.include_descriptions,
            "include_source_metadata": self.include_source_metadata,
            "include_query_metadata": self.include_query_metadata,
            "include_local_notes": self.include_local_notes,
            "group_by": self.group_by,
            "sort_by": self.sort_by,
            "sort_desc": self.sort_desc,
        }


@dataclass
class ExportContext:
    """Provenance for an export run. Never carries a token, password, or PAT.

    ``base_url`` is the full instance URL the UI already holds; the manifest only
    ever records its *host* (see :func:`issue_deck.exporters.pack.host_of`), so a
    URL that embedded credentials could not leak them into an artifact.
    """

    source_type: str = "api"          # "api" | "csv" | "mixed" | "empty"
    deployment: str = ""              # "cloud" | "server" (api origin)
    base_url: str = ""                # full URL; only the host is exported
    jql: str = ""                     # JQL if API source
    csv_source_filename: str = ""     # basename if CSV source
    field_mapping: dict[str, str] = field(default_factory=dict)  # field id -> name
    warnings: list[str] = field(default_factory=list)
    app_version: str = APP_VERSION
    exported_at: str = ""             # ISO-8601 UTC; stamped by the writer when blank
