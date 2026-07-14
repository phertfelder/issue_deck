"""JiraClient tests with mocked HTTP. Preserves Cloud vs Server behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from requests.auth import HTTPBasicAuth

from issue_deck.config import AppConfig
from issue_deck.jira_client import (
    AuthError,
    DeploymentMismatchError,
    JiraClient,
    JiraError,
    JiraPermissionError,
    RateLimitedError,
    RetryPolicy,
    ServerError,
    make_client,
)


@pytest.fixture
def cloud_client():
    cfg = AppConfig(base_url="https://example.atlassian.net",
                    deployment="cloud", email="tester@example.com")
    return JiraClient(cfg, "cloud-token", sleep=lambda *_: None)


@pytest.fixture
def server_client():
    cfg = AppConfig(base_url="https://jira.example.com", deployment="server")
    return JiraClient(cfg, "server-pat", sleep=lambda *_: None)


# ---- auth / endpoint selection ----
def test_cloud_uses_basic_auth(cloud_client):
    assert isinstance(cloud_client.session.auth, HTTPBasicAuth)
    assert cloud_client.session.auth.username == "tester@example.com"
    assert cloud_client.session.auth.password == "cloud-token"
    assert cloud_client.is_cloud is True


def test_server_uses_bearer_header(server_client):
    assert server_client.session.auth is None
    assert server_client.session.headers["Authorization"] == "Bearer server-pat"
    assert server_client.is_cloud is False


# ---- search ----
def test_cloud_search_endpoint_body_and_pagination(cloud_client, requests_mock):
    url = "https://example.atlassian.net/rest/api/3/search/jql"
    requests_mock.post(url, [
        {"json": {"issues": [{"key": "A-1"}], "nextPageToken": "tok2", "isLast": False}},
        {"json": {"issues": [{"key": "A-2"}], "isLast": True}},
    ])
    outcome = cloud_client.search("assignee = currentUser()", ["summary", "status"])
    assert [i["key"] for i in outcome.issues] == ["A-1", "A-2"]
    assert requests_mock.call_count == 2
    first_body = requests_mock.request_history[0].json()
    assert first_body["jql"] == "assignee = currentUser()"
    assert first_body["fields"] == ["summary", "status"]
    assert first_body["maxResults"] == 100
    assert "nextPageToken" not in first_body
    assert requests_mock.request_history[1].json()["nextPageToken"] == "tok2"


def test_server_search_endpoint_and_startat_pagination(server_client, requests_mock):
    url = "https://jira.example.com/rest/api/2/search"
    requests_mock.get(url, [
        {"json": {"issues": [{"key": "S-1"}], "total": 2, "startAt": 0}},
        {"json": {"issues": [{"key": "S-2"}], "total": 2, "startAt": 1}},
    ])
    outcome = server_client.search("assignee = currentUser()", ["summary", "status"])
    assert [i["key"] for i in outcome.issues] == ["S-1", "S-2"]
    assert outcome.total == 2
    assert requests_mock.call_count == 2
    first = requests_mock.request_history[0]
    assert first.qs["startat"] == ["0"]
    assert first.qs["fields"] == ["summary,status"]
    assert requests_mock.request_history[1].qs["startat"] == ["1"]


# ---- comments ----
def test_get_comments_cloud_uses_v3(cloud_client, requests_mock):
    requests_mock.get(
        "https://example.atlassian.net/rest/api/3/issue/CLOUD-1/comment",
        json={"comments": [{"id": "1"}], "total": 1})
    assert cloud_client.get_comments("CLOUD-1") == [{"id": "1"}]


def test_get_comments_server_uses_v2_and_paginates(server_client, requests_mock):
    requests_mock.get(
        "https://jira.example.com/rest/api/2/issue/SRV-42/comment", [
            {"json": {"comments": [{"id": "1"}], "total": 2, "startAt": 0}},
            {"json": {"comments": [{"id": "2"}], "total": 2, "startAt": 1}},
        ])
    comments = server_client.get_comments("SRV-42")
    assert [c["id"] for c in comments] == ["1", "2"]
    assert requests_mock.request_history[1].qs["startat"] == ["1"]


# ---- whoami / field_map ----
def test_whoami_cloud(cloud_client, requests_mock):
    requests_mock.get("https://example.atlassian.net/rest/api/3/myself",
                      json={"displayName": "Ada"})
    assert cloud_client.whoami()["displayName"] == "Ada"


def test_whoami_server(server_client, requests_mock):
    requests_mock.get("https://jira.example.com/rest/api/2/myself", json={"name": "jdoe"})
    assert server_client.whoami()["name"] == "jdoe"


def test_field_map_id_to_name(cloud_client, requests_mock):
    requests_mock.get("https://example.atlassian.net/rest/api/3/field",
                      json=[{"id": "summary", "name": "Summary"},
                            {"id": "customfield_10050", "name": "Client"}])
    fm = cloud_client.field_map()
    assert fm["summary"] == "Summary"
    assert fm["customfield_10050"] == "Client"


# ---- value sources ----
def test_projects_cloud_paginates_project_search(cloud_client, requests_mock):
    url = "https://example.atlassian.net/rest/api/3/project/search"
    requests_mock.get(url, [
        {"json": {"values": [{"key": "ABC"}], "isLast": False}},
        {"json": {"values": [{"key": "DEF"}], "isLast": True}},
    ])
    assert [p["key"] for p in cloud_client.projects()] == ["ABC", "DEF"]


def test_projects_server_uses_plain_array(server_client, requests_mock):
    requests_mock.get("https://jira.example.com/rest/api/2/project",
                      json=[{"key": "ABC"}, {"key": "DEF"}])
    assert [p["key"] for p in server_client.projects()] == ["ABC", "DEF"]


def test_statuses_types_priorities(cloud_client, requests_mock):
    base = "https://example.atlassian.net/rest/api/3"
    requests_mock.get(f"{base}/status", json=[{"name": "Open"}])
    requests_mock.get(f"{base}/issuetype", json=[{"name": "Bug"}])
    requests_mock.get(f"{base}/priority", json=[{"name": "High"}])
    assert cloud_client.statuses()[0]["name"] == "Open"
    assert cloud_client.issue_types()[0]["name"] == "Bug"
    assert cloud_client.priorities()[0]["name"] == "High"


def test_search_users_cloud_uses_query_param(cloud_client, requests_mock):
    requests_mock.get("https://example.atlassian.net/rest/api/3/user/search",
                      json=[{"displayName": "Ada"}])
    users = cloud_client.search_users("ad")
    assert users[0]["displayName"] == "Ada"
    assert requests_mock.request_history[0].qs["query"] == ["ad"]


def test_search_users_server_uses_username_param(server_client, requests_mock):
    requests_mock.get("https://jira.example.com/rest/api/2/user/search",
                      json=[{"name": "ada"}])
    server_client.search_users("ad")
    assert requests_mock.request_history[0].qs["username"] == ["ad"]


def test_project_components_and_versions(cloud_client, requests_mock):
    base = "https://example.atlassian.net/rest/api/3/project/ABC"
    requests_mock.get(f"{base}/components", json=[{"name": "api"}])
    requests_mock.get(f"{base}/versions", json=[{"name": "1.0"}])
    assert cloud_client.project_components("ABC")[0]["name"] == "api"
    assert cloud_client.project_versions("ABC")[0]["name"] == "1.0"


# ---- error handling: typed errors ----
@pytest.mark.parametrize("status,exc,needle", [
    (401, AuthError, "401 Unauthorized"),
    (403, JiraPermissionError, "403 Forbidden"),
    (410, DeploymentMismatchError, "410 Gone"),
])
def test_error_status_typed(cloud_client, requests_mock, status, exc, needle):
    requests_mock.get("https://example.atlassian.net/rest/api/3/myself",
                      status_code=status, text="err")
    with pytest.raises(exc) as ei:
        cloud_client._get("/rest/api/3/myself")
    assert needle in str(ei.value)
    assert isinstance(ei.value, JiraError)   # all specifics subclass the base


def test_invalid_jql_400_is_typed(cloud_client, requests_mock):
    requests_mock.post(
        "https://example.atlassian.net/rest/api/3/search/jql", status_code=400,
        json={"errorMessages": ["Error in the JQL Query: unexpected token 'foo'."]})
    with pytest.raises(JiraError) as ei:
        cloud_client.search("foo bar baz", ["summary"])
    from issue_deck.jira_client import InvalidJQLError
    assert isinstance(ei.value, InvalidJQLError)
    assert "JQL" in str(ei.value)


def test_5xx_retries_then_raises_server_error(cloud_client, requests_mock):
    requests_mock.get("https://example.atlassian.net/rest/api/3/myself",
                      status_code=500, text="boom details")
    with pytest.raises(ServerError) as ei:
        cloud_client._get("/rest/api/3/myself")
    assert "HTTP 500" in str(ei.value)
    assert "boom details" in str(ei.value)
    # 1 initial + max_retries attempts.
    assert requests_mock.call_count == cloud_client.retry_policy.max_retries + 1


def test_transient_503_then_success_recovers(cloud_client, requests_mock):
    requests_mock.get("https://example.atlassian.net/rest/api/3/myself", [
        {"status_code": 503, "text": "warming up"},
        {"json": {"displayName": "Ada"}},
    ])
    assert cloud_client.whoami()["displayName"] == "Ada"
    assert requests_mock.call_count == 2


def test_429_recovers_and_respects_retry_after(requests_mock):
    slept = []
    cfg = AppConfig(base_url="https://example.atlassian.net",
                    deployment="cloud", email="tester@example.com")
    client = JiraClient(cfg, "tok", sleep=slept.append)
    requests_mock.get("https://example.atlassian.net/rest/api/3/myself", [
        {"status_code": 429, "headers": {"Retry-After": "7"}},
        {"json": {"displayName": "Ada"}},
    ])
    assert client.whoami()["displayName"] == "Ada"
    # Retry-After (7s) is honoured verbatim rather than using jittered backoff.
    assert 7 in slept


def test_rate_limited_exhausted_raises_typed(cloud_client, requests_mock):
    requests_mock.get("https://example.atlassian.net/rest/api/3/myself",
                      status_code=429, headers={"Retry-After": "1"})
    with pytest.raises(RateLimitedError) as ei:
        cloud_client._get("/rest/api/3/myself")
    assert ei.value.retry_after == 1.0


def test_zero_retry_policy_raises_immediately(requests_mock):
    cfg = AppConfig(base_url="https://example.atlassian.net",
                    deployment="cloud", email="tester@example.com")
    client = JiraClient(cfg, "tok", sleep=lambda *_: None,
                        retry_policy=RetryPolicy(max_retries=0))
    requests_mock.get("https://example.atlassian.net/rest/api/3/myself",
                      status_code=502, text="bad gateway")
    with pytest.raises(ServerError):
        client._get("/rest/api/3/myself")
    assert requests_mock.call_count == 1


def test_search_max_results_caps_and_flags_truncation(cloud_client, requests_mock):
    requests_mock.post("https://example.atlassian.net/rest/api/3/search/jql", json={
        "issues": [{"key": f"A-{i}"} for i in range(100)],
        "nextPageToken": "more", "isLast": False,
    })
    outcome = cloud_client.search("assignee = currentUser()", ["summary"], max_results=10)
    assert len(outcome.issues) == 10
    assert outcome.truncated is True


def test_user_agent_header_is_set(cloud_client):
    from issue_deck import constants
    assert cloud_client.session.headers["User-Agent"] == constants.USER_AGENT


def test_configurable_timeout_used(requests_mock):
    cfg = AppConfig(base_url="https://example.atlassian.net", deployment="cloud",
                    email="tester@example.com", request_timeout=15)
    client = JiraClient(cfg, "tok", sleep=lambda *_: None)
    assert client.timeout == 15


# ---- timeout ----
def test_get_uses_60s_timeout(cloud_client):
    sess = MagicMock()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"ok": True}
    sess.get.return_value = resp
    cloud_client.session = sess
    cloud_client._get("/rest/api/3/myself")
    assert sess.get.call_args.kwargs["timeout"] == 60


# ---- make_client validation ----
def test_make_client_requires_url_and_token():
    with pytest.raises(JiraError):
        make_client(AppConfig(base_url="", email="x@y.z"), "")


def test_make_client_cloud_requires_email():
    with pytest.raises(JiraError):
        make_client(AppConfig(base_url="https://x", deployment="cloud", email=""), "tok")


def test_make_client_ok():
    client = make_client(
        AppConfig(base_url="https://x", deployment="cloud", email="a@b.c"), "tok")
    assert isinstance(client, JiraClient)
