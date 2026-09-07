"""Semantic model metadata isolation, concurrency and privacy contracts."""

from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from pydantic import ValidationError

from schemii.schemoo.models import (
    ExploreUpdate, LayoutUpdate, ModelCreate, ModelDefinition, ModelUpdate,
)
from schemii.schemoo.store import (
    InMemoryModelRepository, PostgresModelRepository, ModelConflictError, ModelLimitError, ModelNotFoundError, ModelDocumentLimitError,
)


def request(**changes):
    return ModelCreate.model_validate({
        "name": "Staffing", "connection_id": "pg_" + "a" * 32,
        "database": "organization", "namespace": "public",
        "definition": {"root": "people", "nodes": [{"id": "people", "table": "people", "label": "People"}]},
        **changes,
    })


def test_models_are_owner_private_and_multiple_models_share_one_source():
    store = InMemoryModelRepository()
    first = store.create("alice", request())
    second = store.create("alice", request(name="Management"))
    assert first.id != second.id
    assert len(store.list("alice")) == 2
    assert store.list("bob") == []
    with pytest.raises(ModelNotFoundError):
        store.get("bob", first.id)
    with pytest.raises(ModelNotFoundError):
        store.delete("bob", first.id, 1)
    assert store.count_for_connection("alice", first.connection_id) == 2
    assert store.dependencies_for_connection("alice", first.connection_id)[0].kind == "semantic_model"


def test_library_projection_does_not_return_definition_layout_or_explore():
    store = InMemoryModelRepository()
    saved = store.create("alice", request())
    summary = store.list("alice")[0]
    assert summary.id == saved.id and summary.revision == saved.revision
    assert {"definition", "layout", "explore"}.isdisjoint(summary.model_dump())
    assert store.get("alice", saved.id).definition.nodes


def test_document_byte_limit_accepts_exact_boundary_and_rejects_without_mutation():
    content = {"root": "people", "nodes": [{"id": "people", "table": "people", "label": "é" * 100}]}
    data = request(definition=content)
    size = len(json.dumps(data.definition.model_dump(mode="json"), ensure_ascii=True).encode("utf-8"))
    store = InMemoryModelRepository(maximum_document_bytes=size)
    saved = store.create("alice", data)
    assert saved.definition.nodes[0].label == "é" * 100
    limited = InMemoryModelRepository(maximum_document_bytes=size - 1)
    with pytest.raises(ModelDocumentLimitError, match="definition exceeds") as caught:
        limited.create("alice", data)
    assert caught.value.limit == size - 1 and caught.value.document == "definition"
    assert limited.list("alice") == []
    with pytest.raises(ModelDocumentLimitError):
        store.update_explore("alice", saved.id, ExploreUpdate(expected_revision=1,
            explore={"selections": {"scope": {"values": {"value": "a" * (size + 1)}}}}))
    unchanged = store.get("alice", saved.id)
    assert unchanged.explore_revision == 1 and unchanged.explore.selections == {}


def test_postgres_adapter_checks_document_limit_before_opening_storage():
    def forbidden_connection():
        raise AssertionError("Oversized document must not open metadata storage")
    store = PostgresModelRepository(forbidden_connection, maximum_document_bytes=1)
    with pytest.raises(ModelDocumentLimitError):
        store.create("alice", request())
    with pytest.raises(ModelDocumentLimitError):
        store.update("alice", "unused", ModelUpdate(expected_revision=1, name="Name", definition={}))
    with pytest.raises(ModelDocumentLimitError):
        store.update_layout("alice", "unused", LayoutUpdate(expected_revision=1, layout={}))
    with pytest.raises(ModelDocumentLimitError):
        store.update_explore("alice", "unused", ExploreUpdate(expected_revision=1, explore={}))


def test_separate_revisions_prevent_cross_tab_overwrites_without_layout_conflicts():
    store = InMemoryModelRepository()
    model = store.create("alice", request())
    layout = store.update_layout("alice", model.id, LayoutUpdate(expected_revision=1, layout={"positions": [{"id": "people", "x": 5, "y": 9}]}))
    assert layout.revision == 1 and layout.layout_revision == 2
    saved = store.update("alice", model.id, ModelUpdate(expected_revision=1, name="New name", definition=model.definition))
    assert saved.revision == 2 and saved.layout.positions[0].x == 5
    explored = store.update_explore("alice", model.id, ExploreUpdate(expected_revision=1, explore={"fields": [{"table": "people", "column": "name"}]}))
    assert explored.revision == 2 and explored.explore_revision == 2
    with pytest.raises(ModelConflictError):
        store.update_layout("alice", model.id, LayoutUpdate(expected_revision=1, layout={}))
    with pytest.raises(ModelConflictError):
        store.delete("alice", model.id, 1)
    store.delete("alice", model.id, 2)
    assert store.list("alice") == []


def test_copies_do_not_mutate_saved_definition():
    store = InMemoryModelRepository()
    data = request()
    model = store.create("alice", data)
    data.definition.nodes.clear()
    model.definition.nodes.clear()
    assert len(store.get("alice", model.id).definition.nodes) == 1


def test_limit_is_atomic_under_concurrent_creation():
    store = InMemoryModelRepository(maximum_models_per_owner=2)
    def create(_):
        try:
            return store.create("alice", request()).id
        except ModelLimitError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(16)))
    assert sum(result is not None for result in results) == 2
    assert len(store.list("alice")) == 2
    assert store.create("bob", request())


def test_typed_documents_reject_query_rows_and_unbounded_values():
    with pytest.raises(ValidationError):
        request(explore={"rows": [["sensitive"]]})
    with pytest.raises(ValidationError):
        request(definition={"nodes": [{"id": "people", "table": "people", "label": "People", "rows": []}]})
    with pytest.raises(ValidationError):
        request(explore={"selections": {"scope": {"values": {"param": "a" * 4001}}}})
    with pytest.raises(ValidationError):
        request(layout={"positions": [{"id": "people", "x": float("inf"), "y": 1}]})


def test_wire_contract_uses_common_camel_case_aliases():
    model = InMemoryModelRepository().create("alice", request())
    document = model.model_dump(mode="json", by_alias=True)
    assert document["connectionId"] == model.connection_id
    assert document["layoutRevision"] == 1
    assert "owner_id" not in document
    assert ModelCreate.model_validate({key: value for key, value in document.items() if key not in {"id", "ownerId", "revision", "layoutRevision", "exploreRevision", "createdAt", "updatedAt"}})


def test_invalid_drafts_can_be_saved_for_repair_but_duplicate_ids_cannot():
    definition = ModelDefinition(scopes=[{"id": "scope", "kind": "required", "alternatives": [{"id": "a", "conditions": [{"table": "", "column": ""}]}]}])
    assert InMemoryModelRepository().create("alice", request(definition=definition))
    with pytest.raises(ValidationError, match="Duplicate node"):
        request(definition={"nodes": [{"id": "a", "table": "t", "label": "A"}] * 2})
