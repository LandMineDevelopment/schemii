import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.ai_policy import default_policy, effective_capabilities, policy_digest
from schemii.metadata import MetadataStore, MetadataStoreError
from tests.test_metadata_repository import FakeConnection


def revision_row(application="schemii", revision=1):
    policy = default_policy(application)
    now = datetime.now(timezone.utc)
    return {
        "agent_policy_revision_id": uuid.uuid4(), "revision": revision, "schema_version": 1,
        "policy": policy, "policy_digest": policy_digest(policy), "created_at": now, "updated_at": now,
    }


def capability_rows(application="schemii"):
    return [
        {"capability": name, "configured_mode": modes["configuredMode"],
         "effective_mode": modes["effectiveMode"], "safety_floor": modes["safetyFloor"]}
        for name, modes in effective_capabilities(application, default_policy(application)).items()
    ]


class AgentSettingsRepositoryTests(unittest.TestCase):
    def test_get_creates_conservative_default_revision_transactionally(self):
        connection = FakeConnection(rows=[None, revision_row(), capability_rows()])
        result = MetadataStore(lambda: connection).get_agent_settings("schemii", "default")
        self.assertEqual(result["revision"], 1)
        self.assertTrue(all(item["configuredMode"] == "disabled" for item in result["capabilities"].values()))
        sql = "\n".join(statement for statement, _ in connection.cursor_value.executions)
        self.assertIn("metadata_agent_settings", sql)
        self.assertIn("metadata_agent_policy_revisions", sql)
        self.assertNotIn("password", sql.lower())
        self.assertEqual(connection.commits, 1)

    def test_update_is_optimistic_and_revokes_only_explicitly_linked_future_snapshots(self):
        policy = default_policy("schemii")
        policy["capabilities"]["structured_read"] = "automatic"
        connection = FakeConnection(rows=[{"current_revision": 1}, revision_row(revision=2), capability_rows()])
        result = MetadataStore(lambda: connection).update_agent_settings("schemii", "default", 1, policy)
        self.assertEqual(result["revision"], 2)
        sql = "\n".join(statement for statement, _ in connection.cursor_value.executions)
        self.assertIn("v.agent_policy_revision_id = r.agent_policy_revision_id", sql)
        self.assertIn("r.application_id = %s", sql)
        self.assertIn("c.application_id = %s", sql)
        self.assertIn("p.state = 'ready'", sql)
        self.assertIn("q.state = 'ready'", sql)
        self.assertNotIn("metadata_operations SET", sql)

    def test_legacy_schemer_policy_does_not_inherit_new_raw_read_authority(self):
        row = revision_row("schemer")
        row["policy"]["capabilities"].pop("raw_read")
        legacy_capabilities = [item for item in capability_rows("schemer") if item["capability"] != "raw_read"]
        connection = FakeConnection(rows=[{"exists": 1}, row, legacy_capabilities])

        result = MetadataStore(lambda: connection).get_agent_settings("schemer", "default")

        self.assertEqual(result["capabilities"]["raw_read"], {
            "configuredMode": "disabled", "effectiveMode": "disabled", "safetyFloor": "every_action",
        })

    def test_stale_update_reports_policy_changed(self):
        connection = FakeConnection(rows=[{"current_revision": 4}])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).update_agent_settings(
                "schemii", "default", 3, default_policy("schemii"),
            )
        self.assertEqual(caught.exception.code, "policy_changed")
        self.assertEqual(caught.exception.details, {"currentRevision": 4})

    def test_operation_bound_usage_is_durable_and_rejects_overrun_before_execution(self):
        operation_id = str(uuid.uuid4())
        connection = FakeConnection(rows=[{"state": "running", "maximum": "2"}, None])
        result = MetadataStore(lambda: connection).consume_operation_bound(
            operation_id, "pagesInspected", 1, {"offset": 0},
        )
        self.assertEqual((result["used"], result["maximum"]), (1, 2))
        sql = "\n".join(statement for statement, _ in connection.cursor_value.executions)
        self.assertIn("FOR UPDATE OF o", sql)
        self.assertIn("metadata_ai_operation_usage", sql)

        connection = FakeConnection(rows=[{"state": "running", "maximum": "2"}, {"used": 2, "evidence": []}])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).consume_operation_bound(
                operation_id, "pagesInspected", 1, {"offset": 200},
            )
        self.assertEqual(caught.exception.code, "policy_bound_exceeded")


if __name__ == "__main__":
    unittest.main()
