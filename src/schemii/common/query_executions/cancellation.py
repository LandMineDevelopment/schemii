"""Turn-scoped cancellation of in-use PostgreSQL connections, shared by products.

Scopes retain no query data and do not cancel by backend PID: a retained result
connection is registered only while executing/fetching for this exact turn.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import logging
import threading
from collections.abc import Callable, Iterator
from typing import Any

_current: ContextVar["_Scope | None"] = ContextVar("query_cancellation", default=None)
_log = logging.getLogger(__name__)


class _Registration:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.lock = threading.Lock()

    def cancel(self) -> None:
        # Unregistration waits for an already-started signal before the same
        # connection can be used by another turn. Never hold the registry lock
        # during network I/O.
        with self.lock:
            if self.connection is None:
                return
            try:
                cancel_safe = getattr(self.connection, "cancel_safe", None)
                if callable(cancel_safe):
                    cancel_safe(timeout=1.0)
                else:
                    self.connection.cancel()
            except Exception:
                _log.warning("PostgreSQL query cancellation signal failed")

    def detach(self) -> None:
        with self.lock:
            self.connection = None


class _Scope:
    def __init__(self, owner: str, chat_id: str, turn_id: str, authorized: Callable[[], bool]):
        self.key = (owner, chat_id, turn_id)
        self.authorized = authorized
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.registrations: set[_Registration] = set()

    def check(self) -> None:
        if self.stopped.is_set() or not self.authorized():
            # Import lazily: the PostgreSQL package exposes gateway classes,
            # which themselves consume this product-neutral scope.
            from schemii.common.postgres.errors import PostgresConsoleCancelledError

            raise PostgresConsoleCancelledError()

    def cancel(self) -> None:
        self.stopped.set()
        with self.lock:
            registrations = tuple(self.registrations)
        for registration in registrations:
            registration.cancel()


class QueryCancellationRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scopes: set[_Scope] = set()

    @contextmanager
    def scope(
        self, owner: str, chat_id: str, turn_id: str,
        is_authorized: Callable[[], bool] = lambda: True,
    ) -> Iterator[None]:
        scope = _Scope(owner, chat_id, turn_id, is_authorized)
        with self._lock:
            self._scopes.add(scope)
        token = _current.set(scope)
        try:
            scope.check()
            yield
        finally:
            _current.reset(token)
            with self._lock:
                self._scopes.discard(scope)

    def cancel(self, owner: str, chat_id: str, turn_id: str | None = None) -> None:
        with self._lock:
            scopes = tuple(scope for scope in self._scopes
                           if scope.key[:2] == (owner, chat_id)
                           and (turn_id is None or scope.key[2] == turn_id))
        for scope in scopes:
            scope.cancel()


def check_query_authority() -> None:
    """Fence subsequent statements and commits after a turn was stopped."""
    scope = _current.get()
    if scope is not None:
        scope.check()


@contextmanager
def cancellable_connection(connection: Any) -> Iterator[None]:
    scope = _current.get()
    if scope is None:
        yield
        return
    scope.check()
    registration = _Registration(connection)
    with scope.lock:
        scope.registrations.add(registration)
    try:
        # Stop can precede registration (or connection acquisition).
        scope.check()
        try:
            yield
        except Exception:
            scope.check()
            raise
        scope.check()
    finally:
        registration.detach()
        with scope.lock:
            scope.registrations.discard(registration)
