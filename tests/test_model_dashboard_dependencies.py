"""Dashboard dependencies are checked at the model repository mutation boundary."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from schemii.schemoo.store import InMemoryModelRepository, ModelInUseError, ModelNotFoundError
from schemii.schemer.dashboard_store import InMemoryDashboardRepository
from schemii.schemer.dashboard_models import DashboardCreate
from tests.test_schemoo_store import request


def stores():
    models = InMemoryModelRepository()
    dashboards = InMemoryDashboardRepository(models=models)
    model = models.create("alice", request())
    body = DashboardCreate(name="Staffing overview", model_id=model.id, model_revision=1)
    return models, dashboards, model, body


def test_model_dependencies_are_owner_scoped_and_block_only_referenced_model():
    models, dashboards, model, body = stores()
    saved = dashboards.create("alice", body)
    expected = [{"id": saved.id, "name": saved.name}]
    assert models.dashboard_dependencies("alice", model.id) == expected
    with pytest.raises(ModelNotFoundError):
        models.dashboard_dependencies("bob", model.id)
    with pytest.raises(ModelNotFoundError):
        dashboards.create("bob", body)
    with pytest.raises(ModelInUseError) as blocked:
        models.delete("alice", model.id, 1)
    assert blocked.value.dashboards == expected
    assert models.get("alice", model.id) == model
    unrelated = models.create("alice", request(name="Unrelated"))
    models.delete("alice", unrelated.id, 1)
    dashboards.delete("alice", saved.id, 1)
    models.delete("alice", model.id, 1)
    with pytest.raises(ModelNotFoundError):
        dashboards.create("alice", body)


@pytest.mark.parametrize("winner", ["create", "delete"])
def test_memory_create_delete_race_cannot_strand_dashboard(monkeypatch, winner):
    models, dashboards, model, body = stores()
    entered, release, competitor = Event(), Event(), Event()
    original = models.get if winner == "create" else models.dashboard_dependencies

    def pause(*args):
        value = original(*args)
        entered.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(models, "get" if winner == "create" else "dashboard_dependencies", pause)
    create = lambda: dashboards.create("alice", body)
    delete = lambda: models.delete("alice", model.id, 1)

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
    assert bool(dashboards.list("alice")) == (winner == "create")
