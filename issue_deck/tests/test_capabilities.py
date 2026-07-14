"""Tests for instance capability detection (watcher search gating)."""

from __future__ import annotations

from issue_deck.services.capability_service import (
    Capabilities,
    detect_capabilities,
    fetch_capabilities,
)


def _field(fid, name, clause_names=None, searchable=True):
    f = {"id": fid, "name": name, "searchable": searchable}
    if clause_names is not None:
        f["clauseNames"] = clause_names
    return f


def test_watcher_supported_when_clause_present_and_searchable():
    fields = [
        _field("summary", "Summary", ["summary"]),
        _field("watches", "Watchers", ["watcher", "watchers"]),
    ]
    assert detect_capabilities(fields).watcher_search is True


def test_watcher_unsupported_when_clause_absent():
    fields = [
        _field("summary", "Summary", ["summary"]),
        _field("status", "Status", ["status"]),
    ]
    assert detect_capabilities(fields).watcher_search is False


def test_watcher_unsupported_when_present_but_not_searchable():
    fields = [
        _field("summary", "Summary", ["summary"]),
        _field("watches", "Watchers", ["watcher"], searchable=False),
    ]
    assert detect_capabilities(fields).watcher_search is False


def test_optimistic_when_api_omits_clause_names():
    # No field exposes clauseNames at all -> can't tell, assume supported.
    fields = [_field("summary", "Summary"), _field("watches", "Watchers")]
    assert detect_capabilities(fields).watcher_search is True


def test_clause_name_match_is_case_insensitive():
    fields = [_field("watches", "Watchers", ["WATCHER"])]
    assert detect_capabilities(fields).watcher_search is True


def test_default_capabilities_are_optimistic():
    assert Capabilities().watcher_search is True


def test_fetch_capabilities_uses_client_fields_raw():
    class FakeClient:
        def fields_raw(self):
            return [_field("watches", "Watchers", ["watcher"])]

    assert fetch_capabilities(FakeClient()).watcher_search is True
