"""Headless construction tests for the navigation shell (PR 1).

Verifies the nav rail + QStackedWidget replaced the old QTabWidget without
losing pages or behavior. Widgets run under the offscreen Qt platform; the app
dir/config are redirected to a tmp path so nothing touches the real config.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox, QStackedWidget, QToolButton

from issue_deck import constants
from issue_deck.ui import theme
from issue_deck.ui.main_window import MainWindow
from issue_deck.ui.nav_rail import NavRail
from issue_deck.ui.placeholder_page import PlaceholderPage


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
    # Home, Query, Analytics, Exports, Settings — five stacked pages.
    assert win.stack.count() == 5


def test_existing_pages_preserved_in_stack(qapp):
    win = MainWindow()
    # The existing query and analytics widgets are reparented, not recreated.
    assert win.stack.widget(MainWindow.PAGE_QUERY) is win.query
    assert win.stack.widget(MainWindow.PAGE_ANALYTICS) is win.dashboard
    # Connection is demoted into the Settings page (still constructed + reachable).
    settings_page = win.stack.widget(MainWindow.PAGE_SETTINGS)
    assert win.connection in settings_page.findChildren(type(win.connection))


def test_home_is_command_center_and_exports_placeholder(qapp):
    from issue_deck.ui.home_page import HomePage
    win = MainWindow()
    assert isinstance(win.stack.widget(MainWindow.PAGE_HOME), HomePage)
    assert isinstance(win.stack.widget(MainWindow.PAGE_EXPORTS), PlaceholderPage)


def test_home_preset_routes_to_query_and_fetches(qapp, monkeypatch):
    from issue_deck.models import SearchFilters
    win = MainWindow()
    ran = []
    monkeypatch.setattr(win.query, "run_filters", lambda f: ran.append(f))
    filters = SearchFilters(assigned_to_me=True, unresolved=True)
    win.home.presetChosen.emit(filters)
    assert win.stack.currentIndex() == MainWindow.PAGE_QUERY
    assert ran == [filters]


def test_home_custom_query_routes_to_query(qapp):
    win = MainWindow()
    win.home.customQueryRequested.emit()
    assert win.stack.currentIndex() == MainWindow.PAGE_QUERY


def test_nav_buttons_switch_pages(qapp):
    win = MainWindow()
    win._btn_analytics.click()
    assert win.stack.currentIndex() == MainWindow.PAGE_ANALYTICS
    win._btn_home.click()
    assert win.stack.currentIndex() == MainWindow.PAGE_HOME
    # Query and Results share one page until the split lands.
    win._btn_results.click()
    assert win.stack.currentIndex() == MainWindow.PAGE_QUERY


def test_initial_page_depends_on_base_url(qapp):
    # Configured → opens on Query; the button group reflects it.
    win = MainWindow()
    win.cfg.base_url = "https://x.atlassian.net"
    win._build_shell()
    assert win.stack.currentIndex() == MainWindow.PAGE_QUERY
    assert win._btn_query.isChecked()


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
