"""The LLM-ready ZIP export pack.

A *pack* bundles the same dataset in three shapes (Markdown for reading, JSONL
for programmatic consumption, CSV for spreadsheets) alongside the metadata an
LLM (or a human) needs to trust and reproduce it:

    issues.md          human/LLM-readable, grouped & shaped per the options
    issues.jsonl       one JSON issue per line (legacy JiraIssue shape)
    issues.csv         flat summary columns
    manifest.json      provenance: timestamp, version, source, host, JQL, counts…
    field_mapping.json custom-field id -> name + the on-disk column/JSONL schema
    query.jql          the JQL (API source) or a CSV-source note
    warnings.json      fetch/import warnings carried into the export
    README_EXPORT.md   what each file is, for a first-time reader

Determinism (an acceptance criterion): every file except ``manifest.json`` is a
pure function of the (prepared) issues and options, and the ZIP is written with
fixed member timestamps — so re-exporting unchanged data yields byte-identical
archives apart from the manifest's ``export_timestamp``. No secret (token, PAT,
password, or full base URL) is ever written; only the *host* of the base URL is
recorded.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from urllib.parse import urlsplit

from ..schema import NormalizedIssue
from ._compat import as_legacy
from .csv_export import COLUMNS as CSV_COLUMNS
from .jsonl import _JSONL_ISSUE_KEYS  # frozen key order, shared with the JSONL exporter
from .options import ExportConfig, ExportContext
from .render import render_combined
from .transform import prepare_issues

# A fixed epoch for ZIP member timestamps so archive bytes stay stable across runs.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

PACK_FILENAMES = (
    "issues.md",
    "issues.jsonl",
    "issues.csv",
    "manifest.json",
    "field_mapping.json",
    "query.jql",
    "warnings.json",
    "README_EXPORT.md",
)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def host_of(base_url: str) -> str:
    """Return only the host of ``base_url`` (never a path, user-info, or scheme).

    A defensive extra guard against credentials-in-URL: ``urlsplit`` puts any
    ``user:pass@`` in ``username``/``password``, which we deliberately drop.
    """
    if not base_url:
        return ""
    parts = urlsplit(base_url if "//" in base_url else f"//{base_url}")
    return parts.hostname or ""


# --------------------------------------------------------------------------- #
# Individual artifact builders (all deterministic given prepared issues)
# --------------------------------------------------------------------------- #
def issues_jsonl(issues: Sequence[NormalizedIssue]) -> str:
    return "".join(
        json.dumps(asdict(as_legacy(i)), ensure_ascii=False) + "\n" for i in issues
    )


def issues_csv(issues: Sequence[NormalizedIssue]) -> str:
    import csv

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for issue in issues:
        n = as_legacy(issue)
        w.writerow({c: getattr(n, c) for c in CSV_COLUMNS})
    return buf.getvalue()


def field_mapping_json(context: ExportContext) -> str:
    payload = {
        "custom_fields": dict(context.field_mapping),
        "csv_columns": list(CSV_COLUMNS),
        "jsonl_schema": list(_JSONL_ISSUE_KEYS),
        "notes": (
            "custom_fields maps Jira field ids to human names for the mapped "
            "custom fields. csv_columns is the header row of issues.csv. "
            "jsonl_schema is the key order of each object in issues.jsonl."
        ),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def query_jql(context: ExportContext) -> str:
    if context.source_type == "csv":
        name = context.csv_source_filename or "(unknown file)"
        return f"# CSV source — no JQL. Imported from: {name}\n"
    if context.jql:
        return context.jql.rstrip("\n") + "\n"
    return "# No JQL recorded for this export.\n"


def warnings_json(context: ExportContext) -> str:
    payload = {"count": len(context.warnings), "warnings": list(context.warnings)}
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def manifest_json(
    issues: Sequence[NormalizedIssue], config: ExportConfig, context: ExportContext
) -> str:
    config = config.normalized()
    payload = {
        "export_timestamp": context.exported_at or _now_iso(),
        "app_version": context.app_version,
        "source_type": context.source_type,
        "deployment": context.deployment,
        "jira_base_url_host": host_of(context.base_url),
        "jql": context.jql if context.source_type != "csv" else "",
        "csv_source_filename": context.csv_source_filename,
        "issue_count": len(issues),
        "field_mapping": dict(context.field_mapping),
        "redaction": config.redaction_summary(),
        "options": config.options_summary(),
        "includes_local_notes": config.include_local_notes,
        "warnings": list(context.warnings),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def readme_export_md(
    issues: Sequence[NormalizedIssue], config: ExportConfig, context: ExportContext
) -> str:
    config = config.normalized()
    # Only the value-scrubbing redactions belong on this line; dropped
    # comments/descriptions are already called out separately below.
    redactions = [
        label for label in config.redaction_settings().labels()
        if label not in ("comments", "descriptions")
    ]
    redaction_line = (
        "Redacted: " + ", ".join(redactions) + "." if redactions else "No redaction applied."
    )
    lines = [
        "# Jira export pack",
        "",
        f"This pack contains **{len(issues)} issue(s)** exported from "
        f"{context.source_type} source"
        + (f" (`{host_of(context.base_url)}`)" if host_of(context.base_url) else "")
        + ".",
        "",
        redaction_line,
        "",
        "## Files",
        "",
        "| File | Contents |",
        "|---|---|",
        "| `issues.md` | Human/LLM-readable Markdown, one section per issue. |",
        "| `issues.jsonl` | One JSON object per issue (see `field_mapping.json` for the schema). |",
        "| `issues.csv` | Flat summary columns for spreadsheets. |",
        "| `manifest.json` | Provenance: export time, app version, source, host, JQL, counts. |",
        "| `field_mapping.json` | Custom-field id to name plus the CSV/JSONL schema. |",
        "| `query.jql` | The JQL used (API source) or a CSV-source note. |",
        "| `warnings.json` | Any warnings raised while fetching/importing. |",
        "",
        "## Notes for LLM consumers",
        "",
        "- No credentials are present anywhere in this pack; only the Jira *host* is recorded.",
        "- Every file except `manifest.json` is deterministic for a given dataset + options,",
        "  so packs diff cleanly between runs.",
    ]
    if config.group_by:
        lines.append(f"- `issues.md` is grouped by **{config.group_by}**.")
    if config.latest_comments:
        lines.append(
            f"- Only the latest **{config.latest_comments}** comment(s) per issue are included.")
    if not config.include_comments:
        lines.append("- Comments were excluded from this export.")
    if not config.include_descriptions:
        lines.append("- Descriptions were excluded from this export.")
    if config.include_local_notes:
        lines.append(
            "- **Local notes are included.** These are PRIVATE, user-authored "
            "annotations — they were never fetched from or written back to Jira, "
            "and appear only under a clearly-labelled section within each issue in "
            "`issues.md`.")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_pack_files(
    issues: Sequence[NormalizedIssue],
    config: ExportConfig,
    context: ExportContext,
    *,
    notes: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    """Return ``{filename: bytes}`` for the whole pack (issues already un-prepared).

    ``issues`` may be the raw store issues; they are run through
    :func:`prepare_issues` here so a caller cannot forget redaction/shaping.
    ``notes`` maps issue key -> a pre-rendered private-note block; it is only
    woven into ``issues.md`` when ``config.include_local_notes`` survives
    normalization (i.e. keys are not redacted).
    """
    config = config.normalized()
    prepared = prepare_issues(issues, config)
    md_notes = notes if config.include_local_notes else None
    files_text = {
        "issues.md": render_combined(prepared, config, notes=md_notes),
        "issues.jsonl": issues_jsonl(prepared),
        "issues.csv": issues_csv(prepared),
        "manifest.json": manifest_json(prepared, config, context),
        "field_mapping.json": field_mapping_json(context),
        "query.jql": query_jql(context),
        "warnings.json": warnings_json(context),
        "README_EXPORT.md": readme_export_md(prepared, config, context),
    }
    return {name: text.encode("utf-8") for name, text in files_text.items()}


def zip_bytes(files: dict[str, bytes]) -> bytes:
    """Pack ``files`` into a deterministic ZIP (fixed member timestamps, sorted)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(filename=name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, files[name])
    return buf.getvalue()


def write_export_pack(
    issues: Sequence[NormalizedIssue],
    config: ExportConfig,
    context: ExportContext,
    path: str,
    *,
    notes: Mapping[str, str] | None = None,
) -> None:
    """Build and write the export pack ZIP to ``path``."""
    from pathlib import Path

    files = build_pack_files(issues, config, context, notes=notes)
    Path(path).write_bytes(zip_bytes(files))
