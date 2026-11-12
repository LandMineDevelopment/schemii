"""Focused demo readiness and preservation regressions, without a live stack."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from schemii.common.auth.routes import AccountProvision
from schemii.common.auth.service import AuthService
from schemii.common.connections.models import PostgresConnectionUpdate
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import InMemoryConnectionRepository
from schemii.common.postgres.models import (
    PostgresColumn,
    PostgresTable,
    build_postgres_catalog,
)
from schemii.schemii.designs.models import SchemiiDesignReplace
from schemii.schemii.designs.store import InMemoryDesignRepository
from schemii.schemii.migrations.repository import InMemoryMigrationRepository
from schemii.schemii.migrations.service import MigrationService
from schemii.schemii.workspaces.models import WorkspaceMetadataUpdate
from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository

SPEC = importlib.util.spec_from_file_location(
    "demo_prepare", Path(__file__).parents[1] / "dev/demo/prepare.py"
)
assert SPEC and SPEC.loader
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


@pytest.fixture
def payload():
    return {
        "credentials": {
            "username": "demo-admin",
            "password": "private-demo-password-123",
        },
        "target": {
            "host": "demo-postgres",
            "port": 5432,
            "database": "schemii_test",
            "username": "schemii",
            "password": "private-database-password-123",
            "namespace": "bookstore",
        },
        "resources": None,
    }


class Postgres:
    ok = True
    tables = ("orders", "books", "customers")

    def test_connection(self, connection):
        return SimpleNamespace(ok=self.ok, database=connection.database)

    def introspect(self, connection, namespace):
        return build_postgres_catalog(
            database=connection.database,
            namespace=namespace,
            server_version="17.1",
            server_version_num=170001,
            server_timezone="UTC",
            tables=tuple(
                PostgresTable(
                    namespace=namespace,
                    name=name,
                    kind="table",
                    is_partition=False,
                    columns=(
                        PostgresColumn(
                            name="id", ordinal=1, data_type="integer", nullable=False
                        ),
                    ),
                )
                for name in self.tables
            ),
            relationships=(),
            functions=(),
            views=(),
            materialized_views=(),
            captured_at=datetime.now(timezone.utc),
        )


@pytest.fixture
def services():
    designs = InMemoryDesignRepository()
    workspaces = InMemoryWorkspaceRepository(designs=designs)
    connections = ConnectionService(InMemoryConnectionRepository(), (workspaces,))
    postgres = Postgres()
    migrations = MigrationService(
        repository=InMemoryMigrationRepository(designs),
        connection_access=connections.for_product("schemii"),
        postgres=postgres,
        workspaces=workspaces,
        designs=designs,
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(connection_factory=None),
        designs=designs,
        workspaces=workspaces,
        connections=connections,
        postgres=postgres,
        migrations=migrations,
    )


@pytest.fixture
def auth():
    return AuthService(enabled=True, setup_token="private-setup-token")


def run(payload, services, auth):
    return prepare.provision(payload, services=services, auth=auth)


def test_fresh_setup_and_repeat_preserve_account_workspace_and_desired_edits(
    payload, services, auth
):
    first = run(payload, services, auth)
    owner, workspace_id = first["user_id"], first["workspace_id"]
    assert owner == "user_local_prototype"
    assert set(auth.capabilities(owner)) == prepare.CAPABILITIES
    original = services.workspaces.get(owner, workspace_id)
    renamed = services.workspaces.rename(
        owner,
        workspace_id,
        WorkspaceMetadataUpdate(
            expected_revision=original.revision, name="Interview canvas"
        ),
    )
    design = services.designs.get(owner, workspace_id)
    content = design.content.model_copy(deep=True)
    content.tables[0].name = "my_desired_orders"
    edited = services.designs.replace(
        owner,
        workspace_id,
        SchemiiDesignReplace(expected_design_revision=design.revision, content=content),
    )
    password_hash = auth.store.state["users"][owner]["password_hash"]
    payload["resources"] = first
    assert run(payload, services, auth) == first
    assert services.workspaces.get(owner, workspace_id) == renamed
    assert services.designs.get(owner, workspace_id) == edited
    assert auth.store.state["users"][owner]["password_hash"] == password_hash
    assert len(services.connections.list(owner)) == 1
    assert len(services.workspaces.list(owner)) == 1


def test_existing_deployment_creates_separate_demo_admin_without_touching_existing_owner(
    payload, services, auth
):
    existing = auth.create_user(
        AccountProvision(
            username="owner-admin",
            display_name="Owner",
            password="owner-private-password",
        ),
        setup_token=auth.setup_token,
    )
    before = auth.store.state["users"][existing["id"]].copy()
    result = run(payload, services, auth)
    assert result["user_id"] != existing["id"]
    assert auth.store.state["users"][existing["id"]] == before
    assert services.connections.list(existing["id"]) == []


def test_username_collision_never_resets_or_elevates_account(payload, services, auth):
    user = auth.create_user(
        AccountProvision(
            username="demo-admin",
            display_name="Existing",
            password="someone-elses-password",
        ),
        setup_token=auth.setup_token,
    )
    before = auth.store.state["users"][user["id"]].copy()
    with pytest.raises(prepare.DemoError, match="different credentials or permissions"):
        run(payload, services, auth)
    assert auth.store.state["users"][user["id"]] == before
    assert services.connections.list(user["id"]) == []


def test_revoked_product_permission_is_not_silently_restored(payload, services, auth):
    result = run(payload, services, auth)
    with auth.store.transaction(write=True) as state:
        for role in state["roles"].values():
            role["capabilities"] = [
                value for value in role["capabilities"] if value != "schemoo:access"
            ]
    with pytest.raises(prepare.DemoError, match="different credentials or permissions"):
        run(payload, services, auth)
    assert "schemoo:access" not in auth.capabilities(result["user_id"])


@pytest.mark.parametrize("changed", ["host", "password"])
def test_existing_demo_connection_is_not_retargeted_or_recredentialed(
    payload, services, auth, changed
):
    result = run(payload, services, auth)
    owner, connection_id = result["user_id"], result["connection_id"]
    profile = services.connections.get(owner, connection_id)
    # Test ownership fence independently of the normal user-facing mutation guard.
    services.connections._repository.update(
        owner,
        connection_id,
        PostgresConnectionUpdate(
            expected_revision=profile.revision,
            **(
                {"host": "other-db"}
                if changed == "host"
                else {"password": "changed-private-password"}
            ),
        ),
    )
    payload["resources"] = result
    with pytest.raises(prepare.DemoError, match="not changed|not replaced"):
        run(payload, services, auth)
    assert len(services.connections.list(owner)) == 1
    assert services.connections.get(owner, connection_id).revision == 2


@pytest.mark.parametrize("failure", ["connection", "catalog"])
def test_target_readiness_failure_cannot_create_workspace(
    payload, services, auth, failure
):
    if failure == "connection":
        services.postgres.ok = False
    else:
        services.postgres.tables = ("orders",)
    with pytest.raises(prepare.DemoError):
        run(payload, services, auth)
    assert services.workspaces.list("user_local_prototype") == []


def private_request(tmp_path):
    password = tmp_path / "target-password"
    password.write_text("target-secret\n")
    password.chmod(0o640)
    directory = tmp_path / "demo"
    value = prepare.request_payload(directory, "schemii_test", "schemii", password)
    return directory, password, value


def test_private_credentials_reused_and_secret_absent_from_public_result(tmp_path):
    directory, password, first = private_request(tmp_path)
    second = prepare.request_payload(directory, "schemii_test", "schemii", password)
    assert second == first
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / "credentials.json").stat().st_mode & 0o777 == 0o600
    assert len(first["credentials"]["password"]) >= 24


@pytest.mark.parametrize(
    "unsafe",
    ["directory-symlink", "credential-symlink", "public-credential", "bad-credential"],
)
def test_unsafe_private_state_is_rejected_without_replacing_it(tmp_path, unsafe):
    directory, password, _ = private_request(tmp_path)
    path = directory / "credentials.json"
    if unsafe == "directory-symlink":
        link = tmp_path / "link"
        link.symlink_to(directory, target_is_directory=True)
        directory = link
    elif unsafe == "credential-symlink":
        path.unlink()
        path.symlink_to(password)
    elif unsafe == "public-credential":
        path.chmod(0o644)
    else:
        path.write_text("{}")
    before = password.read_text()
    with pytest.raises(prepare.DemoError):
        prepare.request_payload(directory, "schemii_test", "schemii", password)
    assert password.read_text() == before


RESULT = {
    "user_id": "user_demo",
    "connection_id": "pg_" + "1" * 32,
    "workspace_id": "ws_" + "2" * 32,
    "database": "schemii_test",
    "namespace": "bookstore",
}


class FakeApi:
    failure = None
    calls = []

    def __init__(self, origin, *, local):
        self.origin = origin
        self.local = local

    def call(self, path, data=None):
        self.calls.append((self.origin, path))
        if path.endswith("/readiness"):
            return {"ready": True, "persistence": "durable"}
        if path.endswith("/login"):
            return {
                "is_admin": self.failure != "auth",
                "user": {"id": RESULT["user_id"]},
                "capabilities": list(prepare.CAPABILITIES),
            }
        if path.endswith("/logout"):
            return {"ok": True}
        if path.endswith("/test"):
            return {"ok": self.failure != "database", "database": RESULT["database"]}
        if "/relations?" in path:
            return {
                "workspaceId": RESULT["workspace_id"],
                "relations": [
                    {"name": name, "kind": "table", "ref": "rel_" + "a" * 20}
                    for name in (
                        ("orders",)
                        if self.failure == "catalog"
                        else ("orders", "books", "customers")
                    )
                ],
            }
        if "/rows?" in path:
            return {
                "workspaceId": RESULT["workspace_id"],
                "relationRef": "rel_" + "a" * 20,
                "rows": [] if self.failure == "rows" else [[1], [2]],
            }
        return {
            "id": RESULT["workspace_id"],
            "connectionId": RESULT["connection_id"],
            "database": RESULT["database"],
            "namespace": "bookstore",
        }


def test_verify_uses_both_origins_and_real_account_resource_checks(tmp_path):
    directory, _, payload = private_request(tmp_path)
    FakeApi.calls = []
    FakeApi.failure = None
    result = prepare.verify(
        directory,
        RESULT,
        "https://localhost:8001",
        prepare.PREVIEW_ORIGIN,
        api_factory=FakeApi,
    )
    assert len(result["checks"]) == 2
    assert len([path for _, path in FakeApi.calls if path.endswith("/test")]) == 2
    assert len([path for _, path in FakeApi.calls if "/rows?" in path]) == 2
    assert len([path for _, path in FakeApi.calls if path.endswith("/logout")]) == 2
    assert payload["credentials"]["password"] not in json.dumps(result)
    assert (directory / "login.txt").stat().st_mode & 0o777 == 0o600
    assert json.loads((directory / "resources.json").read_text()) == RESULT


@pytest.mark.parametrize("failure", ["auth", "database", "catalog", "rows"])
def test_failed_verification_does_not_publish_login_readiness(tmp_path, failure):
    directory, _, _ = private_request(tmp_path)
    FakeApi.calls = []
    FakeApi.failure = failure
    try:
        with pytest.raises(prepare.DemoError):
            prepare.verify(
                directory,
                RESULT,
                "https://localhost:8001",
                prepare.PREVIEW_ORIGIN,
                api_factory=FakeApi,
            )
        assert not (directory / "login.txt").exists()
        assert FakeApi.calls[-1][1].endswith("/logout")
    finally:
        FakeApi.failure = None


@pytest.mark.parametrize(
    "origin,local",
    [
        ("http://localhost:8001", True),
        ("https://remote.invalid", False),
        ("https://localhost:8001/path", True),
        ("https://localhost:8001@evil.invalid", True),
    ],
)
def test_https_verification_never_sends_credentials_to_another_origin(origin, local):
    with pytest.raises(prepare.DemoError):
        prepare.Api(origin, local=local)


def test_cli_failure_never_echoes_exception_secrets(monkeypatch, capsys):
    monkeypatch.setattr(prepare.sys, "stdin", io.StringIO("{}"))

    def failure(_):
        raise RuntimeError("private-exception-credential")

    monkeypatch.setattr(prepare, "provision", failure)
    assert prepare.main(["provision"]) == 1
    captured = capsys.readouterr()
    assert "private-exception-credential" not in captured.out + captured.err
    assert not captured.out


def test_owned_application_services_are_closed_after_failed_database_check(
    payload, services, auth, monkeypatch
):
    closed = []
    services.metadata.close = lambda: closed.append(True)
    services.postgres.ok = False
    app = SimpleNamespace(state=SimpleNamespace(services=services, auth=auth))
    monkeypatch.setitem(prepare.sys.modules, "schemii.main", SimpleNamespace(app=app))
    with pytest.raises(prepare.DemoError, match="readiness check"):
        prepare.provision(payload)
    assert closed == [True]
