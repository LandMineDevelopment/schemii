import hashlib
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from testing.harness.schemii_preseed import canonical, design_content, prepare


class SchemiiPreseedTests(unittest.TestCase):
    def test_exact_two_table_oracle_and_references(self):
        content = design_content("sep26d")
        projects, tasks = content["tables"]
        self.assertEqual([table["name"] for table in content["tables"]], ["qa_projects", "qa_tasks"])
        self.assertEqual(
            [(column["name"], column["dataType"]) for column in projects["columns"]],
            [("project_id", "bigint"), ("code", "varchar(24)"), ("title", "text"),
             ("budget", "numeric(12,2)"), ("due_on", "date")],
        )
        self.assertEqual(
            [(column["name"], column["dataType"]) for column in tasks["columns"]],
            [("task_id", "bigint"), ("project_id", "bigint"), ("state", "varchar(16)"),
             ("effort_hours", "numeric(6,2)"), ("notes", "text")],
        )
        self.assertEqual(projects["columns"][0]["identity"], "always")
        self.assertEqual(tasks["columns"][0]["identity"], "always")
        self.assertEqual(projects["columns"][3]["defaultExpression"], "0")
        self.assertEqual(tasks["columns"][2]["defaultExpression"], "'todo'")
        self.assertEqual(projects["checks"][0]["expression"], "budget >= 0")
        self.assertEqual(tasks["checks"][0]["expression"], "state IN ('todo','doing','done')")
        relationship = content["relationships"][0]
        self.assertEqual(relationship["sourceColumnIds"], [tasks["columns"][1]["id"]])
        self.assertEqual(relationship["targetColumnIds"], [projects["columns"][0]["id"]])
        self.assertEqual(tasks["indexes"][0]["columnIds"],
                         [tasks["columns"][1]["id"], tasks["columns"][2]["id"]])
        ids = []
        for table in content["tables"]:
            ids.append(table["id"])
            for collection in ("columns", "keys", "checks", "indexes"):
                ids.extend(item["id"] for item in table.get(collection, []))
        ids.extend(item["id"] for item in content["relationships"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(re.fullmatch(r"[a-z]+_[0-9a-f]{32}", item) for item in ids))

    def test_content_is_tag_deterministic(self):
        first = design_content("sep26d")
        self.assertEqual(first, design_content("sep26d"))
        self.assertNotEqual(first, design_content("sep26e"))
        self.assertEqual(hashlib.sha256(canonical(first)).hexdigest(),
                         hashlib.sha256(json.dumps(first, sort_keys=True, separators=(",", ":")).encode()).hexdigest())

    def test_unledgered_chat_collision_is_not_adopted_or_replayed(self):
        class ChatClient:
            chats = []
            posts = 0

            def login(self, slot):
                pass

            def logout(self):
                pass

            def call(self, method, path, payload=None, expected=200):
                if method == "GET" and path.endswith("/ai/settings"):
                    return {"enabled": True, "defaultProviderId": "instance-codex", "defaultModelId": "gpt-6-luna"}
                if method == "GET" and "/ai/chats?" in path:
                    return {"chats": self.chats}
                if method == "POST":
                    self.__class__.posts += 1
                    self.chats.append({"id": "chat_" + "a" * 32, "title": payload["title"], "status": "active"})
                    raise RuntimeError("created before response interrupted")
                raise AssertionError((method, path))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = {"tag": "owned", "lanes": {"qa_designer_001": {
                "prefix": "qa_owned_001_", "localWorkspaceId": "ws_" + "1" * 32,
                "readerWorkspaceId": "ws_" + "2" * 32}}}
            workspace_map, output = root / "workspaces.json", root / "ready.json"
            workspace_map.write_text(json.dumps(source))
            with patch.multiple("testing.harness.schemii_preseed", Client=ChatClient, DESIGN_ACCOUNTS=(),
                                registry_load=lambda state: {"slots": [{"username": "qa_designer_001", "connectionId": "pg_" + "1" * 32}]}), \
                    patch("testing.harness.schemii_preseed._assert_workspace"):
                with self.assertRaisesRegex(RuntimeError, "response interrupted"):
                    prepare(workspace_map, output, root)
                pending = json.loads(output.read_text())["chat"]["qa_designer_001"]["pending"]["reader"]
                self.assertEqual(pending["workspaceId"], source["lanes"]["qa_designer_001"]["readerWorkspaceId"])
                with self.assertRaisesRegex(ValueError, "Unledgered chat name collision"):
                    prepare(workspace_map, output, root)
                self.assertEqual(ChatClient.posts, 1)
                self.assertEqual(len(ChatClient.chats), 1)
                self.assertEqual(json.loads(output.read_text())["chat"]["qa_designer_001"]["chats"], {})


if __name__ == "__main__":
    unittest.main()
