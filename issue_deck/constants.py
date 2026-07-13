"""Application-wide constants and default option lists.

Paths are looked up through this module (``constants.APP_DIR`` etc.) at call
time rather than imported by value elsewhere, so tests can redirect them with a
single monkeypatch on this module.
"""

from __future__ import annotations

from . import paths

# Resolved once at import from the platform-native location (see paths.py).
# Read as ``constants.APP_DIR`` at call time everywhere else; tests redirect it
# with a single monkeypatch.
APP_DIR = paths.resolve_app_dir()
CONFIG_PATH = APP_DIR / "config.json"
KEYRING_SERVICE = "issue_deck"

# --- onboarding / query-authoring defaults (persisted in AppConfig) ---
# Kept as three orthogonal axes so data-source, query authoring, and issue
# scope never get conflated in query construction.
DATA_SOURCE_CHOICES = ("api", "csv", "ask")
QUERY_AUTHORING_MODES = ("structured", "raw", "last_used")
QUERY_SCOPE_CHOICES = ("assigned_to_me", "reported_by_me", "project", "recent")

# Single source of truth for the package version (re-exported as __version__).
APP_VERSION = "0.2.0"
# Sent on every HTTP request so instance admins can identify the client.
USER_AGENT = f"issue-deck/{APP_VERSION} (+https://github.com/phertfelder/issue_deck)"

# Default HTTP request timeout (seconds); overridable per-config.
DEFAULT_REQUEST_TIMEOUT = 60
# Transient HTTP statuses worth retrying with backoff.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

COMMON_STATUSES = [
    "Open", "To Do", "In Progress", "In Review", "Blocked",
    "Reopened", "Resolved", "Closed", "Done",
]
COMMON_ISSUE_TYPES = ["Bug", "Task", "Story", "Sub-task", "Incident", "Epic"]
STATUS_CATEGORIES = ["To Do", "In Progress", "Done"]

# An issue is "stale" when it hasn't been updated in this many days.
STALE_DAYS = 30
# Priority / severity names that flag an issue as high-attention (lowercased).
HIGH_PRIORITY_NAMES = {"highest", "high", "critical", "blocker", "urgent", "p1", "p0"}
HIGH_SEVERITY_NAMES = {"sev-1", "sev1", "s1", "critical", "blocker", "highest", "high"}

# Issue fields always requested from the Jira search API. Custom field ids
# (client/severity) are appended per-config in the issue service.
BASE_SEARCH_FIELDS = [
    "summary", "status", "issuetype", "priority", "assignee", "reporter",
    "created", "updated", "description", "components", "labels",
]

# --- field-value discovery / hydration ---
# A field's distinct-value count decides how its filter is offered, so a
# 5-value status is a checklist while a 3000-value "reporter" never becomes a
# giant unhelpful dropdown.
ENUM_MAX_UNIQUE = 25          # <= this -> checkable list of all values
SEARCH_COMBO_MAX_UNIQUE = 200  # <= this -> searchable/editable combo; above -> free text
# Bounded "sample this project/board" search: how many issues to pull and how
# much of each field's distribution to surface.
SAMPLE_SIZE_DEFAULT = 200
SAMPLE_SIZE_MAX = 2000
SAMPLE_TOP_VALUES = 12         # top values shown per field
SAMPLE_EXAMPLES = 5            # concrete example values shown per field
