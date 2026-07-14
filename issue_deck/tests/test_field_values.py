"""Tests for the (Qt-free) field-value distribution engine."""

from __future__ import annotations

from issue_deck.config import AppConfig
from issue_deck.field_values import (
    WidgetKind,
    custom_field_ids,
    distribution_for,
    distributions_from_issues,
    jql_token,
    recommend_widget,
    values_for,
)
from issue_deck.schema import JiraUser, NormalizedIssue


def _issue(**kw) -> NormalizedIssue:
    return NormalizedIssue(**kw)


# ---- accessors ----
def test_values_for_scalar_and_blank():
    assert values_for(_issue(status="Open"), "status") == ["Open"]
    assert values_for(_issue(status=""), "status") == []


def test_values_for_user_uses_display_name():
    i = _issue(assignee=JiraUser(display_name="Ada Lovelace"))
    assert values_for(i, "assignee") == ["Ada Lovelace"]


def test_values_for_multi_valued_field():
    i = _issue(labels=["backend", "urgent", ""])
    assert values_for(i, "labels") == ["backend", "urgent"]


def test_values_for_story_points_drops_trailing_zero():
    assert values_for(_issue(story_points=3.0), "story_points") == ["3"]
    assert values_for(_issue(story_points=2.5), "story_points") == ["2.5"]
    assert values_for(_issue(story_points=None), "story_points") == []


def test_values_for_custom_field_scalar_list_and_number():
    assert values_for(_issue(raw_field_values={"customfield_1": "S1"}),
                      "customfield_1") == ["S1"]
    assert values_for(_issue(raw_field_values={"customfield_1": ["a", "b"]}),
                      "customfield_1") == ["a", "b"]
    assert values_for(_issue(raw_field_values={"customfield_1": 4.0}),
                      "customfield_1") == ["4"]
    assert values_for(_issue(raw_field_values={}), "customfield_1") == []


# ---- distribution math ----
def test_distribution_counts_coverage_unique_empty():
    issues = [
        _issue(status="Open"),
        _issue(status="Open"),
        _issue(status="Done"),
        _issue(status=""),  # empty
    ]
    dist = distribution_for(issues, "status", "Status")
    assert dist.total == 4
    assert dist.non_empty == 3
    assert dist.empty_count == 1
    assert dist.unique_count == 2
    assert dist.coverage_pct == 75.0
    assert dist.top_values[0].value == "Open"
    assert dist.top_values[0].count == 2


def test_distribution_multi_valued_coverage_is_per_issue():
    # Two issues, one with two labels: coverage 100%, but 3 label occurrences.
    issues = [_issue(labels=["a", "b"]), _issue(labels=["a"])]
    dist = distribution_for(issues, "labels", "Labels")
    assert dist.non_empty == 2
    assert dist.coverage_pct == 100.0
    assert dist.unique_count == 2
    counts = {vc.value: vc.count for vc in dist.top_values}
    assert counts == {"a": 2, "b": 1}


def test_distribution_examples_are_first_seen_distinct():
    issues = [_issue(client="Acme"), _issue(client="Beta"), _issue(client="Acme")]
    dist = distribution_for(issues, "client", "Client", examples_n=2)
    assert dist.examples == ["Acme", "Beta"]


def test_distribution_empty_sample():
    dist = distribution_for([], "status", "Status")
    assert dist.total == 0
    assert dist.coverage_pct == 0.0
    assert dist.unique_count == 0


# ---- widget recommendation ----
def test_recommend_widget_thresholds():
    assert recommend_widget(3) is WidgetKind.CHECK_LIST
    assert recommend_widget(50) is WidgetKind.SEARCH_COMBO
    assert recommend_widget(5000) is WidgetKind.TEXT


def test_distribution_widget_property_tracks_cardinality():
    small = distribution_for([_issue(status="Open")], "status")
    assert small.widget is WidgetKind.CHECK_LIST


# ---- distributions_from_issues ----
def test_distributions_drops_empty_and_sorts_by_coverage():
    issues = [
        _issue(status="Open", priority="High"),
        _issue(status="Done"),  # no priority
    ]
    dists = distributions_from_issues(
        issues, [("status", "Status"), ("priority", "Priority"), ("client", "Client")])
    ids = [d.field_id for d in dists]
    assert "client" not in ids               # zero coverage -> dropped
    assert ids[0] == "status"                # 100% coverage sorts first
    assert ids[1] == "priority"              # 50%


def test_distributions_include_empty_keeps_zero_coverage():
    dists = distributions_from_issues(
        [_issue(status="Open")], [("client", "Client")], include_empty=True)
    assert [d.field_id for d in dists] == ["client"]


# ---- custom field discovery ----
def test_custom_field_ids_first_seen_order():
    issues = [
        _issue(raw_field_values={"customfield_2": "x", "customfield_1": "y"}),
        _issue(raw_field_values={"customfield_1": "z", "customfield_3": "w"}),
    ]
    assert custom_field_ids(issues) == ["customfield_2", "customfield_1", "customfield_3"]


# ---- jql_token ----
def test_jql_token_builtins():
    assert jql_token("status") == "status"
    assert jql_token("issue_type") == "issuetype"
    assert jql_token("components") == "component"
    assert jql_token("fix_versions") == "fixVersion"


def test_jql_token_severity_client_use_config():
    cfg = AppConfig(client_field="customfield_10050", severity_field="customfield_10060")
    assert jql_token("severity", cfg) == "cf[10060]"
    assert jql_token("client", cfg) == "cf[10050]"


def test_jql_token_custom_field_becomes_cf():
    assert jql_token("customfield_12345") == "cf[12345]"


def test_jql_token_none_when_not_searchable():
    assert jql_token("story_points") == ""
    assert jql_token("severity", AppConfig()) == ""  # no configured field id
