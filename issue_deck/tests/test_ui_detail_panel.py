"""Regression tests for the issue detail panel's HTML rendering.

Clicking a Story in My Work routes the selected issue to
``IssueDetailPanel.show_issue`` -> ``_render`` -> ``html.escape``. A multi-value
field that slipped into a scalar attribute (e.g. a multi-select custom field
mapped to ``client``) used to reach ``html.escape`` as a ``list`` and crash with
``'list' object has no attribute 'replace'``. These tests lock the fix in.
"""

from __future__ import annotations

import pytest

from issue_deck.config import AppConfig
from issue_deck.schema import NormalizedIssue, normalized_from_jira
from issue_deck.ui.detail_panel import IssueDetailPanel, _render, _text


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def test_text_coerces_non_string_values():
    # Defense in depth: even a stray list never crashes html.escape. The value is
    # str()'d then HTML-escaped (so the quotes come back as &#x27;).
    assert _text(["Web", "Mobile"]) == "[&#x27;Web&#x27;, &#x27;Mobile&#x27;]"
    assert _text(None) == ""
    assert _text("a & b\nc") == "a &amp; b<br>c"


def test_render_survives_list_in_scalar_field():
    # Simulate the pre-fix pathological shape reaching the renderer directly.
    issue = NormalizedIssue(key="RICH-7", summary="Checkout latency spike",
                            issue_type="Story", client=["Web", "Mobile"])  # type: ignore[arg-type]
    html = _render(issue, {})
    assert "RICH-7" in html
    assert "Web, Mobile" in html or "Web" in html  # rendered, not crashed


def test_show_issue_with_multi_value_client(qapp, rich_issue):
    # End-to-end: a Story whose client field is a multi-select must render.
    cfg = AppConfig(base_url="https://example.atlassian.net",
                    client_field="customfield_10099")  # multi-select in the fixture
    issue = normalized_from_jira(rich_issue, cfg,
                                 story_points_field="customfield_10016",
                                 sprint_field="customfield_10020")
    assert issue.client == "Web, Mobile"  # schema fix: joined, not a list

    panel = IssueDetailPanel()
    panel.show_issue(issue)  # must not raise
    assert "RICH-7" in panel.view.toPlainText()
