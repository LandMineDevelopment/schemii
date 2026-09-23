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
