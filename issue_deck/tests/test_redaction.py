"""Unit tests for the centralized redaction primitives and their consistent
application across every export format (Markdown / JSONL / CSV / pack)."""

from __future__ import annotations

import json

from issue_deck import redaction
from issue_deck.exporters import (
    ExportConfig,
    ExportContext,
    build_pack_files,
    prepare_issues,
    redaction_preview,
    render_combined,
)
from issue_deck.exporters.pack import issues_csv, issues_jsonl
from issue_deck.schema import JiraComment, JiraUser, NormalizedIssue, SourceMetadata


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
def test_redact_key_masks_digits_and_keeps_prefix():
    assert redaction.redact_key("PROJ-123") == "PROJ-•••"
    assert redaction.redact_key("ABC-42") == "ABC-••"
    assert redaction.redact_key("") == ""


def test_redact_emails_and_urls():
    text = "Ping ada@example.com or see https://jira.example.net/browse/X-1 please"
    assert redaction.redact_emails(text) == (
        "Ping [email redacted] or see https://jira.example.net/browse/X-1 please")
    scrubbed = redaction.scrub_text(text, emails=True, urls=True)
    assert "ada@example.com" not in scrubbed
    assert "https://" not in scrubbed
    assert "[email redacted]" in scrubbed and "[url redacted]" in scrubbed


def test_pseudonymize_is_stable_and_first_seen():
    m = redaction.pseudonymize(["Ada", "Grace", "Ada", ""], "Person")
    assert m == {"Ada": "Person 1", "Grace": "Person 2"}


def test_redact_secrets_strips_tokens_headers_and_url_creds():
    samples = {
        "token": "token=ATATT3xFfGF0abcDEF12345 tail",
        "bearer": "Authorization: Bearer supersecretvalue123",
        "basic": "Authorization: Basic dXNlcjpwYXNz",
        "url": "https://user:passw0rd@jira.example.net/x",
        "kv": 'password: "hunter2secret"',
    }
    out = {k: redaction.redact_secrets(v) for k, v in samples.items()}
    assert "ATATT3xFfGF0abcDEF12345" not in out["token"]
    assert "supersecretvalue123" not in out["bearer"]
    assert "dXNlcjpwYXNz" not in out["basic"]
    assert "passw0rd" not in out["url"] and "[redacted]@" in out["url"]
    assert "hunter2secret" not in out["kv"]


def test_redact_secrets_leaves_benign_text_untouched():
    assert redaction.redact_secrets("just a normal log line") == "just a normal log line"


# --------------------------------------------------------------------------- #
# Consistency across formats
# --------------------------------------------------------------------------- #
def _issue():
    return NormalizedIssue(
        key="ACME-7",
        url="https://acme.atlassian.net/browse/ACME-7",
        summary="Contact ada@acme.com about https://tracker.acme.com/42",
        description="Reporter email grace@acme.com; ref https://docs.acme.com/x",
        status="Open",
        assignee=JiraUser(display_name="Ada Lovelace", email="ada@acme.com"),
        reporter=JiraUser(display_name="Grace Hopper"),
        client="Umbrella Corp",
        comments=[JiraComment(author="Ada Lovelace", body="see ada@acme.com")],
        source=SourceMetadata.for_api("cloud"),
    )


def _full_config():
    return ExportConfig(
        redact_keys=True, redact_people=True, redact_clients=True,
        redact_emails=True, redact_urls=True,
    )


def test_redaction_consistent_across_all_formats():
    issues = [_issue()]
    config = _full_config()
    context = ExportContext(source_type="api", base_url="https://acme.atlassian.net")

    prepared = prepare_issues(issues, config)
    md = render_combined(prepared, config)
    jsonl = issues_jsonl(prepared)
    csv_text = issues_csv(prepared)
    pack = build_pack_files(issues, config, context)

    forbidden = [
        "ACME-7", "Ada Lovelace", "Grace Hopper", "Umbrella Corp",
        "ada@acme.com", "grace@acme.com",
        "https://tracker.acme.com", "https://docs.acme.com",
    ]
    blobs = {
        "markdown": md,
        "jsonl": jsonl,
        "csv": csv_text,
        "pack/issues.md": pack["issues.md"].decode("utf-8"),
        "pack/issues.jsonl": pack["issues.jsonl"].decode("utf-8"),
        "pack/issues.csv": pack["issues.csv"].decode("utf-8"),
    }
    for label, blob in blobs.items():
        for needle in forbidden:
            assert needle not in blob, f"{needle!r} leaked into {label}"
    # ...but pseudonyms/placeholders confirm the data was transformed, not dropped.
    assert "Person 1" in md
    assert "[email redacted]" in md
    assert "[url redacted]" in md


def test_manifest_records_all_redaction_flags():
    context = ExportContext(source_type="api", base_url="https://acme.atlassian.net")
    pack = build_pack_files([_issue()], _full_config(), context)
    manifest = json.loads(pack["manifest.json"])
    red = manifest["redaction"]
    assert red["keys"] and red["people"] and red["clients"]
    assert red["emails"] and red["urls"]


def test_redaction_preview_shows_before_after():
    preview = redaction_preview([_issue()], _full_config())
    assert "Ada Lovelace" in preview      # the "before" side
    assert "Person 1" in preview          # the "after" side
    assert "Redaction enabled" in preview


def test_no_redaction_preview_is_explicit():
    preview = redaction_preview([_issue()], ExportConfig())
    assert "No redaction enabled" in preview
