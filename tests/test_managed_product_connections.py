"""Managed profiles bind product access to an exact, revocable database identity."""

import pytest

from schemii.common.auth.service import AuthService
from schemii.common.connections.models import PostgresConnectionCreate
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
    profile = connections.create("organization", PostgresConnectionCreate(
        name="Organization", host="localhost", database="organization",
        username="readonly", password="sample-password"))
    with auth.store.transaction(write=True) as state:
        state["users"]["organization"] = _user("organization")
        state["users"]["friend"] = _user("friend")
        state["roles"]["modelers"] = dict(
            id="modelers", name="Modelers", capabilities=["schemoo:access"],
            user_ids=["friend"], dashboards=[], connections=[dict(
                owner_id="organization", connection_id=profile.id, allow_authoring=True)])

    modeling = connections.for_product("schemoo")
    assert [(item.id, item.owner_id) for item in modeling.list("friend")] == [
        (profile.id, "organization")]
    assert modeling.get("friend", profile.id).owner_id == "organization"
    with modeling.use("friend", profile.id) as resolved:
        assert resolved.owner_id == "organization"
        assert resolved.password.get_secret_value() == "sample-password"
    with pytest.raises(ConnectionNotFoundError):
        connections.for_product("schemii").get("friend", profile.id)
    with pytest.raises(ConnectionNotFoundError):
        connections.get("friend", profile.id)

    models = InMemoryModelRepository()
    saved = models.create("friend", ModelCreate(
        name="Shared source", connection_id=profile.id, database="organization",
        namespace="public"), connection_owner_id="organization")
    assert saved.owner_id == "friend" and saved.connection_owner_id == "organization"
    assert models.count_for_connection("organization", profile.id) == 1
    assert models.count_for_connection("friend", profile.id) == 0

    with auth.store.transaction(write=True) as state:
        state["roles"]["modelers"]["connections"] = []
    assert modeling.list("friend") == []
    with pytest.raises(ConnectionNotFoundError):
        with modeling.use("friend", profile.id):
            pass
