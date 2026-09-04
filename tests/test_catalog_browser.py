from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from schemii.common.postgres.console.execution import ConsoleQueryResult
from schemii.common.postgres.console.models import ConsoleResultColumn
from schemii.common.postgres.models import (
    PostgresColumn,
    PostgresTable,
    PostgresView,
    build_postgres_catalog,
)
from schemii.schemii.catalog.service import RelationBrowserError, RelationBrowserService


def catalog():
    columns = (
        PostgresColumn(name="id", ordinal=1, data_type="bigint", nullable=False),
        PostgresColumn(name="name", ordinal=2, data_type="text", nullable=False),
    )
    return build_postgres_catalog(
        database="demo",
        namespace="public",
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=(PostgresTable(namespace="public", name="customers", kind="table", is_partition=False, columns=columns),),
        relationships=(),
        functions=(),
        views=(PostgresView(namespace="public", name="customer_names", columns=columns, query_definition="SELECT id, name FROM customers"),),
        materialized_views=(),
        captured_at=datetime.now(timezone.utc),
    )


class Workspaces:
    def get(self, owner_id, workspace_id):
        assert owner_id == "owner"
        return SimpleNamespace(id=workspace_id, connection_id="pg_1", database="demo", namespace="public")


class Connections:
    @contextmanager
    def use(self, owner_id, connection_id):
        assert (owner_id, connection_id) == ("owner", "pg_1")
        yield SimpleNamespace(database="demo")


class Postgres:
    def __init__(self):
        self.value = catalog()
        self.statements = []

    def introspect(self, connection, namespace):
        assert namespace == "public"
        return self.value

    def execute_console(self, connection, namespace, statements, *, on_started):
        assert on_started(42)
        self.statements.extend(statements)
        return (ConsoleQueryResult(
            statement_index=0,
            command="SELECT",
            columns=(ConsoleResultColumn(name="id", data_type="bigint"), ConsoleResultColumn(name="name", data_type="text")),
            rows=((1, "Ada"), (2, "Grace")),
            truncated=False,
        ),)


def service():
    postgres = Postgres()
    return RelationBrowserService(workspaces=Workspaces(), connections=Connections(), postgres=postgres), postgres


def test_relation_browser_derives_refs_details_rows_and_view_analysis():
    browser, postgres = service()
    listing = browser.list("owner", "ws_" + "1" * 32, cursor=None, page_size=10, search=None)
    assert [(item.kind, item.name, item.column_count) for item in listing.relations] == [
        ("table", "customers", 2),
        ("view", "customer_names", 2),
    ]

    table = listing.relations[0]
    detail = browser.detail("owner", listing.workspace_id, table.ref)
    assert detail.relation.name == "customers"
    rows = browser.rows("owner", listing.workspace_id, table.ref, cursor=None, page_size=10)
    assert rows.rows == [[1, "Ada"], [2, "Grace"]]
    assert rows.columns[0].data_type == "bigint"
    assert postgres.statements == ['SELECT * FROM "public"."customers" OFFSET 0 LIMIT 11']

    view = listing.relations[1]
    lineage = browser.lineage("owner", listing.workspace_id, view.ref)
    assert lineage.analysis is not None
    assert lineage.analysis.sources[0].name == "customers"
    assert any(edge.classification == "dependency" for edge in lineage.edges)


def test_relation_cursor_is_bound_to_search_and_catalog_fingerprint():
    browser, _ = service()
    workspace_id = "ws_" + "2" * 32
    first = browser.list("owner", workspace_id, cursor=None, page_size=1, search=None)
    assert first.next_cursor
    second = browser.list("owner", workspace_id, cursor=first.next_cursor, page_size=1, search=None)
    assert second.relations[0].name == "customer_names"
    with pytest.raises(RelationBrowserError, match="restart browsing"):
        browser.list("owner", workspace_id, cursor=first.next_cursor, page_size=1, search="view")
