"""Headless tests for the Search page (broad, all-Jira discovery)."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.ui.search_page import SearchPage


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def _page():
    cfg = AppConfig(base_url="https://x.atlassian.net", deployment="cloud", email="a@b.c")
    return SearchPage(cfg, lambda: cfg, lambda: None)


def test_search_has_no_default_assignee_scope(qapp):
    # Search is "all of Jira", not "your work": no assigned-to-me by default.
    f = _page()._filters()
    assert f.assigned_to_me is False
    assert f.reported_by_me is False


def test_pickers_become_field_filters(qapp):
    page = _page()
    page.cmb_assignee.setCurrentText("Jordan Reyes")
    page.cmb_reporter.setCurrentText("Sam Okafor")
    page.cmb_project.setCurrentText("PLAT")
    ffs = page._filters().field_filters
    assert any(ff.field == "assignee" and ff.value == "Jordan Reyes" for ff in ffs)
    assert any(ff.field == "reporter" and ff.value == "Sam Okafor" for ff in ffs)
    assert page._filters().projects == ["PLAT"]


def test_chips_and_text_feed_filters(qapp):
    page = _page()
    page.ed_text.setText("checkout")
    page._on_chip("status", "In Progress", True)
    page._on_chip("type", "Bug", True)
    page._on_chip("time", "7", True)
    f = page._filters()
    assert f.text == "checkout"
    assert f.status_categories == ["In Progress"]
    assert f.issue_types == ["Bug"]
    assert f.updated_days == 7


def test_summary_is_plain_english(qapp):
    page = _page()
    assert page.lbl_summary.text().startswith("◎")
    page.ed_text.setText("login")
    assert "login" in page.lbl_summary.text()


class _FakeOpt:
    def __init__(self, value):
        self.value = value


class _FakeClient:
    def projects(self):
        return [{"key": "PLAT", "name": "Platform"}]

    def user_search(self, query):
        return [{"displayName": "Jordan Reyes"}]


def test_on_connected_fills_pickers_best_effort(qapp, monkeypatch):
    page = _page()
    monkeypatch.setattr(
        "issue_deck.services.value_source_service.project_options",
        lambda client: [_FakeOpt("PLAT")])
    monkeypatch.setattr(
        "issue_deck.services.value_source_service.user_options",
        lambda client, q: [_FakeOpt("Jordan Reyes")])
    page.on_connected(object())
    projects = [page.cmb_project.itemText(i) for i in range(page.cmb_project.count())]
    assert "PLAT" in projects


def test_on_connected_survives_probe_failure(qapp, monkeypatch):
    page = _page()

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "issue_deck.services.value_source_service.project_options", boom)
    monkeypatch.setattr(
        "issue_deck.services.value_source_service.user_options", boom)
    page.on_connected(object())  # must not raise
    assert page.cmb_project.count() >= 1  # keeps the blank "Any" option
