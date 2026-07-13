"""Export dispatch and format helpers.

Two tiers of export live here:

* The **legacy single-file exporters** (``export_markdown_combined``,
  ``export_markdown_per_ticket``, ``export_jsonl``, ``export_csv``) render the
  frozen on-disk contract and keep their original signatures — the existing
  export buttons call these unchanged.
* The **LLM export packs** (:func:`write_export_pack`, :func:`write_prompt_pack`)
  layer on :class:`ExportConfig`/:class:`ExportContext` to produce deterministic,
  provenance-carrying, optionally-redacted bundles.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import ExportOptions
from ._compat import ExportableIssue
from .analytics_export import render_analytics_csv, render_analytics_markdown
from .context import issue_to_llm_context, render_note_block
from .csv_export import export_csv
from .jsonl import export_jsonl
from .markdown import (
    export_markdown_combined,
    export_markdown_per_ticket,
    issue_to_markdown,
)
from .options import GROUP_BY_LABELS, SORT_FIELDS, ExportConfig, ExportContext
from .pack import build_pack_files, write_export_pack, zip_bytes
from .preview import redaction_preview
from .prompts import build_prompt_pack, write_prompt_pack
from .render import render_combined, render_per_ticket
from .transform import prepare_issues

__all__ = [
    "issue_to_markdown",
    "export_markdown_combined",
    "export_markdown_per_ticket",
    "export_jsonl",
    "export_csv",
    "render_analytics_markdown",
    "render_analytics_csv",
    "issue_to_llm_context",
    "render_note_block",
    "run_export",
    # LLM export packs
    "ExportConfig",
    "ExportContext",
    "GROUP_BY_LABELS",
    "SORT_FIELDS",
    "prepare_issues",
    "render_combined",
    "render_per_ticket",
    "build_pack_files",
    "zip_bytes",
    "redaction_preview",
    "write_export_pack",
    "build_prompt_pack",
    "write_prompt_pack",
]


def run_export(issues: Sequence[ExportableIssue], options: ExportOptions) -> None:
    """Dispatch a legacy single-file export by ``options.fmt``.

    The richer pack exports (:func:`write_export_pack`,
    :func:`write_prompt_pack`) take an :class:`ExportConfig`/:class:`ExportContext`
    and so are called directly rather than through this string dispatcher.
    """
    if options.fmt == "markdown_combined":
        export_markdown_combined(issues, options.destination)
    elif options.fmt == "markdown_per_ticket":
        export_markdown_per_ticket(issues, options.destination)
    elif options.fmt == "jsonl":
        export_jsonl(issues, options.destination)
    elif options.fmt == "csv":
        export_csv(issues, options.destination)
    else:
        raise ValueError(f"Unknown export format: {options.fmt}")
