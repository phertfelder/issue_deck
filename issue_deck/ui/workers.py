"""Qt worker objects. All threading/signal machinery lives here in the UI layer;
the actual work is delegated to :mod:`issue_deck.services` and
:mod:`issue_deck.csv_import`.

Workers own a :class:`~issue_deck.cancellation.CancelToken` and expose
``cancel()`` so the UI can stop a long operation. Progress is emitted as a
structured :class:`~issue_deck.progress.FetchProgress` object (not a string) so
the view can render a determinate bar and a status line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from ..cancellation import CancelledError, CancelToken
from ..comments import CommentsOptions
from ..config import AppConfig
from ..csv_import import ParsedCsv, read_csv_file
from ..jira_client import JiraClient
from ..jql_helper import validate_jql
from ..services import issue_service, value_source_service


class FetchWorker(QObject):
    progress = pyqtSignal(object)    # FetchProgress
    finished = pyqtSignal(object)    # FetchResult
    failed = pyqtSignal(object)      # the raw Exception, so the UI can present it typed
    cancelled = pyqtSignal()

    def __init__(self, client: JiraClient, jql: str, cfg: AppConfig, *,
                 comments: CommentsOptions | None = None,
                 max_issues: int | None = None,
                 fail_on_comment_error: bool = False):
        super().__init__()
        self.client = client
        self.jql = jql
        self.cfg = cfg
        self.comments = comments or CommentsOptions()
        self.max_issues = max_issues
        self.fail_on_comment_error = fail_on_comment_error
        self.cancel_token = CancelToken()

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def run(self) -> None:
        try:
            result = issue_service.fetch_issues(
                self.client, self.jql, self.cfg,
                comments=self.comments,
                cancel=self.cancel_token,
                on_progress=self.progress.emit,
                max_issues=self.max_issues,
                fail_on_comment_error=self.fail_on_comment_error,
            )
            self.finished.emit(result)
        except CancelledError:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 - surface any failure to the UI
            self.failed.emit(e)   # the exception object, presented typed by the UI


class ExportWorker(QObject):
    """Run a blocking export ``job`` off the UI thread.

    ``job`` is a thunk that does the prepare+render+write and returns a
    human-readable result message (e.g. the output path). Keeping the file I/O
    here means large packs/per-ticket exports no longer freeze the window. The
    thunk must only *read* app state (it runs on a worker thread).
    """

    finished = pyqtSignal(str)   # success message
    failed = pyqtSignal(str)

    def __init__(self, job: Callable[[], str]):
        super().__init__()
        self._job = job

    def run(self) -> None:
        try:
            self.finished.emit(self._job())
        except Exception as e:  # noqa: BLE001 - surface any write failure to the UI
            self.failed.emit(str(e))


class CsvParseWorker(QObject):
    """Parse a CSV file off the UI thread so large imports stay responsive."""

    finished = pyqtSignal(object)    # ParsedCsv
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, path: str | Path):
        super().__init__()
        self.path = path
        self.cancel_token = CancelToken()

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def run(self) -> None:
        try:
            parsed: ParsedCsv = read_csv_file(self.path, cancel=self.cancel_token)
            self.finished.emit(parsed)
        except CancelledError:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 - surface any parse failure to the UI
            self.failed.emit(str(e))


class ValidateJqlWorker(QObject):
    """Validate a JQL string off-thread by running it with ``maxResults=1``.

    The single round-trip surfaces Jira's own syntax errors without pulling a
    full result set; the pure mapping lives in :func:`issue_deck.jql_helper`.
    """

    finished = pyqtSignal(object)   # JqlValidation
    failed = pyqtSignal(str)

    def __init__(self, client: JiraClient, cfg: AppConfig, jql: str):
        super().__init__()
        self.client = client
        self.cfg = cfg
        self.jql = jql

    def run(self) -> None:
        try:
            self.finished.emit(validate_jql(self.client, self.cfg, self.jql))
        except Exception as e:  # noqa: BLE001 - validate_jql shouldn't raise, but be safe
            self.failed.emit(str(e))


class CountsWorker(QObject):
    """Compute bounded result-counts for a list of JQL strings, off-thread.

    Each count is a single ``maxResults=1`` round-trip (via
    :func:`issue_deck.jql_helper.validate_jql`), so this never pulls a full
    result set — it just reports each query's ``total``. Emits one
    :attr:`countReady` per query as it lands, then :attr:`finished`. Used by the
    Home command center to fill in each preset card's live count pill.
    """

    countReady = pyqtSignal(int, object)   # (index, total: int | None)
    finished = pyqtSignal()

    def __init__(self, client: JiraClient, cfg: AppConfig, jqls: list[str]):
        super().__init__()
        self.client = client
        self.cfg = cfg
        self.jqls = list(jqls)
        self.cancel_token = CancelToken()

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def run(self) -> None:
        for i, jql in enumerate(self.jqls):
            if self.cancel_token.cancelled:
                break
            # validate_jql maps the typed error hierarchy and never raises; an
            # invalid or unauthorized query simply yields total=None (a dash pill).
            result = validate_jql(self.client, self.cfg, jql)
            self.countReady.emit(i, result.total if result.ok else None)
        self.finished.emit()


class SampleWorker(QObject):
    """Pulls a bounded sample of issues off-thread for value discovery."""

    finished = pyqtSignal(list)   # list[NormalizedIssue]
    failed = pyqtSignal(str)

    def __init__(self, client: JiraClient, cfg: AppConfig, jql: str,
                 max_issues: int, extra_field_ids: tuple[str, ...] = ()):
        super().__init__()
        self.client = client
        self.cfg = cfg
        self.jql = jql
        self.max_issues = max_issues
        self.extra_field_ids = extra_field_ids

    def run(self) -> None:
        try:
            issues = value_source_service.sample_issues(
                self.client, self.cfg, jql=self.jql, max_issues=self.max_issues,
                extra_field_ids=self.extra_field_ids)
            self.finished.emit(issues)
        except Exception as e:
            self.failed.emit(str(e))
