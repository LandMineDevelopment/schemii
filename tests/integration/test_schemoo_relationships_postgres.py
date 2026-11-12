"""Real qualified/composite join, identity and privilege oracles on owned schemas."""

from contextlib import contextmanager
import os
from types import SimpleNamespace
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from pydantic import SecretStr
import pytest

from schemii.common.connections.models import (
    PostgresSslMode,
    ResolvedPostgresConnection,
)
from schemii.common.postgres import PsycopgPostgresGateway
from schemii.schemoo.catalog import ModelCatalogs, catalog_contract
from schemii.schemoo.domain import legacy_relationship_id
from schemii.schemoo.models import ModelCreate, ModelDefinition, ModelUpdate
from schemii.schemoo.prototype import compile_preview
from schemii.schemoo.store import PostgresModelRepository


@pytest.fixture
def qualified_sources(postgres_metadata):
    dsn = postgres_metadata.environment["SCHEMII_METADATA_DSN"]
    password = os.environ["SCHEMII_TEST_METADATA_PASSWORD"]
    nonce = uuid.uuid4().hex[:16]
    namespaces = [f"join_one_{nonce}", f"join_two_{nonce}"]
    role = f"join_reader_{nonce}"
    created, role_created = [], False
    with psycopg.connect(dsn, password=password, autocommit=True) as admin:
        if not admin.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone()[0]:
            pytest.skip(
                "isolated qualified-source grants require a disposable PostgreSQL superuser"
            )
        try:
            admin.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role))
            )
            role_created = True
            for namespace in namespaces:
                admin.execute(
                    sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(namespace))
                )
                created.append(namespace)
                admin.execute(
                    sql.SQL(
                        "CREATE TABLE {} (tenant integer NOT NULL, id integer NOT NULL, label text NOT NULL, PRIMARY KEY(tenant,id))"
                    ).format(sql.Identifier(namespace, "same.name"))
                )
                admin.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                        sql.Identifier(namespace), sql.Identifier(role)
                    )
                )
                admin.execute(
                    sql.SQL("GRANT SELECT(tenant,id,label) ON {} TO {}").format(
                        sql.Identifier(namespace, "same.name"), sql.Identifier(role)
                    )
                )
            admin.execute(
                sql.SQL(
                    "INSERT INTO {} VALUES (1,1,'target-a'),(1,2,'target-b'),(2,1,'tenant-two')"
                ).format(sql.Identifier(namespaces[1], "same.name"))
            )
            admin.execute(
                sql.SQL(
                    "INSERT INTO {} VALUES (1,1,'child-a'),(1,2,'child-b'),(2,1,'child-c')"
                ).format(sql.Identifier(namespaces[0], "same.name"))
            )
            admin.execute(
                sql.SQL(
                    "ALTER TABLE {} ADD CONSTRAINT exact_pair FOREIGN KEY(tenant,id) REFERENCES {}(tenant,id)"
                ).format(
                    sql.Identifier(namespaces[0], "same.name"),
                    sql.Identifier(namespaces[1], "same.name"),
                )
            )
            # Same constraint name and relation name in another namespace must
            # never be confused with the imported cross-schema constraint.
            admin.execute(
                sql.SQL(
                    "ALTER TABLE {} ADD CONSTRAINT exact_pair FOREIGN KEY(tenant,id) REFERENCES {}(tenant,id)"
                ).format(
                    sql.Identifier(namespaces[1], "same.name"),
                    sql.Identifier(namespaces[1], "same.name"),
                )
            )

            @contextmanager
            def as_reader():
                with psycopg.connect(dsn, password=password) as connection:
                    connection.execute(
                        sql.SQL("SET ROLE {}").format(sql.Identifier(role))
                    )
                    yield connection

            class ReaderGateway(PsycopgPostgresGateway):
                def _connect(self, target):
                    connection = psycopg.connect(
                        dsn, password=password, row_factory=dict_row
                    )
                    connection.execute(
                        sql.SQL("SET ROLE {}").format(sql.Identifier(role))
                    )
                    connection.commit()
                    return connection

            parameters = conninfo_to_dict(dsn)
            target = ResolvedPostgresConnection(
                id="pg_" + uuid.uuid4().hex,
                revision=1,
                name="Owned qualified join reader",
                host=parameters.get("host") or "localhost",
                port=int(parameters.get("port") or 5432),
                database=parameters["dbname"],
                username=role,
                password=SecretStr(
                    "fixture uses SET ROLE under the disposable administrator"
                ),
                ssl_mode=PostgresSslMode.PREFER,
                connect_timeout=5,
            )

            @contextmanager
            def use(*args):
                yield target

            profile = SimpleNamespace(
                revision=1, database=target.database, owner_id="fixture_manager"
            )
            services = SimpleNamespace(
                connections=SimpleNamespace(get=lambda *args: profile, use=use),
                postgres=ReaderGateway(),
            )
            yield SimpleNamespace(
                admin=admin,
                namespaces=namespaces,
                role=role,
                reader=as_reader,
                services=services,
                owner=postgres_metadata.owner_id,
                metadata=postgres_metadata,
                target=target,
            )
        finally:
            for namespace in reversed(created):
                admin.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(namespace)
                    )
                )
            if role_created:
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def _model(source):
    catalog = ModelCatalogs().get(
        source.services,
        "viewer",
        source.target.id,
        source.namespaces[0],
        namespaces=source.namespaces,
        fresh=True,
    )
    relation = next(
        item
        for item in catalog["relationships"]
        if item["sourceNamespace"] == source.namespaces[0]
    )
    definition = {
        "root": "old_left",
        "nodes": [
            {"id": "old_left", "table": "same.name", "label": "Left"},
            {
                "id": "old_right",
                "namespace": source.namespaces[1],
                "table": "same.name",
                "label": "Right",
            },
        ],
        "edges": [
            {
                "id": "old_edge",
                "relationshipId": relation["id"],
                "source": "old_left",
                "target": "old_right",
            }
        ],
        "scopes": [],
        "exposedFields": [
            {"table": "old_left", "column": "label"},
            {"table": "old_right", "column": "label"},
        ],
    }
    definition["sourceContract"] = catalog_contract(catalog, definition)
    return catalog, definition


def test_imported_logical_reverse_and_exists_use_complete_pairs(qualified_sources):
    source = qualified_sources
    catalog, definition = _model(source)
    fields = [{"table": node, "column": "label"} for node in ["old_left", "old_right"]]
    with source.reader() as connection:
        for root in ["old_left", "old_right"]:
            plan = compile_preview(
                catalog, {**definition, "root": root, "fields": fields}
            )
            assert sorted(connection.execute(plan["sql"]).fetchall()) == [
                ("child-a", "target-a"),
                ("child-b", "target-b"),
                ("child-c", "tenant-two"),
            ]
        logical = {
            **definition,
            "edges": [
                {
                    "id": "logical",
                    "kind": "logical",
                    "source": "old_left",
                    "target": "old_right",
                    "columns": [
                        {"source": "tenant", "target": "tenant"},
                        {"source": "id", "target": "id"},
                    ],
                }
            ],
        }
        assert (
            len(
                connection.execute(
                    compile_preview(catalog, {**logical, "fields": fields})["sql"]
                ).fetchall()
            )
            == 3
        )
        exists = compile_preview(
            catalog,
            {
                **definition,
                "fields": fields[:1],
                "reportFilters": [
                    {
                        "mode": "exists",
                        "conditions": [
                            {
                                "table": "old_right",
                                "column": "label",
                                "operator": "eq",
                                "value": "target-b",
                            }
                        ],
                    }
                ],
            },
        )
        assert connection.execute(exists["sql"]).fetchall() == [("child-b",)]
    # A historical namespace-less hash cannot select the cross-schema FK or
    # another schema's identically named relation/constraint.
    relation = next(
        item
        for item in catalog["relationships"]
        if item["sourceNamespace"] == source.namespaces[0]
    )
    definition["edges"][0]["relationshipId"] = legacy_relationship_id(relation)
    with pytest.raises(ValueError, match="known foreign key"):
        compile_preview(catalog, {**definition, "fields": fields})


def test_current_usage_and_second_pair_grants_control_catalog_admission(
    qualified_sources,
):
    source = qualified_sources
    adapter = ModelCatalogs()
    catalog, definition = _model(source)
    assert len(catalog["tables"]) == 2
    source.admin.execute(
        sql.SQL("REVOKE SELECT(id) ON {} FROM {}").format(
            sql.Identifier(source.namespaces[1], "same.name"),
            sql.Identifier(source.role),
        )
    )
    current = adapter.get(
        source.services,
        "viewer",
        source.target.id,
        source.namespaces[0],
        namespaces=source.namespaces,
    )
    assert not any(
        item["sourceNamespace"] == source.namespaces[0]
        for item in current["relationships"]
    )
    with pytest.raises(ValueError, match="known foreign key"):
        compile_preview(current, {**definition, "fields": definition["exposedFields"]})
    source.admin.execute(
        sql.SQL("GRANT SELECT(id) ON {} TO {}").format(
            sql.Identifier(source.namespaces[1], "same.name"),
            sql.Identifier(source.role),
        )
    )
    source.admin.execute(
        sql.SQL("REVOKE USAGE ON SCHEMA {} FROM {}").format(
            sql.Identifier(source.namespaces[1]), sql.Identifier(source.role)
        )
    )
    current = adapter.get(
        source.services,
        "viewer",
        source.target.id,
        source.namespaces[0],
        namespaces=source.namespaces,
    )
    assert [table["namespace"] for table in current["tables"]] == [source.namespaces[0]]


def test_qualified_metadata_roundtrip_preserves_stable_refs_and_legacy_namespace(
    qualified_sources,
):
    source = qualified_sources
    _, definition = _model(source)
    from schemii.common.connections.models import PostgresConnectionCreate

    # Metadata persistence remains under the existing fixture-owned owner and
    # connection; no production profiles or identities are adopted.
    connection = source.metadata.repositories.connections.create(
        source.owner,
        PostgresConnectionCreate(
            name="Owned model identity",
            host="localhost",
            database=source.target.database,
            username=source.role,
        ),
    )
    repository = PostgresModelRepository(source.metadata.connection_factory)
    model = repository.create(
        source.owner,
        ModelCreate(
            name="Qualified",
            connection_id=connection.id,
            database=source.target.database,
            namespace=source.namespaces[0],
            definition=ModelDefinition.model_validate(definition),
        ),
    )
    try:
        saved = repository.get(source.owner, model.id)
        assert saved.definition.nodes[0].namespace is None
        assert saved.definition.nodes[1].namespace == source.namespaces[1]
        assert saved.definition.edges[0].id == "old_edge"
        updated = repository.update(
            source.owner,
            model.id,
            ModelUpdate(
                expected_revision=saved.revision,
                name="Renamed",
                definition=saved.definition,
            ),
        )
        assert updated.definition == saved.definition
    finally:
        current = repository.get(source.owner, model.id)
        repository.delete(source.owner, model.id, current.revision)
