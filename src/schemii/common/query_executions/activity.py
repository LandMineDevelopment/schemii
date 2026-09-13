"""Request-local progress reporting without storing SQL or growing telemetry history."""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable

_reporter: ContextVar[Callable[[int, bool], None] | None] = ContextVar("query_progress", default=None)


@contextmanager
def report_progress(callback):
    token = _reporter.set(callback)
    try:
        yield
    finally:
        _reporter.reset(token)


def statement_progress(index: int, completed: bool = False) -> None:
    callback = _reporter.get()
    if callback is not None:
        callback(index, completed)
