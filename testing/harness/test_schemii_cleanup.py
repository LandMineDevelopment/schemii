import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from testing.harness.cleanup_schemii_sweep import cleanup, cleanup_guard, owned_workspaces, process_birth
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
    delete_failure = None

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
            if self.delete_failure == len(self.deletes) + 1:
                raise RuntimeError("injected cleanup failure")
            self.deletes.append((self.username, path))
            collection = (self.chats if "/ai/chats/" in path else self.connections
                          if path.startswith("/api/v1/connections/") else self.workspaces)
            object_id = path.split("?")[0].rsplit("/", 1)[1]
            collection[self.username] = [item for item in collection.get(self.username, []) if item["id"] != object_id]
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
        FakeClient.delete_failure = None
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.cleanup_root = patch("testing.harness.cleanup_schemii_sweep.ROOT", self.root)
        self.cleanup_root.start()
        self.addCleanup(self.cleanup_root.stop)
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
        run_dir = self.root / "qa-test-run"
        run_dir.mkdir(exist_ok=True)
        (run_dir / "controller-owner.json").write_text(json.dumps({"pid": 2147483647, "birthTick": "1"}))
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

    def test_retry_preserves_ambiguous_create_that_succeeded_before_response(self):
        output = self.root / "workspaces.json"
        FakeClient.fail_after_create = 1
        with self.assertRaisesRegex(RuntimeError, "interrupted after create"):
            seed(self.root, "test1", output)
        first = ACCOUNTS[0]
        self.assertTrue(json.loads(journal_path(output).read_text())["lanes"][first]["localPending"])
        FakeClient.fail_after_create = None
        with self.assertRaisesRegex(ValueError, "Ambiguous unreceipted"):
            seed(self.root, "test1", output)
        self.assertFalse(output.exists())
        self.assertTrue(json.loads(journal_path(output).read_text())["lanes"][first]["localPending"])
        self.assertEqual(sum(item["id"] == wid(1) for item in FakeClient.workspaces[first]), 1)

    def test_seed_never_adopts_a_merely_name_matching_local_workspace(self):
        first = ACCOUNTS[0]
        FakeClient.workspaces[first] = [{"id": wid(999), "name": "qa_test1_004_seed", "connectionId": None}]
        with self.assertRaisesRegex(ValueError, "Unowned local workspace name collision"):
            seed(self.root, "test1", self.root / "workspaces.json")
        self.assertEqual(FakeClient.posts, 0)

    def test_seed_and_cleanup_support_an_exact_fresh_retained_designer_subset(self):
        selected = ("qa_designer_012", "qa_designer_013", "qa_designer_014")
        self.slots.extend({"username": username, "provisioned": True, "connectionId": "pg_" + "1" * 32}
                          for username in selected)
        output = self.root / "workspaces.json"
        fixture = seed(self.root, "test1", output, selected)
        self.assertEqual(set(fixture["lanes"]), set(selected))
        run_file = self.root / "qa-test-run" / "manifest.json"
        run_file.parent.mkdir()
        run_file.with_name("controller-owner.json").write_text(json.dumps({"pid": 2147483647, "birthTick": "1"}))
        run = {"id": "qa-test-run", "status": "stopped", "createdAt": "2026-09-26T12:00:00Z",
               "fixtureMode": "declared-retained-resources", "fixtureVersion": "schemii-full-qa-v1",
               "accounts": list(selected), "lanes": [
                   {"username": username, "resources": {"scratchPrefix": fixture["lanes"][username]["prefix"],
                    "localWorkspaceId": fixture["lanes"][username]["localWorkspaceId"],
                    "readerWorkspaceId": fixture["lanes"][username]["readerWorkspaceId"]}}
                   for username in selected]}
        run_file.write_text(json.dumps(run))
        with patch.multiple("testing.harness.cleanup_schemii_sweep", Client=FakeClient,
                            registry_load=lambda state: {"slots": self.slots}):
            result = cleanup(self.root, output, run_file)
        self.assertEqual(result["workspaces"], 6)
        self.assertEqual({username for username, _ in FakeClient.deletes}, set(selected))

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
        run_file.parent.mkdir(exist_ok=True)
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
        run_file.parent.mkdir(exist_ok=True)
        run = self.run_data(fixture)
        run["lanes"][0]["resources"]["cleanupReceipts"] = [
            {"kind": "workspace", "id": scratch, "createdAt": "2026-09-26T13:00:00Z"},
            {"kind": "connection", "id": "pg_scratch", "createdAt": "2026-09-26T13:00:00Z"},
        ]
        run_file.write_text(json.dumps(run))
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
        run_file.parent.mkdir(exist_ok=True)
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
        run_file.parent.mkdir(exist_ok=True)
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

    def prepared_run(self):
        output = self.root / "workspaces.json"
        fixture = seed(self.root, "test1", output)
        run = self.run_data(fixture)
        run_file = self.root / "qa-test-run" / "manifest.json"
        run_file.write_text(json.dumps(run))
        return output, run_file, run

    def test_completed_result_still_rejects_live_controller_and_children(self):
        output, run_file, run = self.prepared_run()
        run["status"] = "passed"
        live = {"pid": os.getpid(), "birthTick": process_birth(os.getpid())}
        for kind in ("controller", "browser", "worker"):
            with self.subTest(kind=kind):
                run_file.with_name("controller-owner.json").write_text(json.dumps(
                    live if kind == "controller" else {"pid": 2147483647, "birthTick": "1"}))
                run["lanes"][0].pop("browser", None)
                run["lanes"][0].pop("worker", None)
                if kind != "controller":
                    run["lanes"][0][kind] = live
                run_file.write_text(json.dumps(run))
                with self.assertRaisesRegex(ValueError, "still live"):
                    cleanup(self.root, output, run_file)
                self.assertEqual(FakeClient.deletes, [])

    def test_missing_or_pending_process_ownership_blocks_before_delete(self):
        output, run_file, run = self.prepared_run()
        run_file.with_name("controller-owner.json").unlink()
        with self.assertRaisesRegex(ValueError, "ownership record is missing"):
            cleanup(self.root, output, run_file)
        run_file.with_name("controller-owner.json").write_text(json.dumps({"pid": 2147483647, "birthTick": "1"}))
        run["lanes"][0]["browserLaunchPending"] = True
        run_file.write_text(json.dumps(run))
        with self.assertRaisesRegex(ValueError, "launch ownership is unresolved"):
            cleanup(self.root, output, run_file)
        self.assertEqual(FakeClient.deletes, [])

    def test_direct_module_invocation_rejects_live_owner_without_api_calls(self):
        output, run_file, run = self.prepared_run()
        run_file.with_name("controller-owner.json").write_text(json.dumps(
            {"pid": os.getpid(), "birthTick": process_birth(os.getpid())}))
        code = ("from pathlib import Path; from testing.harness.cleanup_schemii_sweep import cleanup; "
                f"cleanup(Path({str(self.root)!r}), Path({str(output)!r}), Path({str(run_file)!r}))")
        result = subprocess.run(["python", "-c", code], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("still live", result.stderr)

    def test_existing_node_reservation_cannot_interleave_guarded_cleanup(self):
        _, run_file, _ = self.prepared_run()
        entered, release = threading.Event(), threading.Event()
        errors = []

        def guarded():
            try:
                with cleanup_guard(run_file):
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("test guard timed out")
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=guarded)
        thread.start()
        self.assertTrue(entered.wait(5))
        leases = (Path(__file__).parent / "leases.mjs").resolve().as_uri()
        script = (f"import {{ reserveAccounts }} from {json.dumps(leases)}; "
                  f"await reserveAccounts({{root:{json.dumps(str(self.root))},runId:'qa-new-owner',"
                  f"runDir:{json.dumps(str(self.root / 'qa-new-owner'))},accounts:[{json.dumps(ACCOUNTS[0])}]}});")
        child = subprocess.Popen(["node", "--input-type=module", "-e", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                child.communicate(timeout=0.25)
        finally:
            release.set()
            thread.join(5)
        stdout, stderr = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, stderr)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        lease = self.root / ".git/qa-account-leases" / (ACCOUNTS[0] + ".json")
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "reserved by an active or unresolved run"):
                with cleanup_guard(run_file):
                    self.fail("new owner must prevent cleanup")
            self.assertEqual(json.loads(lease.read_text())["runId"], "qa-new-owner")

    def test_unledgered_name_collision_is_preserved_and_reported(self):
        output, run_file, _ = self.prepared_run()
        first = ACCOUNTS[0]
        fixture = json.loads(output.read_text())
        ambiguous = {"id": wid(2000), "name": fixture["lanes"][first]["prefix"] + "collision",
                     "createdAt": "2026-09-26T13:00:00Z", "revision": 1}
        FakeClient.workspaces[first].append(ambiguous)
        with patch.multiple("testing.harness.cleanup_schemii_sweep", Client=FakeClient,
                            registry_load=lambda state: {"slots": self.slots}):
            totals = cleanup(self.root, output, run_file)
        self.assertEqual(totals["pending"], 1)
        self.assertIn(ambiguous, FakeClient.workspaces[first])
        self.assertEqual(json.loads(run_file.with_name("sweep-cleanup.json").read_text())["status"], "cleanup-pending")

    def test_partial_failure_retains_receipts_and_retry_is_idempotent(self):
        output, run_file, _ = self.prepared_run()
        FakeClient.delete_failure = 2
        with patch.multiple("testing.harness.cleanup_schemii_sweep", Client=FakeClient,
                            registry_load=lambda state: {"slots": self.slots}):
            with self.assertRaisesRegex(RuntimeError, "injected cleanup failure"):
                cleanup(self.root, output, run_file)
            receipt = json.loads(run_file.with_name("sweep-cleanup.json").read_text())
            self.assertEqual(len(receipt["removed"]), 1)
            self.assertEqual(receipt["status"], "cleanup-pending")
            FakeClient.delete_failure = None
            cleanup(self.root, output, run_file)
            self.assertEqual(cleanup(self.root, output, run_file),
                             {"chats": 0, "workspaces": 0, "connections": 0, "pending": 0})
        self.assertEqual(run_file.with_name("sweep-cleanup.json").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
