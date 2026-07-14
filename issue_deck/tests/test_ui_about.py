"""Headless smoke test for the About dialog."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QLabel

from issue_deck import constants
from issue_deck.ui.about_dialog import AboutDialog


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_about_dialog_shows_version(qapp):
    dlg = AboutDialog()
    texts = [w.text() for w in dlg.findChildren(QLabel)]
    assert any(constants.APP_VERSION in t for t in texts)
    assert any(str(constants.CONFIG_PATH) in t for t in texts)
