"""Keep live console work safe while deleting unused PostgreSQL profiles."""

from psycopg.errors import LockNotAvailable

from schemii.common.connections.dependencies import ConnectionDependentResource
from schemii.common.connections.store import ConnectionInUseError


class PostgresConsoleConnectionDependencies:
    dependency_name = "console"

    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    def dependencies_for_connection(self, owner_id, connection_id):
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, revision, 'execution' AS kind
                    FROM schemii.console_executions
                    WHERE connection_owner_id = %s AND connection_id = %s
                      AND status IN ('reserved', 'running')
                    UNION ALL
                    SELECT id, revision, 'transaction' AS kind
                    FROM schemii.console_transactions
                    WHERE connection_owner_id = %s AND connection_id = %s
                      AND status IN ('open', 'failed', 'uncertain')
                    """,
                    (owner_id, connection_id, owner_id, connection_id),
                )
                return tuple(ConnectionDependentResource(
                    provider=self.dependency_name, kind=row["kind"],
                    resource_id=row["id"], revision=row["revision"],
                    name=f"SQL {row['kind']}", deletion_blocked=True,
                    blocking_reason="Finish or resolve this SQL execution or transaction before deleting its connection.",
                ) for row in cursor.fetchall())

    def count_for_connection(self, owner_id, connection_id):
        return len(self.dependencies_for_connection(owner_id, connection_id))

    @staticmethod
    def guard_connection_mutation(cursor, owner_id, connection_id, operation, changed_fields):
        if operation != "delete":
            return
        # The connection repository holds FOR UPDATE on the profile, fencing new
        # receipt inserts through their FK. Admission can hold a transaction row
        # before taking that FK lock, so never wait for its receipt rows while
        # holding the profile. A busy receipt is a lifecycle conflict, not an
        # invitation to deadlock or retry deletion silently.
        try:
            cursor.execute(
                """SELECT status FROM schemii.console_transactions
                   WHERE connection_owner_id = %s AND connection_id = %s
                   ORDER BY id FOR UPDATE NOWAIT""", (owner_id, connection_id),
            )
            transactions = cursor.fetchall()
            cursor.execute(
                """SELECT status FROM schemii.console_executions
                   WHERE connection_owner_id = %s AND connection_id = %s
                   ORDER BY id FOR UPDATE NOWAIT""", (owner_id, connection_id),
            )
            executions = cursor.fetchall()
        except LockNotAvailable as error:
            raise ConnectionInUseError({PostgresConsoleConnectionDependencies.dependency_name: 1}) from error
        active = sum(row["status"] in {"open", "failed", "uncertain"} for row in transactions)
        active += sum(row["status"] in {"reserved", "running"} for row in executions)
        if active:
            raise ConnectionInUseError({PostgresConsoleConnectionDependencies.dependency_name: active})
        # These are operational receipts, not saved queries or query history.
        # Delete by credential owner: a shared report receipt belongs to its viewer.
        cursor.execute(
            """DELETE FROM schemii.console_executions
               WHERE connection_owner_id = %s AND connection_id = %s
                 AND status NOT IN ('reserved', 'running')""", (owner_id, connection_id),
        )
        cursor.execute(
            """DELETE FROM schemii.console_transactions
               WHERE connection_owner_id = %s AND connection_id = %s
                 AND status NOT IN ('open', 'failed', 'uncertain')""", (owner_id, connection_id),
        )
