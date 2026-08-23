import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_common import PostgresServiceError, ValidationError, postgres_error_details, postgres_error_diagnostic
from schemii.postgres_console import ConsoleExecutionRegistry, ConsolePolicy, split_console_statements
from schemii.postgres_service import PostgresService
from schemii.result_limits import ResultLimiter, ResultLimits


PROFILE = {
    "name": "Local", "host": "localhost", "port": 5432, "dbname": "demo",
    "user": "developer", "password": "secret", "sslmode": "prefer", "timeout": 5,
}


class ConsoleAuthority:
    def __init__(self, *, enabled=False, revision=1):
        self.settings = {
            "application": "schemii", "revision": revision,
            "writeIntent": "enabled" if enabled else "disabled",
            "defaultMode": "managed", "statementLimit": 20, "rowPageSize": 500,
        }
        self.receipts = {}
        self.receipt_history = []

    def get_console_settings(self, application):
        return {**self.settings, "application": application}

    def update_console_settings(self, application, expected_revision, settings):
        if expected_revision != self.settings["revision"]:
            raise PostgresServiceError(409, "console_settings_conflict", "stale settings")
        self.settings = {**self.settings, **settings, "application": application,
                         "revision": expected_revision + 1}
        return dict(self.settings)

    def get_console_execution_receipt(self, execution_id, *owner):
        receipt = self.receipts.get(execution_id)
        if receipt is None:
            raise PostgresServiceError(404, "execution_not_found", "missing")
        return receipt

    def put_console_execution_receipt(self, receipt):
        public = {key: receipt[key] for key in (
            "executionId", "mode", "settingsRevision", "state", "outcome",
            "completedStatementIndexes", "errorCode", "postgresEvidence", "reconciliationEvidence",
        )}
        existing = self.receipts.get(receipt["executionId"])
        if receipt["state"] == "reserved":
            if existing is not None:
                raise PostgresServiceError(409, "execution_conflict", "reserved")
        elif existing is None or existing["state"] not in {"reserved", "running"}:
            raise PostgresServiceError(409, "execution_conflict", "invalid transition")
        self.receipts[receipt["executionId"]] = public
        self.receipt_history.append(dict(public))
        return public


class Column:
    def __init__(self, name):
        self.name = name


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self.rows = []
        self.statusmessage = ""
        self.rowcount = -1
        self.fetch_offset = 0

    def execute(self, sql, params=()):
        self.fetch_offset = 0
        self.connection.executed.append((sql, params))
        if sql in self.connection.failures:
            self.connection.info.transaction_status = type("Status", (), {"name": "INERROR"})()
            raise self.connection.failures[sql]
        if sql.startswith("ROLLBACK TO"):
            self.connection.info.transaction_status = type("Status", (), {"name": "INTRANS"})()
        if self.connection.block_on == sql:
            self.connection.started.set()
            self.connection.release.wait(2)
            if self.connection.cancelled:
                raise CancelledError()
        if sql == "SELECT current_database() AS database":
            self.rows = [{"database": self.connection.database}]
            self.description = [Column("database")]
            self.statusmessage = "SELECT 1"
        elif "SELECT EXISTS" in sql:
            self.rows = [{"exists": self.connection.namespace_exists}]
            self.description = [Column("exists")]
            self.statusmessage = "SELECT 1"
        elif "pg_current_xact_id" in sql:
            self.rows = [{"xid": "123", "database_oid": "456"}]
            self.description = [Column("xid"), Column("database_oid")]
            self.statusmessage = "SELECT 1"
        elif sql in self.connection.results:
            columns, self.rows, self.statusmessage, self.rowcount = self.connection.results[sql]
            self.description = [Column(name) for name in columns] if columns is not None else None
            for notice in self.connection.notices.get(sql, []):
                for handler in self.connection.notice_handlers:
                    handler(type("Notice", (), {"message_primary": notice})())
        else:
            self.rows = []
            self.description = None
            self.statusmessage = "SET"
            self.rowcount = -1

    def fetchall(self):
        return self.rows

    def fetchmany(self, size):
        rows = self.rows[self.fetch_offset:self.fetch_offset + size]
        self.fetch_offset += len(rows)
        return rows

    def close(self):
        pass


class Diagnostic:
    message_primary = "canceling statement due to user request"


class CancelledError(Exception):
    sqlstate = "57014"
    diag = Diagnostic()


class UnsupportedTransactionError(Exception):
    sqlstate = "25001"
    diag = None


class SqlDiagnostic:
    message_primary = "column missing_column does not exist"
    message_detail = "The referenced output column is unavailable."
    message_hint = "Check the selected relation alias."
    statement_position = "18"


class SqlError(Exception):
    sqlstate = "42703"
    diag = SqlDiagnostic()


class Connection:
    def __init__(self, results=None, *, database="demo", namespace_exists=True, block_on=None, notices=None, commit_error=None, failures=None):
        self.results = results or {}
        self.database = database
        self.namespace_exists = namespace_exists
        self.block_on = block_on
        self.notices = notices or {}
        self.notice_handlers = []
        self.executed = []
        self.rollbacks = 0
        self.commits = 0
        self.commit_error = commit_error
        self.failures = failures or {}
        self.autocommit = False
        self.info = type("Info", (), {})()
        self.info.transaction_status = type("Status", (), {"name": "INTRANS"})()
        self.closed = False
        self.cancelled = False
        self.started = threading.Event()
        self.release = threading.Event()

    def cursor(self):
        return Cursor(self)

    def cancel(self):
        self.cancelled = True
        self.release.set()

    def add_notice_handler(self, handler):
        self.notice_handlers.append(handler)

    def remove_notice_handler(self, handler):
        self.notice_handlers.remove(handler)

    def rollback(self):
        self.rollbacks += 1
        self.info.transaction_status = type("Status", (), {"name": "IDLE"})()

    def commit(self):
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error
        self.info.transaction_status = type("Status", (), {"name": "IDLE"})()

    def close(self):
        self.closed = True


class PostgresConsoleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.connection = Connection()
        self.service = PostgresService(self.directory.name, connect_factory=lambda **kwargs: self.connection)
        self.service.save_profile("local", PROFILE)
        self.authority = ConsoleAuthority()
        self.service.set_metadata_store(self.authority)
        settings = self.service.update_console_settings(1, {
            "writeIntent": "enabled", "defaultMode": "managed",
            "statementLimit": 20, "rowPageSize": 500,
        })
        self.settings_revision = settings["revision"]
        self.fingerprint = self.service.profile_context_fingerprint("local")
        self.human_policy = ConsolePolicy(allow_write=True, human_write_intent=True)

    def tearDown(self):
        self.service.close()
        self.directory.cleanup()

    def request(self, sql, **changes):
        request = {
            "executionId": str(uuid4()), "consoleId": str(uuid4()), "database": "demo",
            "namespace": "public", "sql": sql, "mode": "read", "settingsRevision": self.settings_revision,
            "profileFingerprint": self.fingerprint,
        }
        request.update(changes)
        return request

    def configure(self, service, *, enabled=True, revision=1):
        authority = ConsoleAuthority(enabled=False, revision=revision)
        service.set_metadata_store(authority)
        if enabled:
            service.update_console_settings(revision, {
                "writeIntent": "enabled", "defaultMode": "managed",
                "statementLimit": 20, "rowPageSize": 500,
            })
        return authority

    def test_scanner_splits_quotes_comments_and_dollar_quotes(self):
        statements = split_console_statements("SELECT ';'; -- ;\n SELECT $$;$$; /* ; */ SELECT 3")
        self.assertEqual(statements, ["SELECT ';'", "-- ;\n SELECT $$;$$", "/* ; */ SELECT 3"])
        with self.assertRaises(ValidationError):
            split_console_statements("SELECT 'unterminated")
        for sql in ("BEGIN; SELECT 1", "SET TRANSACTION READ WRITE", "SET LOCAL TRANSACTION READ ONLY", "ROLLBACK TO SAVEPOINT x"):
            with self.subTest(sql=sql), self.assertRaises(PostgresServiceError) as error:
                split_console_statements(sql)
            self.assertEqual(error.exception.code, "unsupported_transaction_control")
        self.assertEqual(len(split_console_statements(";".join("SELECT 1" for _ in range(100)))), 100)
        with self.assertRaises(PostgresServiceError) as error:
            split_console_statements(";".join("SELECT 1" for _ in range(101)))
        self.assertEqual(error.exception.code, "too_many_statements")

    def test_ai_console_policy_enforces_raw_statement_bound_before_connection(self):
        policy = ConsolePolicy(allow_write=True, statement_limit=1)
        with self.assertRaises(PostgresServiceError) as error:
            self.service.execute_console(
                "local", self.request("SELECT 1; SELECT 2", settingsRevision=None), "binding", "server", policy,
            )
        self.assertEqual(error.exception.code, "too_many_statements")
        self.assertEqual(self.connection.executed, [])

    def test_null_ai_timeout_installs_no_override_and_value_narrows_in_postgresql(self):
        self.connection.results = {"SELECT 1": (["value"], [(1,)], "SELECT 1", 1)}
        self.service.execute_console(
            "local", self.request("SELECT 1", settingsRevision=None), "binding", "server",
            ConsolePolicy(allow_write=True, operation_timeout_ms=None),
        )
        self.assertFalse(any("statement_timeout" in sql for sql, _ in self.connection.executed))

        self.connection = Connection(results={"SELECT 1": (["value"], [(1,)], "SELECT 1", 1)})
        self.service._connect_factory = lambda **kwargs: self.connection
        result = self.service.execute_console(
            "local", self.request("SELECT 1", settingsRevision=None), "binding", "server",
            ConsolePolicy(allow_write=True, operation_timeout_ms=2500),
        )
        timeout_calls = [(sql, params) for sql, params in self.connection.executed if "statement_timeout" in sql]
        self.assertEqual(timeout_calls[0][1], (2500, 2500, True))
        self.assertIn("current_setting('statement_timeout')", timeout_calls[0][0])
        self.assertEqual(result["limits"]["statementTimeoutSource"], "policy_narrowing")

    def test_executes_ordered_results_in_one_read_transaction_and_rolls_back(self):
        self.connection.results = {
            "SELECT 1 AS value": (["value"], [(1,)], "SELECT 1", 1),
            "UPDATE example SET value = value": (None, [], "UPDATE 2", 2),
        }
        result = self.service.execute_console(
            "local", self.request("SELECT 1 AS value; UPDATE example SET value = value"), "binding", "server",
        )
        self.assertEqual([entry["command"] for entry in result["statements"]], ["SELECT", "UPDATE"])
        self.assertEqual(result["statements"][0]["rows"], [[1]])
        self.assertEqual(result["statements"][1]["rowCount"], 2)
        self.assertFalse(result["committed"])
        self.assertEqual(self.connection.executed[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.assertIn(("SELECT pg_catalog.set_config('search_path', %s, true)", ('"public"',)), self.connection.executed)
        self.assertEqual(self.connection.rollbacks, 1)
        self.assertTrue(self.connection.closed)

    def test_verifies_target_before_user_sql(self):
        self.connection.database = "other"
        with self.assertRaises(PostgresServiceError) as error:
            self.service.execute_console("local", self.request("SELECT secret"), "binding", "server")
        self.assertEqual(error.exception.code, "database_changed")
        self.assertFalse(any(sql == "SELECT secret" for sql, _ in self.connection.executed))

    def test_retired_write_grants_cannot_authorize_a_human_write(self):
        disabled_settings = self.service.update_console_settings(self.settings_revision, {
            "writeIntent": "disabled", "defaultMode": "managed",
            "statementLimit": 20, "rowPageSize": 500,
        })
        for operation in (
            lambda: self.service.create_console_write_grant("local", {}, "binding", "server"),
            lambda: self.service.revoke_console_write_grant("local", str(uuid4()), "binding", "server"),
        ):
            with self.assertRaises(PostgresServiceError) as retired:
                operation()
            self.assertEqual((retired.exception.status, retired.exception.code), (410, "console_write_grants_retired"))
        with self.assertRaises(PostgresServiceError) as denied:
            self.service.execute_console(
                "local", self.request("UPDATE example SET value = 1", mode="managed",
                                      settingsRevision=disabled_settings["revision"]),
                "binding", "server", self.human_policy,
            )
        self.assertEqual(denied.exception.code, "console_write_intent_disabled")
        self.assertFalse(any(sql.startswith("UPDATE example") for sql, _ in self.connection.executed))

    def test_durable_settings_and_profile_fingerprint_bind_human_writes(self):
        stale_revision = self.request("UPDATE example SET value = 1", mode="managed",
                                      settingsRevision=self.settings_revision + 1)
        with self.assertRaises(PostgresServiceError) as stale:
            self.service.execute_console("local", stale_revision, "binding", "server", self.human_policy)
        self.assertEqual(stale.exception.code, "console_settings_changed")

        changed_target = self.request("UPDATE example SET value = 1", mode="managed", profileFingerprint="0" * 64)
        with self.assertRaises(PostgresServiceError) as changed:
            self.service.execute_console("local", changed_target, "binding", "server", self.human_policy)
        self.assertEqual(changed.exception.code, "console_target_changed")
        self.assertFalse(any(sql.startswith("UPDATE example") for sql, _ in self.connection.executed))

    def test_durable_write_intent_has_no_time_expiry_but_profile_changes_invalidate_target(self):
        now = [1000.0]
        service = PostgresService(self.directory.name, connect_factory=lambda **kwargs: self.connection, clock=lambda: now[0])
        self.configure(service)
        request = self.request("UPDATE x SET y = 1", mode="managed")
        for elapsed in (0, 365 * 24 * 60 * 60):
            now[0] = 1000 + elapsed
            service.execute_console("local", {**request, "executionId": str(uuid4())}, "binding", "server", self.human_policy)
        self.assertEqual(self.connection.commits, 2)
        service.save_profile("local", {**PROFILE, "user": "changed"})
        with self.assertRaises(PostgresServiceError) as changed:
            service.execute_console(
                "local", {**request, "executionId": str(uuid4())}, "binding", "server", self.human_policy,
            )
        self.assertEqual(changed.exception.code, "console_target_changed")
        service.close()

    def test_durable_human_write_commits_once_without_read_only(self):
        now = [1000.0]
        service = PostgresService(self.directory.name, connect_factory=lambda **kwargs: self.connection, clock=lambda: now[0])
        self.configure(service)
        console_id = str(uuid4())
        self.connection.executed.clear()
        self.connection.rollbacks = 0
        now[0] = 1299
        result = service.execute_console(
            "local", self.request("UPDATE example SET value = 1", consoleId=console_id, mode="write"),
            "binding", "server", self.human_policy,
        )
        self.assertTrue(result["committed"])
        self.assertEqual(result["mode"], "write")
        self.assertEqual((self.connection.commits, self.connection.rollbacks), (1, 0))
        self.assertNotIn("SET TRANSACTION READ ONLY", [sql for sql, _ in self.connection.executed])
        self.assertFalse(any("statement_timeout" in sql for sql, _ in self.connection.executed))
        self.assertEqual(result["limits"]["statementTimeoutSource"], "postgresql")
        now[0] = 1598
        service.execute_console(
            "local", self.request("UPDATE example SET value = 2", consoleId=console_id, mode="write"),
            "binding", "server", self.human_policy,
        )
        self.assertEqual(self.connection.commits, 2)
        service.close()

    def test_canonical_modes_aliases_autocommit_partial_success_and_terminal_no_replay(self):
        self.connection.results = {"UPDATE first": (None, [], "UPDATE 1", 1)}
        self.connection.failures["UPDATE second"] = SqlError()
        console_id = str(uuid4())
        request = self.request(
            "UPDATE first; UPDATE second", consoleId=console_id, mode="maintenance",
        )
        with self.assertRaises(PostgresServiceError) as caught:
            self.service.execute_console("local", request, "binding", "server", self.human_policy)
        self.assertEqual(caught.exception.details["completedStatementIndexes"], [0])
        self.assertTrue(caught.exception.details["priorStatementsCommitted"])
        self.assertEqual(caught.exception.details["outcome"], "partial_committed")
        self.assertTrue(self.connection.autocommit)
        status = self.service.console_execution_status(
            "local", request["executionId"], console_id, "demo", "public", "binding", "server",
        )
        self.assertEqual((status["mode"], status["outcome"]), ("autocommit", "partial_committed"))
        with self.assertRaises(PostgresServiceError) as replay:
            self.service.execute_console("local", request, "binding", "server", self.human_policy)
        self.assertEqual(replay.exception.code, "execution_conflict")

    def test_execution_id_is_durably_reserved_before_connect_and_rejected_after_restart_or_owner_change(self):
        connection_calls = []
        def connect(**kwargs):
            connection_calls.append(kwargs)
            return self.connection
        self.service._connect_factory = connect
        request = self.request("SELECT once")
        self.service.execute_console("local", request, "binding", "server")
        states = [item["state"] for item in self.authority.receipt_history if item["executionId"] == request["executionId"]]
        self.assertEqual(states, ["reserved", "running", "succeeded"])
        self.assertEqual(len(connection_calls), 1)

        restarted = PostgresService(self.directory.name, connect_factory=connect)
        restarted.set_metadata_store(self.authority)
        for binding, server in (("binding", "server"), ("new-session", "server"), ("binding", "new-server")):
            with self.subTest(binding=binding, server=server), self.assertRaises(PostgresServiceError) as replay:
                restarted.execute_console("local", request, binding, server)
            self.assertEqual(replay.exception.code, "execution_conflict")
        self.assertEqual(len(connection_calls), 1)
        restarted.close()

    def test_pre_dispatch_admission_failure_becomes_safe_terminal_evidence(self):
        request = self.request("UPDATE never_runs", mode="managed")
        self.service._console.registry._maximum_active = 0
        with self.assertRaises(PostgresServiceError) as caught:
            self.service.execute_console("local", request, "binding", "server", self.human_policy)
        self.assertEqual(caught.exception.code, "execution_busy")
        receipt = self.authority.receipts[request["executionId"]]
        self.assertEqual((receipt["state"], receipt["outcome"]), ("failed", "not_started"))
        self.assertEqual(receipt["reconciliationEvidence"], {"postgresDispatchStarted": False})
        self.assertFalse(any(sql == "UPDATE never_runs" for sql, _ in self.connection.executed))

    def test_autocommit_lost_statement_response_is_uncertain_without_replay(self):
        self.connection.failures["UPDATE uncertain"] = RuntimeError("connection lost")
        console_id = str(uuid4())
        request = self.request(
            "UPDATE uncertain", consoleId=console_id, mode="autocommit",
        )
        with self.assertRaises(PostgresServiceError) as caught:
            self.service.execute_console("local", request, "binding", "server", self.human_policy)
        self.assertEqual(caught.exception.details["outcome"], "uncertain")
        status = self.service.console_execution_status(
            "local", request["executionId"], console_id, "demo", "public", "binding", "server",
        )
        self.assertEqual((status["state"], status["outcome"]), ("uncertain", "uncertain"))

    def test_cancellation_immediately_before_managed_commit_rolls_back_without_commit(self):
        console_id = str(uuid4())
        checks = iter([False, True])
        original = self.service._console.registry.cancel_requested
        self.service._console.registry.cancel_requested = lambda execution_id: next(checks)
        try:
            with self.assertRaises(PostgresServiceError) as caught:
                self.service.execute_console(
                    "local", self.request("UPDATE one", consoleId=console_id, mode="managed"),
                    "binding", "server", self.human_policy,
                )
        finally:
            self.service._console.registry.cancel_requested = original
        self.assertEqual(caught.exception.code, "execution_cancelled")
        self.assertEqual(self.connection.commits, 0)
        self.assertEqual(self.connection.rollbacks, 1)

    def test_explicit_transaction_failed_state_savepoint_recovery_and_shutdown_rollback(self):
        console_id = str(uuid4())
        transaction_id = str(uuid4())
        created = self.service.create_console_transaction(
            "local", {"transactionId": transaction_id, "consoleId": console_id, "database": "demo",
                      "namespace": "public", "settingsRevision": self.settings_revision, "profileFingerprint": self.fingerprint},
            "binding", "server", self.human_policy,
        )
        self.assertEqual(created["transactionId"], transaction_id)
        self.service.execute_console_transaction(
            "local", transaction_id, {"executionId": str(uuid4()), "sql": "SAVEPOINT recover"}, "binding", "server",
        )
        self.connection.failures["SELECT broken"] = SqlError()
        with self.assertRaises(PostgresServiceError):
            self.service.execute_console_transaction(
                "local", transaction_id, {"executionId": str(uuid4()), "sql": "SELECT broken"}, "binding", "server",
            )
        self.assertEqual(self.service.console_transaction_status("local", transaction_id, "binding", "server")["state"], "failed")
        self.service.execute_console_transaction(
            "local", transaction_id, {"executionId": str(uuid4()), "sql": "ROLLBACK TO SAVEPOINT recover"}, "binding", "server",
        )
        self.assertEqual(self.service.console_transaction_status("local", transaction_id, "binding", "server")["state"], "in_transaction")
        before = self.connection.rollbacks
        self.service.close()
        self.assertGreater(self.connection.rollbacks, before)
        self.assertTrue(self.connection.closed)

    def test_explicit_transaction_is_owner_bound_busy_and_failed_commit_reports_rollback(self):
        console_id = str(uuid4())
        transaction_id = str(uuid4())
        self.service.create_console_transaction(
            "local", {"transactionId": transaction_id, "consoleId": console_id, "database": "demo",
                      "namespace": "public", "settingsRevision": self.settings_revision, "profileFingerprint": self.fingerprint},
            "binding", "server", self.human_policy,
        )
        with self.assertRaises(PostgresServiceError) as hidden:
            self.service.console_transaction_status("local", transaction_id, "other-binding", "server")
        self.assertEqual(hidden.exception.code, "transaction_not_found")
        entry = self.service._console.transactions._entries[transaction_id]
        locked = threading.Event()
        release = threading.Event()
        def hold_transaction():
            with entry["lock"]:
                locked.set()
                release.wait(2)
        holder = threading.Thread(target=hold_transaction)
        holder.start()
        self.assertTrue(locked.wait(1))
        try:
            with self.assertRaises(PostgresServiceError) as busy:
                self.service.execute_console_transaction(
                    "local", transaction_id, {"executionId": str(uuid4()), "sql": "SELECT 1"}, "binding", "server",
                )
        finally:
            release.set()
            holder.join(2)
        self.assertEqual(busy.exception.code, "transaction_busy")

        self.connection.info.transaction_status = type("Status", (), {"name": "INERROR"})()
        result = self.service.finish_console_transaction(
            "local", transaction_id, {"executionId": str(uuid4())}, "binding", "server", "commit",
        )
        self.assertEqual(result["outcome"], "rolled_back")

    def test_failed_write_commit_is_uncertain_and_execution_is_not_replayed(self):
        now = [1000.0]
        failure = RuntimeError("commit failed")
        connection = Connection(commit_error=failure)
        service = PostgresService(self.directory.name, connect_factory=lambda **kwargs: connection, clock=lambda: now[0])
        self.configure(service)
        console_id = str(uuid4())
        connection.rollbacks = 0
        now[0] = 1200
        request = self.request("UPDATE example SET value = 1", consoleId=console_id, mode="write")
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_console(
                "local", request, "binding", "server", self.human_policy,
            )
        self.assertEqual(error.exception.code, "execution_outcome_unknown")
        self.assertEqual((connection.commits, connection.rollbacks), (1, 1))
        connection.commit_error = None
        now[0] = 1300
        with self.assertRaises(PostgresServiceError) as replay:
            service.execute_console(
                "local", request, "binding", "server", self.human_policy,
            )
        self.assertEqual(replay.exception.code, "execution_conflict")
        result = service.execute_console(
            "local", self.request("UPDATE example SET value = 2", consoleId=console_id, mode="write"),
            "binding", "server", self.human_policy,
        )
        self.assertTrue(result["committed"])
        service.close()

    def test_ai_write_authorization_is_separate_from_disabled_human_intent(self):
        connection = Connection(commit_error=RuntimeError("commit connection lost"))
        service = PostgresService(self.directory.name, connect_factory=lambda **kwargs: connection)
        authority = self.configure(service, enabled=False)
        console_id = str(uuid4())
        human_request = self.request("UPDATE example SET value = 1", consoleId=console_id, mode="managed")
        with self.assertRaises(PostgresServiceError) as disabled:
            service.execute_console("local", human_request, "binding", "server", self.human_policy)
        self.assertEqual(disabled.exception.code, "console_write_intent_disabled")
        ai_request = {**self.request("UPDATE example SET value = 1", consoleId=console_id, mode="managed"),
                      "settingsRevision": None}
        with self.assertRaises(PostgresServiceError) as caught:
            service.execute_console(
                "local", ai_request, "ai-operation", "server",
                ConsolePolicy(allow_write=True, human_write_intent=False),
            )
        self.assertEqual(caught.exception.code, "execution_outcome_unknown")
        self.assertEqual(authority.settings["writeIntent"], "disabled")
        service.close()

    def test_limits_rows_columns_and_aggregate_bytes(self):
        self.connection.results = {"SELECT rows": (["value"], [(index,) for index in range(501)], "SELECT 501", 501)}
        result = self.service.execute_console("local", self.request("SELECT rows"), "binding", "server")
        self.assertEqual(result["statements"][0]["rowCount"], 500)
        self.assertTrue(result["statements"][0]["truncated"])
        self.assertLessEqual(len(json.dumps(result, separators=(",", ":")).encode()), 1024 * 1024)

        connection = Connection({"SELECT wide": ([f"c{i}" for i in range(101)], [], "SELECT 0", 0)})
        service = PostgresService(self.directory.name, connect_factory=lambda **kwargs: connection)
        service.set_metadata_store(ConsoleAuthority())
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_console("local", self.request("SELECT wide"), "binding", "server")
        self.assertEqual(error.exception.code, "sql_result_too_wide")

    def test_limits_nested_collection_cells_and_rejects_excessive_nesting(self):
        self.connection.results = {
            "SELECT collection": (["value"], [([1, 2, 3],)], "SELECT 1", 1),
        }
        self.service._console.result_limiter = ResultLimiter(ResultLimits(max_collection_items=2))
        result = self.service.execute_console("local", self.request("SELECT collection"), "binding", "server")
        statement = result["statements"][0]
        self.assertEqual(statement["rows"], [[[1, 2]]])
        self.assertTrue(statement["truncated"])
        self.assertEqual(statement["limitEvents"][0]["code"], "result_collection_truncated")

        self.connection.results = {"SELECT nested": (["value"], [([[[1]]],)], "SELECT 1", 1)}
        self.service._console.result_limiter = ResultLimiter(ResultLimits(max_nesting=2))
        with self.assertRaises(PostgresServiceError) as error:
            self.service.execute_console("local", self.request("SELECT nested"), "binding", "server")
        self.assertEqual(error.exception.code, "sql_result_nesting_too_deep")
        self.assertEqual(error.exception.details["policy"], "reject")

    def test_collects_and_bounds_notices_across_statements(self):
        self.connection.results = {
            "SELECT first": (["value"], [(1,)], "SELECT 1", 1),
            "SELECT second": (["value"], [(2,)], "SELECT 1", 1),
        }
        self.connection.notices = {
            "SELECT first": [f"notice {index}" for index in range(49)],
            "SELECT second": ["last notice", "discarded notice"],
        }
        result = self.service.execute_console(
            "local", self.request("SELECT first; SELECT second"), "binding", "server",
        )
        self.assertEqual(len(result["statements"][0]["notices"]), 49)
        self.assertEqual(result["statements"][1]["notices"], ["last notice"])
        self.assertEqual(self.connection.notice_handlers, [])

    def test_registry_enforces_process_and_console_concurrency_and_visibility(self):
        registry = ConsoleExecutionRegistry(maximum_active=2)
        first, second, third = str(uuid4()), str(uuid4()), str(uuid4())
        registry.reserve(first, "console-a", "one", "binding-a", "server")
        with self.assertRaises(PostgresServiceError) as error:
            registry.reserve(first, "console-z", "one", "binding-a", "server")
        self.assertEqual(error.exception.code, "execution_conflict")
        with self.assertRaises(PostgresServiceError) as error:
            registry.reserve(second, "console-a", "two", "binding-b", "server")
        self.assertEqual(error.exception.code, "execution_busy")
        registry.reserve(second, "console-b", "one", "binding-a", "server")
        with self.assertRaises(PostgresServiceError) as error:
            registry.reserve(third, "console-c", "one", "binding-a", "server")
        self.assertEqual(error.exception.code, "execution_busy")
        with self.assertRaises(PostgresServiceError) as error:
            registry.cancel(first, "wrong", "binding-a", "server")
        self.assertEqual(error.exception.code, "execution_not_found")

        pending = ConsoleExecutionRegistry()
        pending_id = str(uuid4())
        pending.reserve(pending_id, "console-pending", "one", "binding", "server")
        self.assertEqual(pending.cancel(pending_id, "one", "binding", "server"), {"requested": True})
        connection = Connection()
        self.assertTrue(pending.attach(pending_id, connection))
        self.assertTrue(connection.cancelled)

    def test_cancel_during_statement_is_distinguished_from_timeout(self):
        self.connection.block_on = "SELECT slow"
        request = self.request("SELECT slow")
        outcome = []

        def execute():
            try:
                self.service.execute_console("local", request, "binding", "server")
            except PostgresServiceError as error:
                outcome.append(error)

        thread = threading.Thread(target=execute)
        thread.start()
        self.assertTrue(self.connection.started.wait(1))
        self.assertEqual(self.service.cancel_console("local", request["executionId"], "binding", "server"), {"requested": True})
        thread.join(2)
        self.assertEqual(outcome[0].code, "execution_cancelled")
        self.assertEqual(outcome[0].details, {
            "statementIndex": 0, "completedStatementIndexes": [], "outcome": "rolled_back",
        })
        self.assertEqual(self.connection.rollbacks, 1)
        self.assertTrue(self.connection.closed)

    def test_maps_commands_unsupported_in_transaction(self):
        error = self.service._console._error(UnsupportedTransactionError(), 2, False)
        self.assertEqual(error.code, "unsupported_in_transaction")
        self.assertEqual(error.details, {
            "statementIndex": 2, "sqlstate": "25001", "postgres": {"sqlstate": "25001"},
            "phase": "execute", "operation": "console_statement",
        })

    def test_returns_bounded_postgres_diagnostics_for_failed_sql(self):
        error = self.service._console._error(SqlError(), 1, False)
        self.assertEqual(error.message, "Console SQL statement failed")
        self.assertEqual(error.details, {
            "statementIndex": 1,
            "sqlstate": "42703",
            "phase": "execute",
            "operation": "console_statement",
            "postgres": {
                "sqlstate": "42703",
                "message": "column missing_column does not exist",
                "detail": "The referenced output column is unavailable.",
                "hint": "Check the selected relation alias.",
                "position": 18,
            },
        })

    def test_postgres_diagnostic_is_normalized_bounded_and_strict(self):
        diagnostic = type("Diagnostic", (), {
            "message_primary": "  primary\n" + "x" * 1200,
            "message_detail": " detail\tvalue ",
            "message_hint": " hint\nvalue ",
            "statement_position": "100001",
            "internal_query": "must not escape",
        })()
        error = type("DatabaseError", (Exception,), {"sqlstate": "invalid", "diag": diagnostic})()
        result = postgres_error_diagnostic(error)
        self.assertEqual(set(result), {"message", "detail", "hint"})
        self.assertEqual(len(result["message"]), 1000)
        self.assertEqual(result["detail"], "detail value")
        self.assertEqual(result["hint"], "hint value")
        self.assertNotIn("x" * 1001, result["message"])

        malformed = type("DatabaseError", (Exception,), {
            "sqlstate": "42P01",
            "diag": type("Diagnostic", (), {"statement_position": True})(),
        })()
        self.assertEqual(postgres_error_diagnostic(malformed), {"sqlstate": "42P01"})

    def test_postgres_details_redacts_context_and_bounds_caller_evidence(self):
        diagnostic = type("Diagnostic", (), {
            "message_primary": "denied 'credential-value'; Key (email)=(secret@example.test) already exists " + "x" * 2000,
            "context": 'SQL statement "INSERT INTO audit VALUES (\'credential-value\')"',
        })()
        failure = type("DatabaseError", (Exception,), {"sqlstate": "42501", "diag": diagnostic})()
        details = postgres_error_details(
            failure, phase="execute", operation="structured_read",
            retry={"safe": False, "password": "must-not-escape", "reason": "r" * 500},
        )
        self.assertEqual(details["postgres"]["sqlstate"], "42501")
        self.assertEqual(len(details["postgres"]["message"]), 1000)
        self.assertIn("[redacted]", details["postgres"]["message"])
        self.assertNotIn("secret@example.test", details["postgres"]["message"])
        self.assertNotIn("context", details["postgres"])
        self.assertNotIn("password", details["retry"])
        self.assertEqual(len(details["retry"]["reason"]), 200)
        self.assertNotIn("credential-value", str(details))


if __name__ == "__main__":
    unittest.main()
