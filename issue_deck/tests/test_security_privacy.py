"""Security / privacy invariants: no credential leakage into artifacts, and
fixtures use only fake/example values. Also documents the forthcoming CSV-import
local-only invariant (Phase 6).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.exporters import export_csv, export_jsonl, export_markdown_combined
from issue_deck.models import JiraIssue

FIXTURES = Path(__file__).parent / "fixtures"
SENTINEL_TOKEN = "SENTINEL-SECRET-TOKEN-should-never-appear"

FORBIDDEN_IN_FIXTURES = [
    "phertfelder@gmail.com",
    "@gmail.com",
    "-----BEGIN",
    "AKIA",
    "xoxb-",
    "ATATT",
]


def _issue():
    return JiraIssue(
        key="CLOUD-1",
        url="https://example.atlassian.net/browse/CLOUD-1",
        summary="no secrets here",
        status="Open",
        issuetype="Bug",
        priority="High",
        assignee="Ada",
        reporter="Grace",
        created="2026-01-01T00:00:00.000+0000",
        updated="2026-01-02T00:00:00.000+0000",
        description="benign description",
    )


def test_config_json_has_no_token(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    AppConfig(base_url="https://example.atlassian.net").save()
    text = (tmp_path / "config.json").read_text(encoding="utf-8")
    assert SENTINEL_TOKEN not in text
    assert "token" not in json.loads(text)


def test_markdown_export_has_no_token(tmp_path):
    out = tmp_path / "e.md"
    export_markdown_combined([_issue()], str(out))
    assert SENTINEL_TOKEN not in out.read_text(encoding="utf-8")


def test_jsonl_export_has_no_token(tmp_path):
    out = tmp_path / "e.jsonl"
    export_jsonl([_issue()], str(out))
    assert SENTINEL_TOKEN not in out.read_text(encoding="utf-8")


def test_csv_export_has_no_token(tmp_path):
    out = tmp_path / "e.csv"
    export_csv([_issue()], str(out))
    assert SENTINEL_TOKEN not in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")))
def test_fixtures_contain_no_real_secrets_or_pii(path):
    text = path.read_text(encoding="utf-8")
    for needle in FORBIDDEN_IN_FIXTURES:
        assert needle not in text, f"{path.name} contains forbidden value: {needle}"
    assert "atlassian.net" not in text or "example.atlassian.net" in text


def test_csv_import_never_persists_raw_rows(tmp_path):
    """CSV sample import is local-only: raw rows and the uploaded file must not
    be serialized. Only the schema-only profile and normalized dataset may be
    written, and only on explicit opt-in."""
    from issue_deck import csv_import
    from issue_deck.schema import IssueCollection

    # A cell value that must never reach disk, and an unmapped/sensitive column.
    raw_secret = "RAW-ROW-SECRET-should-never-be-serialized"
    src = tmp_path / "uploaded" / "export.csv"
    src.parent.mkdir()
    src.write_text(
        "Issue key,Summary,Internal Notes\n"
        f"PROJ-1,Public summary,{raw_secret}\n",
        encoding="utf-8",
    )

    parsed = csv_import.read_csv_file(src)
    profile = csv_import.build_profile(parsed)
    dataset = IssueCollection()
    result = csv_import.commit_import(
        parsed, profile, dataset,
        csv_import.ImportOptions(save_profile=True, save_dataset=True),
        out_dir=tmp_path / "out",
    )

    # The profile is schema-only: no cell values, no absolute file path.
    profile_text = Path(result.profile_path).read_text(encoding="utf-8")
    assert raw_secret not in profile_text
    assert str(src) not in profile_text
    assert profile.source_file_name == "export.csv"  # basename only

    # The normalized dataset keeps mapped values but never the unmapped raw cell
    # (Internal Notes was not mapped to a known field, so it is dropped).
    dataset_text = Path(result.dataset_path).read_text(encoding="utf-8")
    assert raw_secret not in dataset_text

    # The profile type has nowhere to stash rows in the first place.
    assert "rows" not in vars(profile) and "data" not in vars(profile)


# --------------------------------------------------------------------------- #
# No credentials / auth material in export packs
# --------------------------------------------------------------------------- #
def test_export_pack_has_no_credentials_or_auth_headers(tmp_path):
    """A pack records only the host — never a token, auth header, or full URL."""
    from issue_deck.exporters import ExportConfig, ExportContext, build_pack_files
    from issue_deck.schema import NormalizedIssue, SourceMetadata

    context = ExportContext(
        source_type="api",
        # A base URL that (pathologically) embeds credentials must not leak them.
        base_url=f"https://evil:{SENTINEL_TOKEN}@example.atlassian.net/jira",
    )
    issue = NormalizedIssue(key="ACME-1", summary="benign",
                            source=SourceMetadata.for_api("cloud"))
    files = build_pack_files([issue], ExportConfig(), context)
    for name, blob in files.items():
        text = blob.decode("utf-8")
        assert SENTINEL_TOKEN not in text, f"token leaked into {name}"
        assert "Authorization" not in text, f"auth header leaked into {name}"
        assert "Bearer " not in text, f"bearer token leaked into {name}"
    # Only the host survives in the manifest.
    manifest = json.loads(files["manifest.json"])
    assert manifest["jira_base_url_host"] == "example.atlassian.net"


# --------------------------------------------------------------------------- #
# Config never persists tokens or ad-hoc sensitive filter values
# --------------------------------------------------------------------------- #
def test_config_json_has_no_filter_or_token_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    AppConfig(base_url="https://example.atlassian.net", email="a@b.c").save()
    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    # No token, and no query/filter payload lives in config.json.
    for forbidden in ("token", "filters", "raw_jql", "text", "client"):
        assert forbidden not in data


# --------------------------------------------------------------------------- #
# Forget token removes any persisted token
# --------------------------------------------------------------------------- #
def test_forget_token_clears_plaintext(tmp_path, monkeypatch):
    from issue_deck import credentials

    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(credentials, "_HAS_KEYRING", False)
    cfg = AppConfig(base_url="https://example.atlassian.net", remember_token=True)
    backend = cfg.save_token(SENTINEL_TOKEN)
    assert backend == credentials.PLAINTEXT
    assert credentials.token_file_path().exists()
    cfg.clear_token()
    assert not credentials.token_file_path().exists()
    assert cfg.load_token() == ""


# --------------------------------------------------------------------------- #
# The HTTP client never logs the token, even at DEBUG
# --------------------------------------------------------------------------- #
def test_http_client_request_logging_has_no_token(monkeypatch):
    import logging

    from issue_deck import jira_client
    from issue_deck.config import AppConfig as _Cfg

    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = _H()
    handler.setFormatter(logging.Formatter("%(message)s"))
    jira_client.log.addHandler(handler)
    jira_client.log.setLevel(logging.DEBUG)

    cfg = _Cfg(base_url="https://example.atlassian.net", deployment="cloud", email="a@b.c")
    client = jira_client.JiraClient(cfg, SENTINEL_TOKEN)

    # Stub the network layer so _request runs its logging path without HTTP.
    class _Resp:
        status_code = 200

        def json(self):
            return {"issues": [], "isLast": True}

    monkeypatch.setattr(client, "_send", lambda *a, **k: _Resp())
    try:
        client._request("GET", "/rest/api/3/myself")
    finally:
        jira_client.log.removeHandler(handler)

    joined = "\n".join(records)
    assert joined, "expected a debug log line"
    assert SENTINEL_TOKEN not in joined
    assert "myself" in joined  # path is logged, token is not
