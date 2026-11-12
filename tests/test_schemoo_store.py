"""Semantic model metadata isolation, concurrency and privacy contracts."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Event
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from schemii.schemoo.models import (
    ExploreUpdate, LayoutUpdate, ModelCreate, ModelDefinition, ModelUpdate, PreviewCreate, PreviewUpdate,
)
from schemii.schemoo.store import (
    InMemoryModelRepository, PostgresModelRepository, ModelConflictError, ModelInUseError, ModelLimitError, ModelNotFoundError, ModelDocumentLimitError, PreviewNameConflictError,
)


def test_repository_annotations_resolve_despite_list_method_name():
    from schemii.schemoo.models import SavedPreview
    from schemii.schemoo.store import ModelRepository
    assert get_type_hints(ModelRepository.list_previews)["return"] == list[SavedPreview]


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


@pytest.mark.parametrize("change", ["layout", "explore", "preview"])
def test_guarded_delete_preserves_changes_committed_after_cleanup_preflight(change):
    store = InMemoryModelRepository()
    model = store.create("alice", request())
    assert store.list_previews("alice", model.id) == []
    written, release, deleting = Event(), Event(), Event()

    def write():
        with store._lock:
            if change == "layout":
                store.update_layout("alice", model.id, LayoutUpdate(expected_revision=1,
                    layout={"positions": [{"id": "people", "x": 19, "y": 23}]}))
            elif change == "explore":
                store.update_explore("alice", model.id, ExploreUpdate(expected_revision=1,
                    explore={"root": "people"}))
            else:
                store.create_preview("alice", model.id, PreviewCreate(name="Concurrent saved view", explore={}))
            written.set()
            assert release.wait(5)

    def delete():
        deleting.set()
        store.delete("alice", model.id, model.revision, expected_layout_revision=model.layout_revision,
                     expected_explore_revision=model.explore_revision, require_no_previews=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(write)
        try:
            assert written.wait(5)
            pending = pool.submit(delete)
            assert deleting.wait(5)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.05)
        finally:
            release.set()
        writing.result(timeout=5)
        current, previews = store.get("alice", model.id), store.list_previews("alice", model.id)
        with pytest.raises(ModelConflictError, match="saved previews" if change == "preview" else change):
            pending.result(timeout=5)
    assert store.get("alice", model.id) == current
    assert store.list_previews("alice", model.id) == previews
    assert current.revision == model.revision
    with pytest.raises(ModelNotFoundError):
        store.delete("bob", model.id, 1, expected_layout_revision=1,
                     expected_explore_revision=1, require_no_previews=True)
    for preview in previews:
        store.delete_preview("alice", model.id, preview.id, preview.revision)
    store.delete("alice", model.id, current.revision, expected_layout_revision=current.layout_revision,
                 expected_explore_revision=current.explore_revision, require_no_previews=True)
    assert store.list("alice") == []


def test_omitted_delete_guards_preserve_deliberate_independent_state_and_preview_cascade_contract():
    store = InMemoryModelRepository()
    model = store.create("alice", request())
    store.update_explore("alice", model.id, ExploreUpdate(expected_revision=1, explore={"root": "people"}))
    store.create_preview("alice", model.id, PreviewCreate(name="Deliberate cascade", explore={}))
    store.delete("alice", model.id, 1, expected_layout_revision=1)
    assert store.list("alice") == [] and ("alice", model.id) not in store._previews


@pytest.mark.parametrize("state", ["layout", "explore", "preview", "semantic", "owner", "dependency", "empty", "legacy"])
def test_postgres_delete_checks_locked_parent_state_before_any_delete(state):
    model = InMemoryModelRepository().create("alice", request())
    row = model.model_dump()
    if state in {"layout", "explore", "semantic"}:
        row[{"layout": "layout_revision", "explore": "explore_revision", "semantic": "revision"}[state]] = 2
    previews = [{"id": "preview_concurrent"}] if state in {"preview", "legacy"} else []
    dependencies = [{"id": "dashboard_sentinel", "name": "Keep this dashboard"}] if state == "dependency" else []
    original = deepcopy((row, previews, dependencies))

    class Storage:
        def __init__(self):
            self.statements, self.rows = [], []
            self.deleted = self.rolled_back = False

        def __enter__(self):
            return self

        def __exit__(self, kind, *_args):
            self.rolled_back = kind is not None

        def cursor(self):
            return self

        def execute(self, query, parameters):
            self.statements.append((query, parameters))
            if query.startswith("SELECT * FROM schemoo.models"):
                assert query.endswith(" FOR UPDATE")
                self.rows = [deepcopy(row)] if parameters == ("alice", model.id) else []
            elif query.startswith("SELECT 1 FROM schemoo.model_previews"):
                assert parameters == ("alice", model.id)
                self.rows = [{"exists": 1}] if previews else []
            elif query.startswith("SELECT id,name FROM schemer.dashboards"):
                assert parameters == ("alice", model.id)
                self.rows = deepcopy(dependencies)
            elif query == "DELETE FROM schemoo.models WHERE owner_id = %s AND id = %s":
                assert parameters == ("alice", model.id)
                assert not dependencies
                self.deleted = True
                self.rows = []
            else:
                raise AssertionError(f"Unexpected storage operation: {query}")

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return self.rows

    storage = Storage()
    store = PostgresModelRepository(lambda: storage)
    guards = {} if state == "legacy" else dict(expected_layout_revision=1, expected_explore_revision=1,
                                                require_no_previews=True)
    if state in {"empty", "legacy"}:
        store.delete("alice", model.id, 1, **guards)
        assert storage.deleted and not storage.rolled_back
    else:
        error = ModelNotFoundError if state == "owner" else ModelInUseError if state == "dependency" else ModelConflictError
        with pytest.raises(error):
            store.delete("bob" if state == "owner" else "alice", model.id, 1, **guards)
        assert not storage.deleted and storage.rolled_back
    assert (row, previews, dependencies) == original
    assert storage.statements[0][0].endswith(" FOR UPDATE")
    if state == "legacy":
        assert not any("model_previews" in query for query, _ in storage.statements)


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
    assert ModelCreate.model_validate({key: value for key, value in document.items() if key not in {"id", "ownerId", "connectionOwnerId", "revision", "layoutRevision", "exploreRevision", "createdAt", "updatedAt"}})


def test_invalid_drafts_can_be_saved_for_repair_but_duplicate_ids_cannot():
    definition = ModelDefinition(scopes=[{"id": "scope", "kind": "required", "alternatives": [{"id": "a", "conditions": [{"table": "", "column": ""}]}]}])
    assert InMemoryModelRepository().create("alice", request(definition=definition))
    with pytest.raises(ValidationError, match="Duplicate node"):
        request(definition={"nodes": [{"id": "a", "table": "t", "label": "A"}] * 2})


def test_named_previews_have_independent_revisions_and_only_store_query_inputs():
    store = InMemoryModelRepository()
    model = store.create("alice", request())
    first = store.create_preview("alice", model.id, PreviewCreate(name="  Current  ", explore={
        "root": "people", "fields": [{"table": "people", "column": "name"}],
        "selections": {"date": {"values": {"asOf": "today"}}}}))
    second = store.create_preview("alice", model.id, PreviewCreate(name="Historical", explore={}))
    assert first.name == "Current" and first.id != second.id
    assert set(first.model_dump(by_alias=True)) == {"id", "modelId", "name", "explore", "revision", "createdAt", "updatedAt"}
    first.explore.fields.clear()
    assert store.list_previews("alice", model.id)[0].explore.fields
    changed = store.update_preview("alice", model.id, first.id, PreviewUpdate(
        expected_revision=1, name="Current count", explore={"fields": [{"table": "people", "column": "id", "aggregate": "count"}]}))
    assert changed.revision == 2 and store.list_previews("alice", model.id)[1].revision == 1
    assert store.get("alice", model.id) == model
    for action in (
        lambda: store.update_preview("alice", model.id, first.id, PreviewUpdate(expected_revision=1, name="Stale", explore={})),
        lambda: store.delete_preview("alice", model.id, first.id, 1),
    ):
        with pytest.raises(ModelConflictError, match="saved preview"):
            action()
    store.delete_preview("alice", model.id, first.id, 2)
    assert [p.id for p in store.list_previews("alice", model.id)] == [second.id]


def test_named_previews_are_owner_and_model_scoped_and_cascade():
    store = InMemoryModelRepository()
    model = store.create("alice", request())
    other = store.create("alice", request())
    preview = store.create_preview("alice", model.id, PreviewCreate(name="Test", explore={}))
    for action in (
        lambda: store.list_previews("bob", model.id),
        lambda: store.create_preview("bob", model.id, PreviewCreate(name="No", explore={})),
        lambda: store.update_preview("bob", model.id, preview.id, PreviewUpdate(expected_revision=1, name="No", explore={})),
        lambda: store.delete_preview("bob", model.id, preview.id, 1),
        lambda: store.update_preview("alice", other.id, preview.id, PreviewUpdate(expected_revision=1, name="No", explore={})),
        lambda: store.delete_preview("alice", other.id, preview.id, 1),
    ):
        with pytest.raises(ModelNotFoundError):
            action()
    store.delete("alice", model.id, 1)
    assert ("alice", model.id) not in store._previews
    with pytest.raises(ModelNotFoundError):
        store.list_previews("alice", model.id)


def test_named_preview_total_budget_is_atomic_for_create_and_update():
    store = InMemoryModelRepository(maximum_document_bytes=1300)
    model = store.create("alice", request())
    body = PreviewCreate(name="Preview", explore={"selections": {"s": {"values": {"p": "x" * 350}}}})
    def create(_):
        try:
            return store.create_preview("alice", model.id, body.model_copy(update={"name": f"Preview {_}"}))
        except ModelDocumentLimitError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(8)))
    assert sum(item is not None for item in results) == 1
    saved = store.list_previews("alice", model.id)[0]
    with pytest.raises(ModelDocumentLimitError, match="saved previews"):
        store.update_preview("alice", model.id, saved.id, PreviewUpdate(expected_revision=1,
            name="Large", explore={"selections": {"s": {"values": {"p": "é" * 1000}}}}))
    assert store.list_previews("alice", model.id) == [saved]
    store.delete_preview("alice", model.id, saved.id, 1)
    assert store.create_preview("alice", model.id, body)


def test_named_preview_names_are_trimmed_case_insensitive_and_atomic():
    store = InMemoryModelRepository()
    model = store.create("alice", request())
    other = store.create("alice", request())
    body = PreviewCreate(name="  Current ", explore={})
    def create(_):
        try:
            return store.create_preview("alice", model.id, body)
        except PreviewNameConflictError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(8)))
    assert sum(item is not None for item in results) == 1
    first = store.list_previews("alice", model.id)[0]
    second = store.create_preview("alice", model.id, PreviewCreate(name="History", explore={}))
    with pytest.raises(PreviewNameConflictError):
        store.create_preview("alice", model.id, PreviewCreate(name="CURRENT", explore={}))
    with pytest.raises(PreviewNameConflictError):
        store.update_preview("alice", model.id, second.id, PreviewUpdate(expected_revision=1, name=" current ", explore={}))
    assert store.list_previews("alice", model.id) == [first, second]
    renamed = store.update_preview("alice", model.id, first.id, PreviewUpdate(expected_revision=1, name="CURRENT", explore={}))
    assert renamed.revision == 2
    assert store.create_preview("alice", other.id, body)


@pytest.mark.parametrize("body", [
    {"name": " ", "explore": {}}, {"name": "Rows", "explore": {"rows": [["private"]]}},
    {"name": "Model", "explore": {}, "definition": {}},
])
def test_named_preview_rejects_empty_names_rows_and_model_copies(body):
    with pytest.raises(ValidationError):
        PreviewCreate.model_validate(body)
