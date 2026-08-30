import sys
import tempfile
import unittest
from uuid import uuid4
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.metadata import MetadataStore, MetadataStoreError
from schemii.postgres_console import ConsolePolicy
from schemii.postgres_service import PostgresService, PostgresServiceError
from tests.test_postgres_console import Connection as PostgresConnection, PROFILE


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executions = []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.executions.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def close(self):
        pass


class Connection:
    def __init__(self, rows):
        self.cursor_value = Cursor(rows)

    def cursor(self):
        return self.cursor_value

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def settings_row(application="schemii", revision=1):
    now = datetime.now(timezone.utc)
    return {
        "application_id": application, "revision": revision, "write_intent": "disabled",
        "default_mode": "managed_read", "statement_limit": 100, "row_page_size": 100,
        "created_at": now, "updated_at": now,
    }


class ConsoleSettingsTests(unittest.TestCase):
    def test_default_settings_are_durable_concrete_and_application_scoped(self):
        connection = Connection([settings_row("schemii")])
        result = MetadataStore(lambda: connection).get_console_settings("schemii")
        self.assertEqual((result["revision"], result["writeIntent"], result["defaultMode"]), (1, "disabled", "managed_read"))
        self.assertEqual(result["inheritance"], "none")
        self.assertEqual(connection.cursor_value.executions[0][1], ("schemii",))

        migration = resources.files("schemii.metadata.migrations").joinpath("0005_console_execution_receipts.sql").read_text()
        self.assertIn("metadata_console_settings_isolation", migration)
        self.assertIn("application_id text PRIMARY KEY", migration)
        self.assertIn("settings do not inherit across applications", migration)

    def test_update_uses_optimistic_revision_and_operator_maxima(self):
        row = {**settings_row(), "revision": 2, "write_intent": "enabled", "default_mode": "managed",
               "statement_limit": 10, "row_page_size": 50}
        connection = Connection([row])
        result = MetadataStore(lambda: connection).update_console_settings("schemii", 1, {
            "writeIntent": "enabled", "defaultMode": "managed", "statementLimit": 10, "rowPageSize": 50,
        })
        self.assertEqual(result["revision"], 2)
        sql, params = connection.cursor_value.executions[0]
        self.assertIn("WHERE application_id = %s AND revision = %s", sql)
        self.assertEqual(params[-2:], ("schemii", 1))

        with self.assertRaises(MetadataStoreError) as invalid:
            MetadataStore(lambda: Connection([])).update_console_settings("schemii", 1, {
                "writeIntent": "enabled", "defaultMode": "managed", "statementLimit": 101, "rowPageSize": 50,
            })
        self.assertEqual(invalid.exception.code, "invalid_metadata")

    def test_stale_update_reports_current_revision(self):
        connection = Connection([None, {"revision": 4}])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).update_console_settings("schemer", 2, {
                "writeIntent": "disabled", "defaultMode": "managed_read", "statementLimit": 20, "rowPageSize": 100,
            })
        self.assertEqual(caught.exception.code, "console_settings_conflict")
        self.assertEqual(caught.exception.details["currentRevision"], 4)

    def test_human_intent_has_no_expiry_and_cannot_authorize_ai(self):
        class Authority:
            def __init__(self):
                self.settings = {"application": "schemii", "revision": 3, "writeIntent": "enabled",
                                 "defaultMode": "managed", "statementLimit": 20, "rowPageSize": 100}

            def get_console_settings(self, application):
                return dict(self.settings)

            def get_console_execution_receipt(self, *args):
                raise MetadataStoreError("execution_not_found", "missing", status=404)

            def put_console_execution_receipt(self, receipt):
                return {key: receipt[key] for key in ("executionId", "mode", "settingsRevision", "state", "outcome",
                                                       "completedStatementIndexes", "errorCode", "postgresEvidence", "reconciliationEvidence")}

        now = [1000.0]
        connections = []

        def connect(**kwargs):
            connection = PostgresConnection()
            connections.append(connection)
            return connection

        with tempfile.TemporaryDirectory() as directory:
            service = PostgresService(directory, connect_factory=connect, clock=lambda: now[0])
            service.save_profile("local", PROFILE)
            authority = Authority()
            service.set_metadata_store(authority)
            fingerprint = service.profile_context_fingerprint("local")

            def request():
                return {"executionId": str(uuid4()), "consoleId": str(uuid4()), "database": "demo",
                        "namespace": "public", "sql": "UPDATE example SET value = 1", "mode": "managed",
                        "settingsRevision": 3, "profileFingerprint": fingerprint}

            human = ConsolePolicy(allow_write=True, human_write_intent=True)
            service.execute_console("local", request(), "browser", "server", human)
            now[0] += 365 * 24 * 60 * 60
            service.execute_console("local", request(), "browser", "server", human)
            self.assertEqual(sum(connection.commits for connection in connections), 2)

            authority.settings = {**authority.settings, "revision": 4, "writeIntent": "disabled"}
            with self.assertRaises(PostgresServiceError) as disabled:
                service.execute_console("local", {**request(), "settingsRevision": 4}, "browser", "server", human)
            self.assertEqual(disabled.exception.code, "console_write_intent_disabled")

            ai = ConsolePolicy(allow_write=True, human_write_intent=False)
            service.execute_console("local", {**request(), "settingsRevision": None}, "ai-operation", "server", ai)
            with self.assertRaises(PostgresServiceError):
                service.execute_console("local", request(), "ai-operation", "server", ConsolePolicy())
            service.close()


if __name__ == "__main__":
    unittest.main()
