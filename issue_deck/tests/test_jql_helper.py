"""Tests for the deterministic JQL helper (clauses, explain, breadth, validate,
templates, store). No Jira instance is touched — validation uses a fake client.
"""

from __future__ import annotations

import pytest

from issue_deck import constants
from issue_deck.config import AppConfig
from issue_deck.jira_client import AuthError, InvalidJQLError, SearchOutcome
from issue_deck.jql import build_jql
from issue_deck.jql_helper import (
    BUILTIN_TEMPLATES,
    JqlTemplateStore,
    all_templates,
    breadth_warnings,
    decompose,
    explain,
    filters_from_dict,
    missing_bounds,
    render,
    validate_jql,
)
from issue_deck.models import FieldFilter, SearchFilters


# --------------------------------------------------------------------------- #
# decompose / render
# --------------------------------------------------------------------------- #
def test_render_matches_build_jql_for_structured_filters():
    f = SearchFilters(assigned_to_me=True, projects=["ABC"], statuses=["Open"],
                      updated_days=30, unresolved=True)
    clauses = decompose(AppConfig(), f)
    assert render(clauses) == build_jql(AppConfig(), f)


def test_toggling_a_clause_off_drops_it():
    f = SearchFilters(assigned_to_me=True, projects=["ABC"], updated_days=30)
    clauses = decompose(AppConfig(), f)
    # Disable the project clause.
    proj = next(c for c in clauses if c.kind == "project")
    proj.enabled = False
    jql = render(clauses)
    assert "project" not in jql
    assert "assignee = currentUser()" in jql
    assert jql.endswith("ORDER BY updated DESC")


def test_all_clauses_disabled_yields_bare_order_by():
    clauses = decompose(AppConfig(), SearchFilters(assigned_to_me=True))
    for c in clauses:
        c.enabled = False
    assert render(clauses) == " ORDER BY updated DESC"


def test_raw_mode_is_single_verbatim_clause():
    f = SearchFilters(raw_mode=True, raw_jql="project = ZZZ ORDER BY created ASC")
    clauses = decompose(AppConfig(), f)
    assert len(clauses) == 1
    assert clauses[0].kind == "raw_jql"
    assert render(clauses) == "project = ZZZ ORDER BY created ASC"


# --------------------------------------------------------------------------- #
# explain
# --------------------------------------------------------------------------- #
def test_explain_reads_common_clauses():
    f = SearchFilters(assigned_to_me=True, projects=["ABC"], statuses=["Open"],
                      updated_days=14, unresolved=True)
    text = explain(decompose(AppConfig(), f))
    assert "you are the assignee" in text
    assert "in project ABC" in text
    assert "with status Open" in text
    assert "updated in the last 14 days" in text
    assert "unresolved" in text
    assert text.endswith("newest first.")


def test_explain_negated_field_filter_reads_as_excluding():
    f = SearchFilters(assigned_to_me=False, field_filters=[
        FieldFilter(field="status", op="not in", value="Done, Closed"),
    ])
    text = explain(decompose(AppConfig(), f))
    assert "excluding status Done or Closed" in text
    # a "not in" clause must never read like an inclusion
    assert "with status Done" not in text


def test_explain_not_equal_field_filter_reads_as_excluding():
    f = SearchFilters(assigned_to_me=False, field_filters=[
        FieldFilter(field="priority", op="!=", value="Low"),
    ])
    assert "excluding priority Low" in explain(decompose(AppConfig(), f))


def test_explain_raw_mode():
    f = SearchFilters(raw_mode=True, raw_jql="project = ZZZ")
    assert "raw JQL" in explain(decompose(AppConfig(), f))


def test_explain_no_clauses():
    clauses = decompose(AppConfig(), SearchFilters(assigned_to_me=True))
    for c in clauses:
        c.enabled = False
    assert "all issues" in explain(clauses)


# --------------------------------------------------------------------------- #
# breadth warnings
# --------------------------------------------------------------------------- #
def test_broad_query_warns_when_unbounded():
    f = SearchFilters(assigned_to_me=False, text="foo")  # text isn't a bound
    warnings = breadth_warnings(decompose(AppConfig(), f))
    assert warnings and "broad" in warnings[0].lower()


def test_bounded_query_does_not_warn():
    for f in (
        SearchFilters(assigned_to_me=True),                 # scope
        SearchFilters(assigned_to_me=False, projects=["ABC"]),  # project
        SearchFilters(assigned_to_me=False, updated_days=7),    # date
        SearchFilters(assigned_to_me=False, statuses=["Open"]), # status
        SearchFilters(assigned_to_me=False, status_categories=["Done"]),
    ):
        assert breadth_warnings(decompose(AppConfig(), f)) == []


def test_raw_mode_never_warns():
    f = SearchFilters(raw_mode=True, raw_jql="order by created")
    assert breadth_warnings(decompose(AppConfig(), f)) == []


def test_missing_bounds_lists_absent_dimensions():
    clauses = decompose(AppConfig(), SearchFilters(assigned_to_me=True))
    missing = missing_bounds(clauses)
    assert not any("assignee" in m for m in missing)   # scope present
    assert any("project" in m for m in missing)


def test_disabling_the_only_bound_makes_it_broad():
    clauses = decompose(AppConfig(), SearchFilters(assigned_to_me=True))
    assert breadth_warnings(clauses) == []
    for c in clauses:
        c.enabled = False
    assert breadth_warnings(clauses)  # now unbounded


# --------------------------------------------------------------------------- #
# validation (fake client)
# --------------------------------------------------------------------------- #
class _FakeClient:
    def __init__(self, *, issues=None, exc=None):
        self._issues = issues or []
        self._exc = exc
        self.calls: list[tuple] = []

    def search(self, jql, fields, *, max_results=None):
        self.calls.append((jql, tuple(fields), max_results))
        if self._exc is not None:
            raise self._exc
        return SearchOutcome(self._issues, total=len(self._issues), truncated=False)


def test_validate_ok_with_match_uses_max_results_1():
    client = _FakeClient(issues=[{"key": "ABC-1"}])
    result = validate_jql(client, AppConfig(), "project = ABC")
    assert result.ok
    assert "ABC-1" in result.message
    # A single, bounded round-trip.
    assert client.calls[0][2] == 1


def test_validate_ok_no_matches():
    result = validate_jql(_FakeClient(issues=[]), AppConfig(), "project = EMPTY")
    assert result.ok and "no issues" in result.message.lower()


def test_validate_invalid_jql_is_clean():
    client = _FakeClient(exc=InvalidJQLError("Invalid JQL: Field 'foo' does not exist"))
    result = validate_jql(client, AppConfig(), "foo = bar")
    assert not result.ok
    assert result.message.startswith("Jira rejected this JQL:")
    assert "Field 'foo'" in result.message


def test_validate_auth_error():
    result = validate_jql(_FakeClient(exc=AuthError("401")), AppConfig(), "x")
    assert not result.ok and "authenticat" in result.message.lower()


def test_validate_empty_jql():
    result = validate_jql(_FakeClient(), AppConfig(), "   ")
    assert not result.ok


# --------------------------------------------------------------------------- #
# templates + store
# --------------------------------------------------------------------------- #
def test_ten_builtin_templates_all_render():
    assert len(BUILTIN_TEMPLATES) == 10
    names = {t.name for t in BUILTIN_TEMPLATES}
    for expected in ("My open work", "Blocked issues", "Sprint work",
                     "Issues changed since last export"):
        assert expected in names
    for t in BUILTIN_TEMPLATES:
        jql = render(decompose(AppConfig(), t.filters))
        assert jql.endswith("ORDER BY updated DESC")


def test_template_clone_is_independent():
    t = next(t for t in BUILTIN_TEMPLATES if t.name == "High priority stale work")
    clone = t.clone_filters()
    clone.field_filters[0].value = "changed"
    assert t.filters.field_filters[0].value == "High, Highest"


def test_template_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "APP_DIR", tmp_path)
    store = JqlTemplateStore()
    f = SearchFilters(assigned_to_me=True, projects=["OPS"])
    store.save("My ops", f, description="ops work")
    assert "My ops" in store
    # Reload from disk.
    store2 = JqlTemplateStore()
    got = store2.get("My ops")
    assert got is not None
    assert got.description == "ops work"
    assert got.filters.projects == ["OPS"]
    assert got.builtin is False


def test_template_store_persist_and_delete(tmp_path):
    path = tmp_path / "t.json"
    store = JqlTemplateStore(path)
    store.save("A", SearchFilters(projects=["A"]))
    store.save("B", SearchFilters(projects=["B"]))
    assert set(store.names()) == {"A", "B"}
    assert store.delete("A")
    assert JqlTemplateStore(path).names() == ["B"]


def test_template_store_rejects_empty_name(tmp_path):
    store = JqlTemplateStore(tmp_path / "t.json")
    with pytest.raises(ValueError):
        store.save("  ", SearchFilters())


def test_all_templates_merges_builtin_and_custom(tmp_path):
    store = JqlTemplateStore(tmp_path / "t.json")
    store.save("Custom one", SearchFilters(projects=["Z"]))
    names = [t.name for t in all_templates(store)]
    assert names[:len(BUILTIN_TEMPLATES)] == [t.name for t in BUILTIN_TEMPLATES]
    assert "Custom one" in names
    assert all_templates(None) == BUILTIN_TEMPLATES


def test_filters_from_dict_tolerates_unknown_keys():
    f = filters_from_dict({"projects": ["X"], "bogus": 1,
                           "field_filters": [{"field": "labels", "value": "a", "junk": 2}]})
    assert f.projects == ["X"]
    assert f.field_filters[0].field == "labels"
