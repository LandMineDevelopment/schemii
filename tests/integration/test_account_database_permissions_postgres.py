"""Real PostgreSQL column grants and RLS using isolated demo-fixture copies.

Uses the established SCHEMII_TEST_METADATA_DSN/PASSWORD integration target.
The selected disposable target must allow superuser role provisioning; the
ordinary production metadata account intentionally cannot run these tests.
"""

from contextlib import contextmanager
from pathlib import Path
import os
import uuid

import psycopg
from psycopg import sql
import pytest

from schemii.common.postgres.queries import READABLE_COLUMNS_QUERY


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
