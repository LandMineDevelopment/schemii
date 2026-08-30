import sys
import tempfile
import threading
import unittest
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import schemii.postgres_console as console_module
from schemii.postgres_common import PostgresServiceError
from schemii.postgres_console import ConsolePolicy
from schemii.postgres_service import PostgresService
from tests.test_postgres_console import Connection, ConsoleAuthority, PROFILE


class ConsoleResultPaginationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.connections = []
        self.now = [1000.0]

        def connect(**kwargs):
            connection = Connection()
            self.connections.append(connection)
            return connection

        self.service = PostgresService(self.directory.name, connect_factory=connect, clock=lambda: self.now[0])
        self.service.save_profile("local", PROFILE)
        self.service.set_metadata_store(ConsoleAuthority())
        self.fingerprint = self.service.profile_context_fingerprint("local")

    def tearDown(self):
        self.service.close()
        self.directory.cleanup()

    def request(self, sql, mode="managed_read", console_id=None):
        return {
            "executionId": str(uuid4()), "consoleId": console_id or str(uuid4()),
            "database": "demo", "namespace": "public", "sql": sql, "mode": mode,
            "settingsRevision": None, "profileFingerprint": self.fingerprint,
        }

    def page(self, statement, request, *, binding="binding", cursor=None):
        return self.service.console_result_page(
            "local", request["executionId"], statement["resultId"], request["consoleId"],
            "demo", "public", statement["statementIndex"], statement["resultIndex"],
            cursor or statement["nextCursor"], binding, "server",
        )

    def test_managed_read_pages_original_cursor_to_exhaustion_without_rerun(self):
        request = self.request("SELECT paged")
        connection = Connection({"SELECT paged": (["value"], [(index,) for index in range(1001)], "SELECT 1001", 1001)})
        self.service._connect_factory = lambda **kwargs: connection

        result = self.service.execute_console("local", request, "binding", "server")
        first = result["statements"][0]
        self.assertEqual((first["returnedRows"], first["hasMore"], result["outcome"]), (500, True, "transaction_open"))
        self.assertEqual(first["snapshotRetention"], "managed_read_transaction")
        self.assertFalse(connection.closed)
        self.assertEqual(sum(sql == "SELECT paged" for sql, _ in connection.executed), 1)

        first_cursor = first["nextCursor"]
        second = self.page(first, request)
        self.assertEqual((second["rows"][0], second["rows"][-1], second["hasMore"]), ([500], [999], True))
        with self.assertRaises(PostgresServiceError) as stale:
            self.page(first, request, cursor=first_cursor)
        self.assertEqual(stale.exception.code, "result_cursor_stale")
        third = self.page({**first, "nextCursor": second["nextCursor"]}, request)
        self.assertEqual((third["rows"], third["closureEvents"]), ([[1000]], ["exhausted"]))
        self.assertEqual(sum(sql == "SELECT paged" for sql, _ in connection.executed), 1)
        self.assertTrue(connection.closed)
        self.assertGreaterEqual(connection.rollbacks, 1)

    def test_result_ownership_close_and_expiry_are_isolated_and_terminal(self):
        request = self.request("SELECT owned")
        connection = Connection({"SELECT owned": (["value"], [(index,) for index in range(501)], "SELECT 501", 501)})
        self.service._connect_factory = lambda **kwargs: connection
        statement = self.service.execute_console("local", request, "binding", "server")["statements"][0]

        with self.assertRaises(PostgresServiceError) as hidden:
            self.page(statement, request, binding="another-session")
        self.assertEqual(hidden.exception.code, "result_not_found")
        for execution_id, console_id, namespace in (
            (str(uuid4()), request["consoleId"], "public"),
            (request["executionId"], str(uuid4()), "public"),
            (request["executionId"], request["consoleId"], "other"),
        ):
            with self.subTest(execution_id=execution_id, console_id=console_id, namespace=namespace):
                with self.assertRaises(PostgresServiceError) as isolated:
                    self.service.console_result_page(
                        "local", execution_id, statement["resultId"], console_id, "demo", namespace, 0, 0,
                        statement["nextCursor"], "binding", "server",
                    )
                self.assertEqual(isolated.exception.code, "result_not_found")
        closed = self.service.close_console_result(
            "local", request["executionId"], statement["resultId"], request["consoleId"],
            "demo", "public", 0, 0, "binding", "server",
        )
        self.assertEqual(closed["closureEvents"], ["closed"])
        with self.assertRaises(PostgresServiceError) as terminal:
            self.page(statement, request)
        self.assertEqual(terminal.exception.code, "result_closed")

        expiring_request = self.request("SELECT expiring")
        expiring_connection = Connection({"SELECT expiring": (["value"], [(index,) for index in range(501)], "SELECT 501", 501)})
        self.service._connect_factory = lambda **kwargs: expiring_connection
        expiring = self.service.execute_console("local", expiring_request, "binding", "server")["statements"][0]
        self.now[0] += 301
        with self.assertRaises(PostgresServiceError) as expired:
            self.page(expiring, expiring_request)
        self.assertEqual(expired.exception.code, "result_expired")
        self.assertTrue(expiring_connection.closed)

        cancelled_request = self.request("SELECT cancelled")
        cancelled_connection = Connection({"SELECT cancelled": (["value"], [(index,) for index in range(501)], "SELECT 501", 501)})
        self.service._connect_factory = lambda **kwargs: cancelled_connection
        self.service.execute_console("local", cancelled_request, "binding", "server")
        cancelled = self.service.cancel_console("local", cancelled_request["executionId"], "binding", "server")
        self.assertEqual(cancelled["closedResults"][0]["event"], "cancelled")
        self.assertTrue(cancelled_connection.closed)

    def test_explicit_result_serializes_and_commit_closes_it_deterministically(self):
        console_id = str(uuid4())
        transaction_id = str(uuid4())
        connection = Connection({"SELECT explicit": (["value"], [(index,) for index in range(501)], "SELECT 501", 501)})
        self.service._connect_factory = lambda **kwargs: connection
        self.service.create_console_transaction("local", {
            "transactionId": transaction_id, "consoleId": console_id, "database": "demo", "namespace": "public",
            "settingsRevision": None, "profileFingerprint": self.fingerprint,
        }, "binding", "server", ConsolePolicy(allow_write=True))
        execution_id = str(uuid4())
        result = self.service.execute_console_transaction(
            "local", transaction_id, {"executionId": execution_id, "sql": "SELECT explicit"}, "binding", "server",
        )
        statement = result["statements"][0]
        self.assertTrue(statement["transactionRetention"])
        transaction = self.service._console.transactions._entries[transaction_id]
        locked = threading.Event()
        release = threading.Event()
        def hold_transaction():
            with transaction["lock"]:
                locked.set()
                release.wait(2)
        holder = threading.Thread(target=hold_transaction)
        holder.start()
        self.assertTrue(locked.wait(1))
        try:
            with self.assertRaises(PostgresServiceError) as busy:
                self.service.console_result_page(
                    "local", execution_id, statement["resultId"], console_id, "demo", "public", 0, 0,
                    statement["nextCursor"], "binding", "server",
                )
            self.assertEqual(busy.exception.code, "result_busy")
        finally:
            release.set()
            holder.join(2)
        finished = self.service.finish_console_transaction(
            "local", transaction_id, {"executionId": str(uuid4())}, "binding", "server", "commit",
        )
        self.assertEqual(finished["resultClosurePolicy"], "deterministic_before_completion")
        self.assertEqual(finished["closedResults"][0]["event"], "transaction_commit")
        self.assertEqual(connection.commits, 1)

    def test_bulk_close_waits_for_result_operation_and_then_closes_once(self):
        owner = {
            "applicationId": "schemii", "sessionBinding": "binding", "serverId": "server",
            "profileId": "local", "profileFingerprint": self.fingerprint, "database": "demo",
            "namespace": "public", "consoleId": str(uuid4()), "executionId": str(uuid4()),
            "statementIndex": 0, "resultIndex": 0,
        }
        operation_lock = threading.RLock()
        entry = self.service._console.results.add(
            owner=owner, columns=[{"name": "value"}], page_size=1,
            retention="server_spool", rows=[[1], [2]], operation_lock=operation_lock,
        )
        operation_lock.acquire()
        closed = []
        worker = threading.Thread(target=lambda: closed.extend(
            self.service._console.results.close_matching({"executionId": owner["executionId"]}, "cancelled")
        ))
        worker.start()
        self.assertTrue(worker.is_alive())
        self.assertIn(entry["resultId"], self.service._console.results._entries)
        operation_lock.release()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(closed[0]["event"], "cancelled")
        self.assertNotIn(entry["resultId"], self.service._console.results._entries)

    def test_shutdown_waits_for_result_operation_before_closing_resources(self):
        owner = {
            "applicationId": "schemii", "sessionBinding": "binding", "serverId": "server",
            "profileId": "local", "profileFingerprint": self.fingerprint, "database": "demo",
            "namespace": "public", "consoleId": str(uuid4()), "executionId": str(uuid4()),
            "statementIndex": 0, "resultIndex": 0,
        }
        operation_lock = threading.RLock()
        entry = self.service._console.results.add(
            owner=owner, columns=[{"name": "value"}], page_size=1,
            retention="server_spool", rows=[[1], [2]], operation_lock=operation_lock,
        )
        operation_lock.acquire()
        worker = threading.Thread(target=self.service._console.results.close)
        worker.start()
        self.assertTrue(worker.is_alive())
        self.assertIn(entry["resultId"], self.service._console.results._entries)
        operation_lock.release()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertNotIn(entry["resultId"], self.service._console.results._entries)
        with self.assertRaises(PostgresServiceError) as stopping:
            self.service._console.results.add(
                owner=owner, columns=[{"name": "value"}], page_size=1,
                retention="server_spool", rows=[[3]],
            )
        self.assertEqual(stopping.exception.code, "console_shutting_down")

    def test_transaction_capacity_idle_absolute_expiry_tombstones_and_shutdown(self):
        services = []
        connections = []
        now = [1000.0]
        def connect(**kwargs):
            connection = Connection()
            connections.append(connection)
            return connection
        service = PostgresService(
            self.directory.name, connect_factory=connect, clock=lambda: now[0],
            console_transaction_maximum=1, console_transaction_idle_seconds=5,
            console_transaction_lifetime_seconds=8,
        )
        services.append(service)
        service.set_metadata_store(ConsoleAuthority())
        transaction_id = str(uuid4())
        payload = {"transactionId": transaction_id, "consoleId": str(uuid4()), "database": "demo",
                   "namespace": "public", "settingsRevision": None, "profileFingerprint": self.fingerprint}
        created = service.create_console_transaction("local", payload, "binding", "server", ConsolePolicy(allow_write=True))
        self.assertEqual(created["limits"]["policy"], "connection_lifecycle")
        with self.assertRaises(PostgresServiceError) as capacity:
            service.create_console_transaction(
                "local", {**payload, "transactionId": str(uuid4()), "consoleId": str(uuid4())},
                "binding", "server", ConsolePolicy(allow_write=True),
            )
        self.assertEqual(capacity.exception.code, "transaction_capacity_exhausted")
        self.assertFalse(connections[0].closed)
        now[0] += 5
        with self.assertRaises(PostgresServiceError) as expired:
            service.console_transaction_status("local", transaction_id, "binding", "server")
        self.assertEqual(expired.exception.code, "transaction_expired")
        self.assertTrue(connections[0].closed)
        self.assertGreaterEqual(connections[0].rollbacks, 1)

        absolute_id = str(uuid4())
        service.create_console_transaction(
            "local", {**payload, "transactionId": absolute_id, "consoleId": str(uuid4())},
            "binding", "server", ConsolePolicy(allow_write=True),
        )
        now[0] += 4
        service.console_transaction_status("local", absolute_id, "binding", "server")
        now[0] += 4
        with self.assertRaises(PostgresServiceError) as absolute:
            service.console_transaction_status("local", absolute_id, "binding", "server")
        self.assertEqual(absolute.exception.code, "transaction_expired")

        shutdown_id = str(uuid4())
        service.create_console_transaction(
            "local", {**payload, "transactionId": shutdown_id, "consoleId": str(uuid4())},
            "binding", "server", ConsolePolicy(allow_write=True),
        )
        shutdown_connection = connections[-1]
        service.close()
        services.remove(service)
        self.assertTrue(shutdown_connection.closed)
        self.assertGreaterEqual(shutdown_connection.rollbacks, 1)
        with self.assertRaises(PostgresServiceError) as closed:
            service.console_transaction_status("local", shutdown_id, "binding", "server")
        self.assertEqual(closed.exception.code, "transaction_closed")
        for item in services:
            item.close()

    def test_managed_write_commits_when_spool_limit_truncates_display(self):
        prior_rows = console_module.MAX_SPOOL_ROWS
        prior_bytes = console_module.MAX_SPOOL_BYTES
        console_module.MAX_SPOOL_ROWS = 3
        console_module.MAX_SPOOL_BYTES = 1024
        try:
            request = self.request("UPDATE values RETURNING value", mode="managed")
            connection = Connection({
                "UPDATE values RETURNING value": (["value"], [(index,) for index in range(5)], "UPDATE 5", 5),
            })
            self.service._connect_factory = lambda **kwargs: connection
            result = self.service.execute_console(
                "local", request, "binding", "server", ConsolePolicy(allow_write=True),
            )
        finally:
            console_module.MAX_SPOOL_ROWS = prior_rows
            console_module.MAX_SPOOL_BYTES = prior_bytes
        statement = result["statements"][0]
        self.assertTrue(result["committed"])
        self.assertEqual(result["outcome"], "committed")
        self.assertEqual(connection.commits, 1)
        self.assertTrue(statement["truncated"])
        self.assertEqual(statement["truncationEvents"][-1]["code"], "result_spool_limit")
        self.assertFalse(statement["hasMore"])

        autocommit_request = self.request("UPDATE auto RETURNING value", mode="autocommit")
        autocommit_connection = Connection({
            "UPDATE auto RETURNING value": (["value"], [(index,) for index in range(5)], "UPDATE 5", 5),
        })
        self.service._connect_factory = lambda **kwargs: autocommit_connection
        console_module.MAX_SPOOL_ROWS = 3
        try:
            autocommit = self.service.execute_console(
                "local", autocommit_request, "binding", "server", ConsolePolicy(allow_write=True),
            )
        finally:
            console_module.MAX_SPOOL_ROWS = prior_rows
        self.assertEqual((autocommit["outcome"], autocommit["committed"]), ("committed", True))
        self.assertTrue(autocommit_connection.autocommit)
        self.assertEqual(autocommit_connection.commits, 0)
        self.assertEqual(autocommit["statements"][0]["truncationEvents"][-1]["code"], "result_spool_limit")

    def test_shutdown_closes_retained_snapshot(self):
        request = self.request("SELECT shutdown")
        connection = Connection({"SELECT shutdown": (["value"], [(index,) for index in range(501)], "SELECT 501", 501)})
        self.service._connect_factory = lambda **kwargs: connection
        self.service.execute_console("local", request, "binding", "server")
        self.assertFalse(connection.closed)
        self.service.close()
        self.assertTrue(connection.closed)

    def test_retention_exhaustion_is_an_application_limit_and_does_not_rerun(self):
        request = self.request("SELECT capacity")
        connection = Connection({"SELECT capacity": (["value"], [(index,) for index in range(501)], "SELECT 501", 501)})
        self.service._connect_factory = lambda **kwargs: connection
        self.service._console.results.maximum_active = 0
        with self.assertRaises(PostgresServiceError) as caught:
            self.service.execute_console("local", request, "binding", "server")
        self.assertEqual((caught.exception.status, caught.exception.code), (429, "console_result_capacity_exhausted"))
        self.assertEqual(caught.exception.details["limitSource"], "application")
        self.assertEqual(sum(sql == "SELECT capacity" for sql, _ in connection.executed), 1)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
