"""Tests for the LLM export packs: transforms, pack assembly, determinism,
prompt pack, and the no-secrets / no-token invariants."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

from issue_deck.exporters import (
    ExportConfig,
    ExportContext,
    build_pack_files,
    build_prompt_pack,
    prepare_issues,
    render_combined,
    write_export_pack,
    write_prompt_pack,
    zip_bytes,
)
from issue_deck.exporters.pack import PACK_FILENAMES, host_of
from issue_deck.exporters.prompts import PROMPT_FILENAMES
from issue_deck.exporters.transform import group_issues, sort_issues
from issue_deck.models import JiraComment
from issue_deck.schema import JiraUser, NormalizedIssue, SourceMetadata

SENTINEL_TOKEN = "SENTINEL-SECRET-TOKEN-should-never-appear"


def _issue(key, summary="S", *, status="Open", priority="High", severity="",
           client="", assignee="Ada Lovelace", reporter="Grace Hopper",
           updated="2026-02-01", comments=None, description="desc",
           components=None, project="PROJ") -> NormalizedIssue:
    return NormalizedIssue(
        key=key,
        url=f"https://example.atlassian.net/browse/{key}",
        summary=summary,
        description=description,
        status=status,
        issue_type="Bug",
        priority=priority,
        severity=severity,
        client=client,
        assignee=JiraUser(display_name=assignee),
        reporter=JiraUser(display_name=reporter),
        updated=updated,
        components=components or [],
        project_key=project,
        comments=comments or [],
        source=SourceMetadata.for_api("cloud", imported_at="2026-01-01T00:00:00+00:00"),
    )


def _dataset():
    return [
        _issue("PROJ-1", "Alpha", priority="High", updated="2026-02-03",
               comments=[JiraComment(author="Ada Lovelace", created="c1", body="one"),
                         JiraComment(author="Bob", created="c2", body="two")]),
        _issue("PROJ-2", "Beta", priority="Low", updated="2026-02-01", client="Acme"),
        _issue("PROJ-3", "Gamma", priority="Critical", updated="2026-02-02", status="Done"),
    ]


def _context(**kw):
    base = dict(
        source_type="api", deployment="cloud",
        base_url="https://example.atlassian.net",
        jql="assignee = currentUser() ORDER BY updated DESC",
        field_mapping={"customfield_10050": "Client"},
        warnings=["PROJ-9: comments failed"],
        exported_at="2026-07-09T00:00:00+00:00",
    )
    base.update(kw)
    return ExportContext(**base)


# --------------------------------------------------------------------------- #
# Sorting
# --------------------------------------------------------------------------- #
def test_sort_by_priority_ranks_severity_order():
    ordered = sort_issues(_dataset(), ExportConfig(sort_by="priority", sort_desc=False))
    assert [i.key for i in ordered] == ["PROJ-3", "PROJ-1", "PROJ-2"]  # Critical, High, Low


def test_sort_by_key_desc():
    ordered = sort_issues(_dataset(), ExportConfig(sort_by="key", sort_desc=True))
    assert [i.key for i in ordered] == ["PROJ-3", "PROJ-2", "PROJ-1"]


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
def test_redact_keys_masks_key_and_url():
    out = prepare_issues([_issue("PROJ-123")], ExportConfig(redact_keys=True))
    assert out[0].key == "PROJ-•••"
    assert "PROJ-•••" in out[0].url and "PROJ-123" not in out[0].url


def test_redact_people_is_deterministic_pseudonyms():
    issues = prepare_issues(_dataset(), ExportConfig(redact_people=True, sort_by="key",
                                                     sort_desc=False))
    # Ada is assignee on every issue -> same pseudonym everywhere.
    aliases = {i.assignee.name for i in issues}
    assert aliases == {"Person 1"}
    # No original names survive anywhere (incl. comment authors).
    blob = render_combined(issues, ExportConfig(redact_people=True))
    assert "Ada Lovelace" not in blob and "Grace Hopper" not in blob and "Bob" not in blob


def test_redact_clients():
    out = prepare_issues(_dataset(), ExportConfig(redact_clients=True))
    clients = {i.client for i in out if i.client}
    assert clients == {"Client 1"}


# --------------------------------------------------------------------------- #
# Comment / description shaping
# --------------------------------------------------------------------------- #
def test_latest_n_comments_kept():
    out = prepare_issues(_dataset(), ExportConfig(latest_comments=1))
    p1 = next(i for i in out if i.summary == "Alpha")
    assert [c.body for c in p1.comments] == ["two"]  # latest one only


def test_exclude_comments_and_descriptions():
    out = prepare_issues(_dataset(), ExportConfig(include_comments=False,
                                                  include_descriptions=False))
    assert all(not i.comments for i in out)
    assert all(i.description == "" for i in out)


def test_truncate_description():
    issue = _issue("PROJ-1", description="x" * 100)
    out = prepare_issues([issue], ExportConfig(max_description_chars=10))
    assert out[0].description.startswith("x" * 10)
    assert "[truncated]" in out[0].description


# --------------------------------------------------------------------------- #
# Grouping / rendering
# --------------------------------------------------------------------------- #
def test_group_issues_puts_none_bucket_last():
    issues = [_issue("A", severity="S1"), _issue("B", severity=""), _issue("C", severity="S1")]
    groups = group_issues(issues, "severity")
    assert groups[0][0] == "S1"
    assert groups[-1][0] == "(none)"


def test_render_combined_is_deterministic():
    issues = _dataset()
    a = render_combined(prepare_issues(issues, ExportConfig()), ExportConfig())
    b = render_combined(prepare_issues(issues, ExportConfig()), ExportConfig())
    assert a == b
    assert "datetime" not in a.lower()  # no timestamp leaked into the body


# --------------------------------------------------------------------------- #
# Pack assembly
# --------------------------------------------------------------------------- #
def test_pack_contains_all_files():
    files = build_pack_files(_dataset(), ExportConfig(), _context())
    assert set(files) == set(PACK_FILENAMES)


def test_manifest_has_host_only_and_no_full_url():
    files = build_pack_files(_dataset(), ExportConfig(), _context())
    manifest = json.loads(files["manifest.json"])
    assert manifest["jira_base_url_host"] == "example.atlassian.net"
    assert manifest["app_version"]
    assert manifest["issue_count"] == 3
    assert manifest["source_type"] == "api"
    assert manifest["field_mapping"] == {"customfield_10050": "Client"}
    # Full URL / scheme never recorded.
    assert "https://example.atlassian.net" not in files["manifest.json"].decode()


def test_host_of_strips_userinfo_and_path():
    assert host_of("https://user:pass@jira.example.com/rest/api") == "jira.example.com"


# --------------------------------------------------------------------------- #
# Local notes in exports (clearly labelled private; opt-in; not under redaction)
# --------------------------------------------------------------------------- #
def _note_block(text):
    from issue_deck.annotations import Annotation
    from issue_deck.exporters import render_note_block
    return render_note_block(Annotation(key="PROJ-1", note=text, tags=["blocker"]))


def test_render_combined_weaves_notes_by_key():
    issues = prepare_issues(_dataset(), ExportConfig())
    notes = {"PROJ-1": _note_block("private reminder")}
    md = render_combined(issues, ExportConfig(), notes=notes)
    assert "private reminder" in md
    assert "never sent to Jira" in md  # the private label rides along
    # Issues without a note are unaffected.
    assert md.count("private reminder") == 1


def test_pack_includes_local_notes_and_labels_them():
    config = ExportConfig(include_local_notes=True)
    notes = {"PROJ-1": _note_block("ping the client")}
    files = build_pack_files(_dataset(), config, _context(), notes=notes)
    issues_md = files["issues.md"].decode()
    assert "ping the client" in issues_md
    manifest = json.loads(files["manifest.json"])
    assert manifest["includes_local_notes"] is True
    readme = files["README_EXPORT.md"].decode()
    assert "PRIVATE" in readme and "never" in readme


def test_redaction_drops_local_notes():
    # Even if notes are supplied, key redaction wins: no note is woven in.
    config = ExportConfig(include_local_notes=True, redact_keys=True)
    assert config.normalized().include_local_notes is False
    notes = {"PROJ-1": _note_block("should not appear")}
    files = build_pack_files(_dataset(), config, _context(), notes=notes)
    assert "should not appear" not in files["issues.md"].decode()
    assert json.loads(files["manifest.json"])["includes_local_notes"] is False


def test_notes_absent_by_default():
    files = build_pack_files(_dataset(), ExportConfig(), _context(),
                             notes={"PROJ-1": _note_block("secret")})
    # include_local_notes defaults off, so nothing is woven in.
    assert "secret" not in files["issues.md"].decode()
    assert host_of("example.atlassian.net") == "example.atlassian.net"
    assert host_of("") == ""


def test_query_jql_file_records_jql():
    files = build_pack_files(_dataset(), ExportConfig(), _context())
    assert "currentUser()" in files["query.jql"].decode()


def test_csv_source_pack_notes_filename_not_jql():
    ctx = _context(source_type="csv", jql="", csv_source_filename="tickets.csv")
    files = build_pack_files(_dataset(), ExportConfig(), ctx)
    assert "tickets.csv" in files["query.jql"].decode()
    manifest = json.loads(files["manifest.json"])
    assert manifest["csv_source_filename"] == "tickets.csv"
    assert manifest["jql"] == ""


def test_warnings_carried_into_pack():
    files = build_pack_files(_dataset(), ExportConfig(), _context())
    warnings = json.loads(files["warnings.json"])
    assert warnings["count"] == 1
    assert "PROJ-9: comments failed" in warnings["warnings"]


def test_field_mapping_documents_schema():
    files = build_pack_files(_dataset(), ExportConfig(), _context())
    fm = json.loads(files["field_mapping.json"])
    assert fm["jsonl_schema"][0] == "key"
    assert "summary" in fm["csv_columns"]


# --------------------------------------------------------------------------- #
# Determinism of the ZIP + no secrets
# --------------------------------------------------------------------------- #
def test_pack_bytes_deterministic_except_manifest():
    """Two runs differ only in manifest.json (which carries the timestamp)."""
    ctx1 = _context(exported_at="2026-07-09T00:00:00+00:00")
    ctx2 = _context(exported_at="2026-07-10T09:09:09+00:00")
    f1 = build_pack_files(_dataset(), ExportConfig(), ctx1)
    f2 = build_pack_files(_dataset(), ExportConfig(), ctx2)
    for name in PACK_FILENAMES:
        if name == "manifest.json":
            continue
        assert f1[name] == f2[name], f"{name} not deterministic"


def test_zip_bytes_deterministic():
    files = build_pack_files(_dataset(), ExportConfig(), _context())
    assert zip_bytes(files) == zip_bytes(files)


def test_written_zip_roundtrips(tmp_path):
    out = tmp_path / "pack.zip"
    write_export_pack(_dataset(), ExportConfig(group_by="status"), _context(), str(out))
    with zipfile.ZipFile(BytesIO(out.read_bytes())) as zf:
        names = set(zf.namelist())
        assert names == set(PACK_FILENAMES)
        assert b"Status:" in zf.read("issues.md")  # grouped heading present


def test_pack_never_contains_token():
    ctx = _context(base_url=f"https://x:{SENTINEL_TOKEN}@example.atlassian.net")
    files = build_pack_files(_dataset(), ExportConfig(), ctx)
    for name, data in files.items():
        assert SENTINEL_TOKEN.encode() not in data, f"token leaked into {name}"


# --------------------------------------------------------------------------- #
# Prompt pack
# --------------------------------------------------------------------------- #
def test_prompt_pack_has_all_five_prompts():
    prompts = build_prompt_pack(_dataset(), ExportConfig(), _context())
    assert set(prompts) == set(PROMPT_FILENAMES)


def test_prompt_pack_embeds_issue_digest():
    prompts = build_prompt_pack(_dataset(), ExportConfig(), _context())
    triage = prompts["triage_prompt.md"]
    assert "PROJ-1" in triage and "| Key |" in triage


def test_prompt_pack_respects_redaction():
    prompts = build_prompt_pack(_dataset(), ExportConfig(redact_people=True), _context())
    assert "Ada Lovelace" not in prompts["sprint_summary_prompt.md"]


def test_write_prompt_pack_roundtrips(tmp_path):
    out = tmp_path / "prompts.zip"
    write_prompt_pack(_dataset(), ExportConfig(), _context(), str(out))
    with zipfile.ZipFile(BytesIO(out.read_bytes())) as zf:
        assert set(zf.namelist()) == set(PROMPT_FILENAMES)
