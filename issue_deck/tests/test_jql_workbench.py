"""JQL tests for the workbench filter additions (scope, pickers, dates, raw)."""

from __future__ import annotations

from issue_deck.config import AppConfig
from issue_deck.jql import build_jql
from issue_deck.models import FieldFilter, SearchFilters


def test_reported_by_me_scope():
    f = SearchFilters(assigned_to_me=False, reported_by_me=True)
    assert build_jql(AppConfig(), f) == "reporter = currentUser() ORDER BY updated DESC"


def test_multiple_scopes_are_ored():
    f = SearchFilters(assigned_to_me=True, reported_by_me=True, watched_by_me=True)
    assert build_jql(AppConfig(), f) == (
        "(assignee = currentUser() OR reporter = currentUser() "
        "OR watcher = currentUser()) ORDER BY updated DESC"
    )


def test_no_scope_produces_no_who_clause():
    f = SearchFilters(assigned_to_me=False, projects=["ABC"])
    assert build_jql(AppConfig(), f) == 'project = "ABC" ORDER BY updated DESC'


def test_projects_single_and_multi():
    single = build_jql(AppConfig(), SearchFilters(projects=["ABC"]))
    assert single == 'assignee = currentUser() AND project = "ABC" ORDER BY updated DESC'
    multi = build_jql(AppConfig(), SearchFilters(projects=["ABC", "DEF"]))
    assert 'project in ("ABC", "DEF")' in multi


def test_status_category_picker():
    f = SearchFilters(status_categories=["To Do", "Done"])
    jql = build_jql(AppConfig(), f)
    assert 'statusCategory in ("To Do", "Done")' in jql


def test_sprint_and_fix_version():
    f = SearchFilters(sprint="Sprint 12", fix_version="2026.4.0")
    jql = build_jql(AppConfig(), f)
    assert 'sprint = "Sprint 12"' in jql
    assert 'fixVersion = "2026.4.0"' in jql


def test_date_filters_created_resolved_due():
    f = SearchFilters(created_days=30, resolved_days=7, due_days=14)
    jql = build_jql(AppConfig(), f)
    assert "created >= -30d" in jql
    assert "resolved >= -7d" in jql
    assert "duedate <= 14d" in jql


def test_unresolved_clause():
    assert "resolution = Unresolved" in build_jql(AppConfig(), SearchFilters(unresolved=True))


def test_field_filters_eq_and_in():
    f = SearchFilters(field_filters=[
        FieldFilter(field="labels", op="=", value="settlement"),
        FieldFilter(field="component", op="in", value="Frontend, Gateway"),
    ])
    jql = build_jql(AppConfig(), f)
    assert 'labels = "settlement"' in jql
    assert 'component in ("Frontend", "Gateway")' in jql


def test_field_filters_not_in_expands_list():
    # "not in" must expand the comma list like "in" — not quote it as one string.
    f = SearchFilters(field_filters=[
        FieldFilter(field="status", op="not in", value="Done, Closed"),
    ])
    jql = build_jql(AppConfig(), f)
    assert 'status not in ("Done", "Closed")' in jql
    # regression guard: the whole list must never be quoted as a single value
    assert 'status not in "Done, Closed"' not in jql


def test_field_filter_not_in_single_value():
    f = SearchFilters(field_filters=[FieldFilter(field="priority", op="not in", value="Low")])
    assert 'priority not in ("Low")' in build_jql(AppConfig(), f)


def test_field_filter_in_and_not_in_ignored_when_list_empty():
    # A comma-only / whitespace value yields no parts -> the clause is dropped.
    f = SearchFilters(field_filters=[
        FieldFilter(field="status", op="in", value=" , "),
        FieldFilter(field="status", op="not in", value=""),
    ])
    assert build_jql(AppConfig(), f) == "assignee = currentUser() ORDER BY updated DESC"


def test_field_filter_custom_field_accessor():
    f = SearchFilters(field_filters=[FieldFilter(field="cf[10050]", op="~", value="Acme")])
    assert 'cf[10050] ~ "Acme"' in build_jql(AppConfig(), f)


def test_empty_field_filter_is_ignored():
    f = SearchFilters(field_filters=[
        FieldFilter(field="", value=""), FieldFilter(field="x", value="")])
    assert build_jql(AppConfig(), f) == "assignee = currentUser() ORDER BY updated DESC"


def test_raw_mode_returns_verbatim():
    f = SearchFilters(raw_mode=True, raw_jql="project = ZZZ ORDER BY created ASC")
    assert build_jql(AppConfig(), f) == "project = ZZZ ORDER BY created ASC"


def test_raw_mode_empty_falls_back_to_structured():
    f = SearchFilters(raw_mode=True, raw_jql="   ")
    assert build_jql(AppConfig(), f) == "assignee = currentUser() ORDER BY updated DESC"
