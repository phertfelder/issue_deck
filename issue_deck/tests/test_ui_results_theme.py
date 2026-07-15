"""Dark/light readability of the results table (the redesign's usability fix).

Covers the theme tokens the table leans on, the delegate's colour decisions
(selection is authoritative; keys/summaries/metadata form a hierarchy), the
left-edge risk markers (never a full-row fill), and an end-to-end render check
that samples real pixels so "selected row stays readable" is actually verified
rather than asserted in the abstract.
"""

from __future__ import annotations

import datetime as dt

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from issue_deck.schema import JiraUser, NormalizedIssue, SourceMetadata
from issue_deck.ui import theme
from issue_deck.ui.results_table import (
    _MARK_BLOCKED,
    _MARK_HIGH,
    _MARK_STALE,
    _MARKER_ROLE,
    ResultsTable,
    _row_marker,
    _text_color,
)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _issue(key, **kw):
    kw.setdefault("source", SourceMetadata.for_api("cloud"))
    a = kw.pop("assignee", "x")
    return NormalizedIssue(key=key, assignee=JiraUser(display_name=a), **kw)


def _recent() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


_OLD = "2025-01-01T00:00:00+00:00"


def _relative_luminance(c: QColor) -> float:
    def chan(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return (0.2126 * chan(c.red()) + 0.7152 * chan(c.green())
            + 0.0722 * chan(c.blue()))


def _contrast(a: QColor, b: QColor) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# --------------------------------------------------------------------------- #
# Theme tokens
# --------------------------------------------------------------------------- #
def test_both_themes_define_table_tokens():
    for tokens in theme.THEMES.values():
        for field in ("table_sel", "table_sel_unfocused", "table_sel_text",
                      "table_hover", "table_blocked", "table_stale"):
            value = getattr(tokens, field)
            assert isinstance(value, str) and value, field


def test_qss_carries_table_selection_and_scrollbar_rules():
    for tokens in theme.THEMES.values():
        qss = theme.build_qss(tokens)
        assert tokens.table_sel in qss            # solid selection background
        assert "QScrollBar" in qss                # visible scrollbars
        assert "QHeaderView::section" in qss


def test_apply_theme_updates_active_tokens(qapp):
    theme.apply_theme(qapp, "light")
    assert theme.active_tokens() is theme.LIGHT
    theme.apply_theme(qapp, "dark")
    assert theme.active_tokens() is theme.DARK


# --------------------------------------------------------------------------- #
# Marker precedence (blocked ≻ high ≻ stale ≻ none)
# --------------------------------------------------------------------------- #
def test_row_marker_precedence():
    assert _row_marker(_issue("A", status="Blocked", priority="Highest")) == _MARK_BLOCKED
    assert _row_marker(_issue("B", priority="High", updated=_recent())) == _MARK_HIGH
    assert _row_marker(_issue("C", priority="Low", updated=_OLD)) == _MARK_STALE
    assert _row_marker(_issue("D", priority="Low", updated=_recent())) == ""


def test_populate_sets_marker_role_without_full_row_fill(qapp):
    t = ResultsTable()
    t.populate([_issue("A", status="Blocked"), _issue("B", priority="Low",
                                                       updated=_recent())])
    assert t.item(0, 0).data(_MARKER_ROLE) == _MARK_BLOCKED
    assert not t.item(1, 0).data(_MARKER_ROLE)
    # The marker is a delegate-drawn bar; no cell gets a background brush.
    for row in range(t.rowCount()):
        for col in range(t.columnCount()):
            assert t.item(row, col).background().style() == Qt.BrushStyle.NoBrush


# --------------------------------------------------------------------------- #
# Delegate colour decisions
# --------------------------------------------------------------------------- #
def test_selected_foreground_wins_in_every_column():
    t = theme.DARK
    sel = QColor(t.table_sel_text)
    for col in ("key", "summary", "status", "priority", "client", "updated"):
        assert _text_color(col, stale=False, selected=True, t=t) == sel
        # Even a stale row keeps the readable selected foreground.
        assert _text_color(col, stale=True, selected=True, t=t) == sel


def test_unselected_hierarchy_key_summary_metadata():
    t = theme.DARK
    assert _text_color("key", stale=False, selected=False, t=t) == QColor(t.accent)
    assert _text_color("summary", stale=False, selected=False, t=t) == QColor(t.text)
    assert _text_color("status", stale=False, selected=False, t=t) == QColor(t.text_secondary)
    # Stale drops the summary to secondary (de-emphasised, not disabled-grey).
    assert _text_color("summary", stale=True, selected=False, t=t) == QColor(t.text_secondary)


def test_metadata_text_meets_contrast_in_both_themes():
    # Secondary metadata against the row background clears ~4.5:1; the strongest
    # (primary) summary text clears it comfortably.
    for tokens in theme.THEMES.values():
        bg = QColor(tokens.content)
        assert _contrast(QColor(tokens.text), bg) >= 4.5           # summary
        assert _contrast(QColor(tokens.text_secondary), bg) >= 4.5  # metadata
        # White selected text on the solid selection clears large-text 3:1.
        assert _contrast(QColor(tokens.table_sel_text),
                         QColor(tokens.table_sel)) >= 4.5


# --------------------------------------------------------------------------- #
# End-to-end render: a selected row is unmistakable and still readable
# --------------------------------------------------------------------------- #
def _row_center_y(t: ResultsTable, row: int) -> int:
    return (t.rowViewportPosition(row) + t.rowHeight(row) // 2
            + t.horizontalHeader().height())


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_render_selected_row_is_solid_and_readable(qapp, mode):
    theme.apply_theme(qapp, mode)
    tokens = theme.THEMES[mode]
    t = ResultsTable()
    t.setSortingEnabled(False)   # keep the populate order stable for sampling
    t.set_columns(["key", "summary", "status"])
    t.populate([_issue("A-1", summary="high priority row", priority="High",
                       updated=_recent()),
                _issue("A-2", summary="ordinary selectable row", priority="Low",
                       updated=_recent())])
    t.resize(600, 160)
    t.show()
    t.selectRow(1)               # select the ordinary row
    qapp.processEvents()
    img = t.grab().toImage()

    y_sel = _row_center_y(t, 1)
    # The selected row paints the solid selection background under its cells.
    bg = QColor(img.pixel(300, y_sel))
    assert _contrast(bg, QColor(tokens.table_sel)) < 1.15   # ~equal to the token
    # Its text stays high-contrast against that background (sampled across the
    # summary column, which always contains glyphs).
    strongest = max(
        (QColor(img.pixel(x, y_sel)) for x in range(150, 360)),
        key=lambda px: _contrast(px, bg))
    assert _contrast(strongest, bg) >= 4.0

    # The unselected high-priority row (row 0) carries a left-edge risk marker,
    # not a full-row tint — the very-left pixels are the risk colour while the
    # cell body stays the normal row background.
    edge = QColor(img.pixel(1, _row_center_y(t, 0)))
    assert _contrast(edge, QColor(tokens.risk)) < 1.15
    body = QColor(img.pixel(200, _row_center_y(t, 0)))
    assert _contrast(body, QColor(tokens.risk)) > 1.5       # body isn't the marker

    t.hide()
