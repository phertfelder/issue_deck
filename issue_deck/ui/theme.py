"""Warm-neutral QSS theme layer for the workbench shell.

Pure Qt-free-ish string building: this module owns the design tokens from the
redesign spec (``docs/design``) and renders them into a single QSS stylesheet
that :func:`apply_theme` installs on the ``QApplication``. Dark is the default.

Only ``apply_theme`` touches Qt (to set the app style + stylesheet); everything
else is plain data so the tokens and QSS can be unit-tested headlessly.

Scope for PR 1 (navigation shell): style the app chrome — nav rail, content
surface, and the common interactive widgets — with the warm-neutral palette.
Individual widgets that already set inline ``setStyleSheet`` colors keep those;
QSS here provides the coherent baseline, not per-widget overrides.
"""

from __future__ import annotations

from dataclasses import dataclass

# Object names the shell assigns so QSS can target the chrome precisely without
# a blanket ``QWidget { background }`` rule (which would fight Qt's compositing).
NAV_RAIL_OBJECT = "NavRail"
NAV_BUTTON_OBJECT = "NavButton"
CONTENT_STACK_OBJECT = "ContentStack"
PLACEHOLDER_OBJECT = "PlaceholderPage"
# Home command center surfaces.
HOME_CARD_OBJECT = "HomeCard"
HOME_PILL_OBJECT = "HomePill"
HOME_CHIP_OBJECT = "HomeChip"
HOME_SECTION_OBJECT = "HomeSection"
# Guided query builder surfaces.
SEGMENTED_OBJECT = "Segmented"
SEGMENTED_BTN_OBJECT = "SegmentedBtn"
DRAWER_HEADER_OBJECT = "DrawerHeader"
RAW_BANNER_OBJECT = "RawBanner"


@dataclass(frozen=True)
class Tokens:
    """A single theme's color tokens (see the spec's Design tokens table)."""

    win: str
    rail: str
    content: str
    card: str
    card_hover: str
    elevated: str
    border: str
    border_strong: str
    text: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_fill: str
    accent_bg: str
    amber: str
    amber_bg: str
    risk: str


# Dark is the default surface set. Warm neutrals — never pure Qt grey.
DARK = Tokens(
    win="#1b1813",
    rail="#171410",
    content="#1e1a14",
    card="#262117",
    card_hover="#2e2819",
    elevated="#302a1e",
    border="rgba(238,228,210,0.09)",
    border_strong="rgba(238,228,210,0.15)",
    text="#ede6d7",
    text_secondary="#b1a894",
    text_muted="#7d7566",
    accent="#5b9dff",
    accent_hover="#82b4ff",
    accent_fill="#2f6feb",
    accent_bg="rgba(91,157,255,0.14)",
    amber="#e6a94b",
    amber_bg="rgba(230,169,75,0.13)",
    risk="#e5794c",
)

# Light parity (not wired to a toggle in PR 1, but kept so theme.py is complete).
LIGHT = Tokens(
    win="#f7f4ed",
    rail="#efeae0",
    content="#f7f4ed",
    card="#ffffff",
    card_hover="#f0ece3",
    elevated="#ffffff",
    border="rgba(40,32,20,0.10)",
    border_strong="rgba(40,32,20,0.17)",
    text="#2a2620",
    text_secondary="#5f584c",
    text_muted="#8b8577",
    accent="#2f6feb",
    accent_hover="#1f5bd0",
    accent_fill="#2f6feb",
    accent_bg="rgba(47,111,235,0.10)",
    amber="#a5701a",
    amber_bg="rgba(216,158,54,0.17)",
    risk="#c0562b",
)

THEMES = {"dark": DARK, "light": LIGHT}

# Chrome uses the system stack; keys/JQL/dates use a monospace stack. No CDN
# fonts — system faces only (Segoe UI / Consolas on Windows).
FONT_UI = '"Segoe UI", system-ui, sans-serif'
FONT_MONO = '"JetBrains Mono", "Cascadia Mono", Consolas, monospace'


def build_qss(tokens: Tokens) -> str:
    """Render a full QSS stylesheet string for the given tokens."""
    t = tokens
    return f"""
/* ---- base ---- */
QMainWindow, QDialog {{
    background-color: {t.win};
}}
QWidget {{
    color: {t.text};
    font-family: {FONT_UI};
    font-size: 13px;
}}
QToolTip {{
    background-color: {t.elevated};
    color: {t.text};
    border: 1px solid {t.border_strong};
    padding: 4px 7px;
}}

/* ---- nav rail ---- */
QWidget#{NAV_RAIL_OBJECT} {{
    background-color: {t.rail};
    border-right: 1px solid {t.border};
}}
QToolButton#{NAV_BUTTON_OBJECT} {{
    color: {t.text_secondary};
    background-color: transparent;
    border: none;
    border-left: 3px solid transparent;
    padding: 9px 16px;
    text-align: left;
    font-size: 13px;
}}
QToolButton#{NAV_BUTTON_OBJECT}:hover {{
    color: {t.text};
    background-color: {t.card_hover};
}}
QToolButton#{NAV_BUTTON_OBJECT}:checked {{
    color: {t.accent};
    background-color: {t.accent_bg};
    border-left: 3px solid {t.accent};
    font-weight: 600;
}}

/* ---- content surface ---- */
QStackedWidget#{CONTENT_STACK_OBJECT} {{
    background-color: {t.content};
}}
QWidget#{PLACEHOLDER_OBJECT} {{
    background-color: {t.content};
}}

/* ---- inputs ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox, QDateEdit {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: 8px;
    padding: 5px 8px;
    selection-background-color: {t.accent_fill};
    selection-color: #ffffff;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QComboBox:focus, QDateEdit:focus {{
    border: 1px solid {t.accent};
}}
QComboBox QAbstractItemView {{
    background-color: {t.elevated};
    color: {t.text};
    selection-background-color: {t.accent_bg};
    selection-color: {t.text};
    border: 1px solid {t.border_strong};
}}

/* ---- buttons ---- */
QPushButton {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: 8px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    background-color: {t.card_hover};
    border: 1px solid {t.accent};
}}
QPushButton:default {{
    background-color: {t.accent_fill};
    color: #ffffff;
    border: 1px solid {t.accent_fill};
}}
QPushButton:default:hover {{
    background-color: {t.accent_hover};
    border: 1px solid {t.accent_hover};
}}
QPushButton:disabled {{
    color: {t.text_muted};
    border: 1px solid {t.border};
}}

/* ---- containers ---- */
QGroupBox {{
    border: 1px solid {t.border};
    border-radius: 10px;
    margin-top: 10px;
    padding: 10px 12px 12px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {t.text_secondary};
}}
QSplitter::handle {{
    background-color: {t.border};
}}

/* ---- tables ---- */
QTableWidget, QTableView {{
    background-color: {t.content};
    alternate-background-color: {t.card};
    gridline-color: {t.border};
    border: 1px solid {t.border};
    border-radius: 8px;
    selection-background-color: {t.accent_bg};
    selection-color: {t.text};
}}
QHeaderView::section {{
    background-color: {t.card};
    color: {t.text_secondary};
    border: none;
    border-bottom: 1px solid {t.border_strong};
    padding: 6px 8px;
    font-weight: 600;
}}

/* ---- inner tab widgets (csv wizard, dialogs) ---- */
QTabWidget::pane {{
    border: 1px solid {t.border};
    border-radius: 8px;
}}
QTabBar::tab {{
    background-color: {t.card};
    color: {t.text_secondary};
    border: 1px solid {t.border};
    padding: 6px 14px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{
    color: {t.text};
    background-color: {t.card_hover};
    border-bottom: 2px solid {t.accent};
}}

/* ---- home command center ---- */
QLabel#{HOME_SECTION_OBJECT} {{
    color: {t.text_muted};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QFrame#{HOME_CARD_OBJECT} {{
    background-color: {t.card};
    border: 1px solid {t.border};
    border-radius: 12px;
}}
QFrame#{HOME_CARD_OBJECT}:hover {{
    background-color: {t.card_hover};
    border: 1px solid {t.accent};
}}
QLabel#{HOME_PILL_OBJECT} {{
    background-color: {t.accent_bg};
    color: {t.accent};
    border-radius: 10px;
    padding: 1px 9px;
    font-weight: 600;
}}
QPushButton#{HOME_CHIP_OBJECT} {{
    background-color: {t.card};
    border: 1px solid {t.border_strong};
    border-radius: 8px;
    padding: 5px 12px;
}}
QPushButton#{HOME_CHIP_OBJECT}:hover {{
    border: 1px solid {t.accent};
    color: {t.accent};
}}

/* ---- guided query builder ---- */
QWidget#{SEGMENTED_OBJECT} {{
    background-color: {t.card};
    border: 1px solid {t.border_strong};
    border-radius: 8px;
}}
QPushButton#{SEGMENTED_BTN_OBJECT} {{
    background-color: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px 14px;
    color: {t.text_secondary};
}}
QPushButton#{SEGMENTED_BTN_OBJECT}:checked {{
    background-color: {t.accent_fill};
    color: #ffffff;
    font-weight: 600;
}}
QToolButton#{DRAWER_HEADER_OBJECT} {{
    background-color: transparent;
    border: none;
    color: {t.text_secondary};
    padding: 6px 2px;
    text-align: left;
    font-weight: 600;
}}
QToolButton#{DRAWER_HEADER_OBJECT}:hover {{
    color: {t.accent};
}}
QLabel#{RAW_BANNER_OBJECT} {{
    background-color: {t.amber_bg};
    color: {t.amber};
    border: 1px solid {t.amber};
    border-radius: 8px;
    padding: 7px 10px;
}}

/* ---- focus rings (accessibility) ---- */
QPushButton:focus, QToolButton:focus, QCheckBox:focus, QRadioButton:focus {{
    border: 1px solid {t.accent};
}}
QListWidget:focus, QTableWidget:focus, QTableView:focus, QListView:focus {{
    border: 1px solid {t.accent};
}}
QToolButton#{NAV_BUTTON_OBJECT}:focus {{
    border-left: 3px solid {t.accent};
    color: {t.text};
}}

/* ---- menus ---- */
QMenuBar {{
    background-color: {t.win};
    color: {t.text};
}}
QMenuBar::item:selected {{
    background-color: {t.card_hover};
}}
QMenu {{
    background-color: {t.elevated};
    color: {t.text};
    border: 1px solid {t.border_strong};
}}
QMenu::item:selected {{
    background-color: {t.accent_bg};
}}
"""


def build_palette(tokens: Tokens):
    """A Fusion QPalette from the tokens.

    The QSS only paints specific object-named surfaces; the palette sets the
    *base* colors every generic container (scroll-area bodies, plain QWidgets,
    viewports) inherits — without it, Fusion's default light palette shows
    through as grey page backgrounds under the dark chrome.
    """
    from PyQt6.QtGui import QColor, QPalette

    p = QPalette()
    Role = QPalette.ColorRole
    Group = QPalette.ColorGroup
    win = QColor(tokens.win)
    text = QColor(tokens.text)
    muted = QColor(tokens.text_muted)
    p.setColor(Role.Window, win)
    p.setColor(Role.WindowText, text)
    p.setColor(Role.Base, QColor(tokens.content))
    p.setColor(Role.AlternateBase, QColor(tokens.card))
    p.setColor(Role.ToolTipBase, QColor(tokens.elevated))
    p.setColor(Role.ToolTipText, text)
    p.setColor(Role.Text, text)
    p.setColor(Role.Button, QColor(tokens.card))
    p.setColor(Role.ButtonText, text)
    p.setColor(Role.Mid, muted)          # widely used via `color: palette(mid)`
    p.setColor(Role.PlaceholderText, muted)
    p.setColor(Role.Highlight, QColor(tokens.accent_fill))
    p.setColor(Role.HighlightedText, QColor("#ffffff"))
    p.setColor(Role.Link, QColor(tokens.accent))
    for role in (Role.WindowText, Role.Text, Role.ButtonText):
        p.setColor(Group.Disabled, role, muted)
    return p


def apply_theme(app, mode: str = "dark") -> str:
    """Install the theme on ``app``; returns the mode actually applied.

    Fusion base style + a token-derived palette (so containers inherit the warm
    base) + the QSS layer on top. Unknown modes fall back to dark.
    """
    tokens = THEMES.get(mode, DARK)
    resolved = mode if mode in THEMES else "dark"
    app.setStyle("Fusion")
    app.setPalette(build_palette(tokens))
    app.setStyleSheet(build_qss(tokens))
    return resolved
