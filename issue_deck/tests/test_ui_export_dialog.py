"""Headless tests for the export dialog and the query tab's export dispatch."""

from __future__ import annotations

import zipfile

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.datasource import DataSourceInfo, DataSourceKind
from issue_deck.exporters import ExportConfig
from issue_deck.exporters.pack import PACK_FILENAMES
from issue_deck.exporters.prompts import PROMPT_FILENAMES
from issue_deck.schema import IssueCollection, JiraUser, NormalizedIssue, SourceMetadata
from issue_deck.ui.export_dialog import EXPORT_MODES, ExportDialog
from issue_deck.ui.query_tab import QueryTab


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def _tab():
    cfg = AppConfig(base_url="https://x.atlassian.net", deployment="cloud",
                    email="a@b.c", client_field="customfield_10050")
    return QueryTab(cfg, lambda: cfg, lambda: None)


def _dispatch_and_wait(qapp, tab, mode, config):
    """Dispatch an export and pump the loop until the off-thread write completes.

    Pumps events (rather than QThread.wait) so the queued thread.quit + the
    finished-signal handlers actually run — a blocking wait would deadlock them.
    """
    tab._dispatch_export(mode, config)
    thread = tab._export_thread
    for _ in range(20000):
        if thread is None or not thread.isRunning():
            break
        qapp.processEvents()
    qapp.processEvents()


def _load_api_issues(tab):
    issues = [
        NormalizedIssue(key="PROJ-1", summary="Alpha", status="Open", priority="High",
                        assignee=JiraUser(display_name="Ada"),
                        source=SourceMetadata.for_api("cloud")),
        NormalizedIssue(key="PROJ-2", summary="Beta", status="Done", priority="Low",
                        source=SourceMetadata.for_api("cloud")),
    ]
    tab._last_jql = "assignee = currentUser() ORDER BY updated DESC"
    tab.store.replace(
        IssueCollection(issues=issues),
        DataSourceInfo(kind=DataSourceKind.JIRA_API, label="Jira API live search",
                       detail=tab._last_jql, deployment="cloud"))
    return issues


# --------------------------------------------------------------------------- #
# Dialog
# --------------------------------------------------------------------------- #
def test_dialog_default_config(qapp):
    dlg = ExportDialog()
    cfg = dlg.config()
    assert cfg == ExportConfig()  # defaults match the plain config
    assert dlg.mode == EXPORT_MODES[0][0]


def test_dialog_reads_back_choices(qapp):
    dlg = ExportDialog()
    dlg.cb_comments.setChecked(False)
    dlg.sp_latest.setValue(3)
    dlg.cb_redact_people.setChecked(True)
    dlg.cmb_group.setCurrentText("Status")
    dlg.cmb_mode.setCurrentIndex([m[0] for m in EXPORT_MODES].index("zip_pack"))
    cfg = dlg.config()
    assert cfg.include_comments is False
    assert cfg.latest_comments == 3
    assert cfg.redact_people is True
    assert cfg.group_by == "status"
    assert dlg.mode == "zip_pack"


# --------------------------------------------------------------------------- #
# Context builder
# --------------------------------------------------------------------------- #
def test_build_export_context_api(qapp):
    tab = _tab()
    _load_api_issues(tab)
    ctx = tab._build_export_context()
    assert ctx.source_type == "api"
    assert ctx.base_url == "https://x.atlassian.net"
    assert ctx.jql.startswith("assignee = currentUser()")
    assert ctx.field_mapping == {"customfield_10050": "Client"}


# --------------------------------------------------------------------------- #
# Dispatch → real files
# --------------------------------------------------------------------------- #
def test_dispatch_zip_pack_writes_archive(qapp, monkeypatch, tmp_path):
    tab = _tab()
    _load_api_issues(tab)
    out = tmp_path / "pack.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    _dispatch_and_wait(qapp, tab, "zip_pack", ExportConfig(group_by="status"))
    with zipfile.ZipFile(out) as zf:
        assert set(zf.namelist()) == set(PACK_FILENAMES)


def test_dispatch_prompt_pack_writes_archive(qapp, monkeypatch, tmp_path):
    tab = _tab()
    _load_api_issues(tab)
    out = tmp_path / "prompts.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    _dispatch_and_wait(qapp, tab, "prompt_pack", ExportConfig())
    with zipfile.ZipFile(out) as zf:
        assert set(zf.namelist()) == set(PROMPT_FILENAMES)


def test_dispatch_shaped_markdown_applies_options(qapp, monkeypatch, tmp_path):
    tab = _tab()
    _load_api_issues(tab)
    out = tmp_path / "e.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    _dispatch_and_wait(qapp, tab, "markdown_combined", ExportConfig(redact_people=True))
    text = out.read_text(encoding="utf-8")
    assert "Ada" not in text  # people redacted
    assert "PROJ-1" in text


def test_dispatch_per_ticket_folder(qapp, monkeypatch, tmp_path):
    tab = _tab()
    _load_api_issues(tab)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path)))
    _dispatch_and_wait(qapp, tab, "markdown_per_ticket", ExportConfig())
    assert (tmp_path / "PROJ-1.md").exists()
    assert (tmp_path / "PROJ-2.md").exists()
