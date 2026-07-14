"""Headless tests for the Home command center.

Covers the page's cards/signals and the bounded CountsWorker logic. Threaded
count refresh is exercised only through no-op paths (no base URL / failing
client) so the tests stay deterministic; the worker's per-query logic is tested
by calling run() directly with a fake client.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.models import SavedView, SearchFilters
from issue_deck.ui.home_page import _PRESET_NAMES, ClickableCard, HomePage
from issue_deck.ui.workers import CountsWorker
from issue_deck.views import SavedViewStore


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def _page(cfg=None, views=None, provider=None):
    # NB: an empty SavedViewStore is falsy (it defines __len__), so use an
    # explicit None check rather than `views or ...`.
    cfg = cfg if cfg is not None else AppConfig()
    views = views if views is not None else SavedViewStore(path=None)
    provider = provider or (lambda: (_ for _ in ()).throw(RuntimeError("no client")))
    return HomePage(cfg, views, provider)


def _card_by_title(page, title):
    for card in page.findChildren(ClickableCard):
        labels = card.findChildren(QLabel)
        if labels and labels[0].text() == title:
            return card
    return None


# --------------------------------------------------------------------------- #
# Cards + signals
# --------------------------------------------------------------------------- #
def test_common_pull_cards_present(qapp):
    page = _page()
    for name in _PRESET_NAMES:
        assert _card_by_title(page, name) is not None, name
    assert _card_by_title(page, "Build a custom query") is not None


def test_preset_card_emits_search_filters(qapp):
    page = _page()
    seen = []
    page.presetChosen.connect(seen.append)
    _card_by_title(page, "My open work").clicked.emit()
    assert len(seen) == 1
    assert isinstance(seen[0], SearchFilters)
    assert seen[0].assigned_to_me and seen[0].unresolved


def test_custom_and_start_something_signals(qapp):
    page = _page()
    fired = {}
    page.customQueryRequested.connect(lambda: fired.setdefault("custom", True))
    page.importCsvRequested.connect(lambda: fired.setdefault("import", True))
    page.discoverFieldsRequested.connect(lambda: fired.setdefault("discover", True))
    page.rawJqlRequested.connect(lambda: fired.setdefault("raw", True))
    _card_by_title(page, "Build a custom query").clicked.emit()
    _card_by_title(page, "Import Jira CSV").clicked.emit()
    _card_by_title(page, "Discover fields").clicked.emit()
    _card_by_title(page, "Paste raw JQL").clicked.emit()
    assert fired == {"custom": True, "import": True, "discover": True, "raw": True}


def test_saved_views_chip_emits_name(qapp, tmp_path):
    store = SavedViewStore(tmp_path / "views.json")
    store.save(SavedView(name="My triage"))
    page = _page(views=store)
    seen = []
    page.savedViewChosen.connect(seen.append)
    # The saved-view chip is a QPushButton, not a ClickableCard.
    from PyQt6.QtWidgets import QPushButton
    chip = next(b for b in page.findChildren(QPushButton) if b.text() == "My triage")
    chip.click()
    assert seen == ["My triage"]


def test_saved_views_refresh_picks_up_new_view(qapp, tmp_path):
    store = SavedViewStore(tmp_path / "views.json")
    page = _page(views=store)
    from PyQt6.QtWidgets import QPushButton
    assert not any(b.text() == "Later" for b in page.findChildren(QPushButton))
    store.save(SavedView(name="Later"))
    page.refresh()  # re-reads the store
    assert any(b.text() == "Later" for b in page.findChildren(QPushButton))


# --------------------------------------------------------------------------- #
# Connection chip + counts (no-op / deterministic paths)
# --------------------------------------------------------------------------- #
def test_connection_chip_reflects_config(qapp):
    assert "Not connected" in _page(cfg=AppConfig(base_url="")).lbl_connection.text()
    connected = _page(cfg=AppConfig(base_url="https://demo.atlassian.net"))
    assert "demo.atlassian.net" in connected.lbl_connection.text()


def test_refresh_counts_noop_without_base_url(qapp):
    called = []
    page = _page(cfg=AppConfig(base_url=""), provider=lambda: called.append(True))
    page.refresh_counts()
    assert called == []  # never builds a client when unconfigured
    assert all(p.text() == "—" for p in page._pills)


def test_refresh_counts_survives_client_failure(qapp):
    # base_url set but the client can't be built → dashes, no crash.
    page = _page(cfg=AppConfig(base_url="https://x.atlassian.net"))
    page.refresh_counts()
    assert all(p.text() == "—" for p in page._pills)


# --------------------------------------------------------------------------- #
# CountsWorker — bounded per-query logic (no threads)
# --------------------------------------------------------------------------- #
def test_counts_worker_emits_totals(qapp):
    class FakeClient:
        def search(self, jql, fields, max_results=1):
            return SimpleNamespace(issues=[{"key": "X-1"}], total=len(jql))

    jqls = ["a", "bb", "ccc"]
    worker = CountsWorker(FakeClient(), AppConfig(), jqls)
    got = []
    worker.countReady.connect(lambda i, t: got.append((i, t)))
    done = []
    worker.finished.connect(lambda: done.append(True))
    worker.run()
    assert got == [(0, 1), (1, 2), (2, 3)]
    assert done == [True]


def test_counts_worker_stops_when_cancelled(qapp):
    class FakeClient:
        def search(self, jql, fields, max_results=1):
            return SimpleNamespace(issues=[], total=0)

    worker = CountsWorker(FakeClient(), AppConfig(), ["a", "b"])
    worker.cancel()
    got = []
    worker.countReady.connect(lambda i, t: got.append(i))
    worker.run()
    assert got == []  # cancelled before the first round-trip
