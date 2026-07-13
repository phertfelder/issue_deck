"""Cooperative cancellation primitives (Qt-free).

A :class:`CancelToken` is a one-way flag shared between a UI thread (which calls
:meth:`CancelToken.cancel`) and a worker (which polls :meth:`raise_if_cancelled`
at safe points — page boundaries, per-issue, mid-backoff). Kept dependency-free
so the client, services and CSV parser can all cooperate without importing Qt.
"""

from __future__ import annotations


class CancelledError(Exception):
    """Raised by cooperative code when a :class:`CancelToken` has been tripped.

    Distinct from a failure: callers treat it as "the user stopped this", not an
    error to report.
    """


class CancelToken:
    """A thread-safe-enough one-way cancellation flag.

    Setting a plain bool is atomic under CPython's GIL, which is all the
    cross-thread visibility this cooperative model needs — the worker only ever
    reads it and the UI only ever sets it.
    """

    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise CancelledError("Operation cancelled.")
