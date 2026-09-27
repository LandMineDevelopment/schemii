import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from testing import provision


class FixtureUser:
    def __init__(self, slot):
        self.slot = slot
        self.models = {}
        self.dashboards = {}
        self.calls = []
        self.capabilities = ["schemoo:access", "schemer:access", "schemer:author"]
        self.missing_details = set()
        self.revoke_capabilities_on_missing_detail = False
        self.crash_after_dashboard_delete = False

    def call(self, method, path, payload=None, expected=200):
        route = path.split("?", 1)[0]
        self.calls.append((method, path))
        if method == "GET" and route == "/api/v1/schemoo/catalog":
            return {"tables": [{"name": "orders"}]}
        if method == "GET" and route == "/api/v1/auth/me":
            return {"user": {"id": self.slot["accountId"]}, "capabilities": list(self.capabilities)}
        if method == "POST" and route.startswith("/api/v1/connections/"):
            return {}
        if method == "GET" and route == "/api/v1/schemoo/models":
            return {"models": list(self.models.values())}
        if method == "POST" and route == "/api/v1/schemoo/models":
            model_id = "model_" + "1" * 32
            result = {
                "id": model_id,
                "ownerId": self.slot["accountId"],
                "name": payload["name"],
                "connectionId": payload["connectionId"],
                "namespace": payload["namespace"],
                "definition": payload["definition"],
                "revision": 1,
            }
            self.models[model_id] = result
            return result
        if method == "GET" and route.startswith("/api/v1/schemoo/models/"):
            resource_id = route.rsplit("/", 1)[1]
            if ("model", resource_id) in self.missing_details or resource_id not in self.models:
                if self.revoke_capabilities_on_missing_detail:
                    self.capabilities = []
                raise provision.QAApiError(404, "model_source_not_found", "missing")
            return self.models[resource_id]
        if method == "GET" and route == "/api/v1/schemer/dashboards":
            return {"dashboards": list(self.dashboards.values())}
        if method == "POST" and route == "/api/v1/schemer/dashboards":
            dashboard_id = "dashboard_" + "2" * 32
            result = {
                "id": dashboard_id,
                "ownerId": self.slot["accountId"],
                "name": payload["name"],
                "modelId": payload["modelId"],
                "revision": 1,
                **payload,
            }
            self.dashboards[dashboard_id] = result
            return result
        if method == "GET" and route.startswith("/api/v1/schemer/dashboards/"):
            resource_id = route.rsplit("/", 1)[1]
            if ("dashboard", resource_id) in self.missing_details or resource_id not in self.dashboards:
                if self.revoke_capabilities_on_missing_detail:
                    self.capabilities = []
                raise provision.QAApiError(404, "dashboard_not_found", "missing")
            return self.dashboards[resource_id]
        if method == "DELETE" and route.startswith("/api/v1/schemer/dashboards/"):
            del self.dashboards[route.rsplit("/", 1)[1]]
            if self.crash_after_dashboard_delete:
                self.crash_after_dashboard_delete = False
                raise RuntimeError("simulated process interruption after dashboard DELETE")
            return None
        if method == "DELETE" and route.startswith("/api/v1/schemoo/models/"):
            del self.models[route.rsplit("/", 1)[1]]
            return None
        if expected >= 400:
            return None
        raise AssertionError(f"Unexpected fixture request: {method} {path}")

    def login(self, credentials):
        self.calls.append(("LOGIN", credentials["username"]))
        return {
            "user": {"id": self.slot["accountId"]},
            "capabilities": list(self.capabilities),
            "is_admin": False,
        }

    def logout(self):
        self.calls.append(("LOGOUT", ""))


class FixtureAdmin:
    def __init__(self, account, profile):
        self.account = account
        self.profile = profile
        self.calls = []

    def login(self, credentials):
        return {"is_admin": True}

    def logout(self):
        self.calls.append(("LOGOUT", ""))

    def call(self, method, path, payload=None, expected=200):
        self.calls.append((method, path, payload))
        route = path.split("?", 1)[0]
        if method == "GET" and route == "/api/v1/admin/accounts":
            return [self.account]
        if method == "GET" and route == "/api/v1/admin/schemii-connections":
            return {"connections": [self.profile]}
        if (
            method == "POST"
            and route == f"/api/v1/admin/schemii-connections/{self.profile['id']}/test"
        ):
            return {}
        if (
            method == "PATCH"
            and route == f"/api/v1/admin/accounts/{self.account['id']}"
        ):
            self.account["direct_access"] = payload["direct_access"]
            self.account["effective_capabilities"] = sorted(
                payload["direct_access"]["capabilities"]
            )
            return self.account
        raise AssertionError(f"Unexpected administrator request: {method} {path}")


class ReportAuthorFixturesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.slot = {
            "username": "qa_report_author_001",
            "persona": "report_author",
            "schema": "qa_report_author_001",
            "password": "a" * 64,
            "dbPassword": "b" * 64,
            "accountId": "usr_" + "3" * 32,
            "connectionId": "pg_" + "4" * 32,
            "provisioned": True,
        }
        self.registry = {"version": 1, "fixtureVersion": "qa-v2", "slots": [self.slot]}
        provision.write_private(self.directory / "registry.json", self.registry)

    def tearDown(self):
        self.temp.cleanup()

    def test_api_error_summary_keeps_only_redacted_envelope_fields(self):
        envelope = json.dumps(
            {
                "error": {
                    "code": "model_source_not_found",
                    "message": "Authorization: bearer secret-value; detail",
                },
                "accessToken": "must-not-appear",
                "details": {"ownerId": "private-id"},
            }
        ).encode()
        summary = provision.safe_api_error_summary(envelope)
        self.assertIn("code=model_source_not_found", summary)
        self.assertIn("Authorization=[redacted]", summary)
        self.assertNotIn("secret-value", summary)
        self.assertNotIn("must-not-appear", summary)
        self.assertNotIn("private-id", summary)

        client = provision.Client()
        response = MagicMock()
        response.status = 404
        response.read.return_value = envelope
        response.__enter__.return_value = response
        client.opener = MagicMock()
        client.opener.open.return_value = response
        with self.assertRaises(provision.QAApiError) as caught:
            client.call("GET", "/api/v1/schemoo/models/model_" + "1" * 32)
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(caught.exception.code, "model_source_not_found")
        self.assertNotIn("secret-value", str(caught.exception))

    def test_qa_v1_registry_migration_preserves_retained_ids_and_credentials(self):
        old_slot = {
            key: value for key, value in self.slot.items() if key != "provisioned"
        }
        legacy = {"version": 1, "fixtureVersion": "qa-v1", "slots": [old_slot]}
        loaded = provision.validate_registry(legacy)
        self.assertEqual(loaded["fixtureVersion"], "qa-v2")
        self.assertEqual(loaded["slots"][0]["accountId"], self.slot["accountId"])
        self.assertEqual(loaded["slots"][0]["connectionId"], self.slot["connectionId"])
        self.assertEqual(loaded["slots"][0]["password"], self.slot["password"])
        self.assertEqual(loaded["slots"][0]["dbPassword"], self.slot["dbPassword"])

    def test_setup_creates_one_owned_model_and_dashboard_then_verifies_them(self):
        user = FixtureUser(self.slot)
        first = provision.ensure_report_author_fixture(
            self.directory, self.registry, self.slot, user
        )
        second = provision.ensure_report_author_fixture(
            self.directory, self.registry, self.slot, user
        )

        self.assertEqual(first, {"models": 1, "dashboards": 1})
        self.assertEqual(second, {"models": 0, "dashboards": 0})
        self.assertEqual(
            sum(
                method == "POST" and path == "/api/v1/schemoo/models"
                for method, path in user.calls
            ),
            1,
        )
        self.assertEqual(
            sum(
                method == "POST" and path == "/api/v1/schemer/dashboards"
                for method, path in user.calls
            ),
            1,
        )
        fixture = self.slot["reportAuthorFixture"]
        self.assertRegex(fixture["modelId"], r"^model_[0-9a-f]{32}$")
        self.assertRegex(fixture["dashboardId"], r"^dashboard_[0-9a-f]{32}$")
        stored = json.loads((self.directory / "registry.json").read_text())["slots"][0]
        self.assertEqual(stored["reportAuthorFixture"], fixture)

        provision.derived_files(self.directory, self.registry)
        lane = json.loads((self.directory / "fixtures.json").read_text())["lanes"][
            self.slot["username"]
        ]
        self.assertIn("schemoo:access", lane["expectedCapabilities"])
        self.assertEqual(lane["resources"]["schemooModelId"], fixture["modelId"])
        self.assertEqual(
            lane["resources"]["schemerDashboardId"], fixture["dashboardId"]
        )
        self.assertTrue(
            any(
                check["path"].endswith(fixture["modelId"]) and check["status"] == 200
                for check in lane["checks"]
            )
        )
        self.assertTrue(
            any(
                check["path"].endswith(fixture["dashboardId"])
                and check["status"] == 200
                for check in lane["checks"]
            )
        )
        self.assertEqual(lane["url"], "/schemer")
        self.assertIn(
            "Do not edit or delete the retained starter fixtures",
            lane["scenarios"][0]["instructions"],
        )

    def test_provision_migrates_only_the_known_legacy_report_author_grant(self):
        grant = {
            "connection_id": self.slot["connectionId"],
            "owner_id": provision.POOL,
            "allow_authoring": True,
        }
        legacy = {
            "capabilities": ["schemer:access", "schemer:author"],
            "connections": [grant],
            "dashboards": [],
        }
        account = {
            "id": self.slot["accountId"],
            "username": self.slot["username"],
            "is_admin": False,
            "disabled": False,
            "role_ids": [],
            "direct_access": legacy,
            "effective_capabilities": ["schemer:access", "schemer:author"],
        }
        profile = {
            "id": self.slot["connectionId"],
            "name": "QA " + self.slot["username"],
            "host": "qa-postgres",
            "port": 5432,
            "database": "schemii_qa",
            "username": self.slot["username"],
            "sslMode": "disable",
            "connectTimeout": 10,
            "ownerId": provision.POOL,
            "credentialStored": True,
        }
        admin = FixtureAdmin(account, profile)
        user = FixtureUser(self.slot)
        admin_path = self.directory / "admin.json"
        provision.write_private(
            admin_path, {"username": "qa-admin", "password": "secret"}
        )

        with patch.object(provision, "Client", side_effect=[admin, user]):
            result = provision.provision(
                self.directory, admin_path, {self.slot["username"]}
            )

        expected_access = {
            "capabilities": provision.PERSONAS["report_author"]["capabilities"],
            "connections": [grant],
            "dashboards": [],
        }
        self.assertEqual(result["updatedAccounts"], 1)
        self.assertEqual(result["createdModels"], 1)
        self.assertEqual(result["createdDashboards"], 1)
        self.assertEqual(account["direct_access"], expected_access)
        self.assertTrue(self.slot["provisioned"])
        self.assertEqual(self.slot["password"], "a" * 64)
        self.assertEqual(self.slot["dbPassword"], "b" * 64)
        self.assertEqual(self.slot["connectionId"], profile["id"])

    def test_cleanup_deletes_only_recorded_dashboard_then_model(self):
        user = FixtureUser(self.slot)
        provision.ensure_report_author_fixture(
            self.directory, self.registry, self.slot, user
        )
        user.calls.clear()

        with patch.object(provision, "Client", return_value=user):
            result = provision.cleanup_report_author_fixtures(
                self.directory, [self.slot["username"]]
            )

        self.assertEqual(result["removedDashboards"], 1)
        self.assertEqual(result["removedModels"], 1)
        deletes = [(method, path) for method, path in user.calls if method == "DELETE"]
        self.assertEqual(len(deletes), 2)
        self.assertIn("/api/v1/schemer/dashboards/", deletes[0][1])
        self.assertIn("/api/v1/schemoo/models/", deletes[1][1])
        stored = json.loads((self.directory / "registry.json").read_text())["slots"][0]
        self.assertNotIn("reportAuthorFixture", stored)
        self.assertFalse(stored["provisioned"])
        self.assertEqual(stored["password"], self.slot["password"])
        self.assertEqual(stored["connectionId"], self.slot["connectionId"])
        lane = json.loads((self.directory / "fixtures.json").read_text())["lanes"][
            self.slot["username"]
        ]
        self.assertNotIn("schemooModelId", lane["resources"])
        self.assertNotIn("schemerDashboardId", lane["resources"])

    def test_cleanup_retry_reconciles_a_delete_completed_before_registry_write(self):
        user = FixtureUser(self.slot)
        provision.ensure_report_author_fixture(
            self.directory, self.registry, self.slot, user
        )
        dashboard_id = self.slot["reportAuthorFixture"]["dashboardId"]
        user.crash_after_dashboard_delete = True

        with patch.object(provision, "Client", return_value=user):
            with self.assertRaisesRegex(RuntimeError, "simulated process interruption"):
                provision.cleanup_report_author_fixtures(
                    self.directory, [self.slot["username"]]
                )

        retained = json.loads((self.directory / "registry.json").read_text())["slots"][0]
        fixture = retained["reportAuthorFixture"]
        self.assertNotIn(dashboard_id, user.dashboards)
        self.assertEqual(fixture["dashboardId"], dashboard_id)
        self.assertEqual(fixture["cleanupPending"], {"kind": "dashboard", "id": dashboard_id})

        with patch.object(provision, "Client", return_value=user):
            result = provision.cleanup_report_author_fixtures(
                self.directory, [self.slot["username"]]
            )

        self.assertEqual(result["removedDashboards"], 1)
        self.assertEqual(result["removedModels"], 1)
        self.assertFalse(user.dashboards)
        self.assertFalse(user.models)
        retained = json.loads((self.directory / "registry.json").read_text())["slots"][0]
        self.assertNotIn("reportAuthorFixture", retained)

    def test_cleanup_preserves_dashboard_ledger_when_404_object_is_still_listed(self):
        user = FixtureUser(self.slot)
        provision.ensure_report_author_fixture(
            self.directory, self.registry, self.slot, user
        )
        dashboard_id = self.slot["reportAuthorFixture"]["dashboardId"]
        user.missing_details.add(("dashboard", dashboard_id))

        with patch.object(provision, "Client", return_value=user):
            with self.assertRaisesRegex(ValueError, "still appears in the owner list"):
                provision.cleanup_report_author_fixtures(
                    self.directory, [self.slot["username"]]
                )

        retained = json.loads((self.directory / "registry.json").read_text())["slots"][0]
        self.assertEqual(retained["reportAuthorFixture"]["dashboardId"], dashboard_id)
        self.assertNotIn("cleanupPending", retained["reportAuthorFixture"])
        self.assertIn(dashboard_id, user.dashboards)
        self.assertIn(self.slot["reportAuthorFixture"]["modelId"], user.models)

    def test_cleanup_preserves_model_ledger_when_source_404_is_ambiguous(self):
        user = FixtureUser(self.slot)
        provision.ensure_report_author_fixture(
            self.directory, self.registry, self.slot, user
        )
        model_id = self.slot["reportAuthorFixture"]["modelId"]
        user.missing_details.add(("model", model_id))

        with patch.object(provision, "Client", return_value=user):
            with self.assertRaisesRegex(ValueError, "still appears in the owner list"):
                provision.cleanup_report_author_fixtures(
                    self.directory, [self.slot["username"]]
                )

        retained = json.loads((self.directory / "registry.json").read_text())["slots"][0]
        self.assertEqual(retained["reportAuthorFixture"]["modelId"], model_id)
        self.assertNotIn("dashboardId", retained["reportAuthorFixture"])
        self.assertNotIn("cleanupPending", retained["reportAuthorFixture"])
        self.assertIn(model_id, user.models)

    def test_cleanup_keeps_ledger_when_owner_access_is_revoked_during_404_recheck(self):
        user = FixtureUser(self.slot)
        provision.ensure_report_author_fixture(
            self.directory, self.registry, self.slot, user
        )
        dashboard_id = self.slot["reportAuthorFixture"]["dashboardId"]
        user.missing_details.add(("dashboard", dashboard_id))
        user.revoke_capabilities_on_missing_detail = True

        with patch.object(provision, "Client", return_value=user):
            with self.assertRaisesRegex(ValueError, "without current owner access"):
                provision.cleanup_report_author_fixtures(
                    self.directory, [self.slot["username"]]
                )

        retained = json.loads((self.directory / "registry.json").read_text())["slots"][0]
        self.assertEqual(retained["reportAuthorFixture"]["dashboardId"], dashboard_id)
        self.assertIn(dashboard_id, user.dashboards)

    def test_cleanup_refuses_unregistered_accounts_before_api_mutation(self):
        with self.assertRaisesRegex(ValueError, "registered report-author"):
            provision.cleanup_report_author_fixtures(
                self.directory, ["qa_report_author_002"]
            )


if __name__ == "__main__":
    unittest.main()
