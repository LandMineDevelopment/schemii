"""Offline supported-API pairing and dependency protection; no live fixtures."""

import contextlib
import fcntl
import io
import json
import os
import sys
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

    def pair(self, *, expected=None):
        with patch.object(
            provision, "Client", side_effect=[self.admin, self.owner, self.reader]
        ):
            return provision.provision_viewer(
                self.directory,
                self.admin_file,
                self.viewer["username"],
                self.author["username"],
                expected=expected,
            )

    def expectation(self, operation):
        author, viewer = provision.registry_load(self.directory)["slots"]
        return {
            "schemaVersion": 1,
            "operation": operation,
            "viewer": {
                key: viewer.get(key)
                for key in (
                    "username",
                    "accountId",
                    "provisioned",
                    "reportViewerFixture",
                )
            },
            "author": {
                key: author.get(key)
                for key in (
                    "username",
                    "accountId",
                    "connectionId",
                    "schema",
                    "reportAuthorFixture",
                )
            },
        }

    def assert_expected_refusal(self, operation, expected):
        before = (self.directory / "registry.json").read_bytes()
        function = (
            provision.provision_viewer
            if operation == "pair"
            else provision.unpair_viewer
        )
        with (
            patch.object(provision, "Client") as client,
            self.assertRaises(ValueError) as raised,
        ):
            function(
                self.directory,
                self.admin_file,
                self.viewer["username"],
                self.author["username"],
                expected=expected,
            )
        client.assert_not_called()
        self.assertEqual((self.directory / "registry.json").read_bytes(), before)
        for value in (
            "private-resource-sentinel",
            self.viewer["accountId"],
            self.author["accountId"],
        ):
            self.assertNotIn(value, str(raised.exception))

    def test_expected_pair_refuses_author_and_viewer_drift_before_client(self):
        expected = self.expectation("pair")
        original = provision.registry_load(self.directory)
        cases = [
            (0, "accountId", "private-resource-sentinel"),
            (0, "connectionId", "pg_" + "8" * 32),
            (
                0,
                "reportAuthorFixture",
                {"modelId": "model_" + "8" * 32, "dashboardId": self.dashboard["id"]},
            ),
            (
                0,
                "reportAuthorFixture",
                {
                    "modelId": self.author["reportAuthorFixture"]["modelId"],
                    "dashboardId": "dashboard_" + "8" * 32,
                },
            ),
            (1, "accountId", "private-resource-sentinel"),
            (1, "provisioned", False),
            (1, "provisioned", 1),
        ]
        for index, key, value in cases:
            with self.subTest(slot=index, key=key, value_type=type(value).__name__):
                changed = deepcopy(original)
                changed["slots"][index][key] = value
                provision.write_private(self.directory / "registry.json", changed)
                self.assert_expected_refusal("pair", expected)
        provision.write_private(self.directory / "registry.json", original)
        self.assertTrue(self.pair(expected=expected)["verified"])
        # An already-paired current viewer no longer matches its unpaired baseline.
        self.assert_expected_refusal("pair", expected)

    def test_expected_unpair_refuses_changed_pair_receipt_before_client(self):
        self.pair()
        expected = self.expectation("unpair")
        original = provision.registry_load(self.directory)
        for field, value in (
            ("accountId", "private-resource-sentinel"),
            ("connectionId", "pg_" + "8" * 32),
            ("modelId", "model_" + "8" * 32),
            ("dashboardId", "dashboard_" + "8" * 32),
            ("viewerAccountId", "private-resource-sentinel"),
            ("viewerProvisioned", False),
            ("missingAuthor", None),
        ):
            with self.subTest(field=field):
                changed = deepcopy(original)
                author, viewer = changed["slots"]
                if field in ("modelId", "dashboardId"):
                    author["reportAuthorFixture"][field] = value
                    viewer["reportViewerFixture"][field] = value
                elif field in ("accountId", "connectionId"):
                    author[field] = value
                    viewer["reportViewerFixture"][
                        "authorAccountId" if field == "accountId" else field
                    ] = value
                elif field == "viewerAccountId":
                    viewer["accountId"] = value
                elif field == "viewerProvisioned":
                    viewer["provisioned"] = value
                else:
                    changed["slots"].remove(author)
                provision.write_private(self.directory / "registry.json", changed)
                self.assert_expected_refusal("unpair", expected)
        provision.write_private(self.directory / "registry.json", original)
        with patch.object(provision, "Client", side_effect=[self.admin, self.reader]):
            result = provision.unpair_viewer(
                self.directory,
                self.admin_file,
                self.viewer["username"],
                self.author["username"],
                expected=expected,
            )
        self.assertTrue(result["unpaired"])
        # A saved pair receipt cannot authorize an already-unpaired tuple.
        self.assert_expected_refusal("unpair", expected)

    def test_expected_schema_operation_and_identity_mismatches_refuse_before_client(
        self,
    ):
        expected = self.expectation("pair")
        cases = [
            [],
            {},
            {**expected, "schemaVersion": True},
            {**expected, "schemaVersion": 1.0},
            {**expected, "operation": "unpair"},
            {**expected, "password": "private-resource-sentinel"},
            {
                **expected,
                "viewer": {
                    **expected["viewer"],
                    "username": "private-resource-sentinel",
                },
            },
            {
                **expected,
                "author": {
                    **expected["author"],
                    "username": "private-resource-sentinel",
                },
            },
            {**expected, "viewer": {**expected["viewer"], "provisioned": 1}},
            {
                **expected,
                "viewer": {**expected["viewer"], "extra": "private-resource-sentinel"},
            },
            {
                **expected,
                "author": {**expected["author"], "extra": "private-resource-sentinel"},
            },
            {**expected, "author": {**expected["author"], "reportAuthorFixture": {}}},
            {
                **expected,
                "author": {
                    **expected["author"],
                    "reportAuthorFixture": {
                        **expected["author"]["reportAuthorFixture"],
                        "extra": True,
                    },
                },
            },
            {**expected, "viewer": {**expected["viewer"], "reportViewerFixture": {}}},
        ]
        # None is the explicit legacy opt-out; malformed JSON null is rejected by the file reader.
        for index, malformed in enumerate(cases):
            with self.subTest(case=index):
                self.assert_expected_refusal("pair", malformed)
        self.assert_expected_refusal("unpair", expected)

    def test_expected_file_is_bounded_private_regular_strict_json(self):
        path = self.directory / "expectation.json"
        expected = self.expectation("pair")
        provision.write_private(path, expected)
        self.assertEqual(provision.read_pairing_expectation(path, "pair"), expected)
        contents = [
            "{",
            "null",
            "[]",
            json.dumps({**expected, "operation": "unpair"}),
            '{"schemaVersion":1,"schemaVersion":1}',
            " " * (16 * 1024 + 1),
            b"\xff",
            "[" * 2000,
        ]
        for index, content in enumerate(contents):
            with self.subTest(case=index):
                path.write_bytes(
                    content if isinstance(content, bytes) else content.encode()
                )
                with self.assertRaisesRegex(
                    ValueError, "Invalid private pairing expectation"
                ):
                    provision.read_pairing_expectation(path, "pair")
        provision.write_private(path, expected)
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            provision.read_pairing_expectation(path, "pair")
        path.chmod(0o600)
        link = self.directory / "expectation-link.json"
        link.symlink_to(path)
        for invalid in (link, self.directory, self.directory / "missing"):
            with self.subTest(path_kind=invalid.name), self.assertRaises(ValueError):
                provision.read_pairing_expectation(invalid, "pair")
        fifo = self.directory / "expectation-fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(ValueError):
            provision.read_pairing_expectation(fifo, "pair")

    def test_main_reads_and_checks_expected_pairing_only_inside_provision_lock(self):
        read = provision.read_pairing_expectation
        check = provision.check_pairing_expectation

        def assert_locked():
            with (self.directory / ".provision.lock").open("r+") as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def locked_read(path, operation):
            assert_locked()
            return read(path, operation)

        def locked_check(*args):
            assert_locked()
            return check(*args)

        for command, operation in (("viewer", "pair"), ("unpair-viewer", "unpair")):
            with self.subTest(command=command):
                path = self.directory / "expectation.json"
                provision.write_private(path, self.expectation(operation))
                argv = [
                    "provision.py",
                    command,
                    "--account",
                    self.viewer["username"],
                    "--author",
                    self.author["username"],
                    "--admin-credentials",
                    str(self.admin_file),
                    "--state-dir",
                    str(self.directory),
                    "--expected-pairing-file",
                    str(path),
                ]
                clients = (
                    [self.admin, self.owner, self.reader]
                    if operation == "pair"
                    else [self.admin, self.reader]
                )
                with (
                    patch.object(sys, "argv", argv),
                    patch.object(
                        provision, "read_pairing_expectation", side_effect=locked_read
                    ) as reader,
                    patch.object(
                        provision, "check_pairing_expectation", side_effect=locked_check
                    ) as checker,
                    patch.object(provision, "Client", side_effect=clients),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    provision.main()
                reader.assert_called_once_with(path, operation)
                checker.assert_called_once()

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
