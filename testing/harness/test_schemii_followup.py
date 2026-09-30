import copy
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from testing.harness.schemii_followup import (
    ACCOUNTS, MISSIONS, PRESEEDED, ROW_SELECTOR, WRITERS, build, main,
)


class SchemiiFollowupTest(unittest.TestCase):
    tag = "wave2"

    def source(self):
        lanes = {}
        for account in ACCOUNTS:
            resources = {"schema": account, "connectionId": "pg_" + "1" * 32,
                         "password": "never-copy-this"}
            if account in WRITERS:
                resources.update(writableConnectionId="pg_" + "2" * 32,
                                 writableSchema="qa_write_designer_" + account[-3:])
            lane = {"persona": "designer", "resources": resources,
                    "expectedCapabilities": ["schemii:access"], "deniedProducts": ["schemoo"]}
            if account in {"qa_designer_001", "qa_designer_002"}:
                lane["chatProvider"] = {"providerId": "instance-codex", "modelId": "gpt-6-luna",
                                        "reasoningEffort": "default"}
            lanes[account] = lane
        return {"lanes": lanes}

    def workspaces(self):
        lanes = {}
        for index, account in enumerate(ACCOUNTS):
            lanes[account] = {
                "prefix": f"qa_{self.tag}_{account[-3:]}_",
                "localWorkspaceId": "ws_" + format(index * 3 + 1, "032x"),
                "readerWorkspaceId": "ws_" + format(index * 3 + 2, "032x"),
                "writerWorkspaceId": "ws_" + format(index * 3 + 3, "032x") if account in WRITERS else None,
                "localCreated": True, "readerCreated": True,
                "writerCreated": account in WRITERS,
            }
        preseed = {
            account: {"workspaceId": lanes[account]["writerWorkspaceId" if account == "qa_designer_011" else "localWorkspaceId"],
                      "marker": lanes[account]["prefix"], "designHash": "a" * 64,
                      "fingerprint": "b" * 64,
                      "tables": ["qa_projects", "qa_tasks"]}
            for account in PRESEEDED
        }
        chat = {"qa_designer_001": {
            "readerWorkspaceId": lanes["qa_designer_001"]["readerWorkspaceId"],
            "localWorkspaceId": lanes["qa_designer_001"]["localWorkspaceId"],
            "chats": {
                "reader": {"id": "chat_" + "a" * 32,
                           "workspaceId": lanes["qa_designer_001"]["readerWorkspaceId"]},
                "design": {"id": "chat_" + "b" * 32,
                           "workspaceId": lanes["qa_designer_001"]["localWorkspaceId"]},
            },
        }}
        return {"tag": self.tag, "lanes": lanes, "preseed": preseed, "chat": chat}

    def test_ten_lanes_fifty_broad_scenarios_and_exact_scope(self):
        source, workspaces = self.source(), self.workspaces()
        source["lanes"]["qa_designer_001"]["chatProvider"]["token"] = "never-copy-chat-token"
        workspaces["chat"]["qa_designer_001"]["token"] = "never-copy-chat-map-token"
        original = copy.deepcopy(source)
        manifest = build(source, self.tag, workspaces)
        self.assertEqual(source, original)
        self.assertEqual(list(manifest["lanes"]), list(ACCOUNTS))
        self.assertEqual(set(MISSIONS), set(ACCOUNTS))
        self.assertEqual(sum(map(lambda lane: len(lane["scenarios"]), manifest["lanes"].values())), 50)
        self.assertTrue(all(len(lane["scenarios"]) == 5 for lane in manifest["lanes"].values()))
        serialized = json.dumps(manifest)
        self.assertNotIn("never-copy-this", serialized)
        self.assertNotIn("never-copy-chat-token", serialized)
        self.assertNotIn("never-copy-chat-map-token", serialized)
        self.assertIn(ROW_SELECTOR, serialized)
        self.assertIn("33724083", serialized)
        self.assertIn("horizontal", serialized)
        self.assertIn("harness download", serialized)
        self.assertIn("harness upload", serialized)
        self.assertIn("qa_write_designer_011", manifest["lanes"]["qa_designer_011"]["scenarios"][1]["instructions"])
        self.assertTrue(any("upload COPY" in operation for operation in
                            manifest["lanes"]["qa_designer_010"]["writeAuthorization"]["operations"]))
        for account, lane in manifest["lanes"].items():
            self.assertEqual(lane["resources"]["workspaceTag"], self.tag)
            self.assertEqual(lane["resources"]["seedOwnership"]["writerCreated"], account in WRITERS)
            self.assertTrue(lane["url"].startswith("/?workspace=ws_"))
            self.assertEqual(len({s["id"] for s in lane["scenarios"]}), 5)
            self.assertTrue(all("390x844" in s["viewportContracts"]["mobile"]["instructions"] for s in lane["scenarios"]))
            self.assertEqual(lane["writeAuthorization"]["enabled"], True)

    def test_dispatch_checks_exact_design_start_state_and_reader_approval(self):
        manifest = build(self.source(), self.tag, self.workspaces())
        creator = manifest["lanes"]["qa_designer_006"]
        self.assertTrue(any(check.get("equals", {}).get("content.tables.length") == 0 for check in creator["checks"]))
        seeded = manifest["lanes"]["qa_designer_007"]
        self.assertTrue(any(check.get("equals", {}).get("fingerprint") == "b" * 64 for check in seeded["checks"]))
        writer = manifest["lanes"]["qa_designer_011"]
        self.assertTrue(any(check.get("equals", {}).get("catalog.tables.length") == 0 for check in writer["checks"]))
        chat = manifest["lanes"]["qa_designer_001"]
        self.assertTrue(any("approve read-only SELECT" in operation for operation in chat["writeAuthorization"]["operations"]))
        self.assertTrue(any(check.get("equals", {}).get("capabilities.sqlWriteExecute") is False for check in chat["checks"]))
        self.assertIn("page starts 1,101,201,301,401,501", creator["resources"]["oracle"])

    def test_preseed_workspace_hash_and_table_contract(self):
        ws = self.workspaces()
        ws["preseed"]["qa_designer_011"]["workspaceId"] = ws["lanes"]["qa_designer_011"]["readerWorkspaceId"]
        with self.assertRaisesRegex(ValueError, "preseed"):
            build(self.source(), self.tag, ws)
        ws = self.workspaces()
        ws["preseed"]["qa_designer_007"]["designHash"] = "bad"
        with self.assertRaisesRegex(ValueError, "preseed"):
            build(self.source(), self.tag, ws)
        ws = self.workspaces()
        ws["preseed"]["qa_designer_005"]["tables"] = ["qa_projects"]
        with self.assertRaisesRegex(ValueError, "preseed"):
            build(self.source(), self.tag, ws)

    def test_chat_grants_and_workspace_binding(self):
        source = self.source()
        del source["lanes"]["qa_designer_001"]["chatProvider"]
        with self.assertRaisesRegex(ValueError, "chat policy"):
            build(source, self.tag, self.workspaces())
        ws = self.workspaces()
        ws["chat"]["qa_designer_001"]["chats"]["design"]["workspaceId"] = ws["lanes"]["qa_designer_001"]["readerWorkspaceId"]
        with self.assertRaisesRegex(ValueError, "design chat workspace"):
            build(self.source(), self.tag, ws)
        ws = self.workspaces()
        ws["chat"]["qa_designer_001"]["chats"]["design"]["id"] = ws["chat"]["qa_designer_001"]["chats"]["reader"]["id"]
        with self.assertRaisesRegex(ValueError, "must differ"):
            build(self.source(), self.tag, ws)

    def test_writer_profile_and_workspace_ownership_required(self):
        source = self.source()
        source["lanes"]["qa_designer_010"]["resources"]["writableSchema"] = "public"
        with self.assertRaisesRegex(ValueError, "writer schema"):
            build(source, self.tag, self.workspaces())
        ws = self.workspaces()
        del ws["lanes"]["qa_designer_004"]["writerCreated"]
        with self.assertRaisesRegex(ValueError, "creation ownership"):
            build(self.source(), self.tag, ws)

    def test_exact_ten_unique_workspace_ids_and_tag(self):
        ws = self.workspaces()
        del ws["lanes"]["qa_designer_002"]
        with self.assertRaisesRegex(ValueError, "exact ten accounts"):
            build(self.source(), self.tag, ws)
        ws = self.workspaces()
        ws["lanes"]["qa_designer_009"]["readerWorkspaceId"] = ws["lanes"]["qa_designer_006"]["readerWorkspaceId"]
        with self.assertRaisesRegex(ValueError, "collide"):
            build(self.source(), self.tag, ws)
        with self.assertRaisesRegex(ValueError, "matching tag"):
            build(self.source(), "other", self.workspaces())

    def test_cli_writes_new_private_manifest_without_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, workspace_path, output_path = (root / name for name in
                                                        ("source.json", "workspaces.json", "manifest.json"))
            source_path.write_text(json.dumps(self.source()))
            workspace_path.write_text(json.dumps(self.workspaces()))
            argv = ["schemii_followup", "--fixtures", str(source_path),
                    "--workspace-fixtures", str(workspace_path), "--tag", self.tag,
                    "--output", str(output_path)]
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                main()
            self.assertEqual(os.stat(output_path).st_mode & 0o777, 0o600)
            written = json.loads(output_path.read_text())
            self.assertEqual(len(written["lanes"]), 10)
            self.assertNotIn("never-copy-this", output_path.read_text())
            with patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main()


if __name__ == "__main__":
    unittest.main()
