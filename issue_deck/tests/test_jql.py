"""Characterization tests for build_jql (now driven by SearchFilters)."""

from __future__ import annotations

from issue_deck.config import AppConfig
from issue_deck.jql import build_jql
from issue_deck.models import SearchFilters


def test_default_query():
    jql = build_jql(AppConfig(), SearchFilters())
    assert jql == "assignee = currentUser() ORDER BY updated DESC"


def test_status_filter():
    jql = build_jql(AppConfig(), SearchFilters(statuses=["Open", "In Progress"]))
    assert jql == (
        'assignee = currentUser() AND status in ("Open", "In Progress") '
        "ORDER BY updated DESC"
    )


def test_issue_type_filter():
    jql = build_jql(AppConfig(), SearchFilters(issue_types=["Bug", "Task"]))
    assert jql == (
        'assignee = currentUser() AND issuetype in ("Bug", "Task") ORDER BY updated DESC'
    )


def test_severity_custom_field():
    cfg = AppConfig(severity_field="customfield_10060")
    jql = build_jql(cfg, SearchFilters(severity="S1"))
    assert jql == 'assignee = currentUser() AND cf[10060] = "S1" ORDER BY updated DESC'


def test_client_custom_field():
    cfg = AppConfig(client_field="customfield_10050")
    jql = build_jql(cfg, SearchFilters(client="Acme"))
    assert jql == 'assignee = currentUser() AND cf[10050] ~ "Acme" ORDER BY updated DESC'


def test_text_search():
    jql = build_jql(AppConfig(), SearchFilters(text="payment"))
    assert jql == 'assignee = currentUser() AND text ~ "payment" ORDER BY updated DESC'


def test_updated_within_days():
    jql = build_jql(AppConfig(), SearchFilters(updated_days=7))
    assert jql == "assignee = currentUser() AND updated >= -7d ORDER BY updated DESC"


def test_extra_jql_wrapped_in_parens():
    jql = build_jql(AppConfig(), SearchFilters(extra="project = ABC AND labels = x"))
    assert jql == (
        "assignee = currentUser() AND (project = ABC AND labels = x) ORDER BY updated DESC"
    )


def test_quote_escaping():
    jql = build_jql(AppConfig(), SearchFilters(text='a"b'))
    assert jql == 'assignee = currentUser() AND text ~ "a\\"b" ORDER BY updated DESC'


def test_no_severity_clause_when_field_id_blank():
    jql = build_jql(AppConfig(severity_field=""), SearchFilters(severity="High"))
    assert jql == "assignee = currentUser() ORDER BY updated DESC"


def test_no_client_clause_when_field_id_blank():
    jql = build_jql(AppConfig(client_field=""), SearchFilters(client="Acme"))
    assert jql == "assignee = currentUser() ORDER BY updated DESC"


def test_all_filters_combined_order():
    cfg = AppConfig(client_field="customfield_10050", severity_field="customfield_10060")
    filters = SearchFilters(
        statuses=["Open"], issue_types=["Bug"], severity="S1", client="Acme",
        text="payment", updated_days=7, extra="project = ABC")
    jql = build_jql(cfg, filters)
    assert jql == (
        'assignee = currentUser() AND status in ("Open") '
        'AND issuetype in ("Bug") AND cf[10060] = "S1" AND cf[10050] ~ "Acme" '
        'AND text ~ "payment" AND updated >= -7d AND (project = ABC) '
        "ORDER BY updated DESC"
    )


def test_commented_days_is_not_part_of_jql():
    # commented_within is a client-side filter and must not affect the JQL.
    jql = build_jql(AppConfig(), SearchFilters(commented_days=5))
    assert jql == "assignee = currentUser() ORDER BY updated DESC"
