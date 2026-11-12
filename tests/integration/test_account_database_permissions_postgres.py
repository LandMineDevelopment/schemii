"""Real PostgreSQL column grants and RLS using isolated demo-fixture copies.

Uses the established SCHEMII_TEST_METADATA_DSN/PASSWORD integration target.
The selected disposable target must allow superuser role provisioning; the
ordinary production metadata account intentionally cannot run these tests.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
import os
import uuid

import psycopg
from psycopg import sql
import pytest

from schemii.common.postgres.queries import READABLE_COLUMNS_QUERY
from schemii.common.auth.service import AuthService
from schemii.common.connections.models import PostgresConnectionCreate, SCHEMII_CONNECTION_OWNER_ID
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import InMemoryConnectionRepository


@pytest.fixture
def reporting_database():
    dsn = os.environ.get("SCHEMII_TEST_METADATA_DSN")
    password = os.environ.get("SCHEMII_TEST_METADATA_PASSWORD")
    if not dsn or not password:
        pytest.skip("real PostgreSQL requires SCHEMII_TEST_METADATA_DSN and SCHEMII_TEST_METADATA_PASSWORD")
    namespace = f"accounts_it_{uuid.uuid4().hex[:16]}"
    roles = {region: f"{namespace}_{region}" for region in ("east", "west")}
    with psycopg.connect(dsn, password=password, autocommit=True) as admin:
        if not admin.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[0]:
            pytest.skip("account permission fixture requires a disposable PostgreSQL superuser for isolated roles")
        fixture = (Path(__file__).parents[2] / "dev/postgres/account-access-fixture.sql").read_text()
        fixture = fixture.replace("accounts_demo", namespace)
        fixture = fixture.replace("report_east", roles["east"]).replace("report_west", roles["west"])
        fixture = fixture.replace("'report_'", f"'{namespace}_'")

        @contextmanager
        def as_region(region):
            with psycopg.connect(dsn, password=password, autocommit=True) as connection:
                connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(roles[region])))
                yield connection

        try:
            # Bootstrap runs every launch; its second pass must preserve the data.
            admin.execute(fixture)
            admin.execute(fixture)
            yield admin, namespace, roles, as_region
        finally:
            admin.execute("ROLLBACK")
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(namespace)))
            for role in roles.values():
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@pytest.mark.parametrize("region,expected_rows,expected_total", [
    ("east", [(1, "east", 100), (2, "east", 250)], 350),
    ("west", [(3, "west", 800)], 800),
])
def test_rls_applies_to_rows_totals_and_filter_values(reporting_database, region, expected_rows, expected_total):
    _, namespace, _, as_region = reporting_database
    table = sql.Identifier(namespace, "sales")
    with as_region(region) as connection:
        assert connection.execute(sql.SQL("SELECT id,region,amount FROM {} ORDER BY id").format(table)).fetchall() == expected_rows
        assert connection.execute(sql.SQL("SELECT sum(amount) FROM {}").format(table)).fetchone()[0] == expected_total
        assert connection.execute(sql.SQL("SELECT DISTINCT region FROM {}").format(table)).fetchall() == [(region,)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(sql.SQL("SELECT secret FROM {}").format(table))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(sql.SQL("SELECT * FROM {}").format(table))


def test_catalog_accepts_column_only_grants_and_hides_revoked_columns(reporting_database):
    admin, namespace, roles, as_region = reporting_database
    with as_region("east") as connection:
        assert connection.execute("SELECT has_table_privilege(%s, 'SELECT')", (f"{namespace}.sales",)).fetchone() == (False,)
        assert set(connection.execute(READABLE_COLUMNS_QUERY, (namespace, 100)).fetchall()) == {
            ("sales", "id"), ("sales", "region"), ("sales", "amount"),
        }
        admin.execute(sql.SQL("REVOKE SELECT (amount) ON {} FROM {}").format(
            sql.Identifier(namespace, "sales"), sql.Identifier(roles["east"])))
        assert set(connection.execute(READABLE_COLUMNS_QUERY, (namespace, 100)).fetchall()) == {
            ("sales", "id"), ("sales", "region"),
        }
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(sql.SQL("SELECT amount FROM {}").format(sql.Identifier(namespace, "sales")))
        admin.execute(sql.SQL("REVOKE USAGE ON SCHEMA {} FROM {}").format(
            sql.Identifier(namespace), sql.Identifier(roles["east"])))
        assert connection.execute(READABLE_COLUMNS_QUERY, (namespace, 100)).fetchall() == []


def test_reporting_logins_cannot_bypass_rls_or_write(reporting_database):
    _, namespace, _, as_region = reporting_database
    with as_region("east") as connection:
        assert connection.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone() == (False, False)
        table = sql.Identifier(namespace, "sales")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(sql.SQL("UPDATE {} SET amount=0").format(table))
        connection.execute("SET row_security=off")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(sql.SQL("SELECT id FROM {}").format(table))


def test_schemii_owned_profiles_keep_distinct_rls_views_and_cannot_write(reporting_database):
    admin, namespace, roles, _ = reporting_database
    auth = AuthService(enabled=True, setup_token="unused")
    connections = ConnectionService(InMemoryConnectionRepository(), ())
    connections.set_authority(auth)
    profiles = {}
    for region, password in (("east", "schemii-demo-east-only"),
                             ("west", "schemii-demo-west-only")):
        profiles[region] = connections.create_schemii_owned(PostgresConnectionCreate(
            name=f"{region} reporting", host=admin.info.host or "localhost",
            port=admin.info.port, database=admin.info.dbname,
            username=roles[region], password=password))
    with auth.store.transaction(write=True) as state:
        state["users"]["friend"] = dict(
            id="friend", username="friend", display_name="Friend",
            password_hash="unused", is_admin=False, disabled=False)
        state["roles"]["readers"] = dict(
            id="readers", name="Readers", capabilities=["schemii:access"],
            user_ids=["friend"], dashboards=[], connections=[dict(
                owner_id=SCHEMII_CONNECTION_OWNER_ID,
                connection_id=profile.id, allow_authoring=True,
            ) for profile in profiles.values()])

    visible = connections.for_product("schemii")
    assert {profile.id for profile in visible.list("friend")} == {
        profile.id for profile in profiles.values()}
    table = sql.Identifier(namespace, "sales")
    for region, expected_rows in (("east", [(1, "east"), (2, "east")]),
                                  ("west", [(3, "west")])):
        with visible.use("friend", profiles[region].id) as selected:
            assert selected.owner_id == SCHEMII_CONNECTION_OWNER_ID
            assert selected.ownership == "schemii"
            with psycopg.connect(os.environ["SCHEMII_TEST_METADATA_DSN"],
                                 user=selected.username,
                                 password=selected.password.get_secret_value(),
                                 autocommit=True) as connection:
                assert connection.execute(sql.SQL(
                    "SELECT id,region FROM {} ORDER BY id").format(table)).fetchall() == expected_rows
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(sql.SQL("DELETE FROM {}").format(table))


@pytest.fixture
def managed_writer_application(reporting_database):
    """Add scoped writes to one disposable identity, never to launcher demo data."""
    from schemii.common.metadata.factory import MetadataRepositories
    from schemii.common.postgres import PsycopgPostgresGateway
    from schemii.main import ApplicationServices, create_app
    from schemii.schemii.designs.store import InMemoryDesignRepository
    from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository

    admin, namespace, roles, _ = reporting_database
    ddl_namespace = namespace + "_ddl"
    admin.execute(sql.SQL("GRANT UPDATE (amount) ON {} TO {}").format(
        sql.Identifier(namespace, "sales"), sql.Identifier(roles["east"])))
    admin.execute(sql.SQL(
        "CREATE POLICY scoped_update ON {} FOR UPDATE TO {} "
        "USING (region='east') WITH CHECK (region='east')").format(
            sql.Identifier(namespace, "sales"), sql.Identifier(roles["east"])))
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(ddl_namespace)))
    try:
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}, {}").format(
            sql.Identifier(ddl_namespace), sql.Identifier(roles["east"]), sql.Identifier(roles["west"])))
        admin.execute(sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(
            sql.Identifier(ddl_namespace), sql.Identifier(roles["east"])))
        repository = InMemoryConnectionRepository()
        connections = ConnectionService(repository, ())
        designs = InMemoryDesignRepository()
        workspaces = InMemoryWorkspaceRepository(designs=designs)
        gateway = PsycopgPostgresGateway()
        app = create_app(ApplicationServices(
            metadata=MetadataRepositories(connections=repository), connections=connections,
            postgres=gateway, designs=designs, workspaces=workspaces))
        auth = AuthService(enabled=True, setup_token="unused")
        app.state.auth = auth
        connections.set_authority(auth)
        profiles = {}
        for actor, region in (("developer", "east"), ("reporter", "west")):
            profile = connections.create_schemii_owned(PostgresConnectionCreate(
                name=actor, host=admin.info.host or "localhost", port=admin.info.port,
                database=admin.info.dbname, username=roles[region], ssl_mode="prefer",
                password=f"schemii-demo-{region}-only"))
            profiles[actor] = profile
            with auth.store.transaction(write=True) as state:
                state["users"][actor] = dict(
                    id=actor, username=actor, display_name=actor,
                    password_hash="unused", is_admin=False, disabled=False)
                state["roles"][actor] = dict(
                    id=actor, name=actor, capabilities=["schemii:access"],
                    user_ids=[actor], dashboards=[], connections=[dict(
                        owner_id=SCHEMII_CONNECTION_OWNER_ID,
                        connection_id=profile.id, allow_authoring=True)])
        yield SimpleNamespace(admin=admin, namespace=namespace, ddl_namespace=ddl_namespace,
            roles=roles, app=app, services=app.state.services, auth=auth, profiles=profiles)
    finally:
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(ddl_namespace)))


def _managed_workspace(fixture, actor, namespace):
    from schemii.schemii.designs.importer import import_postgres_catalog
    from schemii.schemii.workspaces.models import WorkspaceCreateRecord

    profile = fixture.profiles[actor]
    with fixture.services.connections.for_product("schemii").use(actor, profile.id) as selected:
        catalog = fixture.services.postgres.introspect(selected, namespace)
    return fixture.services.migrations.create_import_workspace(actor, WorkspaceCreateRecord(
        name="Owned permission proof", connection_id=profile.id,
        connection_owner_id=SCHEMII_CONNECTION_OWNER_ID,
        database=profile.database, namespace=namespace),
        import_postgres_catalog(catalog), profile.revision, catalog)


def _console_batch(fixture, actor, workspace, statements):
    from schemii.common.postgres.console.models import (
        ConsoleTransactionCommand, ConsoleTransactionCreate, ConsoleTransactionExecutionCreate,
    )

    console = fixture.services.console
    transaction = console.create_transaction(actor, workspace.id, ConsoleTransactionCreate(
        console_id="con_" + uuid.uuid4().hex, expected_workspace_revision=workspace.revision,
        expected_settings_revision=console.settings(actor).revision))
    try:
        execution = console.reserve_transaction_execution(actor, workspace.id, transaction.id,
            ConsoleTransactionExecutionCreate(expected_revision=transaction.revision, statements=statements))
        console.run_transaction(actor, execution.id)
        result = console.get(actor, workspace.id, execution.id)
        current = console.get_transaction(actor, workspace.id, transaction.id)
        command = ConsoleTransactionCommand(expected_revision=current.revision)
        if result.status == "succeeded":
            console.commit_transaction(actor, workspace.id, transaction.id, command)
        else:
            console.rollback_transaction(actor, workspace.id, transaction.id, command)
        return result
    finally:
        # Close any still-open owned transaction if an assertion or setup fails.
        current = console.get_transaction(actor, workspace.id, transaction.id)
        if current.status in {"open", "failed"}:
            console.rollback_transaction(actor, workspace.id, transaction.id,
                ConsoleTransactionCommand(expected_revision=current.revision))


def test_managed_writer_console_and_ai_obey_rls_column_grants_and_ddl(managed_writer_application):
    from schemii.schemii.ai.service import AiServiceError
    from schemii.schemii.ai.write_actions import execute_write

    fixture = managed_writer_application
    workspace = _managed_workspace(fixture, "developer", fixture.namespace)
    reader = _managed_workspace(fixture, "reporter", fixture.namespace)
    table = f'"{fixture.namespace}".sales'
    console = fixture.services.console
    identity = _console_batch(fixture, "developer", workspace,
        [f"SELECT current_user::text, region, amount FROM {table} ORDER BY id"])
    assert identity.status == "succeeded"
    page = console.page("developer", workspace.id, identity.id, identity.results[0].id, None)
    assert page.rows == [[fixture.roles["east"], "east", 100], [fixture.roles["east"], "east", 250]]
    assert _console_batch(fixture, "developer", workspace,
        [f"UPDATE {table} SET amount=amount+1"]).status == "succeeded"
    outcome = execute_write(fixture.services, "developer", workspace.id, workspace.revision,
        {"statements": [f"UPDATE {table} SET amount=amount+10"]})
    assert outcome["commitOutcome"] == "committed"
    assert fixture.admin.execute(sql.SQL("SELECT amount FROM {} ORDER BY id").format(
        sql.Identifier(fixture.namespace, "sales"))).fetchall() == [(111,), (261,), (800,)]

    # These would all succeed as the fixture administrator. None may fall back.
    for statement in (
        f"SELECT secret FROM {table}", f"UPDATE {table} SET secret='changed'",
        f"ALTER TABLE {table} ADD COLUMN forbidden integer",
    ):
        assert _console_batch(fixture, "developer", workspace, [statement]).status == "failed"
        with pytest.raises(AiServiceError) as denied:
            execute_write(fixture.services, "developer", workspace.id, workspace.revision,
                {"statements": [statement]})
        assert denied.value.code == "ai_write_failed"
    assert _console_batch(fixture, "reporter", reader,
        [f"UPDATE {table} SET amount=0"]).status == "failed"
    with pytest.raises(AiServiceError):
        execute_write(fixture.services, "reporter", reader.id, reader.revision,
            {"statements": [f"UPDATE {table} SET amount=0"]})
    assert fixture.admin.execute(sql.SQL("SELECT secret FROM {} ORDER BY id").format(
        sql.Identifier(fixture.namespace, "sales"))).fetchall() == [
            ("east-private",), ("east-private-two",), ("west-private",)]


def _managed_create_table_plan(fixture, actor):
    from schemii.schemii.designs.models import DesignColumn, DesignTable, SchemiiDesignContent, SchemiiDesignReplace
    from schemii.schemii.migrations.models import MigrationExecutionCreate, MigrationPlanCreate

    workspace = _managed_workspace(fixture, actor, fixture.ddl_namespace)
    current = fixture.services.designs.get(actor, workspace.id)
    table = DesignTable(
        id="table_" + uuid.uuid4().hex, name="owned_development",
        columns=[DesignColumn(id="column_" + uuid.uuid4().hex, name="id", data_type="integer")],
    )
    desired = fixture.services.designs.replace(actor, workspace.id, SchemiiDesignReplace(
        expected_design_revision=current.revision,
        content=SchemiiDesignContent(tables=[table]),
    ))
    migrations = fixture.services.migrations
    plan = migrations.create_plan(actor, workspace.id, MigrationPlanCreate(
        expected_workspace_revision=workspace.revision, expected_design_revision=desired.revision))
    assert plan.apply_capable and len(plan.steps) == 1, plan.model_dump(mode="json")
    execution = migrations.create_execution(actor, plan.id,
        MigrationExecutionCreate(review_digest=plan.review_digest))
    return execution


def _process_managed_migration(fixture):
    coordinator = fixture.services.migrations.execution_coordinator
    now = datetime.now(timezone.utc)
    work = fixture.services.migrations._repository.claim_next_execution(
        claimed_at=now, lease_owner="mls_" + uuid.uuid4().hex,
        lease_expires_at=now + timedelta(minutes=2))
    assert work is not None
    return coordinator.process(work)


@pytest.mark.parametrize("actor,expected", [("developer", "succeeded"), ("reporter", "failed")])
def test_managed_migration_uses_only_granted_schema_ddl_identity(managed_writer_application, actor, expected):
    fixture = managed_writer_application
    _managed_create_table_plan(fixture, actor)
    terminal = _process_managed_migration(fixture)
    assert terminal.status == expected
    row = fixture.admin.execute(
        "SELECT tableowner FROM pg_tables WHERE schemaname=%s AND tablename='owned_development'",
        (fixture.ddl_namespace,)).fetchone()
    if actor == "developer":
        assert row == (fixture.roles["east"],)
    else:
        assert row is None
        assert terminal.error_code == "postgres_migration_failed"


def _managed_catalog(fixture, namespace):
    profile = fixture.profiles["developer"]
    with fixture.services.connections.for_product("schemii").use("developer", profile.id) as selected:
        return fixture.services.postgres.introspect(selected, namespace)


def test_self_referencing_policy_keeps_rls_owner_protected(managed_writer_application):
    from schemii.schemii.designs.importer import import_postgres_catalog
    from schemii.schemii.migrations.planner import migration_compatibility

    fixture = managed_writer_application
    catalog = _managed_catalog(fixture, fixture.namespace)
    assert catalog.safety.dependency_complete
    imported = import_postgres_catalog(catalog)
    assert not imported.summary.complete
    assert any(issue.category == "row_security" for issue in imported.summary.issues)
    desired = imported.content.model_copy(deep=True)
    amount = next(column for column in desired.tables[0].columns if column.name == "amount")
    amount.nullable = True
    compatibility = migration_compatibility(catalog, imported.content, desired)
    assert not compatibility.operation_safe
    assert "Changes intersect the protected dependency closure" in compatibility.blocking_reasons
    assert any(item.namespace == fixture.namespace and item.name == "sales"
               for item in compatibility.protected_scope)


def test_external_policy_reference_retains_graph_edge_and_unknown_boundary(managed_writer_application):
    from schemii.common.postgres.models import PostgresObjectIdentity
    from schemii.schemii.designs.importer import import_postgres_catalog
    from schemii.schemii.migrations.planner import migration_compatibility

    fixture = managed_writer_application
    fixture.admin.execute(sql.SQL("CREATE TABLE {} (id integer NOT NULL)").format(
        sql.Identifier(fixture.ddl_namespace, "policy_scope")))
    fixture.admin.execute(sql.SQL(
        "CREATE POLICY referenced_scope ON {} FOR SELECT TO {} "
        "USING (EXISTS (SELECT 1 FROM {} scope WHERE scope.id=sales.id))").format(
            sql.Identifier(fixture.namespace, "sales"), sql.Identifier(fixture.roles["east"]),
            sql.Identifier(fixture.ddl_namespace, "policy_scope")))
    owner = PostgresObjectIdentity(kind="relation", namespace=fixture.namespace, name="sales")
    referenced = PostgresObjectIdentity(kind="relation", namespace=fixture.ddl_namespace, name="policy_scope")
    for namespace in (fixture.namespace, fixture.ddl_namespace):
        catalog = _managed_catalog(fixture, namespace)
        assert any(edge.source == owner and edge.target == referenced
                   for edge in catalog.safety.dependencies)
        assert not catalog.safety.dependency_complete
        imported = import_postgres_catalog(catalog)
        desired = imported.content.model_copy(deep=True)
        desired.tables[0].columns[0].nullable = True
        compatibility = migration_compatibility(catalog, imported.content, desired)
        assert not compatibility.operation_safe
        assert "Dependency evidence is incomplete" in compatibility.blocking_reasons
        if namespace == fixture.namespace:
            assert {owner, referenced} <= set(compatibility.protected_scope)


def test_revoked_managed_migration_and_ai_do_not_use_personal_or_peer_profile(managed_writer_application):
    from schemii.common.connections.store import ConnectionNotFoundError
    from schemii.schemii.ai.write_actions import execute_write
    from schemii.schemii.console.service import ConsoleServiceError

    fixture = managed_writer_application
    execution = _managed_create_table_plan(fixture, "developer")
    fixture.services.connections.create("developer", PostgresConnectionCreate(
        name="Stronger private profile", host=fixture.admin.info.host or "localhost",
        port=fixture.admin.info.port, database=fixture.admin.info.dbname,
        username=fixture.admin.info.user, password=os.environ["SCHEMII_TEST_METADATA_PASSWORD"]))
    with fixture.auth.store.transaction(write=True) as state:
        state["roles"]["developer"]["connections"] = []
    terminal = _process_managed_migration(fixture)
    assert terminal.id == execution.id and terminal.status == "failed"
    assert fixture.admin.execute("SELECT 1 FROM pg_tables WHERE schemaname=%s", (fixture.ddl_namespace,)).fetchone() is None
    with pytest.raises(ConsoleServiceError) as revoked:
        execute_write(fixture.services, "developer", execution.workspace_id, 1,
            {"statements": [f'CREATE TABLE "{fixture.ddl_namespace}".forbidden (id integer)']})
    assert revoked.value.code == "console_connection_missing"
    with pytest.raises(ConnectionNotFoundError):
        fixture.services.connections.for_product("schemii").get("developer", fixture.profiles["developer"].id)
    peer = _managed_workspace(fixture, "reporter", fixture.namespace)
    result = _console_batch(fixture, "reporter", peer,
        [f'SELECT region FROM "{fixture.namespace}".sales'])
    assert result.status == "succeeded"
    page = fixture.services.console.page("reporter", peer.id, result.id, result.results[0].id, None)
    assert page.rows == [["west"]]
