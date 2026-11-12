"""Offline supported-API pairing and dependency protection; no live fixtures."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from testing import provision
from testing.test_report_author_fixtures import FixtureAdmin, FixtureUser


class ViewerUser:
    def __init__(self, slot, dashboard):
        self.slot, self.dashboard = slot, dashboard
        self.calls = []
        self.capabilities = ["schemer:access"]

    def login(self, credentials):
        self.calls.append(("LOGIN", credentials["username"]))
        return {
            "user": {"id": self.slot["accountId"]},
            "capabilities": self.capabilities,
            "is_admin": False,
        }

    def logout(self):
        self.calls.append(("LOGOUT", ""))

    def call(self, method, path, payload=None, expected=200):
        self.calls.append((method, path))
        if method == "GET" and path.endswith(self.dashboard["id"]):
            return None if expected == 404 else self.dashboard
        if path in ("/api/v1/schemoo/models", "/api/v1/schemii/workspaces"):
            assert expected == 403
            return None
        raise AssertionError((method, path, expected))


class ReportViewerFixturesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.author = {
            "username": "qa_report_author_001",
            "persona": "report_author",
            "schema": "qa_report_author_001",
            "password": "a" * 64,
            "dbPassword": "b" * 64,
            "accountId": "usr_author",
            "connectionId": "pg_" + "4" * 32,
            "provisioned": True,
        }
        self.viewer = {
            "username": "qa_report_viewer_001",
            "persona": "report_viewer",
            "schema": "qa_report_viewer_001",
            "password": "c" * 64,
            "dbPassword": "d" * 64,
            "accountId": "usr_viewer",
            "provisioned": True,
        }
        self.registry = {
            "version": 1,
            "fixtureVersion": "qa-v2",
            "slots": [self.author, self.viewer],
        }
        self.owner = FixtureUser(self.author)
        provision.ensure_report_author_fixture(
            self.directory, self.registry, self.author, self.owner
        )
        self.dashboard = next(iter(self.owner.dashboards.values()))
        self.reader = ViewerUser(self.viewer, self.dashboard)
        self.profile = {
            "id": self.author["connectionId"],
            "ownerId": provision.POOL,
            "host": "qa-postgres",
            "database": "schemii_qa",
            "username": self.author["schema"],
            "credentialStored": True,
        }
        self.account = {
            "id": self.viewer["accountId"],
            "username": self.viewer["username"],
            "is_admin": False,
            "disabled": False,
            "role_ids": [],
            "effective_capabilities": ["schemer:access"],
            "direct_access": {
                "capabilities": ["schemer:access"],
                "connections": [],
                "dashboards": [],
            },
        }
        self.admin = FixtureAdmin(self.account, self.profile)
        self.admin_file = self.directory / "admin.json"
        provision.write_private(
            self.admin_file, {"username": "admin", "password": "private"}
        )

    def pair(self):
        with patch.object(
            provision, "Client", side_effect=[self.admin, self.owner, self.reader]
        ):
            return provision.provision_viewer(
                self.directory,
                self.admin_file,
                self.viewer["username"],
                self.author["username"],
            )

    def test_explicit_pairing_same_role_grants_preserves_credentials_and_is_idempotent(
        self,
    ):
        before = deepcopy(self.viewer)
        self.assertTrue(self.pair()["verified"])
        stored = provision.registry_load(self.directory)["slots"][1]
        self.assertEqual(stored["password"], before["password"])
        self.assertEqual(stored["dbPassword"], before["dbPassword"])
        access = self.account["direct_access"]
        self.assertEqual(access["capabilities"], ["schemer:access"])
        self.assertFalse(access["connections"][0]["allow_authoring"])
        self.assertEqual(
            access["connections"][0]["connection_id"],
            access["dashboards"][0]["connection_id"],
        )
        self.assertEqual(access["dashboards"][0]["owner_id"], self.author["accountId"])
        patches = len([call for call in self.admin.calls if call[0] == "PATCH"])
        self.assertTrue(self.pair()["verified"])
        self.assertEqual(
            len([call for call in self.admin.calls if call[0] == "PATCH"]), patches
        )
        manifest = json.loads((self.directory / "fixtures.json").read_text())["lanes"][
            self.viewer["username"]
        ]
        self.assertEqual(manifest["resources"]["schema"], self.viewer["schema"])
        self.assertEqual(
            manifest["resources"]["sharedNamespace"], self.author["schema"]
        )
        self.assertFalse(manifest["writeAuthorization"]["enabled"])
        self.assertTrue(manifest["scenarios"])

    def test_unrelated_grant_and_source_profile_drift_refuse_before_patch(self):
        for key in ("access", "profile"):
            with self.subTest(key=key):
                account, profile = deepcopy(self.account), deepcopy(self.profile)
                if key == "access":
                    account["direct_access"]["capabilities"].append("schemer:author")
                else:
                    profile["username"] = self.viewer["schema"]
                admin = FixtureAdmin(account, profile)
                with (
                    patch.object(provision, "Client", return_value=admin),
                    self.assertRaisesRegex(ValueError, "drift|unrelated"),
                ):
                    provision.provision_viewer(
                        self.directory,
                        self.admin_file,
                        self.viewer["username"],
                        self.author["username"],
                    )
                self.assertFalse(any(call[0] == "PATCH" for call in admin.calls))

    def test_failed_effective_verification_preserves_pairing_and_protects_author_cleanup(
        self,
    ):
        self.reader.capabilities = ["schemer:access", "schemer:author"]
        with self.assertRaisesRegex(ValueError, "capabilities drift"):
            self.pair()
        stored = provision.registry_load(self.directory)
        self.assertFalse(stored["slots"][1]["provisioned"])
        self.assertIn("reportViewerFixture", stored["slots"][1])
        with (
            patch.object(provision, "Client") as client,
            self.assertRaisesRegex(ValueError, "protect"),
        ):
            provision.cleanup_report_author_fixtures(
                self.directory, [self.author["username"]]
            )
        client.assert_not_called()
        self.assertEqual(len(self.owner.dashboards), 1)

    def test_explicit_unpair_reconciles_exact_grants_before_releasing_author_dependency(
        self,
    ):
        self.pair()
        with patch.object(provision, "Client", side_effect=[self.admin, self.reader]):
            result = provision.unpair_viewer(
                self.directory,
                self.admin_file,
                self.viewer["username"],
                self.author["username"],
            )
        self.assertTrue(result["unpaired"])
        stored = provision.registry_load(self.directory)["slots"][1]
        self.assertNotIn("reportViewerFixture", stored)
        self.assertEqual(stored["password"], self.viewer["password"])
        self.assertEqual(
            self.account["direct_access"],
            {"capabilities": ["schemer:access"], "connections": [], "dashboards": []},
        )
        with patch.object(provision, "Client") as client:
            self.assertFalse(
                provision.unpair_viewer(
                    self.directory,
                    self.admin_file,
                    self.viewer["username"],
                    self.author["username"],
                )["changed"]
            )
            client.assert_not_called()

    def test_pairing_cannot_adopt_changed_author_or_model(self):
        self.pair()
        stored = provision.registry_load(self.directory)
        stored["slots"][0]["reportAuthorFixture"]["modelId"] = "model_" + "8" * 32
        with self.assertRaisesRegex(ValueError, "pairing drift"):
            provision.validate_registry(stored)


if __name__ == "__main__":
    unittest.main()
