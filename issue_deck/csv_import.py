"""Local-only CSV sample import wizard (no Jira API access).

This service module lets the app ingest a Jira CSV export (or any sample CSV)
and, entirely offline, infer available fields, build dynamic filters, and preview
normalized issues before committing them to the in-memory dataset. It is the
Phase-6 counterpart to the live-API path in :mod:`issue_deck.services`.

The wizard is expressed as a set of *pure* functions over small dataclasses so
the UI layer can drive each stage without embedding any parsing/inference logic
itself:

1. Privacy      -> :class:`ImportOptions` (``redact_keys`` toggle).
2. File/parse   -> :func:`parse_csv` / :func:`read_csv_file` -> :class:`ParsedCsv`.
3. Field map    -> :func:`auto_detect_mappings` / :func:`build_profile`.
4. Filters      -> :func:`profile_columns` / :func:`recommend_filters`.
5. Preview      -> :func:`build_preview` -> :class:`ImportPreview`.
6. Commit       -> :func:`commit_import` -> :class:`ImportResult`.

Privacy invariants (enforced here, not just documented):

* **Raw rows are transient.** :class:`ParsedCsv` holds parsed rows *in memory*
  only for inference/preview. They are never serialized. Committing derives
  :class:`~issue_deck.schema.NormalizedIssue` objects and discards the rows.
* **Only schema + normalized data may be saved.** :func:`commit_import` persists
  at most the :class:`~issue_deck.schema.CsvImportProfile` (column names +
  mappings) and the normalized dataset — never raw cells or the uploaded file.
* **No absolute paths leak.** Only the file *basename* is retained, for display.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from . import constants
from .cancellation import CancelToken
from .redaction import redact_key
from .schema import (
    CsvImportProfile,
    FieldMapping,
    IssueCollection,
    NormalizedIssue,
    SourceMetadata,
)

__all__ = [
    "ColumnType",
    "CanonicalField",
    "CANONICAL_FIELDS",
    "REQUIRED_TARGETS",
    "GROUP_BY_FIELDS",
    "ParsedCsv",
    "ColumnProfile",
    "FilterRecommendation",
    "GroupByPreview",
    "ImportPreview",
    "ImportOptions",
    "ImportResult",
    "detect_encoding",
    "sniff_delimiter",
    "parse_csv",
    "read_csv_file",
    "auto_detect_mappings",
    "build_profile",
    "infer_column_type",
    "profile_columns",
    "recommend_filters",
    "redact_key",
    "to_normalized_issues",
    "build_preview",
    "commit_import",
    "save_profile",
    "load_profile",
    "save_dataset",
]


# --------------------------------------------------------------------------- #
# Column types + canonical Jira field catalog
# --------------------------------------------------------------------------- #
class ColumnType(str, Enum):
    """Inferred logical type of a CSV column, driving filter widgets."""

    TEXT = "text"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    USER = "user"
    DATE = "date"
    NUMBER = "number"
    BOOLEAN = "boolean"


@dataclass(frozen=True)
class CanonicalField:
    """One known Jira concept and how to recognize/coerce it from a CSV column.

    ``target`` is a :class:`NormalizedIssue` attribute (or a custom key that
    lands in ``raw_field_values``). ``aliases`` are lowercased header phrases we
    match against; ``transform`` is the coercion hint stored on the mapping.
    """

    target: str
    label: str
    aliases: tuple[str, ...]
    transform: str = ""          # "" | "multi_select" | "number" | "user" | "date"
    required: bool = False


# Order matters only as a tie-breaker during auto-detection.
CANONICAL_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField("key", "Issue key", ("issue key", "key", "issue id"), required=True),
    CanonicalField("summary", "Summary", ("summary", "title"), required=True),
    CanonicalField("description", "Description", ("description", "desc")),
    CanonicalField("status", "Status", ("status",)),
    CanonicalField("issue_type", "Issue type", ("issue type", "issuetype", "type")),
    CanonicalField("priority", "Priority", ("priority",)),
    CanonicalField("assignee", "Assignee", ("assignee", "assigned to"), "user"),
    CanonicalField("reporter", "Reporter", ("reporter", "creator"), "user"),
    CanonicalField("created", "Created", ("created", "created date", "date created"), "date"),
    CanonicalField("updated", "Updated", ("updated", "updated date", "last updated"), "date"),
    CanonicalField(
        "resolved", "Resolved", ("resolved", "resolution date", "resolved date"), "date"
    ),
    CanonicalField("due_date", "Due date", ("due date", "due"), "date"),
    CanonicalField("labels", "Labels", ("labels", "label"), "multi_select"),
    CanonicalField(
        "components", "Components", ("components", "component/s", "component"), "multi_select"
    ),
    CanonicalField("project_key", "Project key", ("project key",)),
    CanonicalField("project_name", "Project", ("project name", "project")),
    CanonicalField("epic_key", "Epic link", ("epic link", "parent", "parent key", "epic key")),
    CanonicalField("epic_name", "Epic name", ("epic name", "epic")),
    CanonicalField("sprints", "Sprint", ("sprint", "sprints"), "multi_select"),
    CanonicalField(
        "fix_versions", "Fix version",
        ("fix version/s", "fix versions", "fix version", "fixversion"), "multi_select",
    ),
    CanonicalField(
        "story_points", "Story points",
        ("story points", "story point estimate", "points"), "number",
    ),
    CanonicalField("client", "Client", ("client", "customer", "account")),
    CanonicalField("severity", "Severity", ("severity",)),
)

# Fields whose absence should raise a preview warning.
REQUIRED_TARGETS: tuple[str, ...] = tuple(
    f.target for f in CANONICAL_FIELDS if f.required
)

_CANONICAL_BY_TARGET = {f.target: f for f in CANONICAL_FIELDS}

# Group-by option name -> the NormalizedIssue attribute it summarizes. "auto" is
# resolved dynamically in build_preview.
GROUP_BY_FIELDS: dict[str, str] = {
    "component": "components",
    "project": "project_key",
    "epic": "epic_key",
    "label": "labels",
    "assignee": "assignee",
    "status": "status",
    "priority": "priority",
}

_LIST_ATTRS = {"labels", "components", "fix_versions", "sprints"}
_USER_ATTRS = {"assignee", "reporter"}


# --------------------------------------------------------------------------- #
# Stage 2 — parsed CSV (transient rows, never persisted)
# --------------------------------------------------------------------------- #
@dataclass
class ParsedCsv:
    """In-memory result of parsing a CSV. ``rows`` are transient scratch data.

    Nothing here is ever serialized: rows exist only to power inference and
    preview. Callers must derive :class:`NormalizedIssue` objects (via the
    profile) before persisting anything.
    """

    columns: list[str]
    rows: list[dict[str, str]]
    delimiter: str = ","
    encoding: str = "utf-8"
    source_file_name: str = ""   # basename only

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def values(self, column: str) -> list[str]:
        """All cell values for ``column`` (missing cells excluded)."""
        return [r[column] for r in self.rows if column in r]


def detect_encoding(raw: bytes) -> str:
    """Best-effort encoding sniff without third-party deps.

    Honors a UTF-8/UTF-16 BOM, then tries strict UTF-8, then falls back to
    Windows-1252 (a superset of Latin-1 covering typical Jira exports). We never
    raise: the last fallback decodes any byte sequence.
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def sniff_delimiter(text: str) -> str:
    """Pick ``,`` or ``;`` for ``text`` (semicolon is the common EU variant)."""
    sample = "\n".join(text.splitlines()[:10])
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,").delimiter
    except csv.Error:
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        return ";" if first.count(";") > first.count(",") else ","


def _dedupe_headers(header: Sequence[str]) -> list[str]:
    """Make headers unique so duplicate columns are both preserved.

    A repeated header ``Status`` becomes ``Status``, ``Status (2)``, ``Status
    (3)`` … Blank headers become ``Column N`` so every column is addressable.
    """
    seen: Counter[str] = Counter()
    out: list[str] = []
    for i, raw in enumerate(header):
        name = (raw or "").strip() or f"Column {i + 1}"
        seen[name] += 1
        out.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return out


def parse_csv(
    data: bytes | str,
    *,
    delimiter: str | None = None,
    source_file_name: str = "",
    cancel: "CancelToken | None" = None,
) -> ParsedCsv:
    """Parse CSV bytes/text into a :class:`ParsedCsv`.

    Handles comma and semicolon variants (auto-sniffed when ``delimiter`` is
    ``None``), quoted fields containing the delimiter, duplicate headers, and
    reasonable encoding detection for ``bytes`` input. Only the file *basename*
    is retained from ``source_file_name``. A :class:`CancelToken` is polled every
    few thousand rows so a large parse can be aborted (raising ``CancelledError``).
    """
    if isinstance(data, bytes):
        encoding = detect_encoding(data)
        text = data.decode(encoding, errors="replace")
    else:
        encoding = "utf-8"
        text = data
    # A leading BOM can survive str input too; strip it so the first header is clean.
    text = text.lstrip("﻿")

    if delimiter is None:
        delimiter = sniff_delimiter(text)

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    raw_rows = [r for r in reader]
    # Skip leading fully-blank lines before the header.
    while raw_rows and not any(c.strip() for c in raw_rows[0]):
        raw_rows.pop(0)
    if not raw_rows:
        return ParsedCsv([], [], delimiter, encoding, _basename(source_file_name))

    columns = _dedupe_headers(raw_rows[0])
    rows: list[dict[str, str]] = []
    for ridx, raw in enumerate(raw_rows[1:]):
        if cancel is not None and ridx % 2000 == 0:
            cancel.raise_if_cancelled()
        if not any(cell.strip() for cell in raw):
            continue  # skip blank separator lines
        rows.append(
            {col: (raw[i].strip() if i < len(raw) else "") for i, col in enumerate(columns)}
        )
    return ParsedCsv(columns, rows, delimiter, encoding, _basename(source_file_name))


def read_csv_file(path: str | Path, *, delimiter: str | None = None,
                  cancel: "CancelToken | None" = None) -> ParsedCsv:
    """Read + parse a CSV file, keeping only its basename for provenance."""
    p = Path(path)
    return parse_csv(p.read_bytes(), delimiter=delimiter, source_file_name=p.name,
                     cancel=cancel)


# --------------------------------------------------------------------------- #
# Stage 3 — field mapping
# --------------------------------------------------------------------------- #
def _normalize_header(header: str) -> list[str]:
    """Lowercase, strip punctuation, split into word tokens.

    ``"Custom field (Story Points)"`` -> ``["custom", "field", "story",
    "points"]`` so alias matching is punctuation-insensitive.
    """
    cleaned = re.sub(r"[^0-9a-z]+", " ", header.lower())
    return cleaned.split()


def _alias_score(header_tokens: list[str], alias: str) -> int:
    """Score how well ``alias`` matches a header's tokens (0 = no match)."""
    at = alias.split()
    if not at or len(at) > len(header_tokens):
        return 0
    if header_tokens == at:
        return 3
    if header_tokens[: len(at)] == at or header_tokens[-len(at):] == at:
        return 2
    # alias tokens appear as a contiguous run anywhere in the header
    n = len(at)
    for i in range(len(header_tokens) - n + 1):
        if header_tokens[i : i + n] == at:
            return 1
    return 0


def _best_field_for(header: str) -> tuple[CanonicalField | None, int]:
    """Best canonical field for a single header, with its match score."""
    tokens = _normalize_header(header)
    best: CanonicalField | None = None
    best_score = 0
    best_alias_len = 0
    for f in CANONICAL_FIELDS:
        for alias in f.aliases:
            score = _alias_score(tokens, alias)
            alias_len = len(alias.split())
            better_tie = score == best_score and score > 0 and alias_len > best_alias_len
            if score > best_score or better_tie:
                best, best_score, best_alias_len = f, score, alias_len
    return (best, best_score) if best_score > 0 else (None, 0)


def auto_detect_mappings(columns: Iterable[str]) -> list[FieldMapping]:
    """Auto-map columns onto canonical targets.

    Each target is claimed by at most its best-matching column, so duplicate
    headers (``Status``/``Status (2)``) don't produce conflicting mappings. The
    returned mappings preserve original column order.
    """
    cols = list(columns)
    order = {c: i for i, c in enumerate(cols)}
    # target -> (column, field, score)
    claimed: dict[str, tuple[str, CanonicalField, int]] = {}
    for col in cols:
        f, score = _best_field_for(col)
        if not f:
            continue
        cur = claimed.get(f.target)
        if cur is None or score > cur[2]:
            claimed[f.target] = (col, f, score)
    mappings = [
        FieldMapping(source=col, target=f.target, transform=f.transform)
        for col, f, _ in claimed.values()
    ]
    mappings.sort(key=lambda m: order.get(m.source, len(cols)))
    return mappings


def build_profile(
    parsed: ParsedCsv,
    mappings: Sequence[FieldMapping] | None = None,
    *,
    name: str = "",
) -> CsvImportProfile:
    """Assemble a schema-only :class:`CsvImportProfile` from a parse + mappings.

    When ``mappings`` is ``None`` they are auto-detected. The profile holds no
    row data by construction.
    """
    if mappings is None:
        mappings = auto_detect_mappings(parsed.columns)
    return CsvImportProfile(
        name=name or (Path(parsed.source_file_name).stem if parsed.source_file_name else "import"),
        delimiter=parsed.delimiter,
        columns=list(parsed.columns),
        mappings=list(mappings),
        source_file_name=parsed.source_file_name,
    )


# --------------------------------------------------------------------------- #
# Stage 4 — column profiling / type inference / filter recommendations
# --------------------------------------------------------------------------- #
@dataclass
class ColumnProfile:
    """Statistics about one column, used to build dynamic filters."""

    name: str
    column_type: ColumnType
    total: int
    non_empty: int
    unique_count: int
    examples: list[str]

    @property
    def coverage(self) -> float:
        """Fraction of rows with a non-empty value (0..1)."""
        return (self.non_empty / self.total) if self.total else 0.0


_BOOL_TOKENS = {"true", "false", "yes", "no", "y", "n"}
_MULTI_SPLIT_RE = re.compile(r"[;,]")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?")
# European / Jira-export forms: 01/04/2026, 1.4.2026, 12/Apr/26 8:00 AM
_EU_DATE_RE = re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}")
_JIRA_DATE_RE = re.compile(r"^\d{1,2}/[A-Za-z]{3}/\d{2,4}")


def _looks_like_number(s: str) -> bool:
    return bool(_NUMBER_RE.match(s.strip()))


def _looks_like_date(s: str) -> bool:
    s = s.strip()
    return bool(_ISO_DATE_RE.match(s) or _EU_DATE_RE.match(s) or _JIRA_DATE_RE.match(s))


def _is_multi(values: Sequence[str]) -> bool:
    """True when a meaningful share of values pack multiple ``;``/``,`` tokens."""
    multi = sum(
        1 for v in values if len([p for p in _MULTI_SPLIT_RE.split(v) if p.strip()]) > 1
    )
    return bool(values) and multi >= max(1, len(values) // 10)


def infer_column_type(
    values: Sequence[str],
    *,
    target: str | None = None,
    unique_ratio_threshold: float = 0.5,
    max_categories: int = 30,
) -> ColumnType:
    """Infer a :class:`ColumnType` from sample values, refined by mapping target.

    A known mapping ``target`` is authoritative for user/list/number/date fields
    (a column mapped to ``assignee`` is a USER even if names look like plain
    text). Otherwise the type is derived from the values themselves.
    """
    if target in _USER_ATTRS:
        return ColumnType.USER
    if target in _LIST_ATTRS:
        return ColumnType.MULTI_SELECT
    if target == "story_points":
        return ColumnType.NUMBER
    canon = _CANONICAL_BY_TARGET.get(target or "")
    if canon and canon.transform == "date":
        return ColumnType.DATE

    non_empty = [v for v in values if v.strip()]
    if not non_empty:
        return ColumnType.TEXT

    lowered = {v.strip().lower() for v in non_empty}
    if lowered <= _BOOL_TOKENS:
        return ColumnType.BOOLEAN
    if all(_looks_like_number(v) for v in non_empty):
        return ColumnType.NUMBER
    if all(_looks_like_date(v) for v in non_empty):
        return ColumnType.DATE
    if _is_multi(non_empty):
        return ColumnType.MULTI_SELECT

    unique = len({v.strip() for v in non_empty})
    if unique <= max_categories and unique / len(non_empty) <= unique_ratio_threshold:
        return ColumnType.SINGLE_SELECT
    return ColumnType.TEXT


def _column_examples(values: Sequence[str], limit: int = 5) -> list[str]:
    """Up to ``limit`` distinct non-empty example values, most common first."""
    counts = Counter(v.strip() for v in values if v.strip())
    return [v for v, _ in counts.most_common(limit)]


def profile_columns(
    parsed: ParsedCsv, profile: CsvImportProfile | None = None
) -> list[ColumnProfile]:
    """Compute a :class:`ColumnProfile` for every column.

    When a ``profile`` is supplied its mappings inform type inference (so mapped
    user/date/list columns are typed correctly).
    """
    target_by_source: dict[str, str] = {}
    if profile is not None:
        target_by_source = {m.source: m.target for m in profile.mappings}

    profiles: list[ColumnProfile] = []
    for col in parsed.columns:
        values = parsed.values(col)
        non_empty = [v for v in values if v.strip()]
        col_type = infer_column_type(values, target=target_by_source.get(col))
        if col_type == ColumnType.MULTI_SELECT:
            unique = len({
                p.strip() for v in non_empty for p in _MULTI_SPLIT_RE.split(v) if p.strip()
            })
        else:
            unique = len({v.strip() for v in non_empty})
        profiles.append(
            ColumnProfile(
                name=col,
                column_type=col_type,
                total=len(values),
                non_empty=len(non_empty),
                unique_count=unique,
                examples=_column_examples(values),
            )
        )
    return profiles


@dataclass
class FilterRecommendation:
    """A suggested filter derived purely from CSV column statistics."""

    column: str
    column_type: ColumnType
    score: float
    reason: str


_FILTERABLE_TYPES = {
    ColumnType.SINGLE_SELECT,
    ColumnType.MULTI_SELECT,
    ColumnType.USER,
    ColumnType.BOOLEAN,
    ColumnType.DATE,
    ColumnType.NUMBER,
}


def recommend_filters(
    profiles: Sequence[ColumnProfile], *, min_coverage: float = 0.3
) -> list[FilterRecommendation]:
    """Rank columns worth exposing as filters, best first.

    Favors categorical/user/boolean columns with good coverage and low-to-
    moderate cardinality; date/number columns are offered as range filters at a
    lower base score. Free-text and effectively-unique columns are skipped.
    """
    recs: list[FilterRecommendation] = []
    for p in profiles:
        if p.column_type not in _FILTERABLE_TYPES or p.coverage < min_coverage:
            continue
        # A column with a distinct value in (almost) every row is an identifier,
        # not a filter dimension.
        if p.non_empty and p.column_type in {ColumnType.SINGLE_SELECT, ColumnType.USER} \
                and p.unique_count >= max(20, int(0.9 * p.non_empty)):
            continue

        if p.column_type in {ColumnType.DATE, ColumnType.NUMBER}:
            score = 0.4 * p.coverage
            reason = f"{p.column_type.value} range filter ({p.coverage:.0%} coverage)"
        else:
            # Cardinality fit peaks around a handful of categories.
            fit = 1.0 if 2 <= p.unique_count <= 15 else (0.6 if p.unique_count <= 40 else 0.2)
            score = p.coverage * fit
            reason = (
                f"{p.unique_count} distinct value(s), {p.coverage:.0%} coverage"
            )
        recs.append(FilterRecommendation(p.name, p.column_type, round(score, 4), reason))

    recs.sort(key=lambda r: r.score, reverse=True)
    return recs


# --------------------------------------------------------------------------- #
# Stage 5 — preview
# --------------------------------------------------------------------------- #
@dataclass
class GroupByPreview:
    """Counts of issues per value for one group-by dimension."""

    field: str
    groups: "OrderedDict[str, int]"

    @property
    def group_count(self) -> int:
        return len(self.groups)


@dataclass
class ImportPreview:
    """Everything the UI needs to render the preview stage."""

    issues: list[NormalizedIssue]
    filter_chips: list[str]
    group_by: dict[str, GroupByPreview]
    warnings: list[str]
    redacted: bool = False

    def __len__(self) -> int:
        return len(self.issues)


def _with_redacted_key(issue: NormalizedIssue) -> NormalizedIssue:
    from dataclasses import replace

    return replace(issue, key=redact_key(issue.key))


def to_normalized_issues(
    parsed: ParsedCsv, profile: CsvImportProfile
) -> list[NormalizedIssue]:
    """Derive normalized issues from the transient rows via ``profile``.

    Rows are consumed here and never retained on the issues (only mapped values
    survive, per :meth:`NormalizedIssue.from_csv_row`).
    """
    source = SourceMetadata.for_csv(profile.source_file_name)
    return [NormalizedIssue.from_csv_row(row, profile, source=source) for row in parsed.rows]


def _group_value_of(issue: NormalizedIssue, attr: str) -> list[str]:
    """The value(s) an issue contributes to a group-by dimension."""
    val = getattr(issue, attr, "")
    if attr in _USER_ATTRS:
        name = getattr(val, "name", "") if val else ""
        return [name] if name else []
    if isinstance(val, list):
        return [str(v) for v in val if str(v).strip()]
    return [str(val)] if str(val).strip() else []


def _build_group_by(issues: Sequence[NormalizedIssue], attr: str) -> GroupByPreview:
    counts: Counter[str] = Counter()
    for issue in issues:
        for v in _group_value_of(issue, attr):
            counts[v] += 1
    ordered: "OrderedDict[str, int]" = OrderedDict(counts.most_common())
    return GroupByPreview(field=attr, groups=ordered)


def _auto_group_field(issues: Sequence[NormalizedIssue]) -> str | None:
    """Pick the group-by dimension with the healthiest distribution."""
    best: str | None = None
    best_score = 0.0
    for name, attr in GROUP_BY_FIELDS.items():
        gb = _build_group_by(issues, attr)
        if gb.group_count < 2:
            continue
        covered = sum(gb.groups.values())
        coverage = covered / len(issues) if issues else 0.0
        fit = 1.0 if 2 <= gb.group_count <= 12 else 0.4
        score = coverage * fit
        if score > best_score:
            best, best_score = name, score
    return best


def build_preview(
    parsed: ParsedCsv,
    profile: CsvImportProfile,
    options: "ImportOptions | None" = None,
    *,
    group_by_fields: Iterable[str] = ("status", "assignee", "auto"),
    pinned_filters: Sequence[str] = (),
) -> ImportPreview:
    """Build the preview: normalized issues, filter chips, group-bys, warnings.

    ``group_by_fields`` accepts any keys of :data:`GROUP_BY_FIELDS` plus
    ``"auto"`` (auto-selected dimension). When ``options.redact_keys`` is set,
    the preview's issue keys are masked. Warnings cover unmapped required fields,
    missing story points, empty grouping fields, and duplicate keys.
    """
    options = options or ImportOptions()
    issues = to_normalized_issues(parsed, profile)

    warnings = _preview_warnings(parsed, profile, issues, group_by_fields)

    # Group-by previews (resolve "auto" to a concrete dimension).
    group_by: dict[str, GroupByPreview] = {}
    for name in group_by_fields:
        if name == "auto":
            chosen = _auto_group_field(issues)
            if chosen:
                group_by["auto"] = _build_group_by(issues, GROUP_BY_FIELDS[chosen])
            continue
        attr = GROUP_BY_FIELDS.get(name)
        if attr:
            group_by[name] = _build_group_by(issues, attr)

    # Filter chips: recommended filters, plus any user-pinned columns.
    profiles = profile_columns(parsed, profile)
    chips = [r.column for r in recommend_filters(profiles)]
    for pin in pinned_filters:
        if pin not in chips:
            chips.append(pin)

    display_issues = [_with_redacted_key(i) for i in issues] if options.redact_keys else issues
    return ImportPreview(
        issues=display_issues,
        filter_chips=chips,
        group_by=group_by,
        warnings=warnings,
        redacted=options.redact_keys,
    )


def _preview_warnings(
    parsed: ParsedCsv,
    profile: CsvImportProfile,
    issues: Sequence[NormalizedIssue],
    group_by_fields: Iterable[str],
) -> list[str]:
    warnings: list[str] = []
    mapped_targets = {m.target for m in profile.mappings}

    for target in REQUIRED_TARGETS:
        if target not in mapped_targets:
            label = _CANONICAL_BY_TARGET[target].label
            warnings.append(f"Required field '{label}' is not mapped.")

    if "story_points" not in mapped_targets:
        warnings.append("Story points column is not mapped.")
    else:
        missing = sum(1 for i in issues if i.story_points is None)
        if missing:
            warnings.append(f"Story points missing for {missing} of {len(issues)} issue(s).")

    for name in group_by_fields:
        if name == "auto":
            continue
        attr = GROUP_BY_FIELDS.get(name)
        if attr and issues and not any(_group_value_of(i, attr) for i in issues):
            warnings.append(f"Grouping field '{name}' is empty for all issues.")

    if "key" in mapped_targets:
        keys = [i.key for i in issues if i.key]
        dupes = sorted({k for k, c in Counter(keys).items() if c > 1})
        if dupes:
            shown = ", ".join(dupes[:5]) + (" …" if len(dupes) > 5 else "")
            warnings.append(f"{len(dupes)} duplicate issue key(s): {shown}")
        blank = sum(1 for i in issues if not i.key)
        if blank:
            warnings.append(f"{blank} row(s) have no issue key.")

    return warnings


# --------------------------------------------------------------------------- #
# Stage 6 — commit
# --------------------------------------------------------------------------- #
@dataclass
class ImportOptions:
    """User choices carried across the wizard.

    ``redact_keys`` (privacy stage) masks issue keys in preview and any export.
    ``save_profile`` / ``save_dataset`` are the commit-stage opt-ins; both
    default to *off*, so committing only mutates the in-memory dataset unless the
    user explicitly asks to persist.
    """

    redact_keys: bool = False
    save_profile: bool = False
    save_dataset: bool = False


@dataclass
class ImportResult:
    """Outcome of a commit: what was added and what (if anything) was saved."""

    added: int
    dataset_size: int
    profile_path: str = ""
    dataset_path: str = ""

    @property
    def profile_saved(self) -> bool:
        return bool(self.profile_path)

    @property
    def dataset_saved(self) -> bool:
        return bool(self.dataset_path)


def commit_import(
    parsed: ParsedCsv,
    profile: CsvImportProfile,
    dataset: IssueCollection,
    options: ImportOptions | None = None,
    *,
    out_dir: str | Path | None = None,
) -> ImportResult:
    """Add normalized issues to ``dataset`` and optionally persist schema/data.

    Issues are derived fresh from the transient rows (redacted if
    ``options.redact_keys``) and appended to the in-memory ``dataset``. The raw
    rows and the uploaded file are never persisted. Only when the user opts in
    are the schema-only profile and/or the normalized dataset written to disk.
    """
    options = options or ImportOptions()
    issues = to_normalized_issues(parsed, profile)
    if options.redact_keys:
        issues = [_with_redacted_key(i) for i in issues]

    dataset.issues.extend(issues)

    base = Path(out_dir) if out_dir is not None else constants.APP_DIR
    result = ImportResult(added=len(issues), dataset_size=len(dataset))
    if options.save_profile:
        result.profile_path = str(save_profile(profile, base / "csv_profiles"))
    if options.save_dataset:
        result.dataset_path = str(save_dataset(dataset, base / "datasets", name=profile.name))
    return result


# --------------------------------------------------------------------------- #
# Persistence (schema + normalized data only)
# --------------------------------------------------------------------------- #
def _slug(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z._-]+", "-", (name or "import").strip()).strip("-")
    return s or "import"


def _basename(path: str) -> str:
    return Path(path).name if path else ""


def save_profile(profile: CsvImportProfile, directory: str | Path) -> Path:
    """Serialize a :class:`CsvImportProfile` (schema only) to JSON.

    The dataclass has nowhere to store rows, so this can never leak raw data.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_slug(profile.name)}.json"
    path.write_text(json.dumps(asdict(profile), indent=2), encoding="utf-8")
    return path


def load_profile(path: str | Path) -> CsvImportProfile:
    """Load a previously-saved import profile."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    mappings = [FieldMapping(**m) for m in data.get("mappings", [])]
    return CsvImportProfile(
        name=data.get("name", ""),
        delimiter=data.get("delimiter", ","),
        columns=list(data.get("columns", [])),
        mappings=mappings,
        source_file_name=data.get("source_file_name", ""),
    )


def save_dataset(
    dataset: IssueCollection, directory: str | Path, *, name: str = "dataset"
) -> Path:
    """Serialize the *normalized* dataset to JSONL (one issue per line).

    Uses ``dataclasses.asdict`` on :class:`NormalizedIssue`, which carries only
    mapped/normalized values — never raw CSV rows.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_slug(name)}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for issue in dataset.issues:
            fh.write(json.dumps(asdict(issue), ensure_ascii=False) + "\n")
    return path
