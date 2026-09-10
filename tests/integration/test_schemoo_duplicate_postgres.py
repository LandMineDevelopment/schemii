"""Duplication transactions preserve saved state across PostgreSQL workers."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from schemii.common.connections.models import PostgresConnectionCreate
from schemii.schemoo import store as model_store
from schemii.schemoo.models import ModelCreate, ModelDuplicate, PreviewCreate, PreviewUpdate
from schemii.schemoo.store import (
    PostgresModelRepository, ModelConflictError, ModelDocumentLimitError, ModelNotFoundError,
    ModelStorageUnavailableError,
)


@pytest.fixture
def source(postgres_metadata):
    harness = postgres_metadata
    connection = harness.repositories.connections.create(harness.owner_id, PostgresConnectionCreate(
        name="Duplicate source", host="application-postgres", database="application_data", username="application_user"))
    store = PostgresModelRepository(harness.connection_factory)
    model = store.create(harness.owner_id, ModelCreate(name="Draft", connection_id=connection.id,
        database=connection.database, namespace="public", definition={"root": "missing"},
        layout={"positions": [{"id": "missing", "x": 40, "y": 20}]},
        explore={"root": "missing", "selections": {"scope": {"values": {"day": "today"}}}}))
    store.create_preview(harness.owner_id, model.id, PreviewCreate(name="Current", explore={"root": "first"}))
    store.create_preview(harness.owner_id, model.id, PreviewCreate(name="Historical", explore={"root": "failure"}))
    return harness, store, model


def body(model):
    return ModelDuplicate(name="Draft copy", expected_revision=model.revision,
        expected_layout_revision=model.layout_revision, expected_explore_revision=model.explore_revision)


def test_duplicate_postgres_roundtrip_ownership_revisions_and_independence(source):
    harness, store, model = source
    owner = harness.owner_id
    originals = store.list_previews(owner, model.id)
    with pytest.raises(ModelNotFoundError):
        store.duplicate("other", model.id, body(model))
    for field in ("expected_revision", "expected_layout_revision", "expected_explore_revision"):
        with pytest.raises(ModelConflictError):
            store.duplicate(owner, model.id, body(model).model_copy(update={field: 2}))
    limited = PostgresModelRepository(harness.connection_factory, maximum_document_bytes=100)
    with pytest.raises(ModelDocumentLimitError):
        limited.duplicate(owner, model.id, body(model))
    assert len(store.list(owner)) == 1
    copied = store.duplicate(owner, model.id, body(model))
    reopened = PostgresModelRepository(harness.connection_factory)
    assert reopened.get(owner, copied.id) == copied
    assert copied.id != model.id
    assert (copied.revision, copied.layout_revision, copied.explore_revision) == (1, 1, 1)
    assert copied.definition == model.definition and copied.layout == model.layout and copied.explore == model.explore
    previews = reopened.list_previews(owner, copied.id)
    assert [(p.name, p.explore) for p in previews] == [(p.name, p.explore) for p in originals]
    assert {p.id for p in previews}.isdisjoint(p.id for p in originals)
    assert all(p.model_id == copied.id and p.revision == 1 for p in previews)
    reopened.update_preview(owner, copied.id, previews[0].id,
        PreviewUpdate(expected_revision=1, name="Changed copy", explore={}))
    reopened.delete(owner, copied.id, 1)
    assert reopened.get(owner, model.id) == model
    assert reopened.list_previews(owner, model.id) == originals


def test_duplicate_postgres_rolls_back_model_and_previews_after_partial_insert(source, monkeypatch):
    harness, store, model = source
    original_adapter = model_store.Jsonb
    def fail_second_preview(document):
        if document.get("root") == "failure":
            raise RuntimeError("Injected second preview insert failure")
        return original_adapter(document)
    monkeypatch.setattr(model_store, "Jsonb", fail_second_preview)
    with pytest.raises(ModelStorageUnavailableError):
        store.duplicate(harness.owner_id, model.id, body(model))
    assert [item.id for item in store.list(harness.owner_id)] == [model.id]
    with harness.connection_factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) AS count FROM schemoo.model_previews WHERE owner_id = %s", (harness.owner_id,))
            assert cursor.fetchone()["count"] == 2


def test_duplicate_postgres_snapshot_serializes_with_preview_writers(source, monkeypatch):
    harness, store, model = source
    snapshot_locked, release_snapshot, writer_started = Event(), Event(), Event()
    read_previews = store._preview_rows
    def pause_at_snapshot(cursor, owner, model_id):
        snapshot_locked.set()
        assert release_snapshot.wait(10), "Snapshot was not released"
        return read_previews(cursor, owner, model_id)
    monkeypatch.setattr(store, "_preview_rows", pause_at_snapshot)
    other_worker = PostgresModelRepository(harness.connection_factory)
    def write_preview():
        writer_started.set()
        return other_worker.create_preview(harness.owner_id, model.id, PreviewCreate(name="Concurrent", explore={}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        copying = pool.submit(store.duplicate, harness.owner_id, model.id, body(model))
        try:
            assert snapshot_locked.wait(10)
            writing = pool.submit(write_preview)
            assert writer_started.wait(10)
            # The writer cannot complete while the source model is locked.
            with pytest.raises(TimeoutError):
                writing.result(timeout=0.2)
        finally:
            release_snapshot.set()
        copied = copying.result(timeout=10)
        writing.result(timeout=10)
    assert len(other_worker.list_previews(harness.owner_id, copied.id)) == 2
    assert len(other_worker.list_previews(harness.owner_id, model.id)) == 3
