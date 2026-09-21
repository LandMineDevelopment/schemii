"""Cross-connection dashboard insertion/model deletion race regression checks."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest
from pydantic import SecretStr

from schemii.common.connections.models import PostgresConnectionCreate
from schemii.schemoo.models import ModelCreate
from schemii.schemoo.store import PostgresModelRepository, ModelInUseError, ModelNotFoundError
from schemii.schemer.dashboard_store import PostgresDashboardRepository
from schemii.schemer.dashboard_models import DashboardCreate


def create_model(harness):
    connection = harness.repositories.connections.create(harness.owner_id, PostgresConnectionCreate(
        name="Dependency source", host="application-postgres", database="application_data",
        username="application_user", password=SecretStr("target-only-secret")))
    model = PostgresModelRepository(harness.connection_factory).create(harness.owner_id,
        ModelCreate(name="People", connection_id=connection.id, database=connection.database, namespace="public"))
    return model


@pytest.mark.parametrize("winner", ["create", "delete"])
def test_durable_create_delete_race_cannot_strand_dashboard(postgres_metadata, winner):
    harness = postgres_metadata
    model = create_model(harness)
    entered, release, competitor = Event(), Event(), Event()
    statement = "INSERT INTO schemer.dashboards" if winner == "create" else "DELETE FROM schemoo.models"

    @contextmanager
    def gated_factory():
        with harness.connection_factory() as connection:
            class Cursor:
                def __init__(self, cursor):
                    self.cursor = cursor

                def execute(self, query, params=None):
                    result = self.cursor.execute(query, params)
                    if query.startswith(statement):
                        entered.set()
                        assert release.wait(5)
                    return result

                def __getattr__(self, name):
                    return getattr(self.cursor, name)

            class Connection:
                @contextmanager
                def cursor(self):
                    with connection.cursor() as cursor:
                        yield Cursor(cursor)

            yield Connection()

    models = PostgresModelRepository(gated_factory if winner == "delete" else harness.connection_factory)
    dashboards = PostgresDashboardRepository(gated_factory if winner == "create" else harness.connection_factory)
    body = DashboardCreate(name="People overview", model_id=model.id, model_revision=1)
    create = lambda: dashboards.create(harness.owner_id, body)
    delete = lambda: models.delete(harness.owner_id, model.id, 1)

    def second():
        competitor.set()
        return (delete if winner == "create" else create)()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create if winner == "create" else delete)
        try:
            assert entered.wait(5)
            other = pool.submit(second)
            assert competitor.wait(5)
        finally:
            release.set()
        first.result(timeout=5)
        with pytest.raises(ModelInUseError if winner == "create" else ModelNotFoundError):
            other.result(timeout=5)
    assert bool(dashboards.list(harness.owner_id)) == (winner == "create")
    assert harness.repositories.connections.get(harness.owner_id, model.connection_id)


def test_durable_dependency_details_and_resolution(postgres_metadata):
    harness = postgres_metadata
    model = create_model(harness)
    models = PostgresModelRepository(harness.connection_factory)
    dashboards = PostgresDashboardRepository(harness.connection_factory)
    saved = dashboards.create(harness.owner_id,
        DashboardCreate(name="People overview", model_id=model.id, model_revision=1))
    expected = [{"id": saved.id, "name": saved.name}]
    assert models.dashboard_dependencies(harness.owner_id, model.id) == expected
    with pytest.raises(ModelNotFoundError):
        models.dashboard_dependencies("other-owner", model.id)
    with pytest.raises(ModelInUseError) as blocked:
        models.delete(harness.owner_id, model.id, 1)
    assert blocked.value.dashboards == expected
    dashboards.delete(harness.owner_id, saved.id, 1)
    models.delete(harness.owner_id, model.id, 1)
    with pytest.raises(ModelNotFoundError):
        dashboards.create(harness.owner_id,
            DashboardCreate(name="Too late", model_id=model.id, model_revision=1))
