import unittest

from testing.harness.schemii_sweep import ACCOUNTS, CHAT, MISSIONS, WRITERS, build


class SchemiiSweepTest(unittest.TestCase):
    def fixture(self):
        lanes = {}
        for account in ACCOUNTS:
            resources = {"schema": account, "connectionId": "pg_" + "0" * 32}
            if account in WRITERS:
                resources.update(writableConnectionId="pg_" + "1" * 32,
                                 writableSchema="qa_write_designer_" + account[-3:])
            lanes[account] = {"persona": "designer", "resources": resources,
                              "expectedCapabilities": ["schemii:access"], "deniedProducts": [],
                              "checks": [{"path": "/api/v1/auth/me", "status": 200}]}
            if account in CHAT:
                lanes[account]["chatProvider"] = {"providerId": "instance-codex", "modelId": "gpt-6-luna"}
        return {"lanes": lanes}

    def workspaces(self):
        return {"tag": "sweep1", "lanes": {
            account: {"prefix": "qa_sweep1_" + account[-3:] + "_",
                      "localWorkspaceId": "ws_" + format(index * 3 + 1, "032x"),
                      "readerWorkspaceId": "ws_" + format(index * 3 + 2, "032x"),
                      "writerWorkspaceId": "ws_" + format(index * 3 + 3, "032x") if account in WRITERS else None,
                      "localCreated": True, "readerCreated": True, "writerCreated": account in WRITERS}
            for index, account in enumerate(ACCOUNTS)
        }}

    def test_ten_owned_prescriptive_missions(self):
        manifest = build(self.fixture(), "sweep1", self.workspaces())
        self.assertEqual(list(manifest["lanes"]), list(ACCOUNTS))
        self.assertEqual(len(MISSIONS), 10)
        self.assertEqual(sum(len(lane["scenarios"]) for lane in manifest["lanes"].values()), 60)
        self.assertTrue(all(len(lane["scenarios"]) == 6 for lane in manifest["lanes"].values()))
        self.assertTrue(all(lane["writeAuthorization"]["resources"] for lane in manifest["lanes"].values()))
        self.assertTrue(all("orders: id bigint" in lane["resources"]["oracle"] for lane in manifest["lanes"].values()))
        self.assertTrue(all(lane["resources"]["workspaceTag"] == "sweep1" for lane in manifest["lanes"].values()))
        self.assertTrue(all(lane["resources"]["seedOwnership"]["localCreated"] for lane in manifest["lanes"].values()))
        self.assertIn("chat", " ".join(s["id"] for s in manifest["lanes"]["qa_designer_001"]["scenarios"]))

    def test_missing_chat_or_writer_prerequisite_fails_before_dispatch(self):
        source = self.fixture()
        del source["lanes"]["qa_designer_001"]["chatProvider"]
        with self.assertRaisesRegex(ValueError, "Missing active chat policy"):
            build(source, "sweep1", self.workspaces())
        source = self.fixture()
        del source["lanes"]["qa_designer_010"]["resources"]["writableConnectionId"]
        with self.assertRaisesRegex(ValueError, "Missing exact writable profile"):
            build(source, "sweep1", self.workspaces())

    def test_missing_workspace_prerequisite_fails_before_dispatch(self):
        source = self.workspaces()
        del source["lanes"]["qa_designer_001"]["readerWorkspaceId"]
        with self.assertRaisesRegex(ValueError, "Missing exact pre-opened"):
            build(self.fixture(), "sweep1", source)


if __name__ == "__main__":
    unittest.main()
