"""HTTP client for both Jira Cloud and Server/Data Center.

Cloud uses ``/rest/api/3`` (nextPageToken pagination, ADF bodies, basic auth);
Server/DC uses ``/rest/api/2`` (startAt pagination, wiki/plain bodies, Bearer
PAT). This module is the only place that talks to Jira over HTTP.

Hardening lives here too: every request goes through :meth:`JiraClient._request`,
which retries transient failures (429/5xx and timeouts/connection drops) with
exponential backoff + jitter, honours ``Retry-After``, and raises *typed* errors
(:class:`AuthError`, :class:`RateLimitedError`, …) so callers can react precisely.
Retries, timeouts and backoff sleeps all cooperate with a :class:`CancelToken`.
"""

from __future__ import annotations

import datetime as _dt
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Callable, cast

import requests
from requests.auth import HTTPBasicAuth

from . import constants
from .cancellation import CancelToken
from .config import AppConfig
from .logging_utils import get_logger

if TYPE_CHECKING:
    from .progress import RetryEvent

# Namespaced, secret-redacting logger. We deliberately log only the HTTP method
# and the request *path* — never headers, auth, query params, or bodies — so a
# token cannot reach the logs. The redacting filter is a second line of defence.
log = get_logger("jira_client")

ProgressFn = Callable[[int, "int | None"], None]     # (fetched, total|None)
RetryFn = Callable[["RetryEvent"], None]             # backoff notifications


# --------------------------------------------------------------------------- #
# Typed error hierarchy — every failure is a JiraError subclass, so existing
# ``except JiraError`` handlers keep working while new code can catch specifics.
# --------------------------------------------------------------------------- #
class JiraError(Exception):
    """Base for all Jira client errors."""

    def __init__(self, message: str, *, status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class AuthError(JiraError):
    """401 — bad/expired credentials or wrong deployment type."""


class JiraPermissionError(JiraError):
    """403 — authenticated but not authorized for the resource."""


class InvalidJQLError(JiraError):
    """400 whose body indicates the JQL could not be parsed/executed."""


class RateLimitedError(JiraError):
    """429 — too many requests (retries exhausted). Carries ``retry_after``."""


class ServerError(JiraError):
    """5xx server-side failure (retries exhausted)."""


class NetworkTimeoutError(JiraError):
    """The request timed out (retries exhausted)."""


class SSLCertError(JiraError):
    """TLS/certificate verification failed — not retried."""


class NetworkError(JiraError):
    """Connection could not be established (retries exhausted)."""


class DeploymentMismatchError(JiraError):
    """The endpoint isn't there for this deployment (e.g. 410 Gone on legacy)."""


# --------------------------------------------------------------------------- #
# Retry policy + backoff helpers
# --------------------------------------------------------------------------- #
@dataclass
class RetryPolicy:
    """Exponential-backoff-with-jitter parameters for transient failures."""

    max_retries: int = 4
    base_delay: float = 0.5
    max_delay: float = 30.0
    backoff_factor: float = 2.0
    retry_statuses: frozenset[int] = constants.RETRYABLE_STATUSES


def _backoff_delay(attempt: int, policy: RetryPolicy, rng: random.Random) -> float:
    """Full-jitter backoff: uniform(0, min(max, base * factor**attempt))."""
    ceiling = min(policy.max_delay, policy.base_delay * (policy.backoff_factor ** attempt))
    return rng.uniform(0, ceiling)


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP date) into seconds."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return max(0.0, (when - _dt.datetime.now(_dt.timezone.utc)).total_seconds())


@dataclass
class SearchOutcome:
    """Result of a paginated search: the raw issues plus cap/total metadata."""

    issues: list[dict]
    total: int | None = None      # reported by Server; unknown (None) for Cloud
    truncated: bool = False       # True when a max_results cap hid more results


class JiraClient:
    def __init__(self, cfg: AppConfig, token: str, *,
                 retry_policy: RetryPolicy | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 rng: random.Random | None = None):
        self.cfg = cfg
        self.token = token
        self.base = cfg.base_url.rstrip("/")
        self.is_cloud = cfg.deployment == "cloud"
        self.timeout = cfg.request_timeout or constants.DEFAULT_REQUEST_TIMEOUT
        self.retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        self._rng = rng or random.Random()
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": constants.USER_AGENT,
        })
        if self.is_cloud:
            self.session.auth = HTTPBasicAuth(cfg.email, token)
        else:
            self.session.headers["Authorization"] = f"Bearer {token}"

    # ---- low level: the single choke point for every HTTP call ----
    def _send(self, method: str, url: str, params: dict | None,
              json: dict | None) -> requests.Response:
        if method == "GET":
            return self.session.get(url, params=params, timeout=self.timeout)
        return self.session.post(
            url, json=json, timeout=self.timeout,
            headers={"Content-Type": "application/json"},
        )

    def _sleep_cancellable(self, delay: float, cancel: CancelToken | None) -> None:
        """Sleep, but wake ~10×/s to notice a cancel during a long backoff."""
        if cancel is None:
            self._sleep(delay)
            return
        remaining = delay
        while remaining > 0:
            cancel.raise_if_cancelled()
            step = min(0.1, remaining)
            self._sleep(step)
            remaining -= step

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 json: dict | None = None, on_retry: RetryFn | None = None,
                 cancel: CancelToken | None = None) -> dict:
        """Perform one logical request, retrying transient failures with backoff."""
        from .progress import RetryEvent  # local import: keep this module Qt/UI-free

        url = f"{self.base}{path}"
        policy = self.retry_policy
        attempt = 0
        log.debug("HTTP %s %s", method, path)  # path only — no auth, params or body
        while True:
            if cancel is not None:
                cancel.raise_if_cancelled()
            try:
                resp = self._send(method, url, params, json)
            except requests.exceptions.SSLError as e:
                raise SSLCertError(
                    f"SSL/certificate error contacting {self.base}: {e}") from e
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError) as e:
                is_timeout = isinstance(e, requests.exceptions.Timeout)
                if attempt < policy.max_retries:
                    delay = _backoff_delay(attempt, policy, self._rng)
                    reason = "Request timed out" if is_timeout else "Connection error"
                    if on_retry is not None:
                        on_retry(RetryEvent(attempt + 1, policy.max_retries, delay,
                                            None, reason, False))
                    self._sleep_cancellable(delay, cancel)
                    attempt += 1
                    continue
                if is_timeout:
                    raise NetworkTimeoutError(
                        f"Request to {self.base} timed out after {self.timeout}s "
                        f"({policy.max_retries} retries).") from e
                raise NetworkError(
                    f"Could not connect to {self.base}: {e}") from e

            if resp.status_code in policy.retry_statuses and attempt < policy.max_retries:
                retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                delay = (retry_after if retry_after is not None
                         else _backoff_delay(attempt, policy, self._rng))
                rate_limited = resp.status_code == 429
                reason = ("Rate limited (429)" if rate_limited
                          else f"Server error ({resp.status_code})")
                if on_retry is not None:
                    on_retry(RetryEvent(attempt + 1, policy.max_retries, delay,
                                        resp.status_code, reason, rate_limited))
                self._sleep_cancellable(delay, cancel)
                attempt += 1
                continue

            self._check(resp)
            return resp.json()

    # thin back-compat wrappers (used by whoami/field_map and callers/tests)
    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, json=body)

    @staticmethod
    def _extract_error(resp: requests.Response) -> str:
        """Pull Jira's ``errorMessages``/``errors`` out of a JSON error body."""
        try:
            data = resp.json()
        except ValueError:
            return ""
        if isinstance(data, dict):
            msgs = data.get("errorMessages") or []
            if msgs:
                return "; ".join(str(m) for m in msgs)
            errs = data.get("errors") or {}
            if isinstance(errs, dict) and errs:
                return "; ".join(f"{k}: {v}" for k, v in errs.items())
        return ""

    @staticmethod
    def _looks_like_jql_error(text: str) -> bool:
        low = text.lower()
        return any(kw in low for kw in (
            "jql", "does not exist", "clause", "expecting", "unable to parse",
            "field", "operator",
        ))

    def _check(self, resp: requests.Response) -> None:
        sc = resp.status_code
        if sc < 400:
            return
        detail = self._extract_error(resp) or (resp.text or "")[:400]
        if sc == 400:
            if self._looks_like_jql_error(detail):
                raise InvalidJQLError(f"Invalid JQL: {detail}", status=400)
            raise JiraError(f"HTTP 400: {detail}", status=400)
        if sc == 401:
            raise AuthError(
                "401 Unauthorized — check email/token and deployment type.", status=401)
        if sc == 403:
            raise JiraPermissionError(
                "403 Forbidden — token valid but lacks permission.", status=403)
        if sc == 404:
            raise JiraError(f"404 Not Found — check base URL / path. {detail}", status=404)
        if sc == 410:
            raise DeploymentMismatchError(
                "410 Gone — this instance removed the legacy /search endpoint. "
                "Set deployment to 'cloud' (uses /search/jql).", status=410)
        if sc == 429:
            raise RateLimitedError(
                "429 Too Many Requests — rate limited.", status=429,
                retry_after=parse_retry_after(resp.headers.get("Retry-After")))
        if sc >= 500:
            raise ServerError(f"HTTP {sc}: {detail}", status=sc)
        raise JiraError(f"HTTP {sc}: {detail}", status=sc)

    # ---- identity ----
    def whoami(self) -> dict:
        ver = "3" if self.is_cloud else "2"
        return self._get(f"/rest/api/{ver}/myself")

    # ---- search ----
    def search(self, jql: str, fields: list[str], *,
               on_progress: ProgressFn | None = None,
               cancel: CancelToken | None = None,
               on_retry: RetryFn | None = None,
               max_results: int | None = None) -> SearchOutcome:
        """Paginate a JQL search, cooperating with cancel/retry/caps."""
        if self.is_cloud:
            return self._search_cloud(jql, fields, on_progress, cancel, on_retry, max_results)
        return self._search_server(jql, fields, on_progress, cancel, on_retry, max_results)

    def _search_cloud(self, jql: str, fields: list[str], on_progress: ProgressFn | None,
                      cancel: CancelToken | None, on_retry: RetryFn | None,
                      max_results: int | None) -> SearchOutcome:
        issues: list[dict] = []
        token = None
        truncated = False
        while True:
            if cancel is not None:
                cancel.raise_if_cancelled()
            body: dict = {"jql": jql, "fields": fields, "maxResults": 100}
            if token:
                body["nextPageToken"] = token
            data = self._request("POST", "/rest/api/3/search/jql", json=body,
                                 on_retry=on_retry, cancel=cancel)
            batch = data.get("issues", [])
            issues.extend(batch)
            token = data.get("nextPageToken")
            is_last = bool(data.get("isLast")) or not token or not batch
            if max_results is not None and len(issues) >= max_results:
                truncated = (not is_last) or len(issues) > max_results
                issues = issues[:max_results]
                if on_progress:
                    on_progress(len(issues), None)
                break
            if on_progress:
                on_progress(len(issues), None)
            if is_last:
                break
        return SearchOutcome(issues, total=None, truncated=truncated)

    def _search_server(self, jql: str, fields: list[str], on_progress: ProgressFn | None,
                       cancel: CancelToken | None, on_retry: RetryFn | None,
                       max_results: int | None) -> SearchOutcome:
        issues: list[dict] = []
        start = 0
        total: int | None = None
        truncated = False
        while True:
            if cancel is not None:
                cancel.raise_if_cancelled()
            params = {"jql": jql, "fields": ",".join(fields),
                      "startAt": start, "maxResults": 100}
            data = self._request("GET", "/rest/api/2/search", params=params,
                                 on_retry=on_retry, cancel=cancel)
            batch = data.get("issues", [])
            issues.extend(batch)
            total = data.get("total", total if total is not None else len(issues))
            start += len(batch)
            reached_end = (not batch) or (total is not None and start >= total)
            if max_results is not None and len(issues) >= max_results:
                truncated = ((not reached_end) or len(issues) > max_results
                             or (total is not None and total > max_results))
                issues = issues[:max_results]
                if on_progress:
                    on_progress(len(issues), total)
                break
            if on_progress:
                on_progress(len(issues), total)
            if reached_end:
                break
        return SearchOutcome(issues, total=total, truncated=truncated)

    # ---- comments (fetched per-issue for completeness) ----
    def get_comments(self, key: str, *, cancel: CancelToken | None = None,
                     on_retry: RetryFn | None = None) -> list[dict]:
        ver = "3" if self.is_cloud else "2"
        comments: list[dict] = []
        start = 0
        while True:
            if cancel is not None:
                cancel.raise_if_cancelled()
            data = self._request(
                "GET", f"/rest/api/{ver}/issue/{key}/comment",
                params={"startAt": start, "maxResults": 100, "orderBy": "created"},
                on_retry=on_retry, cancel=cancel,
            )
            batch = data.get("comments", [])
            comments.extend(batch)
            total = data.get("total", len(comments))
            start += len(batch)
            if not batch or start >= total:
                break
        return comments

    def fields_raw(self) -> list[dict]:
        """Raw field descriptors from ``/field`` (name, clauseNames, searchable…)."""
        ver = "3" if self.is_cloud else "2"
        # /field returns a JSON array (the shared _get is typed loosely as dict).
        return cast("list[dict]", self._get(f"/rest/api/{ver}/field"))

    def field_map(self) -> dict[str, str]:
        """id -> human name, for resolving custom fields."""
        return {f["id"]: f.get("name", f["id"]) for f in self.fields_raw()}

    # ---- value sources (for populating filter dropdowns) ----
    @property
    def _ver(self) -> str:
        return "3" if self.is_cloud else "2"

    def projects(self) -> list[dict]:
        """All visible projects. Cloud paginates via ``/project/search``; Server
        returns a plain array from ``/project``."""
        if self.is_cloud:
            out: list[dict] = []
            start = 0
            while True:
                data = self._get(
                    "/rest/api/3/project/search", {"startAt": start, "maxResults": 100})
                values = data.get("values", [])
                out.extend(values)
                if data.get("isLast") or not values:
                    break
                start += len(values)
            return out
        return cast("list[dict]", self._get("/rest/api/2/project"))

    def statuses(self) -> list[dict]:
        """Global status definitions (``/status`` returns an array on both)."""
        return cast("list[dict]", self._get(f"/rest/api/{self._ver}/status"))

    def issue_types(self) -> list[dict]:
        return cast("list[dict]", self._get(f"/rest/api/{self._ver}/issuetype"))

    def priorities(self) -> list[dict]:
        return cast("list[dict]", self._get(f"/rest/api/{self._ver}/priority"))

    def search_users(self, query: str, max_results: int = 50) -> list[dict]:
        """Find users. Cloud filters with ``query=``; Server with ``username=``."""
        param = "query" if self.is_cloud else "username"
        return cast("list[dict]", self._get(
            f"/rest/api/{self._ver}/user/search",
            {param: query, "maxResults": max_results}))

    def project_components(self, project_key: str) -> list[dict]:
        return cast("list[dict]", self._get(
            f"/rest/api/{self._ver}/project/{project_key}/components"))

    def project_versions(self, project_key: str) -> list[dict]:
        return cast("list[dict]", self._get(
            f"/rest/api/{self._ver}/project/{project_key}/versions"))


def make_client(cfg: AppConfig, token: str) -> JiraClient:
    """Validate connection settings and construct a client (used by the UI)."""
    if not cfg.base_url or not token:
        raise JiraError("Base URL and token are required.")
    if cfg.deployment == "cloud" and not cfg.email:
        raise JiraError("Cloud deployment requires an email.")
    return JiraClient(cfg, token)
