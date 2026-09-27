import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from testing.harness.cleanup_schemii_sweep import cleanup, owned_workspaces
from testing.harness.schemii_sweep import ACCOUNTS, WRITERS
from testing.harness.schemii_workspaces import journal_path, seed


def wid(number):
    return "ws_" + format(number, "032x")


class FakeClient:
    workspaces = {}
    chats = {}
    connections = {}
    posts = 0
    fail_after = None
    fail_after_create = None
    deletes = []

    def login(self, slot):
        self.username = slot["username"]

    def logout(self):
        pass

    def call(self, method, path, payload=None, expected=200):
        workspace = self.workspaces.setdefault(self.username, [])
        if method == "GET":
            if path == "/api/v1/schemii/workspaces":
                return {"workspaces": list(workspace)}
            if path == "/api/v1/schemii/ai/chats":
                return {"chats": list(self.chats.get(self.username, []))}
            if path == "/api/v1/connections?product=schemii":
                return {"connections": list(self.connections.get(self.username, []))}
        if method == "POST":
            self.__class__.posts += 1
            if self.fail_after == self.posts:
                raise RuntimeError("injected failure")
            item = {"id": wid(self.posts), "revision": 1, "createdAt": "2026-09-26T13:00:00Z"}
            if path.endswith("/postgres"):
                item.update(name="backed", connectionId=payload["connectionId"], database="schemii_qa",
                            namespace=payload["namespace"])
                workspace.append(item)
                if self.fail_after_create == self.posts:
                    raise RuntimeError("interrupted after create")
                return {"workspace": item}
            item.update(name=payload["name"], connectionId=None)
            workspace.append(item)
            if self.fail_after_create == self.posts:
                raise RuntimeError("interrupted after create")
            return item
        if method == "DELETE":
            self.deletes.append((self.username, path))
            return None
        raise AssertionError((method, path))


class SchemiiOwnershipTest(unittest.TestCase):
    def setUp(self):
        FakeClient.workspaces = {}
        FakeClient.chats = {}
        FakeClient.connections = {}
        FakeClient.posts = 0
        FakeClient.fail_after = None
        FakeClient.fail_after_create = None
        FakeClient.deletes = []
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.slots = []
        for username in ACCOUNTS:
            slot = {"username": username, "provisioned": True, "connectionId": "pg_" + "1" * 32}
            if username in WRITERS:
                slot.update(writableConnectionId="pg_" + "2" * 32,
                            writableSchema="qa_write_designer_" + username[-3:])
            self.slots.append(slot)
        self.client_patch = patch.multiple("testing.harness.schemii_workspaces", Client=FakeClient,
                                           registry_load=lambda state: {"slots": self.slots})
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def run_data(self, fixture, tag="test1"):
        lanes = []
        for username in ACCOUNTS:
            item = fixture["lanes"][username]
            lanes.append({"username": username, "resources": {
                "scratchPrefix": item["prefix"],
                "localWorkspaceId": item["localWorkspaceId"],
                "readerWorkspaceId": item["readerWorkspaceId"],
                **({"writerWorkspaceId": item["writerWorkspaceId"]} if username in WRITERS else {}),
            }})
        return {"id": "qa-test-run", "status": "stopped", "createdAt": "2026-09-26T12:00:00Z",
                "fixtureMode": "declared-retained-resources", "fixtureVersion": "schemii-full-qa-v1",
                "accounts": list(ACCOUNTS), "lanes": lanes}

    def test_seed_retry_keeps_original_ownership_and_preexisting_workspace(self):
        username = ACCOUNTS[0]
        existing = {"id": wid(100), "name": "backed", "connectionId": self.slots[0]["connectionId"],
                    "database": "schemii_qa", "namespace": username, "revision": 1}
        FakeClient.workspaces[username] = [existing]
        output = self.root / "workspaces.json"
        FakeClient.fail_after = 3
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            seed(self.root, "test1", output)
        journal = json.loads(journal_path(output).read_text())
        self.assertTrue(journal["lanes"][username]["localCreated"])
        self.assertFalse(journal["lanes"][username]["readerCreated"])
        FakeClient.fail_after = None
        result = seed(self.root, "test1", output)
        self.assertEqual(result["lanes"][username]["localWorkspaceId"], wid(1))
        self.assertTrue(result["lanes"][username]["localCreated"])
        self.assertEqual(result["lanes"][username]["readerWorkspaceId"], wid(100))
        self.assertFalse(result["lanes"][username]["readerCreated"])
        self.assertFalse(journal_path(output).exists())
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_retry_recovers_create_that_succeeded_before_response(self):
        output = self.root / "workspaces.json"
        FakeClient.fail_after_create = 1
        with self.assertRaisesRegex(RuntimeError, "interrupted after create"):
            seed(self.root, "test1", output)
        first = ACCOUNTS[0]
        self.assertTrue(json.loads(journal_path(output).read_text())["lanes"][first]["localPending"])
        FakeClient.fail_after_create = None
        result = seed(self.root, "test1", output)
        self.assertEqual(result["lanes"][first]["localWorkspaceId"], wid(1))
        self.assertTrue(result["lanes"][first]["localCreated"])
        self.assertEqual(sum(item["id"] == wid(1) for item in FakeClient.workspaces[first]), 1)

    def test_followup_manifest_uses_exact_workspace_tag(self):
        fixture = seed(self.root, "test1", self.root / "workspaces.json")
        run = self.run_data(fixture)
        run["fixtureVersion"] = "schemii-followup-qa-v1"
        for lane in run["lanes"]:
            lane["resources"]["workspaceTag"] = "test1"
        run_file = self.root / "qa-test-run" / "manifest.json"
        owned = owned_workspaces(fixture, run, run_file)
        self.assertEqual(len(owned), len(ACCOUNTS))
        run["lanes"][0]["resources"]["workspaceTag"] = "another"
        with self.assertRaisesRegex(ValueError, "Workspace tag differs"):
            owned_workspaces(fixture, run, run_file)

    def test_followup_subset_only_cleans_its_run_accounts(self):
        output = self.root / "workspaces.json"
        fixture = seed(self.root, "test1", output)
        run = self.run_data(fixture)
        selected = [ACCOUNTS[0], ACCOUNTS[2]]
        run["fixtureVersion"] = "schemii-followup-qa-v1"
        run["accounts"] = selected
        run["lanes"] = [lane for lane in run["lanes"] if lane["username"] in selected]
        run_file = self.root / "qa-test-run" / "manifest.json"
        run_file.parent.mkdir()
        run_file.write_text(json.dumps(run))
        with patch.multiple("testing.harness.cleanup_schemii_sweep", Client=FakeClient,
                            registry_load=lambda state: {"slots": self.slots}):
            totals = cleanup(self.root, output, run_file)
        self.assertEqual(totals["workspaces"], 2 * len(selected) + len(set(selected) & set(WRITERS)))
        self.assertEqual({username for username, _ in FakeClient.deletes}, set(selected))

    def test_cleanup_scopes_chats_and_workspace_deletion(self):
        output = self.root / "workspaces.json"
        fixture = seed(self.root, "test1", output)
        first = ACCOUNTS[0]
        owned = fixture["lanes"][first]["localWorkspaceId"]
        prefix = fixture["lanes"][first]["prefix"]
        older = wid(999)
        scratch = wid(1000)
        other_tag = wid(1001)
        unprefixed = wid(1002)
        FakeClient.workspaces[first] += [
            {"id": older, "name": prefix + "preexisting", "createdAt": "2026-09-26T11:00:00Z", "revision": 1},
            {"id": scratch, "name": prefix + "scratch", "createdAt": "2026-09-26T13:00:00Z", "revision": 1},
            {"id": other_tag, "name": "qa_else_004_scratch", "createdAt": "2026-09-26T13:00:00Z", "revision": 1},
            {"id": unprefixed, "name": "personal", "createdAt": "2026-09-26T13:00:00Z", "revision": 1},
        ]
        FakeClient.chats[first] = [
            {"id": "chat_owned", "workspaceId": owned, "createdAt": "2026-09-26T13:00:00Z"},
            {"id": "chat_scratch", "workspaceId": scratch, "createdAt": "2026-09-26T13:00:00Z"},
            {"id": "chat_other", "workspaceId": older, "createdAt": "2026-09-26T13:00:00Z"},
            {"id": "chat_old", "workspaceId": owned, "createdAt": "2026-09-26T11:00:00Z"},
        ]
        FakeClient.connections[first] = [
            {"id": "pg_scratch", "name": prefix + "scratch", "ownership": "user",
             "createdAt": "2026-09-26T13:00:00Z", "revision": 1},
            {"id": "pg_old", "name": prefix + "old", "ownership": "user",
             "createdAt": "2026-09-26T11:00:00Z", "revision": 1},
            {"id": "pg_other", "name": "qa_else_004_scratch", "ownership": "user",
             "createdAt": "2026-09-26T13:00:00Z", "revision": 1},
            {"id": "pg_managed", "name": prefix + "managed", "ownership": "managed",
             "createdAt": "2026-09-26T13:00:00Z", "revision": 1},
        ]
        run_file = self.root / "qa-test-run" / "manifest.json"
        run_file.parent.mkdir()
        run_file.write_text(json.dumps(self.run_data(fixture)))
        with patch.multiple("testing.harness.cleanup_schemii_sweep", Client=FakeClient,
                            registry_load=lambda state: {"slots": self.slots}):
            totals = cleanup(self.root, output, run_file)
        self.assertEqual(totals["chats"], 2)
        self.assertIn((first, "/api/v1/schemii/ai/chats/chat_owned"), FakeClient.deletes)
        self.assertIn((first, "/api/v1/schemii/ai/chats/chat_scratch"), FakeClient.deletes)
        self.assertIn((first, f"/api/v1/schemii/workspaces/{scratch}?expectedRevision=1"), FakeClient.deletes)
        self.assertIn((first, "/api/v1/connections/pg_scratch?expectedRevision=1"), FakeClient.deletes)
        self.assertFalse(any("chat_other" in path or "chat_old" in path or older in path
                             or other_tag in path or unprefixed in path or "pg_old" in path
                             or "pg_other" in path or "pg_managed" in path
                             for _, path in FakeClient.deletes))

    def test_mismatched_fixture_rejected_before_login_or_delete(self):
        output = self.root / "workspaces.json"
        fixture = seed(self.root, "test1", output)
        run_file = self.root / "qa-test-run" / "manifest.json"
        run_file.parent.mkdir()
        run = self.run_data(fixture)
        run["lanes"][0]["resources"]["localWorkspaceId"] = wid(999)
        run_file.write_text(json.dumps(run))
        with self.assertRaisesRegex(ValueError, "Workspace ID differs"):
            cleanup(self.root, output, run_file)
        self.assertEqual(FakeClient.deletes, [])

    def test_seed_ownership_mismatch_rejected_before_login(self):
        output = self.root / "workspaces.json"
        fixture = seed(self.root, "test1", output)
        run_file = self.root / "qa-test-run" / "manifest.json"
        run_file.parent.mkdir()
        run = self.run_data(fixture)
        for lane in run["lanes"]:
            source = fixture["lanes"][lane["username"]]
            lane["resources"]["seedOwnership"] = {
                key: source[key] for key in ("localCreated", "readerCreated", "writerCreated")}
        run["lanes"][0]["resources"]["seedOwnership"]["localCreated"] = False
        run_file.write_text(json.dumps(run))
        with self.assertRaisesRegex(ValueError, "Workspace ownership differs"):
            cleanup(self.root, output, run_file)
        self.assertEqual(FakeClient.deletes, [])


if __name__ == "__main__":
    unittest.main()
