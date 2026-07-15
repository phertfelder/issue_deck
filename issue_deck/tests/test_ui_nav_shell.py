"""Headless construction tests for the four-item navigation shell.

Verifies the nav rail + QStackedWidget expose the redesigned IA
(My Work · Search · Reports · Settings) without losing pages or behavior.
Widgets run under the offscreen Qt platform; the app dir/config are redirected
to a tmp path so nothing touches the real config.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox, QStackedWidget, QToolButton

from issue_deck import constants
from issue_deck.ui import theme
from issue_deck.ui.main_window import MainWindow
from issue_deck.ui.nav_rail import NavRail


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def test_shell_has_rail_and_stack(qapp):
    win = MainWindow()
    assert isinstance(win.rail, NavRail)
    assert isinstance(win.stack, QStackedWidget)
    # My Work, Search, Reports, Settings — four stacked pages, one per nav item.
    assert win.stack.count() == 4


def test_existing_pages_preserved_in_stack(qapp):
    win = MainWindow()
    # The workbench IS the My Work page; the analytics dashboard is folded into Reports.
    assert win.stack.widget(MainWindow.PAGE_MYWORK) is win.query
    assert win.dashboard in win.reports.findChildren(type(win.dashboard))
    assert win.stack.widget(MainWindow.PAGE_REPORTS) is win.reports
    # Connection is demoted into the Settings page (still constructed + reachable).
    settings_page = win.stack.widget(MainWindow.PAGE_SETTINGS)
    assert win.connection in settings_page.findChildren(type(win.connection))


def test_search_page_is_distinct_from_mywork(qapp):
    from issue_deck.ui.search_page import SearchPage
    win = MainWindow()
    assert isinstance(win.stack.widget(MainWindow.PAGE_SEARCH), SearchPage)
    assert win.stack.widget(MainWindow.PAGE_SEARCH) is win.search


def test_preset_routes_to_mywork_and_fetches(qapp, monkeypatch):
    from issue_deck.models import SearchFilters
    win = MainWindow()
    ran = []
    monkeypatch.setattr(win.query, "run_filters", lambda f: ran.append(f))
    filters = SearchFilters(assigned_to_me=True, unresolved=True)
    win._run_preset(filters)
    assert win.stack.currentIndex() == MainWindow.PAGE_MYWORK
    assert ran == [filters]


def test_nav_buttons_switch_pages(qapp):
    win = MainWindow()
    win._btn_search.click()
    assert win.stack.currentIndex() == MainWindow.PAGE_SEARCH
    win._btn_reports.click()
    assert win.stack.currentIndex() == MainWindow.PAGE_REPORTS
    win._btn_mywork.click()
    assert win.stack.currentIndex() == MainWindow.PAGE_MYWORK


def test_no_duplicate_results_nav_item(qapp):
    # The redesign removes the duplicate "Results" item; every rail button maps
    # to a distinct page.
    win = MainWindow()
    labels = [b.text() for b in (win._btn_mywork, win._btn_search,
                                 win._btn_reports, win._btn_settings)]
    assert labels == ["My Work", "Search", "Reports", "Settings"]


def test_initial_page_depends_on_base_url(qapp):
    # Configured → opens on My Work; the button group reflects it.
    win = MainWindow()
    win.cfg.base_url = "https://x.atlassian.net"
    win._build_shell()
    assert win.stack.currentIndex() == MainWindow.PAGE_MYWORK
    assert win._btn_mywork.isChecked()


def test_build_qss_covers_both_modes(qapp):
    for mode, tokens in theme.THEMES.items():
        qss = theme.build_qss(tokens)
        assert theme.NAV_RAIL_OBJECT in qss
        assert tokens.accent in qss


def test_apply_theme_sets_stylesheet(qapp):
    applied = theme.apply_theme(qapp, "dark")
    assert applied == "dark"
    assert qapp.styleSheet()
    # Unknown mode falls back to dark rather than blanking the stylesheet.
    assert theme.apply_theme(qapp, "nonsense") == "dark"


def test_nav_rail_emits_page_index(qapp):
    rail = NavRail()
    seen = []
    rail.navigated.connect(seen.append)
    btn = rail.add_item("Thing", 3)
    assert isinstance(btn, QToolButton)
    btn.click()
    assert seen == [3]


# --------------------------------------------------------------------------- #
# Theme toggle (PR 7)
# --------------------------------------------------------------------------- #
def test_theme_toggle_persists_and_applies(qapp, tmp_path, monkeypatch):
    from issue_deck.config import AppConfig
    monkeypatch.setattr(constants, "CONFIG_PATH", tmp_path / "config.json")
    win = MainWindow()
    # Spy on apply_theme to avoid an expensive global re-style of every widget
    # accumulated by the module-scoped qapp (a headless-test artifact only).
    applied = []
    monkeypatch.setattr("issue_deck.ui.main_window.apply_theme",
                        lambda app, mode: applied.append(mode))
    win._set_theme("light")
    assert win.cfg.theme == "light"
    assert applied == ["light"]                 # live-applied
    assert AppConfig.load().theme == "light"    # persisted


def test_light_qss_uses_light_tokens(qapp):
    qss = theme.build_qss(theme.LIGHT)
    assert theme.LIGHT.win in qss and theme.LIGHT.accent in qss


def test_palette_sets_dark_window_base(qapp):
    # The palette (not just QSS) must carry the warm base so generic containers /
    # scroll-area bodies don't fall back to Fusion's light grey.
    from PyQt6.QtGui import QColor, QPalette
    pal = theme.build_palette(theme.DARK)
    assert pal.color(QPalette.ColorRole.Window) == QColor(theme.DARK.win)
    assert pal.color(QPalette.ColorRole.Base) == QColor(theme.DARK.content)
    # Dark window is genuinely dark (low luminance).
    assert pal.color(QPalette.ColorRole.Window).lightnessF() < 0.25
