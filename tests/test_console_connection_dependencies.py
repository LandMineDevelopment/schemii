"""Deletion must not erase active work or unresolved SQL transactions."""

import pytest
from psycopg.errors import LockNotAvailable

from schemii.common.connections.store import ConnectionInUseError
from schemii.schemii.console.connection_dependencies import PostgresConsoleConnectionDependencies


class Cursor:
    def __init__(self, transaction_statuses=(), execution_statuses=()):
        self.results = iter((transaction_statuses, execution_statuses))
        self.commands = []

    def execute(self, sql, parameters):
        self.commands.append((sql, parameters))

    def fetchall(self):
        return [{"status": status} for status in next(self.results)]


@pytest.mark.parametrize("kind,status", [
    ("transaction", "open"), ("transaction", "failed"), ("transaction", "uncertain"),
    ("execution", "reserved"), ("execution", "running"),
])
def test_live_or_unresolved_work_blocks_deletion_without_pruning(kind, status):
    cursor = Cursor([status] if kind == "transaction" else [], [status] if kind == "execution" else [])
    with pytest.raises(ConnectionInUseError) as error:
        PostgresConsoleConnectionDependencies.guard_connection_mutation(cursor, "credential-owner", "profile", "delete", frozenset())
    assert error.value.dependencies == {"console": 1}
    assert not any("DELETE" in sql for sql, _ in cursor.commands)


def test_completed_receipts_are_pruned_only_for_the_selected_credential_identity():
    cursor = Cursor(["committed", "rolled_back", "expired"], ["succeeded", "failed", "cancelled"])
    PostgresConsoleConnectionDependencies.guard_connection_mutation(cursor, "credential-owner", "profile", "delete", frozenset())
    deletes = [(sql, parameters) for sql, parameters in cursor.commands if "DELETE" in sql]
    assert len(deletes) == 2
    assert all("connection_owner_id = %s AND connection_id = %s" in sql for sql, _ in deletes)
    assert all(parameters == ("credential-owner", "profile") for _, parameters in deletes)
    assert all("query_history" not in sql and "saved_queries" not in sql for sql, _ in cursor.commands)


def test_profile_update_does_not_prune_receipts():
    cursor = Cursor()
    PostgresConsoleConnectionDependencies.guard_connection_mutation(cursor, "owner", "profile", "update", frozenset({"password"}))
    assert cursor.commands == []


@pytest.mark.parametrize("busy_table", ["console_transactions", "console_executions"])
def test_receipt_lock_contention_is_a_conflict_without_pruning(busy_table):
    class BusyCursor(Cursor):
        def execute(self, sql, parameters):
            super().execute(sql, parameters)
            if busy_table in sql:
                raise LockNotAvailable("another admission holds this receipt")
    cursor = BusyCursor()
    with pytest.raises(ConnectionInUseError):
        PostgresConsoleConnectionDependencies.guard_connection_mutation(cursor, "owner", "profile", "delete", frozenset())
    assert not any("DELETE" in sql for sql, _ in cursor.commands)
