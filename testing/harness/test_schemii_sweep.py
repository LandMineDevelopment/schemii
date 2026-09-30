import unittest
import copy

from testing.harness.schemii_sweep import ACCOUNTS, CHAT, MISSIONS, WRITERS, build, build_pilot


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

    def test_native_pilot_has_two_owned_writers_and_explicit_mobile_readback(self):
        source = self.fixture()
        source["lanes"]["qa_designer_005"]["resources"]["password"] = "private-not-copied"
        manifest = build_pilot(source, "sweep1", self.workspaces())
        self.assertEqual(list(manifest["lanes"]), ["qa_designer_005", "qa_designer_006"])
        import json
        self.assertNotIn("private-not-copied", json.dumps(manifest))
        self.assertEqual(sum(len(lane["scenarios"]) for lane in manifest["lanes"].values()), 6)
        ids = set()
        for username, lane in manifest["lanes"].items():
            ids.add(lane["resources"]["localWorkspaceId"])
            self.assertTrue(any(check.get("equals", {}).get("user.username") == username for check in lane["checks"]))
            self.assertTrue(any(check.get("equals", {}).get("content.tables.length") == 0 for check in lane["checks"]))
            self.assertTrue(any(check.get("status") == 404 for check in lane["checks"]))
            self.assertTrue(any("upload" in operation for operation in lane["writeAuthorization"]["operations"]))
            for scenario in lane["scenarios"]:
                self.assertEqual(scenario["viewportContracts"]["mobile"]["mode"], "readback")
                self.assertTrue(scenario["viewportContracts"]["mobile"]["dependsOnDesktop"])
                self.assertIn("not independent mobile creation", scenario["viewportContracts"]["mobile"]["instructions"])
        self.assertEqual(len(ids), 2)

    def test_pilot_refuses_unowned_starting_state_or_missing_effective_grant(self):
        workspaces = self.workspaces()
        workspaces["lanes"]["qa_designer_005"]["localCreated"] = False
        with self.assertRaisesRegex(ValueError, "fresh owned"):
            build_pilot(self.fixture(), "sweep1", workspaces)
        source = self.fixture()
        source["lanes"]["qa_designer_006"]["expectedCapabilities"] = []
        with self.assertRaisesRegex(ValueError, "effective designer grants"):
            build_pilot(source, "sweep1", self.workspaces())

    def test_pilot_accepts_fresh_tester_accounts_and_a_separate_owned_reviewer(self):
        source, workspaces = self.fixture(), self.workspaces()
        for old, new in zip(("qa_designer_005", "qa_designer_006", "qa_designer_007"),
                            ("qa_designer_012", "qa_designer_013", "qa_designer_014"), strict=True):
            source["lanes"][new] = copy.deepcopy(source["lanes"][old])
            source["lanes"][new]["resources"]["schema"] = new
            workspaces["lanes"][new] = copy.deepcopy(workspaces["lanes"][old])
            workspaces["lanes"][new]["prefix"] = "qa_sweep1_" + new[-3:] + "_"
        manifest = build_pilot(source, "sweep1", workspaces,
                               ("qa_designer_012", "qa_designer_013"), "qa_designer_014")
        self.assertEqual(list(manifest["lanes"]), ["qa_designer_012", "qa_designer_013", "qa_designer_014"])
        self.assertTrue(manifest["lanes"]["qa_designer_014"]["independentReviewer"])
        self.assertFalse(manifest["lanes"]["qa_designer_012"]["independentReviewer"])
        self.assertEqual(len({lane["resources"]["localWorkspaceId"] for lane in manifest["lanes"].values()}), 3)
        with self.assertRaisesRegex(ValueError, "optional separate reviewer"):
            build_pilot(source, "sweep1", workspaces,
                        ("qa_designer_012", "qa_designer_013"), "qa_designer_013")


if __name__ == "__main__":
    unittest.main()
