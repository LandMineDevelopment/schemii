"""Behavior shared by developer route maps and provider grant requests."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from schemii.common.ai.routes import RoleGrant, ZenGrant
from schemii.common.route_contexts import public_route_contexts


def test_public_route_contexts_include_only_schema_visible_routes() -> None:
    application = FastAPI()
    application.add_api_route("/visible", lambda: None)
    application.add_api_route("/hidden", lambda: None, include_in_schema=False)
    product_visible = SimpleNamespace(include_in_schema=True)
    product_hidden = SimpleNamespace(include_in_schema=False)
    application.router.routes.append(
        SimpleNamespace(
            effective_route_contexts=lambda: (product_visible, product_hidden)
        )
    )

    contexts = list(public_route_contexts(application))

    assert [context.path for context in contexts if hasattr(context, "path")] == [
        "/visible"
    ]
    assert contexts[-1] is product_visible
    assert product_hidden not in contexts


@pytest.mark.parametrize(
    "grant_type,identity", [(ZenGrant, "userId"), (RoleGrant, "roleId")]
)
def test_provider_grants_share_connection_scope_validation(
    grant_type, identity
) -> None:
    fields = {identity: "owner"}
    connection_id = "pg_" + "a" * 32

    detached = grant_type.model_validate({**fields, "product": "schemii"})
    assert detached.connectionId is None
    connected = grant_type.model_validate(
        {
            **fields,
            "product": "schemer",
            "connectionOwnerId": "source-owner",
            "connectionId": connection_id,
        }
    )
    assert connected.connectionId == connection_id
    for invalid in (
        {**fields, "product": "schemoo"},
        {**fields, "product": "schemii", "connectionOwnerId": "source-owner"},
        {**fields, "product": "schemii", "connectionId": connection_id},
        {
            **fields,
            "product": "schemii",
            "connectionOwnerId": "source-owner",
            "connectionId": "bad",
        },
    ):
        with pytest.raises(ValidationError):
            grant_type.model_validate(invalid)
