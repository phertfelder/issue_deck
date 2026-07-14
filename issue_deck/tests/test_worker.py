"""Low-friction tests for the Qt FetchWorker.

run() executes synchronously, so we drive it directly and capture emitted
signals without a QThread. A headless QApplication is created once.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from issue_deck.cancellation import CancelledError
from issue_deck.comments import CommentsMode, CommentsOptions
from issue_deck.jira_client import SearchOutcome
from issue_deck.schema import NormalizedIssue
from issue_deck.ui.workers import FetchWorker


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeClient:
    def __init__(self, raw_issues, comments=None, search_error=None, comment_error=None):
        self._raw = raw_issues
        self._comments = comments or []
        self._search_error = search_error
        self._comment_error = comment_error

    def search(self, jql, fields, *, on_progress=None, cancel=None, on_retry=None,
               max_results=None):
        if self._search_error:
            raise self._search_error
        if on_progress:
            on_progress(len(self._raw), None)
        return SearchOutcome(list(self._raw), total=len(self._raw))

    def get_comments(self, key, *, cancel=None, on_retry=None):
        if self._comment_error:
            raise self._comment_error
        return self._comments


def _run(client, cfg, comments=None, **kw):
    worker = FetchWorker(client, "assignee = currentUser()", cfg,
                         comments=comments or CommentsOptions(mode=CommentsMode.NONE), **kw)
    finished, failed, cancelled = [], [], []
    worker.finished.connect(finished.append)
    worker.failed.connect(failed.append)
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.run()
    return finished, failed, cancelled


def test_successful_run_emits_normalized_issues(qapp, cloud_issue, cloud_cfg):
    finished, failed, _ = _run(FakeClient([cloud_issue]), cloud_cfg)
    assert not failed
    assert len(finished) == 1
    result = finished[0]
    assert isinstance(result.issues[0], NormalizedIssue)
    assert result.issues[0].key == "CLOUD-1"
    assert result.issues[0].comments == []


def test_comment_load_failure_collects_warning_not_fail(qapp, cloud_issue, cloud_cfg):
    client = FakeClient([cloud_issue], comment_error=RuntimeError("boom"))
    finished, failed, _ = _run(client, cloud_cfg,
                               comments=CommentsOptions(mode=CommentsMode.ALL))
    assert not failed
    result = finished[0]
    assert result.issues[0].comments == []
    assert len(result.warnings) == 1
    assert result.warnings[0].key == "CLOUD-1"
    assert "boom" in result.warnings[0].message


def test_comment_failure_can_fail_whole_run_when_configured(qapp, cloud_issue, cloud_cfg):
    client = FakeClient([cloud_issue], comment_error=RuntimeError("boom"))
    finished, failed, _ = _run(client, cloud_cfg,
                               comments=CommentsOptions(mode=CommentsMode.ALL),
                               fail_on_comment_error=True)
    assert not finished
    assert failed and "boom" in str(failed[0])   # failed now carries the exception


def test_failed_search_emits_failed_signal(qapp, cloud_cfg):
    client = FakeClient([], search_error=RuntimeError("search exploded"))
    finished, failed, _ = _run(client, cloud_cfg)
    assert not finished
    assert [str(e) for e in failed] == ["search exploded"]


def test_cancelled_run_emits_cancelled_signal(qapp, cloud_cfg):
    client = FakeClient([], search_error=CancelledError("stopped"))
    finished, failed, cancelled = _run(client, cloud_cfg)
    assert not finished and not failed
    assert cancelled == [True]


# --------------------------------------------------------------------------- #
# CSV parse worker
# --------------------------------------------------------------------------- #
def test_csv_parse_worker_emits_parsed(qapp, tmp_path):
    from issue_deck.ui.workers import CsvParseWorker
    f = tmp_path / "s.csv"
    f.write_text("key,summary\nK-1,hi\nK-2,yo", encoding="utf-8")
    worker = CsvParseWorker(str(f))
    done, failed = [], []
    worker.finished.connect(done.append)
    worker.failed.connect(failed.append)
    worker.run()
    assert not failed
    assert done[0].row_count == 2


def test_csv_parse_worker_cancel_emits_cancelled(qapp, tmp_path):
    from issue_deck.ui.workers import CsvParseWorker
    f = tmp_path / "big.csv"
    f.write_text("key,summary\n" + "\n".join(f"K-{i},r{i}" for i in range(5000)),
                 encoding="utf-8")
    worker = CsvParseWorker(str(f))
    cancelled = []
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.cancel()          # pre-cancel: the parse aborts at the first check
    worker.run()
    assert cancelled == [True]
