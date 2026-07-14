"""Tests for client-backed value sources and bounded sampling."""

from __future__ import annotations

from issue_deck.config import AppConfig
from issue_deck.jira_client import JiraError, SearchOutcome
from issue_deck.services import value_source_service as vss


class FakeClient:
    """A stand-in exposing just the value-source endpoints under test."""

    def __init__(self, **responses):
        self._responses = responses
        self.searched = None

    def projects(self):
        return self._responses.get("projects", [])

    def statuses(self):
        return self._responses.get("statuses", [])

    def issue_types(self):
        return self._responses.get("issue_types", [])

    def priorities(self):
        return self._responses.get("priorities", [])

    def search_users(self, query):
        return self._responses.get("users", [])

    def project_components(self, key):
        return self._responses.get("components", [])

    def project_versions(self, key):
        return self._responses.get("versions", [])

    def search(self, jql, fields, *, on_progress=None, cancel=None,
               on_retry=None, max_results=None):
        self.searched = {"jql": jql, "fields": fields, "max_results": max_results}
        raw = self._responses.get("issues", [])
        capped = raw[:max_results] if max_results is not None else raw
        if on_progress:
            on_progress(len(capped), None)
        return SearchOutcome(issues=capped, truncated=len(capped) < len(raw))


# ---- FieldOption ----
def test_field_option_label_defaults_to_value():
    assert vss.FieldOption(value="Open").label == "Open"
    assert vss.FieldOption(value="Open", label="Open status").label == "Open status"


# ---- authoritative option lists ----
def test_project_options_use_key_and_name():
    client = FakeClient(projects=[{"key": "ABC", "name": "Alpha"}, {"key": "DEF", "name": "Delta"}])
    opts = vss.project_options(client)
    assert [o.value for o in opts] == ["ABC", "DEF"]
    assert opts[0].label == "ABC — Alpha"


def test_status_and_type_options_dedup_and_drop_blanks():
    client = FakeClient(statuses=[{"name": "Open"}, {"name": "Open"}, {"name": ""}])
    assert [o.value for o in vss.status_options(client)] == ["Open"]


def test_user_options_include_email_in_label():
    client = FakeClient(users=[{"displayName": "Ada", "emailAddress": "ada@x.io"}])
    opt = vss.user_options(client, "ad")[0]
    assert opt.value == "Ada"
    assert opt.label == "Ada <ada@x.io>"


def test_component_and_version_options():
    client = FakeClient(components=[{"name": "api"}], versions=[{"name": "1.0"}])
    assert [o.value for o in vss.component_options(client, "ABC")] == ["api"]
    assert [o.value for o in vss.version_options(client, "ABC")] == ["1.0"]


# ---- options_for_field routing ----
def test_options_for_field_global_source():
    client = FakeClient(statuses=[{"name": "Open"}])
    opts = vss.options_for_field(client, "status")
    assert [o.value for o in opts] == ["Open"]


def test_options_for_field_project_source_needs_project():
    client = FakeClient(components=[{"name": "api"}])
    # No project -> None (fall back to sampling)
    assert vss.options_for_field(client, "components") is None
    opts = vss.options_for_field(client, "components", project_key="ABC")
    assert [o.value for o in opts] == ["api"]


def test_options_for_field_unknown_returns_none():
    assert vss.options_for_field(FakeClient(), "customfield_10099") is None
    assert vss.options_for_field(FakeClient(), "severity") is None


def test_options_for_field_swallows_api_error():
    class Boom(FakeClient):
        def statuses(self):
            raise JiraError("403")

    assert vss.options_for_field(Boom(), "status") == []


# ---- bounded sampling ----
def test_sample_issues_normalizes_and_caps(cloud_issue):
    cfg = AppConfig(base_url="https://x", deployment="cloud", email="a@b.c")
    client = FakeClient(issues=[cloud_issue, cloud_issue, cloud_issue])
    issues = vss.sample_issues(client, cfg, jql="project = ABC", max_issues=2)
    assert len(issues) == 2
    assert issues[0].key == "CLOUD-1"
    assert issues[0].comments == []  # sampling never fetches comments
    assert client.searched["max_results"] == 2


def test_sample_issues_requests_extra_custom_fields():
    cfg = AppConfig(base_url="https://x", deployment="cloud", email="a@b.c",
                    client_field="customfield_10050")
    client = FakeClient(issues=[])
    vss.sample_issues(client, cfg, jql="x", max_issues=10,
                      extra_field_ids=("customfield_10077",))
    assert "customfield_10050" in client.searched["fields"]
    assert "customfield_10077" in client.searched["fields"]
