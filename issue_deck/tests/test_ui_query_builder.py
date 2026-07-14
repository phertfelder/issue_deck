"""Headless tests for the reusable guided-builder widgets (PR 4)."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QLabel

from issue_deck.ui.query_builder import CollapsibleSection, SegmentedToggle


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


# --------------------------------------------------------------------------- #
# SegmentedToggle
# --------------------------------------------------------------------------- #
def test_segmented_emits_on_click(qapp):
    seg = SegmentedToggle(["Guided", "Raw"])
    seen = []
    seg.changed.connect(seen.append)
    seg._buttons[1].click()
    assert seen == [1]
    assert seg.current_index() == 1


def test_segmented_set_index_is_emit_free(qapp):
    seg = SegmentedToggle(["Guided", "Raw"])
    seen = []
    seg.changed.connect(seen.append)
    seg.set_index(1)
    assert seen == []              # programmatic sync never re-emits
    assert seg.current_index() == 1


# --------------------------------------------------------------------------- #
# CollapsibleSection
# --------------------------------------------------------------------------- #
def test_collapsible_starts_collapsed(qapp):
    sec = CollapsibleSection("Advanced")
    sec.addWidget(QLabel("body"))
    assert sec.is_expanded() is False
    assert sec._body.isVisible() is False


def test_collapsible_toggles_and_emits(qapp):
    sec = CollapsibleSection("Advanced")
    states = []
    sec.toggled.connect(states.append)
    sec._header.click()
    assert sec.is_expanded() is True
    assert states == [True]
    sec._header.click()
    assert sec.is_expanded() is False
    assert states == [True, False]


def test_collapsible_header_shows_arrow(qapp):
    sec = CollapsibleSection("Advanced — generated JQL")
    assert sec._header.text().startswith("▸")
    sec.set_expanded(True)
    assert sec._header.text().startswith("▾")
