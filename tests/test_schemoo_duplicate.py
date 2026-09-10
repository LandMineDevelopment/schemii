"""Saved model duplication is atomic, owner scoped and independent of its source."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from schemii.schemoo.models import (
    ExploreUpdate, LayoutUpdate, ModelDuplicate, ModelUpdate, PreviewCreate, PreviewUpdate,
)
from schemii.schemoo.store import (
    InMemoryModelRepository, ModelConflictError, ModelDocumentLimitError, ModelLimitError,
    ModelNotFoundError,
)
from tests.test_schemoo_store import request


def duplicate_request(model, **changes):
    return ModelDuplicate(name="  Staffing copy  ", expected_revision=model.revision,
        expected_layout_revision=model.layout_revision,
        expected_explore_revision=model.explore_revision, **changes)


def test_duplicate_copies_saved_documents_and_previews_with_independent_lifetimes():
    store = InMemoryModelRepository()
    source = store.create("alice", request(catalog_fingerprint="saved-schema",
        definition={"root": "people", "nodes": [
            {"id": "people", "table": "people", "label": "People"},
            {"id": "manager", "table": "people", "label": "Manager"}],
            "scopes": [{"id": "draft", "kind": "required", "alternatives": [
                {"id": "unfinished", "conditions": [{"table": "", "column": ""}]}]}]}))
    store.update("alice", source.id, ModelUpdate(expected_revision=1, name="Staffing",
        definition=source.definition, catalog_fingerprint=source.catalog_fingerprint))
    store.update_layout("alice", source.id, LayoutUpdate(expected_revision=1,
        layout={"positions": [{"id": "manager", "x": 40, "y": 60}]}))
    source = store.update_explore("alice", source.id, ExploreUpdate(expected_revision=1,
        explore={"root": "manager", "fields": [{"table": "manager", "column": "name"}],
            "selections": {"draft": {"alternativeId": "unfinished", "values": {"day": "today"}}}}))
    first = store.create_preview("alice", source.id, PreviewCreate(name="Current", explore=source.explore))
    store.update_preview("alice", source.id, first.id, PreviewUpdate(expected_revision=1,
        name="Updated current", explore=source.explore))
    store.create_preview("alice", source.id, PreviewCreate(name="Empty draft", explore={}))
    originals = store.list_previews("alice", source.id)

    copied = store.duplicate("alice", source.id, duplicate_request(source))
    copies = store.list_previews("alice", copied.id)
    assert copied.id != source.id and copied.name == "Staffing copy"
    assert copied.owner_id == source.owner_id
    assert (copied.revision, copied.layout_revision, copied.explore_revision) == (1, 1, 1)
    for field in ("definition", "layout", "explore", "connection_id", "database", "namespace", "catalog_fingerprint"):
        assert getattr(copied, field) == getattr(source, field)
    assert copied.created_at >= source.created_at
    assert [(p.name, p.explore) for p in copies] == [(p.name, p.explore) for p in originals]
    assert {p.id for p in copies}.isdisjoint(p.id for p in originals)
    assert all(p.model_id == copied.id and p.revision == 1 for p in copies)
    copied.definition.nodes[0].label = "Local mutation"
    copies[0].explore.selections["draft"].values["day"] = "yesterday"
    assert store.get("alice", copied.id).definition == source.definition
    assert store.list_previews("alice", copied.id)[0].explore == originals[0].explore
    store.update("alice", copied.id, ModelUpdate(expected_revision=1, name="Changed copy", definition={}))
    store.update_preview("alice", copied.id, copies[0].id, PreviewUpdate(expected_revision=1,
        name="Changed copy", explore={}))
    store.delete_preview("alice", copied.id, copies[1].id, 1)
    store.delete("alice", copied.id, 2)
    assert store.get("alice", source.id) == source
    assert store.list_previews("alice", source.id) == originals


@pytest.mark.parametrize("revision_field", ["expected_revision", "expected_layout_revision", "expected_explore_revision"])
def test_duplicate_rejects_each_stale_revision_without_creating_anything(revision_field):
    store = InMemoryModelRepository()
    source = store.create("alice", request())
    body = duplicate_request(source).model_copy(update={revision_field: 2})
    with pytest.raises(ModelConflictError) as caught:
        store.duplicate("alice", source.id, body)
    assert caught.value.current_revision == 1
    assert len(store.list("alice")) == 1 and store._previews == {}


def test_duplicate_owner_scope_and_document_limits_leave_no_partial_copy():
    store = InMemoryModelRepository()
    source = store.create("alice", request())
    preview = store.create_preview("alice", source.id, PreviewCreate(name="Large", explore={
        "selections": {"s": {"values": {"p": "x" * 2000}}}}))
    body = duplicate_request(source)
    with pytest.raises(ModelNotFoundError):
        store.duplicate("bob", source.id, body)
    for limit, match in ((1, "definition"), (1000, "saved previews")):
        store._maximum_document_bytes = limit
        with pytest.raises(ModelDocumentLimitError, match=match):
            store.duplicate("alice", source.id, body)
        assert len(store.list("alice")) == 1
        assert store.list_previews("alice", source.id) == [preview]
        assert len(store._previews) == 1


def test_concurrent_duplication_enforces_owner_model_limit():
    store = InMemoryModelRepository(maximum_models_per_owner=2)
    source = store.create("alice", request())
    store.create_preview("alice", source.id, PreviewCreate(name="Test", explore={}))
    def duplicate(_):
        try:
            return store.duplicate("alice", source.id, duplicate_request(source))
        except ModelLimitError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(duplicate, range(8)))
    assert sum(result is not None for result in results) == 1
    assert len(store.list("alice")) == 2 and len(store._previews) == 2


@pytest.mark.parametrize("changes", [{"name": " "}, {"name": "x" * 129}, {"expected_revision": 0},
    {"definition": {}}, {"ownerId": "bob"}, {"rows": [["private"]]}])
def test_duplicate_request_rejects_invalid_names_revisions_and_extra_content(changes):
    with pytest.raises(ValidationError):
        ModelDuplicate.model_validate({"name": "Copy", "expectedRevision": 1,
            "expectedLayoutRevision": 1, "expectedExploreRevision": 1, **changes})
