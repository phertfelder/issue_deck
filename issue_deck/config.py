"""Persisted application configuration.

``AppConfig`` holds connection settings only — tokens are never serialized here
(see :mod:`issue_deck.credentials`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields

from . import constants, credentials


@dataclass
class AppConfig:
    base_url: str = ""              # https://yourco.atlassian.net or https://jira.yourco.com
    deployment: str = "cloud"       # "cloud" | "server"
    email: str = ""                 # cloud basic-auth username (ignored for server PAT)
    remember_token: bool = False
    client_field: str = ""          # e.g. "customfield_10050"
    severity_field: str = ""        # e.g. "customfield_10060"
    # Optional per-instance custom-field ids for roles the schema already
    # supports (populated via the field-mapping modal). Empty = unmapped.
    story_points_field: str = ""    # e.g. "customfield_10016"
    sprint_field: str = ""          # e.g. "customfield_10020"
    epic_field: str = ""            # epic link field, e.g. "customfield_10014" or "parent"
    request_timeout: int = 60       # per-request HTTP timeout (seconds)

    # ---- default authoring choices (kept as three orthogonal axes) ----
    # Set during onboarding; see the matching constants.*_CHOICES tuples. These
    # never mix data-source, query authoring, and issue scope.
    default_data_source: str = "ask"                  # "api" | "csv" | "ask"
    default_query_authoring_mode: str = "structured"  # "structured" | "raw" | "last_used"
    default_query_scope: str = "assigned_to_me"       # see QUERY_SCOPE_CHOICES

    # ---- preference defaults (managed in the Settings dialog) ----
    default_export_folder: str = ""     # remembered export destination
    max_issues: int = 0                 # fetch cap (0 = no cap)
    comments_mode: str = "all"          # CommentsMode value: none|latest|all|since
    comments_latest_n: int = 5
    comments_since: str = ""            # ISO date, used only when mode is "since"
    export_redact_keys: bool = False    # default redaction toggles for exports
    export_redact_people: bool = False
    export_redact_clients: bool = False
    export_redact_emails: bool = False
    export_redact_urls: bool = False

    # UI theme: "dark" (default) or "light". Unknown values fall back to dark.
    theme: str = "dark"

    # True once first-run onboarding has completed (or been dismissed).
    onboarded: bool = False

    # ---- token storage (delegated to credentials) ----
    def load_token(self) -> str:
        return credentials.load_token(self.base_url)

    def save_token(self, token: str) -> str:
        """Persist/clear the token; returns the backend used (see credentials)."""
        return credentials.save_token(self.base_url, token, self.remember_token)

    def clear_token(self) -> None:
        credentials.clear_token(self.base_url)

    # ---- persistence ----
    @classmethod
    def load(cls) -> "AppConfig":
        if constants.CONFIG_PATH.exists():
            try:
                data = json.loads(constants.CONFIG_PATH.read_text(encoding="utf-8"))
                names = {f.name for f in fields(cls)}
                return cls(**{k: v for k, v in data.items() if k in names})
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        constants.APP_DIR.mkdir(parents=True, exist_ok=True)
        constants.CONFIG_PATH.write_text(
            json.dumps(asdict(self), indent=2), encoding="utf-8")


# Backward-compatible alias for the previous class name.
Config = AppConfig
