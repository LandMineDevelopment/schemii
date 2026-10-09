"""Provider administration enumerates current permissions without per-user I/O."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemii.common.ai.instance_provider_store import MemoryInstanceAiProviderStore
from schemii.common.ai.credential_store import MemoryAiCredentialStore
from schemii.common.ai.routes import (
    _admin_instance_state,
    _role_grants_state,
    admin_router,
    shared_codex_router,
)
from schemii.common.api.errors import install_api_error_handlers
from schemii.common.auth.service import AuthService
from schemii.common.connections.models import (
    SCHEMII_CONNECTION_OWNER_ID,
    PostgresConnectionCreate,
    PostgresConnectionUpdate,
)
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import (
    InMemoryConnectionRepository,
    ConnectionStorageUnavailableError,
)
from schemii.common.metadata.models import Principal, get_current_principal


def _user(identifier, *, disabled=False):
    return dict(
        id=identifier,
        username=identifier,
        display_name=identifier,
        password_hash="unused",
        is_admin=False,
        disabled=disabled,
    )


def _profile(repository, owner, name):
    return repository.create(
        owner,
        PostgresConnectionCreate(
            name=name,
            host="localhost",
            database="example",
            username="reader",
            password="must-not-be-decrypted",
        ),
    )


def _request(auth, repository, instance=None):
    connections = ConnectionService(repository, ())
    connections.set_authority(auth)
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth=auth,
                services=SimpleNamespace(
                    connections=connections,
                    metadata=SimpleNamespace(
                        ai_instance_providers=instance
                        or MemoryInstanceAiProviderStore()
                    ),
                ),
            )
        )
    )


def test_admin_inventory_has_a_constant_storage_read_budget(monkeypatch):
    """Count real authority/repository reads, rather than using elapsed sleeps."""
    auth = AuthService(enabled=True, setup_token="unused")
    repository = InMemoryConnectionRepository()
    users = [f"person-{index}" for index in range(40)]
    shared = [
        _profile(repository, SCHEMII_CONNECTION_OWNER_ID, name) for name in ("A", "B")
    ]
    for user in users:
        _profile(repository, user, user)
    with auth.store.transaction(write=True) as state:
        state["users"] = {user: _user(user) for user in users}
        state["roles"]["all-apps"] = dict(
            id="all-apps",
            name="All apps",
            user_ids=users,
            capabilities=[
                "schemii:access",
                "schemoo:access",
                "schemer:access",
                "schemer:author",
            ],
            connections=[
                dict(
                    owner_id=SCHEMII_CONNECTION_OWNER_ID,
                    connection_id=item.id,
                    allow_authoring=True,
                )
                for item in shared
            ],
            dashboards=[],
        )
    reads = dict(authority=0, connections=0)
    transaction = auth.store.transaction

    @contextmanager
    def counted_transaction(*args, **kwargs):
        reads["authority"] += 1
        with transaction(*args, **kwargs) as state:
            yield state

    monkeypatch.setattr(auth.store, "transaction", counted_transaction)
    for name in ("list", "get", "list_for_owners"):
        method = getattr(repository, name, None)
        if method is None:
            continue

        def counted(*args, _method=method, **kwargs):
            reads["connections"] += 1
            return _method(*args, **kwargs)

        monkeypatch.setattr(repository, name, counted)
    response = _admin_instance_state(_request(auth, repository), "opencode")
    assert len(response["connections"]) == len(users) * 3 * 3
    assert reads == {"authority": 1, "connections": 1}


class RecordingAuthorityFactory:
    """Execute the actual AuthStore/AuthService read paths against explicit rows."""

    def __init__(self, users, roles):
        self.users = users
        self.roles = roles
        self.calls = 0
        self.statements = []
        self.result = []

    def __call__(self):
        self.calls += 1
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self

    def execute(self, sql, parameters=()):
        sql = " ".join(sql.split())
        self.statements.append((sql, parameters))
        if (
            sql.startswith("SET TRANSACTION")
            or "FROM metadata.auth_sessions" in sql
            or "FROM metadata.auth_login_attempts" in sql
        ):
            self.result = []
        elif sql.startswith("SELECT a.*, u.display_name"):
            self.result = [
                {**user, "user_id": user["id"]} for user in self.users.values()
            ]
        elif sql.startswith("SELECT a.user_id AS id"):
            user = self.users.get(parameters[0])
            self.result = [dict(user)] if user and not user["disabled"] else []
        elif sql == "SELECT * FROM metadata.auth_roles ORDER BY name":
            self.result = [
                {key: role[key] for key in ("id", "name", "capabilities")}
                for role in self.roles.values()
            ]
        elif sql == "SELECT * FROM metadata.auth_user_roles":
            self.result = [
                dict(user_id=user, role_id=role["id"])
                for role in self.roles.values()
                for user in role["user_ids"]
            ]
        elif sql in {
            "SELECT * FROM metadata.auth_role_connections",
            "SELECT * FROM metadata.auth_role_dashboards",
        }:
            key = "connections" if sql.endswith("connections") else "dashboards"
            self.result = [
                {**grant, "role_id": role["id"]}
                for role in self.roles.values()
                for grant in role[key]
            ]
        elif sql.startswith("SELECT r.capabilities"):
            self.result = [
                dict(capabilities=role["capabilities"])
                for role in self._roles_for(parameters[0])
            ]
        elif sql.startswith("SELECT g.*"):
            key = "connections" if "auth_role_connections" in sql else "dashboards"
            owner_field = "owner_id" if key == "connections" else "connection_owner_id"
            self.result = [
                {**grant, "role_id": role["id"]}
                for role in self._roles_for(parameters[0])
                for grant in role[key]
                if grant[owner_field] == parameters[1]
            ]
        elif sql.startswith("SELECT g.role_id,g.owner_id,g.connection_id"):
            user, connection, owner, capability, *author = parameters
            self.result = [
                dict(
                    role_id=role["id"],
                    owner_id=grant["owner_id"],
                    connection_id=grant["connection_id"],
                )
                for role in self._roles_for(user)
                if capability in role["capabilities"]
                and (not author or author[0] in role["capabilities"])
                for grant in role["connections"]
                if grant["connection_id"] == connection
                and grant["owner_id"] == owner
                and grant["allow_authoring"]
            ]
        else:
            raise AssertionError(f"unexpected authority SQL: {sql}")

    def _roles_for(self, user_id):
        user = self.users.get(user_id)
        return [
            role
            for role in self.roles.values()
            if user and not user["disabled"] and user_id in role["user_ids"]
        ]

    def fetchall(self):
        return self.result


@pytest.mark.parametrize("user_count", [1, 40])
@pytest.mark.parametrize("role_count", [1, 8])
def test_durable_inventory_has_constant_factory_and_statement_budget(
    user_count, role_count
):
    repository = InMemoryConnectionRepository()
    shared = _profile(repository, SCHEMII_CONNECTION_OWNER_ID, "Shared")
    users = {f"person-{index}": _user(f"person-{index}") for index in range(user_count)}
    users["disabled"] = _user("disabled", disabled=True)
    for user in users:
        _profile(repository, user, user)
    roles = {
        f"role-{index}": dict(
            id=f"role-{index}",
            name=f"Role {index}",
            user_ids=list(users),
            capabilities=[
                "schemii:access",
                "schemoo:access",
                "schemer:access",
                "schemer:author",
            ],
            connections=[
                dict(
                    owner_id=SCHEMII_CONNECTION_OWNER_ID,
                    connection_id=shared.id,
                    allow_authoring=True,
                )
            ],
            dashboards=[
                dict(
                    dashboard_id=f"dashboard-{index}",
                    owner_id="author",
                    connection_owner_id=SCHEMII_CONNECTION_OWNER_ID,
                    connection_id=shared.id,
                )
            ],
        )
        for index in range(role_count)
    }
    factory = RecordingAuthorityFactory(users, roles)
    auth = AuthService(factory, enabled=True, setup_token="unused")
    instance = MemoryInstanceAiProviderStore()
    for role in roles:
        instance.upsert_role_grant(
            role, "schemii", SCHEMII_CONNECTION_OWNER_ID, shared.id
        )
    request = _request(auth, repository, instance)
    result = _admin_instance_state(request, "opencode")
    assert len(result["connections"]) == user_count * 3 * 2
    assert len(result["roleGrants"]) == role_count
    assert all(grant["active"] for grant in result["roleGrants"])
    assert factory.calls == 1, (factory.calls, len(factory.statements))
    assert len(factory.statements) == 8
    assert not any("disabled" == row["userId"] for row in result["connections"])
    factory.calls = 0
    factory.statements.clear()
    assert all(grant["active"] for grant in _role_grants_state(request, "opencode"))
    assert factory.calls == 1 and len(factory.statements) == 8


@pytest.mark.parametrize("provider", ["opencode", "openai-codex"])
def test_inventory_preserves_exact_visibility_and_reads_changes_on_the_next_request(
    provider, monkeypatch
):
    auth = AuthService(enabled=True, setup_token="unused")
    repository = InMemoryConnectionRepository()
    instance = MemoryInstanceAiProviderStore()
    personal = {
        user: _profile(repository, user, f"Private {user}")
        for user in ("alice", "bob", "reader", "disabled", "no-access", "former-owner")
    }
    shared = _profile(repository, SCHEMII_CONNECTION_OWNER_ID, "Shared author source")
    dashboard = _profile(repository, SCHEMII_CONNECTION_OWNER_ID, "Dashboard source")
    hidden = _profile(repository, SCHEMII_CONNECTION_OWNER_ID, "Unassigned source")
    missing = "pg_" + "f" * 32

    def connection(profile, *, allow=True):
        return dict(
            owner_id=profile.owner_id, connection_id=profile.id, allow_authoring=allow
        )

    def report(profile, suffix):
        return dict(
            dashboard_id=f"dashboard-{suffix}",
            owner_id="author",
            connection_owner_id=profile.owner_id,
            connection_id=profile.id,
        )

    def role(identifier, users, capabilities, connections=(), dashboards=()):
        return dict(
            id=identifier,
            name=identifier,
            user_ids=users,
            capabilities=capabilities,
            connections=list(connections),
            dashboards=list(dashboards),
        )

    all_apps = ["schemii:access", "schemoo:access", "schemer:access", "schemer:author"]
    with auth.store.transaction(write=True) as state:
        state["users"] = {
            user: _user(user, disabled=user == "disabled") for user in personal
        }
        state["roles"] = {
            "alice-author": role(
                "alice-author",
                ["alice"],
                all_apps,
                [connection(shared), connection(personal["former-owner"])],
                [report(shared, "duplicate"), report(dashboard, "alice")],
            ),
            "bob-schemii": role("bob-schemii", ["bob"], ["schemii:access"]),
            "bob-schemoo": role(
                "bob-schemoo", ["bob"], ["schemoo:access"], [connection(dashboard)]
            ),
            "reader": role(
                "reader",
                ["reader"],
                ["schemer:access"],
                [connection(shared)],
                [
                    report(shared, "reader"),
                    report(dashboard, "reader-2"),
                    dict(
                        dashboard_id="missing",
                        owner_id="author",
                        connection_owner_id=SCHEMII_CONNECTION_OWNER_ID,
                        connection_id=missing,
                    ),
                ],
            ),
            "disabled": role("disabled", ["disabled"], all_apps, [connection(shared)]),
            "no-access": role(
                "no-access", ["no-access", "deleted-user"], [], [connection(hidden)]
            ),
            "stale-source": role(
                "stale-source",
                ["alice"],
                ["schemii:access"],
                [
                    dict(
                        owner_id=SCHEMII_CONNECTION_OWNER_ID,
                        connection_id=missing,
                        allow_authoring=True,
                    )
                ],
            ),
        }
    for role_id, product, source in (
        ("alice-author", "schemii", shared.id),
        ("reader", "schemer", dashboard.id),
        ("bob-schemii", "schemii", dashboard.id),
        ("stale-source", "schemii", missing),
        ("removed-role", "schemii", shared.id),
    ):
        instance.upsert_role_grant(
            role_id, product, SCHEMII_CONNECTION_OWNER_ID, source, provider_id=provider
        )
    instance.upsert_grant("alice", "schemii", provider_id=provider)

    def forbidden_resolve(*_args):
        raise AssertionError(
            "provider inventory must never resolve/decrypt a credential"
        )

    monkeypatch.setattr(repository, "resolve", forbidden_resolve)
    request = _request(auth, repository, instance)
    result = _admin_instance_state(request, provider)
    identities = {
        (row["userId"], row["product"], row["connectionOwnerId"], row["connectionId"])
        for row in result["connections"]
    }
    expected = {
        ("alice", product, "alice", personal["alice"].id)
        for product in ("schemii", "schemoo", "schemer")
    }
    expected |= {
        ("alice", product, SCHEMII_CONNECTION_OWNER_ID, shared.id)
        for product in ("schemii", "schemoo", "schemer")
    }
    expected |= {
        ("alice", "schemer", SCHEMII_CONNECTION_OWNER_ID, dashboard.id),
        ("bob", "schemii", "bob", personal["bob"].id),
        ("bob", "schemoo", "bob", personal["bob"].id),
        ("bob", "schemoo", SCHEMII_CONNECTION_OWNER_ID, dashboard.id),
        ("reader", "schemer", SCHEMII_CONNECTION_OWNER_ID, shared.id),
        ("reader", "schemer", SCHEMII_CONNECTION_OWNER_ID, dashboard.id),
    }
    assert identities == expected
    assert len(result["connections"]) == len(expected)
    assert {grant["roleId"]: grant["active"] for grant in result["roleGrants"]} == {
        "alice-author": True,
        "reader": True,
        "bob-schemii": False,
        "stale-source": False,
        "removed-role": False,
    }
    assert result["grants"] == instance.list_grants(provider)
    assert "must-not-be-decrypted" not in str(result)

    with auth.store.transaction(write=True) as state:
        state["roles"]["alice-author"]["connections"].clear()
        state["roles"]["alice-author"]["dashboards"].clear()
        state["users"]["bob"]["disabled"] = True
    repository.update(
        "alice",
        personal["alice"].id,
        PostgresConnectionUpdate(expected_revision=1, name="Renamed now"),
    )
    repository.delete(SCHEMII_CONNECTION_OWNER_ID, dashboard.id, dashboard.revision)
    instance.delete_grant("alice", "schemii", provider_id=provider)
    changed = _admin_instance_state(request, provider)
    assert not any(
        row["userId"] == "bob" or row["connectionId"] == dashboard.id
        for row in changed["connections"]
    )
    alice_rows = [row for row in changed["connections"] if row["userId"] == "alice"]
    assert len(alice_rows) == 3 and all(
        row["name"] == "Renamed now" for row in alice_rows
    )
    assert changed["grants"] == []
    assert not any(grant["active"] for grant in changed["roleGrants"])


def test_inventory_storage_failure_is_visible_and_the_next_request_recovers(
    monkeypatch,
):
    auth = AuthService(enabled=True, setup_token="unused")
    repository = InMemoryConnectionRepository()
    request = _request(auth, repository)
    with monkeypatch.context() as patch:

        def unavailable(_owners):
            raise ConnectionStorageUnavailableError("controlled metadata failure")

        patch.setattr(repository, "list_for_owners", unavailable)
        with pytest.raises(
            ConnectionStorageUnavailableError, match="controlled metadata failure"
        ):
            _admin_instance_state(request, "opencode")
    assert _admin_instance_state(request, "opencode")["connections"] == []


@pytest.mark.parametrize("user", ["reader", "disabled-admin", "removed-user"])
@pytest.mark.parametrize(
    "path", ["/api/v1/admin/ai/zen", "/api/v1/admin/ai/shared-codex"]
)
def test_provider_state_keeps_admin_guard_before_inventory_reads(
    user, path, monkeypatch
):
    auth = AuthService(enabled=True, setup_token="unused")
    with auth.store.transaction(write=True) as state:
        state["users"] = {
            "reader": _user("reader"),
            "disabled-admin": _user("disabled-admin", disabled=True),
        }
        state["roles"]["provisioners"] = dict(
            id="provisioners",
            name="Provisioners",
            capabilities=["accounts:provision"],
            user_ids=["disabled-admin"],
            connections=[],
            dashboards=[],
        )
    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(shared_codex_router)
    app.state.auth = auth

    def forbidden_snapshot():
        raise AssertionError(
            "denied principal must not reach inventory or provider metadata"
        )

    monkeypatch.setattr(auth, "read_snapshot", forbidden_snapshot)
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=user, authentication_source="local_prototype"
    )
    assert TestClient(app).get(path).status_code == 403


def test_shared_catalog_failure_and_current_provider_metadata_remain_truthful():
    auth = AuthService(enabled=True, setup_token="unused")
    with auth.store.transaction(write=True) as state:
        state["users"]["admin"] = _user("admin")
        state["roles"]["provisioners"] = dict(
            id="provisioners",
            name="Provisioners",
            capabilities=["accounts:provision"],
            user_ids=["admin"],
            connections=[],
            dashboards=[],
        )
    instance = MemoryInstanceAiProviderStore()
    credentials = MemoryAiCredentialStore()
    request = _request(auth, InMemoryConnectionRepository(), instance)
    app = FastAPI()
    app.include_router(shared_codex_router)
    install_api_error_handlers(app)
    app.state.auth = auth
    app.state.services = request.app.state.services
    app.state.services.metadata.ai_credentials = credentials

    def unavailable_catalog():
        raise RuntimeError("controlled local catalog failure")

    runtime = SimpleNamespace(
        _supported_models=unavailable_catalog,
        instance_codex_catalog=lambda: dict(verifiedModels=[], catalogCheckedAt=None),
    )
    app.state.ai_service = SimpleNamespace(runtime=runtime)
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id="admin", authentication_source="local_prototype"
    )
    client = TestClient(app)
    failed_catalog = client.get("/api/v1/admin/ai/shared-codex")
    assert failed_catalog.status_code == 200
    assert failed_catalog.json()["connected"] is False
    assert failed_catalog.json()["sourceConnected"] is False
    assert (
        failed_catalog.json()["models"] == [] and failed_catalog.json()["catalogError"]
    )
    credentials.begin_login("admin", "codex-prototype", "openai-codex")
    credentials.save(
        "admin",
        "codex-prototype",
        "openai-codex",
        {"type": "oauth", "refresh": "never-return-this"},
        1,
    )
    instance.set_credential(
        "openai-codex", {"type": "oauth", "refresh": "never-return-instance"}
    )
    runtime._supported_models = lambda: [
        dict(
            providerId="openai-codex",
            id="model",
            name="Model",
            reasoningLevels=["default"],
        )
    ]
    recovered = client.get("/api/v1/admin/ai/shared-codex")
    assert recovered.status_code == 200
    assert (
        recovered.json()["connected"] is True
        and recovered.json()["sourceConnected"] is True
    )
    assert recovered.json()["models"] == [
        dict(id="model", name="Model", reasoningLevels=["default"])
    ]
    assert (
        "catalogError" not in recovered.json() and "never-return" not in recovered.text
    )
