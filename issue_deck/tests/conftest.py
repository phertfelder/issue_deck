"""Shared fixtures. Tests use mocks/fixtures only — never a live Jira instance."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Qt must import headlessly (some tests construct QObjects/QApplication).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Isolate the app data dir so tests never read/write the real per-user location
# (constants.APP_DIR resolves from this on import). Tests that exercise path
# resolution delenv it explicitly.
os.environ.setdefault("ISSUE_DECK_HOME", tempfile.mkdtemp(prefix="issue_deck_test_"))

# Make the repo root importable even without an editable install.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from issue_deck.config import AppConfig  # noqa: E402  (after sys.path setup)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def cloud_issue() -> dict:
    return _load("cloud_issue_adf.json")


@pytest.fixture
def server_issue() -> dict:
    return _load("server_issue_plain.json")


@pytest.fixture
def rich_issue() -> dict:
    return _load("cloud_issue_rich.json")


@pytest.fixture
def cloud_comments() -> list[dict]:
    return _load("cloud_comments_adf.json")


@pytest.fixture
def server_comments() -> list[dict]:
    return _load("server_comments_plain.json")


@pytest.fixture
def cloud_cfg() -> AppConfig:
    return AppConfig(
        base_url="https://example.atlassian.net",
        deployment="cloud",
        email="tester@example.com",
        client_field="customfield_10050",
        severity_field="customfield_10060",
    )


@pytest.fixture
def server_cfg() -> AppConfig:
    return AppConfig(
        base_url="https://jira.example.com",
        deployment="server",
        email="",
        client_field="customfield_10050",
        severity_field="customfield_10060",
    )
