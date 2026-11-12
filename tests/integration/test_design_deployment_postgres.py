"""Opt-in durable metadata-copy and real source namespace/rollback proofs."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from pydantic import SecretStr
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from schemii.common.connections.models import (
    PostgresConnectionCreate,
    ResolvedPostgresConnection,
    PostgresConnectionUpdate,
)
from schemii.common.postgres.errors import PostgresGatewayError
from schemii.common.postgres.gateway import PsycopgPostgresGateway
from schemii.common.postgres.models import build_postgres_catalog
from schemii.schemii.designs.importer import import_postgres_catalog
from schemii.schemii.designs.models import (
    DesignDeploymentCopyRequest,
    DesignDeploymentReviewRequest,
    SchemiiDesignContent,
    SchemiiDesignReplace,
)
from schemii.schemii.designs.postgres_store import PostgresDesignRepository
from schemii.schemii.migrations.repository import PostgresMigrationRepository
from schemii.schemii.migrations.service import MigrationService, MigrationServiceError
from schemii.schemii.workspaces.models import WorkspaceCreateRecord
from schemii.schemii.workspaces.postgres_store import PostgresWorkspaceRepository


def _content(name: str, data_type: str = "bigint") -> SchemiiDesignContent:
    return SchemiiDesignContent.model_validate(
        {
            "tables": [
                {
                    "id": "table_" + "1" * 32,
                    "name": name,
                    "columns": [
                        {
                            "id": "column_" + "2" * 32,
                            "name": "id",
                            "dataType": data_type,
                        }
                    ],
                }
            ]
        }
    )


def test_actual_metadata_copy_is_atomic_undoable_source_immutable_and_stale_safe(
    postgres_metadata, monkeypatch
):
    harness = postgres_metadata
    owner = harness.owner_id
    profile = harness.repositories.connections.create(
        owner,
        PostgresConnectionCreate(
            name="Owned copy target",
            host="application-postgres",
            database="owned_application",
            username="application_user",
        ),
    )
    resolved = harness.repositories.connections.resolve(owner, profile.id).model_copy(
        update={"owner_id": owner}
    )

    class Access:
        @contextmanager
        def use(self, actor, connection_id):
            assert (actor, connection_id) == (owner, profile.id)
            yield resolved

    designs = PostgresDesignRepository(harness.connection_factory)
    workspaces = PostgresWorkspaceRepository(harness.connection_factory)
    baselines = PostgresMigrationRepository(harness.connection_factory)
    catalog = build_postgres_catalog(
        database=resolved.database,
        namespace='A "schema"',
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=(),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(),
        captured_at=datetime.now(timezone.utc),
    )
    service = MigrationService(
        repository=baselines,
        connection_access=Access(),
        postgres=SimpleNamespace(introspect=lambda *_: catalog),
        workspaces=workspaces,
        designs=designs,
    )
    source = workspaces.create(owner, WorkspaceCreateRecord(name="Detached source"))
    original = designs.replace(
        owner,
        source.id,
        SchemiiDesignReplace(expected_design_revision=0, content=_content("incoming")),
    )
    target = service.create_import_workspace(
        owner,
        WorkspaceCreateRecord(
            name="Existing target",
            connection_id=profile.id,
            connection_owner_id=owner,
            database=resolved.database,
            namespace=catalog.namespace,
        ),
        import_postgres_catalog(catalog),
        profile.revision,
        catalog,
    )
    target_design = designs.get(owner, target.id)
    baseline = baselines.current_baseline(owner, target.id)
    review = service.review_design_deployment(
        owner, source.id, DesignDeploymentReviewRequest(target_workspace_id=target.id)
    )
    request = DesignDeploymentCopyRequest(
        target_workspace_id=target.id, review_digest=review.review_digest
    )
    copied = service.copy_design_deployment(owner, source.id, request)
    assert copied.design.content.tables[0].name == "incoming"
    assert designs.get(owner, source.id) == original
    assert workspaces.get(owner, target.id) == target
    assert baselines.current_baseline(owner, target.id) == baseline
    undone = designs.undo(owner, target.id, copied.design.revision)
    assert undone.content == target_design.content

    # Interleave an actual source commit after service preview but before the
    # copy repository takes its source/target locks. No target row may change.
    review = service.review_design_deployment(
        owner, source.id, DesignDeploymentReviewRequest(target_workspace_id=target.id)
    )
    original_replace = designs.replace

    def interleave(actor, workspace, body, **kwargs):
        current = designs.get(owner, source.id)
        original_replace(
            owner,
            source.id,
            SchemiiDesignReplace(
                expected_design_revision=current.revision,
                content=_content("new_source"),
            ),
        )
        return original_replace(actor, workspace, body, **kwargs)

    monkeypatch.setattr(designs, "replace", interleave)
    with pytest.raises(MigrationServiceError):
        service.copy_design_deployment(
            owner,
            source.id,
            DesignDeploymentCopyRequest(
                target_workspace_id=target.id, review_digest=review.review_digest
            ),
        )
    assert designs.get(owner, target.id) == undone
    monkeypatch.setattr(designs, "replace", original_replace)

    # A profile changed between service review and the metadata write must be
    # rejected by the durable connection-row check, even if an in-process user
    # still holds the old resolved profile.
    review = service.review_design_deployment(
        owner, source.id, DesignDeploymentReviewRequest(target_workspace_id=target.id)
    )

    def change_connection(actor, workspace, body, **kwargs):
        harness.repositories.connections.update(
            owner,
            profile.id,
            PostgresConnectionUpdate(
                expected_revision=profile.revision, name="Changed during copy"
            ),
        )
        return original_replace(actor, workspace, body, **kwargs)

    monkeypatch.setattr(designs, "replace", change_connection)
    with pytest.raises(MigrationServiceError):
        service.copy_design_deployment(
            owner,
            source.id,
            DesignDeploymentCopyRequest(
                target_workspace_id=target.id, review_digest=review.review_digest
            ),
        )
    assert designs.get(owner, target.id) == undone
    monkeypatch.setattr(designs, "replace", original_replace)
    resolved = harness.repositories.connections.resolve(owner, profile.id).model_copy(
        update={"owner_id": owner}
    )

    # Baseline replacement also races independently of desired-design edits.
    review = service.review_design_deployment(
        owner, source.id, DesignDeploymentReviewRequest(target_workspace_id=target.id)
    )

    def change_baseline(actor, workspace, body, **kwargs):
        current = baselines.current_baseline(owner, target.id)
        baselines.create_baseline(
            owner_id=owner,
            workspace_id=target.id,
            connection_id=profile.id,
            connection_owner_id=owner,
            connection_revision=resolved.revision,
            database=resolved.database,
            namespace=catalog.namespace,
            design_revision=current.design_revision,
            content=current.content,
            catalog=current.catalog,
            complete=current.complete,
            issues=current.issues,
            source="drift_reconciliation",
            expected_predecessor_id=current.id,
        )
        return original_replace(actor, workspace, body, **kwargs)

    monkeypatch.setattr(designs, "replace", change_baseline)
    with pytest.raises(MigrationServiceError):
        service.copy_design_deployment(
            owner,
            source.id,
            DesignDeploymentCopyRequest(
                target_workspace_id=target.id, review_digest=review.review_digest
            ),
        )
    assert designs.get(owner, target.id) == undone
    monkeypatch.setattr(designs, "replace", original_replace)

    # Fail inside the real metadata transaction after target update/history
    # insertion, proving rollback also restores history and layout atomically.
    review = service.review_design_deployment(
        owner, source.id, DesignDeploymentReviewRequest(target_workspace_id=target.id)
    )
    before = designs.snapshot(
        owner, target.id, service._design_baseline(owner, target.id)[0]
    )
    original_layout = designs._advance_layout

    def fail_layout(*_):
        raise RuntimeError("owned injected post-write failure")

    monkeypatch.setattr(designs, "_advance_layout", fail_layout)
    from schemii.schemii.designs.store import DesignStorageUnavailableError

    with pytest.raises(DesignStorageUnavailableError):
        service.copy_design_deployment(
            owner,
            source.id,
            DesignDeploymentCopyRequest(
                target_workspace_id=target.id, review_digest=review.review_digest
            ),
        )
    monkeypatch.setattr(designs, "_advance_layout", original_layout)
    assert (
        designs.snapshot(
            owner, target.id, service._design_baseline(owner, target.id)[0]
        )
        == before
    )


@pytest.fixture
def owned_source(postgres_metadata):
    """Explicit disposable external source, with no application/control-plane reuse."""
    del postgres_metadata
    dsn, password = (
        os.environ.get("SCHEMII_TEST_SOURCE_DSN"),
        os.environ.get("SCHEMII_TEST_SOURCE_PASSWORD"),
    )
    assert dsn and password, (
        "namespace acceptance needs a disposable separate source DSN/password with CREATEDB and CREATEROLE"
    )
    parameters = conninfo_to_dict(dsn)
    database, role = "deployment_" + uuid4().hex, "deployment_role_" + uuid4().hex
    role_password = uuid4().hex
    database_created = role_created = False

    def admin():
        return psycopg.connect(
            dsn,
            password=password,
            autocommit=True,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c lock_timeout=2000",
        )

    def connect(database_name=database, username=None, secret=None):
        values = {**parameters, "dbname": database_name, "password": secret or password}
        if username:
            values["user"] = username
        return psycopg.connect(**values, autocommit=True, connect_timeout=3)

    try:
        with admin() as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                    sql.Identifier(database)
                )
            )
            database_created = True
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(role_password)
                )
            )
            role_created = True
        profile = ResolvedPostgresConnection(
            id="pg_" + uuid4().hex,
            owner_id="owned-proof",
            revision=1,
            name="Disposable namespace proof",
            host=parameters.get("host") or parameters.get("hostaddr") or "localhost",
            port=int(parameters.get("port") or 5432),
            database=database,
            username=parameters["user"],
            password=password,
            ssl_mode=parameters.get("sslmode", "prefer"),
            connect_timeout=3,
        )
        yield SimpleNamespace(
            profile=profile, connect=connect, role=role, role_password=role_password
        )
    finally:
        if database_created:
            with admin() as connection:
                connection.execute(
                    sql.SQL("DROP DATABASE {}").format(sql.Identifier(database))
                )
        if role_created:
            with admin() as connection:
                connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_real_migration_namespace_external_reference_permission_and_rollback(
    owned_source,
):
    source = owned_source
    namespace = 'Target "schema"'
    with source.connect() as connection:
        connection.execute(
            sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(namespace))
        )
        connection.execute("CREATE TABLE public.external_marker (value text)")
        connection.execute(
            "INSERT INTO public.external_marker VALUES ('external kept')"
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(namespace), sql.Identifier(source.role)
            )
        )
    gateway = PsycopgPostgresGateway()
    catalog = gateway.introspect(source.profile, namespace)
    statements = [
        "CREATE TYPE \"State\" AS ENUM ('new');",
        'CREATE TABLE "Rows" (id integer, state "State");',
        'CREATE FUNCTION "Value"() RETURNS text LANGUAGE SQL AS $$ SELECT value FROM public.external_marker $$;',
        'CREATE FUNCTION "Changed"() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;',
        'CREATE TRIGGER "Row change" BEFORE INSERT ON "Rows" FOR EACH ROW EXECUTE FUNCTION "Changed"();',
    ]
    gateway.execute_migration(
        source.profile,
        namespace,
        catalog.fingerprint,
        statements,
        on_started=lambda *_: None,
        on_intended=lambda *_: None,
    )
    with source.connect() as connection:
        assert (
            connection.execute(
                sql.SQL("SELECT {}.{}()").format(
                    sql.Identifier(namespace), sql.Identifier("Value")
                )
            ).fetchone()[0]
            == "external kept"
        )
        for relation in ["Rows"]:
            assert (
                connection.execute(
                    "SELECT to_regclass(%s)", (f'public."{relation}"',)
                ).fetchone()[0]
                is None
            )
        assert (
            connection.execute(
                "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname=%s AND t.typname=%s",
                (namespace, "State"),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND t.tgname=%s",
                (namespace, "Row change"),
            ).fetchone()[0]
            == 1
        )
    current = gateway.introspect(source.profile, namespace)
    with pytest.raises(PostgresGatewayError):
        gateway.execute_migration(
            source.profile,
            namespace,
            current.fingerprint,
            ["CREATE TYPE \"Rolled back\" AS ENUM ('lost');", "SELECT 1/0;"],
            on_started=lambda *_: None,
            on_intended=lambda *_: None,
        )
    assert (
        gateway.introspect(source.profile, namespace).fingerprint == current.fingerprint
    )
    denied = source.profile.model_copy(
        update={"username": source.role, "password": SecretStr(source.role_password)}
    )
    denied_catalog = gateway.introspect(denied, namespace)
    with pytest.raises(PostgresGatewayError):
        gateway.execute_migration(
            denied,
            namespace,
            denied_catalog.fingerprint,
            ["CREATE TYPE \"Denied\" AS ENUM ('no');"],
            on_started=lambda *_: None,
            on_intended=lambda *_: None,
        )
    assert (
        gateway.introspect(source.profile, namespace).fingerprint == current.fingerprint
    )
