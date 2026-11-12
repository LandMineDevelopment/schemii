"""Managed profiles bind product access to an exact, revocable database identity."""

import pytest

from schemii.common.auth.service import AuthService
from schemii.common.connections.models import PostgresConnectionCreate, SCHEMII_CONNECTION_OWNER_ID
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import ConnectionNotFoundError, InMemoryConnectionRepository
from schemii.schemoo.models import ModelCreate
from schemii.schemoo.store import InMemoryModelRepository


def _user(identifier):
    return dict(id=identifier, username=identifier, display_name=identifier,
                password_hash="unused", is_admin=False, disabled=False)


def test_managed_profile_is_product_scoped_and_revoked_without_moving_credential():
    auth = AuthService(enabled=True, setup_token="unused")
    repository = InMemoryConnectionRepository()
    connections = ConnectionService(repository, ())
    connections.set_authority(auth)
    profile = connections.create_schemii_owned(PostgresConnectionCreate(
        name="Organization", host="localhost", database="organization",
        username="readonly", password="sample-password"))
    west = connections.create_schemii_owned(PostgresConnectionCreate(
        name="West rows", host="localhost", database="organization",
        username="west_reader", password="west-password"))
    with auth.store.transaction(write=True) as state:
        state["users"]["friend"] = _user("friend")
        state["roles"]["modelers"] = dict(
            id="modelers", name="Modelers", capabilities=["schemoo:access"],
            user_ids=["friend"], dashboards=[], connections=[dict(
                owner_id=SCHEMII_CONNECTION_OWNER_ID, connection_id=item.id,
                allow_authoring=True) for item in (profile, west)])

    modeling = connections.for_product("schemoo")
    assert [(item.id, item.owner_id) for item in modeling.list("friend")] == [
        (profile.id, SCHEMII_CONNECTION_OWNER_ID),
        (west.id, SCHEMII_CONNECTION_OWNER_ID)]
    assert modeling.get("friend", profile.id).owner_id == SCHEMII_CONNECTION_OWNER_ID
    with modeling.use("friend", profile.id) as resolved:
        assert resolved.owner_id == SCHEMII_CONNECTION_OWNER_ID
        assert resolved.ownership == "schemii"
        assert resolved.password.get_secret_value() == "sample-password"
    with modeling.use("friend", west.id) as resolved:
        assert resolved.username == "west_reader"
        assert resolved.password.get_secret_value() == "west-password"
    with pytest.raises(ConnectionNotFoundError):
        connections.for_product("schemii").get("friend", profile.id)
    with pytest.raises(ConnectionNotFoundError):
        connections.get("friend", profile.id)

    models = InMemoryModelRepository()
    saved = models.create("friend", ModelCreate(
        name="Shared source", connection_id=profile.id, database="organization",
        namespace="public"), connection_owner_id=SCHEMII_CONNECTION_OWNER_ID)
    assert saved.owner_id == "friend" and saved.connection_owner_id == SCHEMII_CONNECTION_OWNER_ID
    assert models.count_for_connection(SCHEMII_CONNECTION_OWNER_ID, profile.id) == 1
    assert models.count_for_connection("friend", profile.id) == 0

    with auth.store.transaction(write=True) as state:
        state["roles"]["modelers"]["connections"] = []
    assert modeling.list("friend") == []
    with pytest.raises(ConnectionNotFoundError):
        with modeling.use("friend", profile.id):
            pass


def test_personal_profile_needs_only_app_capability_and_remains_private():
    auth = AuthService(enabled=True, setup_token="unused")
    connections = ConnectionService(InMemoryConnectionRepository(), ())
    connections.set_authority(auth)
    personal = connections.create("friend", PostgresConnectionCreate(
        name="My database", host="localhost", database="organization",
        username="writer", password="personal-password"))
    with auth.store.transaction(write=True) as state:
        state["users"]["friend"] = _user("friend")
        state["users"]["other"] = _user("other")
        state["roles"]["modelers"] = dict(
            id="modelers", name="Modelers", capabilities=["schemoo:access"],
            user_ids=["friend", "other"], dashboards=[], connections=[])

    modeling = connections.for_product("schemoo")
    assert [(item.id, item.ownership) for item in modeling.list("friend")] == [
        (personal.id, "user")]
    with modeling.use("friend", personal.id) as resolved:
        assert resolved.owner_id == "friend"
        assert resolved.password.get_secret_value() == "personal-password"
    with pytest.raises(ConnectionNotFoundError):
        modeling.get("other", personal.id)
    with pytest.raises(ConnectionNotFoundError):
        connections.for_product("schemii").get("friend", personal.id)
    with auth.store.transaction(write=True) as state:
        state["roles"]["modelers"]["capabilities"] = []
    with pytest.raises(ConnectionNotFoundError):
        modeling.get("friend", personal.id)


def test_legacy_human_owned_role_grant_is_stored_but_not_executable():
    auth = AuthService(enabled=True, setup_token="unused")
    connections = ConnectionService(InMemoryConnectionRepository(), ())
    connections.set_authority(auth)
    personal = connections.create("former_admin", PostgresConnectionCreate(
        name="Legacy", host="localhost", database="organization",
        username="readonly", password="private-password"))
    with auth.store.transaction(write=True) as state:
        state["users"]["former_admin"] = _user("former_admin")
        state["users"]["friend"] = _user("friend")
        state["roles"]["legacy"] = dict(
            id="legacy", name="Legacy", capabilities=["schemii:access"],
            user_ids=["friend"], dashboards=[], connections=[dict(
                owner_id="former_admin", connection_id=personal.id, allow_authoring=True)])

    assert auth.connection_access("friend", personal.id, "schemii") is None
    assert auth.connection_grants("friend") == []
    assert connections.for_product("schemii").list("friend") == []
    with pytest.raises(ConnectionNotFoundError):
        connections.for_product("schemii").get("friend", personal.id)


def managed_export(product, postgres=None):
    from tests.test_console import ConsolePostgresGateway, console_client
    from schemii.common.postgres.console.models import ConsoleExecutionCreate
    from schemii.schemii.workspaces.models import WorkspaceCreateRecord

    api, _ = console_client(postgres or ConsolePostgresGateway())
    services = api.app.state.services
    profile = services.connections.create_schemii_owned(PostgresConnectionCreate(
        name="Managed export", host="localhost", database="analytics",
        username="reader", password="synthetic-test-password"))
    auth = AuthService(enabled=True, setup_token="synthetic-test-token")
    services.connections.set_authority(auth)
    with auth.store.transaction(write=True) as state:
        state["users"]["friend"] = _user("friend")
        state["roles"]["readers"] = dict(id="readers", name="Readers",
            capabilities=[f"{product}:access"], user_ids=["friend"], dashboards=[],
            connections=[dict(owner_id=SCHEMII_CONNECTION_OWNER_ID,
                connection_id=profile.id, allow_authoring=True)])
    service = services.console
    access = services.connections.for_product(product)
    if product == "schemii":
        workspace = services.workspaces.create("friend", WorkspaceCreateRecord(
            name="Managed export", connection_id=profile.id,
            connection_owner_id=SCHEMII_CONNECTION_OWNER_ID,
            database="analytics", namespace="public"))
        receipt = service.reserve("friend", workspace.id, ConsoleExecutionCreate(
            console_id="con_" + "1" * 32, expected_workspace_revision=workspace.revision,
            expected_settings_revision=1, mode="managed_read", statements=["SELECT 1"]))
    else:
        receipt = service.reserve_read_target("friend", connection_id=profile.id,
            database="analytics", namespace="public", console_id="con_" + "1" * 32,
            statements=["SELECT 1"], connection_access=access)
    service.run("friend", receipt.id, connection_access=access)
    receipt = service.get_owned("friend", receipt.id)
    assert receipt.status == "succeeded"
    return api, auth, service, access, profile, receipt, receipt.results[0].id


@pytest.mark.parametrize(("product", "route"), [
    ("schemii", "service"), ("schemoo", "service"),
    ("schemii", "common"), ("schemoo", "common"), ("schemii", "legacy"),
])
def test_csv_export_rechecks_managed_connection_grant_before_reserving_a_lease(product, route):
    from types import SimpleNamespace
    from schemii.common.api.errors import ApiProblem
    from schemii.common.query_executions.errors import ConsoleServiceError
    from schemii.common.query_executions.routes import export_result
    from schemii.schemii.console.routes import export_console_result_csv

    api, auth, service, access, _, receipt, result_id = managed_export(product)
    with auth.store.transaction(write=True) as state:
        state["roles"]["readers"]["connections"] = []
    with pytest.raises((ConsoleServiceError, ApiProblem)) as caught:
        if route == "service":
            service.export_csv("friend", receipt.workspace_id, receipt.id, result_id,
                connection_access=access)
        elif route == "legacy":
            export_console_result_csv(receipt.workspace_id, receipt.id, result_id,
                SimpleNamespace(app=api.app), SimpleNamespace(user_id="friend"))
        else:
            export_result(receipt.id, result_id,
                SimpleNamespace(app=api.app), SimpleNamespace(user_id="friend"))
    assert caught.value.code == "console_connection_revoked"
    assert service._transient_results[result_id].active_exports == 0


@pytest.mark.parametrize("product", ["schemii", "schemoo"])
@pytest.mark.parametrize("change", ["grant", "capability", "revision"])
def test_csv_export_stops_before_next_batch_after_authority_changes(product, change):
    from schemii.common.connections.models import PostgresConnectionUpdate
    from schemii.common.query_executions.errors import ConsoleServiceError

    api, auth, service, access, profile, receipt, result_id = managed_export(product)
    stream = service.export_csv("friend", receipt.workspace_id, receipt.id, result_id,
        connection_access=access)
    assert next(stream) == b"value\r\n"
    if change == "revision":
        api.app.state.services.connections.update_schemii_owned(profile.id,
            PostgresConnectionUpdate(expected_revision=profile.revision, name="Revised profile"))
    else:
        with auth.store.transaction(write=True) as state:
            state["roles"]["readers"]["connections" if change == "grant" else "capabilities"] = []
    with pytest.raises(ConsoleServiceError) as caught:
        next(stream)
    assert caught.value.code == ("console_target_changed" if change == "revision" else "console_connection_revoked")
    assert service._transient_results[result_id].active_exports == 0
    stream.close()
    assert service._transient_results[result_id].active_exports == 0


@pytest.mark.parametrize("product", ["schemii", "schemoo"])
def test_common_csv_export_retains_the_originating_product_authority(product):
    import asyncio
    from types import SimpleNamespace
    from schemii.common.query_executions.routes import export_result

    api, _, service, _, _, receipt, result_id = managed_export(product)
    response = export_result(receipt.id, result_id,
        SimpleNamespace(app=api.app), SimpleNamespace(user_id="friend"))

    async def consume():
        rows = [chunk async for chunk in response.body_iterator]
        return b"".join(rows)

    assert asyncio.run(consume()).startswith(b"value\r\n0\r\n1\r\n")
    assert service._transient_results[result_id].active_exports == 0


@pytest.mark.parametrize("product", ["schemii", "schemoo"])
def test_csv_export_checks_revocation_after_database_fetch_before_emitting_rows(product):
    from tests.test_console import RetainedReadGateway
    from schemii.common.query_executions.errors import ConsoleServiceError

    gateway = RetainedReadGateway()
    _, auth, service, access, _, receipt, result_id = managed_export(product, gateway)
    stream = service.export_csv("friend", receipt.workspace_id, receipt.id, result_id,
        connection_access=access)
    assert next(stream) == b"value\r\n"

    def fetch(*args):
        with auth.store.transaction(write=True) as state:
            state["roles"]["readers"]["connections"] = []
        return ((99,),)

    gateway.sessions[0].export_page = fetch
    with pytest.raises(ConsoleServiceError) as caught:
        next(stream)
    assert caught.value.code == "console_connection_revoked"
    assert service._transient_results[result_id].active_exports == 0
    assert not gateway.sessions[0].cancelled.is_set()
    service.close_result("friend", receipt.workspace_id, receipt.id, result_id)
    assert gateway.sessions[0].closed
