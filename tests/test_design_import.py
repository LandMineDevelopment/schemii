from datetime import datetime, timezone

from schemii.common.postgres.models import (
    PostgresCheckConstraint,
    PostgresColumn,
    PostgresForeignKeyRelationship,
    PostgresFunction,
    PostgresIndex,
    PostgresPrimaryKey,
    PostgresTable,
    PostgresTrigger,
    PostgresType,
    PostgresUniqueConstraint,
    PostgresView,
    build_postgres_catalog,
)
from schemii.schemii.designs.importer import import_postgres_catalog
from schemii.schemii.migrations.planner import (
    compile_migration_steps,
    new_baseline_content,
    reconcile_designs,
)


def catalog():
    customer_columns = (
        PostgresColumn(name="id", ordinal=1, data_type="bigint", nullable=False),
        PostgresColumn(name="name", ordinal=2, data_type="text", nullable=False),
        PostgresColumn(
            name="normalized_name",
            ordinal=3,
            data_type="text",
            nullable=True,
            default_expression="lower(name)",
            generated="stored",
        ),
    )
    order_columns = (
        PostgresColumn(name="id", ordinal=1, data_type="bigint", nullable=False),
        PostgresColumn(name="customer_id", ordinal=2, data_type="bigint", nullable=False),
    )
    customer_key = PostgresPrimaryKey(
        name="customers_pkey",
        table="customers",
        columns=("id",),
        definition="PRIMARY KEY (id)",
        validated=True,
        deferrable=False,
        initially_deferred=False,
    )
    order_key = PostgresPrimaryKey(
        name="orders_pkey",
        table="orders",
        columns=("id",),
        definition="PRIMARY KEY (id)",
        validated=True,
        deferrable=False,
        initially_deferred=False,
    )
    return build_postgres_catalog(
        database="analytics",
        namespace="public",
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        types=(
            PostgresType(
                namespace="public",
                name="customer_state",
                kind="enum",
                definition="CREATE TYPE customer_state AS ENUM ('active', 'paused')",
            ),
        ),
        tables=(
            PostgresTable(
                namespace="public",
                name="customers",
                kind="table",
                is_partition=False,
                columns=customer_columns,
                primary_key=customer_key,
                unique_constraints=(
                    PostgresUniqueConstraint(
                        name="customers_name_key",
                        table="customers",
                        columns=("name",),
                        definition="UNIQUE (name)",
                        validated=True,
                        deferrable=False,
                        initially_deferred=False,
                    ),
                ),
                checks=(
                    PostgresCheckConstraint(
                        name="customers_name_check",
                        table="customers",
                        columns=("name",),
                        definition="CHECK (char_length(name) > 0)",
                        validated=True,
                    ),
                ),
                indexes=(
                    PostgresIndex(
                        name="customers_lower_name_idx",
                        table="customers",
                        definition=(
                            "CREATE INDEX customers_lower_name_idx ON public.customers "
                            "USING btree (lower(name)) WHERE name IS NOT NULL"
                        ),
                        method="btree",
                        unique=False,
                        valid=True,
                        predicate="name IS NOT NULL",
                    ),
                ),
                triggers=(
                    PostgresTrigger(
                        name="customers_touch",
                        table="customers",
                        definition=(
                            "CREATE TRIGGER customers_touch BEFORE UPDATE OF name "
                            "ON public.customers FOR EACH ROW "
                            "EXECUTE FUNCTION public.touch_customer()"
                        ),
                        enabled="origin",
                    ),
                ),
            ),
            PostgresTable(
                namespace="public",
                name="orders",
                kind="table",
                is_partition=False,
                columns=order_columns,
                primary_key=order_key,
            ),
        ),
        relationships=(
            PostgresForeignKeyRelationship(
                name="orders_customer_id_fkey",
                source_namespace="public",
                source_table="orders",
                source_columns=("customer_id",),
                target_namespace="public",
                target_table="customers",
                target_columns=("id",),
                definition="FOREIGN KEY (customer_id) REFERENCES customers(id)",
                on_update="NO ACTION",
                on_delete="CASCADE",
                match_type="SIMPLE",
                validated=True,
                deferrable=False,
                initially_deferred=False,
            ),
        ),
        functions=(
            PostgresFunction(
                namespace="public",
                name="touch_customer",
                kind="function",
                identity_arguments="",
                arguments="",
                return_type="trigger",
                language="plpgsql",
                definition=(
                    "CREATE FUNCTION public.touch_customer() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"
                ),
            ),
        ),
        views=(
            PostgresView(
                namespace="public",
                name="customer_names",
                columns=(
                    PostgresColumn(name="id", ordinal=1, data_type="bigint", nullable=True),
                    PostgresColumn(name="name", ordinal=2, data_type="text", nullable=True),
                ),
                query_definition="SELECT id, name FROM public.customers",
            ),
        ),
        materialized_views=(),
        captured_at=datetime.now(timezone.utc),
    )


def test_catalog_import_derives_a_editable_design_and_stable_layout() -> None:
    source = catalog()

    imported = import_postgres_catalog(source)

    assert imported.summary.complete is True
    assert imported.summary.catalog_fingerprint == source.fingerprint
    assert imported.summary.imported_objects == {
        "tables": 2,
        "columns": 5,
        "keys": 3,
        "checks": 1,
        "indexes": 1,
        "relationships": 1,
        "functions": 1,
        "views": 1,
        "triggers": 1,
        "types": 1,
    }
    customers = imported.content.tables[0]
    columns = {column.name: column for column in customers.columns}
    assert columns["normalized_name"].generated_expression == "lower(name)"
    assert columns["normalized_name"].generated_source_column_ids == [
        columns["name"].id
    ]
    assert customers.checks[0].expression == "char_length(name) > 0"
    assert customers.checks[0].column_ids == [columns["name"].id]
    assert customers.indexes[0].expression == "(lower(name))"
    assert customers.indexes[0].predicate == "name IS NOT NULL"
    assert imported.content.functions[0].definition.startswith(
        "CREATE FUNCTION touch_customer()"
    )
    assert " ON customers " in imported.content.triggers[0].definition
    assert imported.content.relationships[0].on_delete == "CASCADE"
    assert imported.content.types[0].enum_values == ["active", "paused"]
    assert {position.layer for position in imported.layout.objects} == {
        "tables",
        "views",
    }

    repeated = import_postgres_catalog(source)
    assert repeated.content == imported.content
    assert repeated.layout == imported.layout


def test_expression_dependencies_use_table_order_for_migration_verification() -> None:
    source = catalog()
    customers = source.tables[0]
    reverse_order_check = PostgresCheckConstraint(
        name="customers_order_check",
        table="customers",
        columns=("id", "name"),
        definition="CHECK (name <> '' OR id > 0)",
        validated=True,
    )
    source = build_postgres_catalog(
        **source.model_dump(exclude={"fingerprint", "captured_at", "tables"}),
        tables=(
            customers.model_copy(update={
                "checks": (*customers.checks, reverse_order_check),
            }),
            source.tables[1],
        ),
        captured_at=source.captured_at,
    )

    desired = import_postgres_catalog(source).content
    table = desired.tables[0]
    columns = {column.name: column.id for column in table.columns}
    check = next(item for item in table.checks if item.name == reverse_order_check.name)

    assert check.column_ids == [columns["id"], columns["name"]]

    desired = desired.model_copy(deep=True)
    desired_check = next(
        item
        for item in desired.tables[0].checks
        if item.name == reverse_order_check.name
    )
    desired_check.column_ids.reverse()
    refreshed, issues, complete = new_baseline_content(source, desired)
    remaining, blockers = compile_migration_steps("public", refreshed, desired)
    reconciled = reconcile_designs(refreshed, desired, source)

    assert complete is True
    assert issues == []
    assert remaining == []
    assert blockers == []
    assert reconciled.conflicts == []
    assert reconciled.external_changes == []
    assert reconciled.merged is not None
    merged_check = next(
        item
        for item in reconciled.merged.tables[0].checks
        if item.name == reverse_order_check.name
    )
    assert merged_check.column_ids == desired_check.column_ids


def test_catalog_import_reports_unrepresentable_source_state() -> None:
    source = catalog()
    first = source.tables[0]
    unsupported_index = PostgresIndex(
        name="customers_covering_idx",
        table="customers",
        definition=(
            "CREATE INDEX customers_covering_idx ON public.customers "
            "USING btree (name) INCLUDE (normalized_name)"
        ),
        method="btree",
        unique=False,
        valid=True,
    )
    source = build_postgres_catalog(
        **source.model_dump(
            exclude={"fingerprint", "captured_at", "tables"},
        ),
        tables=(
            first.model_copy(update={
                "kind": "partitioned_table",
                "partition_key": "LIST (name)",
                "indexes": (*first.indexes, unsupported_index),
            }),
            source.tables[1],
        ),
        captured_at=source.captured_at,
    )

    imported = import_postgres_catalog(source)

    assert imported.summary.complete is False
    assert {issue.category for issue in imported.summary.issues} == {"index", "table"}
    assert imported.summary.imported_objects["indexes"] == 1
    assert all(index.name != "customers_covering_idx" for index in imported.content.tables[0].indexes)


def test_catalog_import_preserves_constraint_names_scoped_to_source_tables() -> None:
    source = catalog()
    repeated_name = source.relationships[0].name
    self_reference = PostgresForeignKeyRelationship(
        name=repeated_name,
        source_namespace="public",
        source_table="customers",
        source_columns=("name",),
        target_namespace="public",
        target_table="customers",
        target_columns=("name",),
        definition="FOREIGN KEY (name) REFERENCES customers(name)",
        on_update="NO ACTION",
        on_delete="NO ACTION",
        match_type="SIMPLE",
        validated=True,
        deferrable=False,
        initially_deferred=False,
    )
    source = build_postgres_catalog(
        **source.model_dump(
            exclude={"fingerprint", "captured_at", "relationships"},
        ),
        relationships=(*source.relationships, self_reference),
        captured_at=source.captured_at,
    )

    imported = import_postgres_catalog(source)

    assert [relationship.name for relationship in imported.content.relationships] == [
        repeated_name,
        repeated_name,
    ]
    assert len({relationship.source_table_id for relationship in imported.content.relationships}) == 2
