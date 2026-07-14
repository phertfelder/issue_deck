"""Main window: a left nav rail driving a QStackedWidget of pages.

PR 1 of the workbench redesign (see ``docs/design``) swaps the old
``QTabWidget`` for a task-first navigation shell. This is a pure re-parenting:
the existing Connection, Query & Results, and Analytics widgets become stacked
pages unchanged, joined by placeholder Home and Exports pages that later PRs
build out. Connection is demoted from a top-level tab into the Settings page.

No behavior changes: config, client building, first-run onboarding, and the
File/Help menus are all preserved; only the container changed.
"""

from __future__ import annotations

from PyQt6.QtGui import QActionGroup, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..jira_client import JiraClient, make_client
from .connection_tab import ConnectionTab
from .dashboard_tab import DashboardTab
from .home_page import HomePage
from .nav_rail import NavRail
from .placeholder_page import PlaceholderPage
from .query_tab import QueryTab
from .theme import CONTENT_STACK_OBJECT, apply_theme


class MainWindow(QMainWindow):
    # Stack page indices. Query and Results both point at PAGE_QUERY until the
    # query/results split lands in a later PR.
    PAGE_HOME = 0
    PAGE_QUERY = 1
    PAGE_ANALYTICS = 2
    PAGE_EXPORTS = 3
    PAGE_SETTINGS = 4

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("IssueDeck — assigned tickets → LLM export")
        self.resize(1150, 760)
        self.cfg = AppConfig.load()
        # app.py applies the dark bootstrap; only re-style when the saved theme
        # differs (avoids a redundant global setStyleSheet on the common path).
        app = QApplication.instance()
        if app is not None and self.cfg.theme != "dark":
            apply_theme(app, self.cfg.theme)

        self.connection = ConnectionTab(self.cfg)
        self.query = QueryTab(self.cfg, self.sync_config, self.build_client)
        # A successful connection test lets the query tab probe instance
        # capabilities (e.g. gate "Watched by me" where watcher search is off).
        self.connection.connected.connect(self.query.on_connected)

        # Local analytics over whatever the query tab currently holds. It recomputes
        # on every dataset change (fetch/import/merge/clear) and needs no Jira access.
        self.dashboard = DashboardTab(lambda: self.query.issues, self.query.annotations)
        self.query.datasetChanged.connect(self.dashboard.refresh)

        self._build_shell()
        self._build_menu()

        # Global command palette (Ctrl+K), cross-cutting all pages/actions.
        self._palette_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._palette_shortcut.activated.connect(self._open_command_palette)

    # ---- command palette ----
    def _open_command_palette(self) -> None:
        from .command_palette import CommandPalette

        dlg = CommandPalette(self, self._build_commands())
        if dlg.exec() and dlg.chosen is not None:
            dlg.chosen.run()

    def _build_commands(self) -> list:
        """Assemble the palette command list from current app state."""
        from ..jql_helper import BUILTIN_TEMPLATES
        from .command_palette import Command

        cmds: list[Command] = []
        nav = [
            ("Home", self.PAGE_HOME, self._btn_home),
            ("Query", self.PAGE_QUERY, self._btn_query),
            ("Results", self.PAGE_QUERY, self._btn_results),
            ("Analytics", self.PAGE_ANALYTICS, self._btn_analytics),
            ("Exports", self.PAGE_EXPORTS, self._btn_exports),
            ("Settings", self.PAGE_SETTINGS, self._btn_settings),
        ]
        for label, idx, btn in nav:
            cmds.append(Command(f"Go to {label}", "Navigate",
                                (lambda i=idx, b=btn: self._navigate(i, b)), keywords="page"))
        for tpl in BUILTIN_TEMPLATES:
            cmds.append(Command(f"Run preset: {tpl.name}", "Preset",
                                (lambda t=tpl: self._run_preset(t.clone_filters())),
                                keywords=tpl.description))
        for name in sorted(self.query.views.names()):
            cmds.append(Command(f"Run saved view: {name}", "Saved view",
                                (lambda n=name: self._run_saved_view(n)), keywords="view"))
        cmds.append(Command("Import Jira CSV…", "Action",
                            lambda: self._home_query_action(self.query._import_csv),
                            keywords="csv import"))
        cmds.append(Command("Map custom fields…", "Action", self._open_field_mapping,
                            keywords="field discovery mapping"))
        cmds.append(Command("Export…", "Action",
                            lambda: self._home_query_action(self.query._open_export_dialog),
                            keywords="export pack llm"))
        cmds.append(Command("Toggle raw JQL", "Action",
                            lambda: self._home_query_action(self.query.enter_raw_mode),
                            keywords="raw jql advanced"))
        cmds.append(Command("Open Settings…", "Action", self._open_settings,
                            keywords="preferences defaults"))
        # Issue-key search over the loaded dataset (bounded to keep the list light).
        for issue in self.query.issues[:300]:
            cmds.append(Command(f"Open issue {issue.key}", "Issue",
                                (lambda k=issue.key: self._reveal_issue(k)),
                                keywords=issue.summary or ""))
        return cmds

    def _reveal_issue(self, key: str) -> None:
        self._navigate(self.PAGE_QUERY, self._btn_query)
        self.query.reveal_issue(key)

    def _open_field_mapping(self) -> None:
        from .field_mapping_dialog import FieldMappingDialog

        FieldMappingDialog(self, cfg=self.cfg, client_provider=self.build_client).exec()

    # ---- navigation shell ----
    def _build_shell(self) -> None:
        """Assemble the nav rail + stacked pages as the central widget."""
        self.stack = QStackedWidget()
        self.stack.setObjectName(CONTENT_STACK_OBJECT)
        # Order must match the PAGE_* indices above.
        self.home = HomePage(self.cfg, self.query.views, self.build_client)
        self._wire_home()
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.query)
        self.stack.addWidget(self.dashboard)
        self.stack.addWidget(PlaceholderPage(
            "Exports",
            "This will become the report & LLM-pack builder. For now, export "
            "actions still live on the Query & Results page."))
        self.stack.addWidget(self._build_settings_page())

        self.rail = NavRail()
        self._btn_home = self.rail.add_item("Home", self.PAGE_HOME)
        self._btn_query = self.rail.add_item("Query", self.PAGE_QUERY)
        # Not split from Query yet — points at the same combined page for now.
        self._btn_results = self.rail.add_item("Results", self.PAGE_QUERY)
        self._btn_analytics = self.rail.add_item("Analytics", self.PAGE_ANALYTICS)
        self._btn_exports = self.rail.add_item("Exports", self.PAGE_EXPORTS)
        self.rail.add_stretch()
        self._btn_settings = self.rail.add_item("Settings", self.PAGE_SETTINGS)
        self.rail.navigated.connect(self._on_nav)

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.rail)
        row.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # Open on Query when configured, else Settings (which holds Connection).
        if self.cfg.base_url:
            self._navigate(self.PAGE_QUERY, self._btn_query)
        else:
            self._navigate(self.PAGE_SETTINGS, self._btn_settings)

    def _build_settings_page(self) -> QWidget:
        """Wrap the existing Connection UI as the Settings page.

        Connection is no longer a standalone tab; it lives here alongside a link
        into the full Settings dialog (defaults, saved views, CSV profiles).
        """
        page = QWidget()
        v = QVBoxLayout(page)
        heading = QLabel("Connection & settings")
        f = heading.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        heading.setFont(f)
        v.addWidget(heading)
        v.addWidget(self.connection)

        row = QHBoxLayout()
        more = QPushButton("More settings… (defaults, saved views, CSV profiles)")
        more.clicked.connect(self._open_settings)
        replay = QPushButton("Replay first-run setup…")
        replay.setToolTip("Re-run the guided connection + defaults wizard.")
        replay.clicked.connect(self._replay_onboarding)
        row.addWidget(more)
        row.addWidget(replay)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return page

    def _navigate(self, page_index: int, button) -> None:
        """Show a page and reflect it in the rail without re-emitting."""
        self.stack.setCurrentIndex(page_index)
        self.rail.set_active(button)

    def _on_nav(self, page_index: int) -> None:
        """Rail click handler: switch pages and refresh Home when it's shown."""
        self.stack.setCurrentIndex(page_index)
        if page_index == self.PAGE_HOME:
            self.home.refresh()

    # ---- Home command center routing ----
    def _wire_home(self) -> None:
        """Route Home intents into the existing Query/Results flows."""
        self.home.presetChosen.connect(self._run_preset)
        self.home.savedViewChosen.connect(self._run_saved_view)
        self.home.customQueryRequested.connect(
            lambda: self._navigate(self.PAGE_QUERY, self._btn_query))
        self.home.importCsvRequested.connect(
            lambda: self._home_query_action(self.query._import_csv))
        self.home.discoverFieldsRequested.connect(
            lambda: self._home_query_action(self.query._discover_values))
        self.home.rawJqlRequested.connect(
            lambda: self._home_query_action(self.query.enter_raw_mode))

    def _home_query_action(self, action) -> None:
        """Navigate to the Query page, then run a QueryTab action there."""
        self._navigate(self.PAGE_QUERY, self._btn_query)
        action()

    def _run_preset(self, filters) -> None:
        """Apply a Home preset's filters on the Query page and fetch."""
        self._navigate(self.PAGE_QUERY, self._btn_query)
        self.query.run_filters(filters)

    def _run_saved_view(self, name: str) -> None:
        view = self.query.views.get(name)
        if view is not None:
            self._run_preset(view.filters)

    # ---- menu ----
    def _build_menu(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        file_menu.addAction("Settings…", self._open_settings)
        file_menu.addSeparator()
        file_menu.addAction("Quit", self.close)

        view_menu = bar.addMenu("&View")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        for label, mode in (("&Dark theme", "dark"), ("&Light theme", "light")):
            act = view_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.cfg.theme == mode)
            act.triggered.connect(lambda _=False, m=mode: self._set_theme(m))
            theme_group.addAction(act)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction("About", self._open_about)

    def _set_theme(self, mode: str) -> None:
        """Persist and live-apply the chosen theme (dark/light)."""
        self.cfg.theme = mode
        self.cfg.save()
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode)

    def _open_settings(self) -> None:
        from ..constants import APP_DIR
        from .settings_dialog import SettingsDialog

        dlg = SettingsDialog(self, cfg=self.cfg, views=self.query.views,
                             profiles_dir=APP_DIR / "csv_profiles")
        accepted = dlg.exec() == dlg.DialogCode.Accepted
        if accepted:
            # Re-apply cfg-driven defaults that other tabs cached at build time.
            self.connection.load_config(self.cfg)
            self.query.apply_fetch_defaults()
        self.query.refresh_saved_views()  # deletes apply even on Cancel

    def _open_about(self) -> None:
        from .about_dialog import AboutDialog

        AboutDialog(self).exec()

    # ---- first-run onboarding ----
    def run_first_run_if_needed(self) -> None:
        """Show the onboarding wizard on a fresh install (no config, not onboarded)."""
        if self.cfg.onboarded or self.cfg.base_url:
            return
        self._run_onboarding()

    def _replay_onboarding(self) -> None:
        """Re-run the onboarding wizard on demand (Settings → Replay)."""
        self._run_onboarding()

    def _run_onboarding(self) -> None:
        """Show onboarding and, if accepted, persist + re-wire the result.

        Shared by first-run and the Settings *Replay* action so the single
        credential surface has exactly one apply path.
        """
        from .onboarding import OnboardingDialog

        dlg = OnboardingDialog(self, cfg=self.cfg)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        dlg.apply_to_config()
        self.cfg.save()
        self.cfg.save_token(dlg.raw_token())
        self.connection.load_config(self.cfg)
        self.query.apply_fetch_defaults()
        self.home.refresh_connection()
        if self.cfg.base_url:
            self._navigate(self.PAGE_QUERY, self._btn_query)
        else:
            self._navigate(self.PAGE_SETTINGS, self._btn_settings)
        if dlg.want_csv_import:
            self.query._import_csv()

    # ---- shared config/client access for the query tab ----
    def sync_config(self) -> AppConfig:
        """Fold the connection tab's current widget values into the config."""
        return self.connection.apply_to_config(self.cfg)

    def build_client(self) -> JiraClient:
        self.sync_config()
        return make_client(self.cfg, self.connection.token())
