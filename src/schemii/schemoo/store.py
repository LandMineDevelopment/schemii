"""Owner-scoped current semantic models, without retained catalogs or query rows."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Protocol
import secrets
import json

from psycopg.types.json import Jsonb

from schemii.common.connections.dependencies import ConnectionDependentResource
from schemii.common.connections.store import ConnectionInUseError
from schemii.common.errors import MetadataStorageUnavailableError
from .models import (ExploreUpdate, LayoutUpdate, ModelCreate, ModelDuplicate, ModelUpdate, SchemooModel,
                     ModelSummary, PreviewCreate, PreviewUpdate, SavedPreview)


class ModelNotFoundError(RuntimeError):
    pass


class ModelConflictError(RuntimeError):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__("This model changed in another request. Reload before saving.")


class PreviewConflictError(ModelConflictError):
    def __init__(self, current_revision: int):
        super().__init__(current_revision)
        self.args = ("This saved preview changed in another request. Reload previews before saving or deleting it.",)


class PreviewNameConflictError(RuntimeError):
    def __init__(self):
        super().__init__("A saved preview with this name already exists in this model. Choose a different name or update that preview.")


class ModelLimitError(RuntimeError):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"The semantic model limit of {limit} has been reached. Delete an unused model or ask the administrator to raise the limit.")


class ModelStorageUnavailableError(MetadataStorageUnavailableError):
    pass


class ModelDocumentLimitError(RuntimeError):
    def __init__(self, document: str, limit: int):
        self.document = document
        self.limit = limit
        super().__init__(f"The semantic model {document} exceeds the configured {limit}-byte document limit. Reduce its size or ask the administrator to raise the limit.")


def _check_documents(documents, limit):
    for name in ("definition", "layout", "explore"):
        if name not in documents:
            continue
        # Match the JSON adapter's serialization, including multibyte strings.
        size = len(json.dumps(documents[name], ensure_ascii=True).encode("utf-8"))
        if size > limit:
            raise ModelDocumentLimitError(name, limit)


def _check_previews(previews, limit):
    # One aggregate budget per model prevents unbounded copies of Explore inputs.
    size = len(json.dumps([preview.model_dump(mode="json") for preview in previews],
                          ensure_ascii=True).encode("utf-8"))
    if size > limit:
        raise ModelDocumentLimitError("saved previews (combined)", limit)


def _check_preview_name(previews, name, replacing_id=None):
    normalized = name.strip().casefold()
    if any(item.id != replacing_id and item.name.strip().casefold() == normalized for item in previews):
        raise PreviewNameConflictError()


class ModelRepository(Protocol):
    dependency_name: str
    def list(self, owner_id: str) -> list[ModelSummary]: ...
    def get(self, owner_id: str, model_id: str) -> SchemooModel: ...
    def create(self, owner_id: str, request: ModelCreate) -> SchemooModel: ...
    def duplicate(self, owner_id: str, model_id: str, request: ModelDuplicate) -> SchemooModel: ...
    def update(self, owner_id: str, model_id: str, request: ModelUpdate) -> SchemooModel: ...
    def update_layout(self, owner_id: str, model_id: str, request: LayoutUpdate) -> SchemooModel: ...
    def update_explore(self, owner_id: str, model_id: str, request: ExploreUpdate) -> SchemooModel: ...
    def list_previews(self, owner_id: str, model_id: str) -> list[SavedPreview]: ...
    def create_preview(self, owner_id: str, model_id: str, request: PreviewCreate) -> SavedPreview: ...
    def update_preview(self, owner_id: str, model_id: str, preview_id: str, request: PreviewUpdate) -> SavedPreview: ...
    def delete_preview(self, owner_id: str, model_id: str, preview_id: str, expected_revision: int) -> None: ...
    def delete(self, owner_id: str, model_id: str, expected_revision: int) -> None: ...
    def count_for_connection(self, owner_id: str, connection_id: str) -> int: ...
    def dependencies_for_connection(self, owner_id: str, connection_id: str) -> tuple[ConnectionDependentResource, ...]: ...


def _duplicate_snapshot(source, previews, request, document_limit):
    for revision in ("revision", "layout_revision", "explore_revision"):
        if getattr(source, revision) != getattr(request, "expected_" + revision):
            raise ModelConflictError(getattr(source, revision))
    now = datetime.now(timezone.utc)
    model = source.model_copy(deep=True, update={
        "id": f"model_{secrets.token_hex(16)}", "name": request.name,
        "revision": 1, "layout_revision": 1, "explore_revision": 1,
        "created_at": now, "updated_at": now})
    # Durable preview lists sort by creation time and ID. Give each new preview
    # a distinct timestamp so fresh random IDs cannot scramble the saved order.
    copies = [preview.model_copy(deep=True, update={
        "id": f"preview_{secrets.token_hex(16)}", "model_id": model.id,
        "revision": 1, "created_at": now + timedelta(microseconds=index),
        "updated_at": now + timedelta(microseconds=index)}) for index, preview in enumerate(previews)]
    _check_documents(model.model_dump(mode="json"), document_limit)
    _check_previews(copies, document_limit)
    return model, copies


class _Dependencies:
    dependency_name = "schemooModels"

    def count_for_connection(self, owner_id, connection_id):
        return len(self.dependencies_for_connection(owner_id, connection_id))

    def dependencies_for_connection(self, owner_id, connection_id):
        return tuple(ConnectionDependentResource(
            provider=self.dependency_name, kind="semantic_model", resource_id=model.id,
            revision=model.revision, name=model.name, target=f"{model.database}.{model.namespace}",
        ) for model in self.list(owner_id) if model.connection_id == connection_id)


class InMemoryModelRepository(_Dependencies):
    def __init__(self, *, maximum_models_per_owner=100, maximum_document_bytes=1_048_576):
        if maximum_models_per_owner < 1 or maximum_document_bytes < 1:
            raise ValueError("Model limits must be positive")
        self._maximum = maximum_models_per_owner
        self._maximum_document_bytes = maximum_document_bytes
        self._records = {}
        self._previews = {}
        self._lock = RLock()

    def list(self, owner_id):
        with self._lock:
            return [ModelSummary(**{key: getattr(model, key) for key in ModelSummary.model_fields})
                for (owner, _), model in self._records.items() if owner == owner_id]

    def get(self, owner_id, model_id):
        with self._lock:
            try:
                return self._records[owner_id, model_id].model_copy(deep=True)
            except KeyError as error:
                raise ModelNotFoundError("Semantic model was not found") from error

    def create(self, owner_id, request):
        request = ModelCreate.model_validate(request)
        _check_documents(request.model_dump(mode="json"), self._maximum_document_bytes)
        with self._lock:
            if len(self.list(owner_id)) >= self._maximum:
                raise ModelLimitError(self._maximum)
            now = datetime.now(timezone.utc)
            model = SchemooModel(**request.model_dump(), id=f"model_{secrets.token_hex(16)}",
                owner_id=owner_id, revision=1, created_at=now, updated_at=now)
            self._records[owner_id, model.id] = model.model_copy(deep=True)
            return model

    def duplicate(self, owner_id, model_id, request):
        request = ModelDuplicate.model_validate(request)
        with self._lock:
            source = self.get(owner_id, model_id)
            model, previews = _duplicate_snapshot(source, self.list_previews(owner_id, model_id),
                request, self._maximum_document_bytes)
            if len(self.list(owner_id)) >= self._maximum:
                raise ModelLimitError(self._maximum)
            self._records[owner_id, model.id] = model.model_copy(deep=True)
            self._previews[owner_id, model.id] = {item.id: item.model_copy(deep=True) for item in previews}
            return model

    def _update(self, owner_id, model_id, expected, revision_key, changes):
        _check_documents(changes, self._maximum_document_bytes)
        with self._lock:
            model = self.get(owner_id, model_id)
            if getattr(model, revision_key) != expected:
                raise ModelConflictError(getattr(model, revision_key))
            data = model.model_dump()
            data.update(changes, **{revision_key: expected + 1}, updated_at=datetime.now(timezone.utc))
            updated = SchemooModel.model_validate(data)
            self._records[owner_id, model_id] = updated.model_copy(deep=True)
            return updated

    def update(self, owner_id, model_id, request):
        request = ModelUpdate.model_validate(request)
        return self._update(owner_id, model_id, request.expected_revision, "revision", request.model_dump(exclude={"expected_revision"}))

    def update_layout(self, owner_id, model_id, request):
        request = LayoutUpdate.model_validate(request)
        return self._update(owner_id, model_id, request.expected_revision, "layout_revision", {"layout": request.layout.model_dump()})

    def update_explore(self, owner_id, model_id, request):
        request = ExploreUpdate.model_validate(request)
        return self._update(owner_id, model_id, request.expected_revision, "explore_revision", {"explore": request.explore.model_dump()})

    def delete(self, owner_id, model_id, expected_revision):
        with self._lock:
            model = self.get(owner_id, model_id)
            if model.revision != expected_revision:
                raise ModelConflictError(model.revision)
            del self._records[owner_id, model_id]
            self._previews.pop((owner_id, model_id), None)

    def list_previews(self, owner_id, model_id):
        with self._lock:
            self.get(owner_id, model_id)
            return [item.model_copy(deep=True) for item in self._previews.get((owner_id, model_id), {}).values()]

    def create_preview(self, owner_id, model_id, request):
        request = PreviewCreate.model_validate(request)
        with self._lock:
            current = self.list_previews(owner_id, model_id)
            _check_preview_name(current, request.name)
            now = datetime.now(timezone.utc)
            preview = SavedPreview(**request.model_dump(), id=f"preview_{secrets.token_hex(16)}",
                model_id=model_id, created_at=now, updated_at=now)
            _check_previews([*current, preview], self._maximum_document_bytes)
            self._previews.setdefault((owner_id, model_id), {})[preview.id] = preview.model_copy(deep=True)
            return preview

    def update_preview(self, owner_id, model_id, preview_id, request):
        request = PreviewUpdate.model_validate(request)
        with self._lock:
            current = self.list_previews(owner_id, model_id)
            preview = next((item for item in current if item.id == preview_id), None)
            if preview is None:
                raise ModelNotFoundError("Saved preview was not found")
            if preview.revision != request.expected_revision:
                raise PreviewConflictError(preview.revision)
            _check_preview_name(current, request.name, preview_id)
            updated = SavedPreview.model_validate({**preview.model_dump(),
                **request.model_dump(exclude={"expected_revision"}), "revision": preview.revision + 1,
                "updated_at": datetime.now(timezone.utc)})
            _check_previews([updated if item.id == preview_id else item for item in current], self._maximum_document_bytes)
            self._previews[owner_id, model_id][preview_id] = updated.model_copy(deep=True)
            return updated

    def delete_preview(self, owner_id, model_id, preview_id, expected_revision):
        with self._lock:
            current = self.list_previews(owner_id, model_id)
            preview = next((item for item in current if item.id == preview_id), None)
            if preview is None:
                raise ModelNotFoundError("Saved preview was not found")
            if preview.revision != expected_revision:
                raise PreviewConflictError(preview.revision)
            del self._previews[owner_id, model_id][preview_id]


class PostgresModelRepository(_Dependencies):
    def __init__(self, connection_factory, *, maximum_models_per_owner=100, maximum_document_bytes=1_048_576):
        if maximum_models_per_owner < 1 or maximum_document_bytes < 1:
            raise ValueError("Model limits must be positive")
        self._factory = connection_factory
        self._maximum = maximum_models_per_owner
        self._maximum_document_bytes = maximum_document_bytes

    @contextmanager
    def _transaction(self):
        try:
            with self._factory() as connection:
                with connection.cursor() as cursor:
                    yield cursor
        except (ModelNotFoundError, ModelConflictError, ModelLimitError, ModelDocumentLimitError, PreviewNameConflictError):
            raise
        except Exception as error:
            raise ModelStorageUnavailableError("Semantic model storage is temporarily unavailable") from error

    @staticmethod
    def _row(cursor, owner_id, model_id, *, lock=False):
        cursor.execute("SELECT * FROM schemoo.models WHERE owner_id = %s AND id = %s" + (" FOR UPDATE" if lock else ""), (owner_id, model_id))
        row = cursor.fetchone()
        if row is None:
            raise ModelNotFoundError("Semantic model was not found")
        return SchemooModel.model_validate(row)

    def list(self, owner_id):
        with self._transaction() as cursor:
            columns = ", ".join(ModelSummary.model_fields)
            cursor.execute(f"SELECT {columns} FROM schemoo.models WHERE owner_id = %s ORDER BY created_at, id", (owner_id,))
            return [ModelSummary.model_validate(row) for row in cursor.fetchall()]

    def get(self, owner_id, model_id):
        with self._transaction() as cursor:
            return self._row(cursor, owner_id, model_id)

    @staticmethod
    def guard_connection_mutation(cursor, owner_id, connection_id, operation, changed_fields=frozenset()):
        """Called under the saved connection row lock, including across workers."""
        if operation != "delete" and {"host", "port", "database", "username"}.isdisjoint(changed_fields):
            return
        cursor.execute("SELECT count(*) AS count FROM schemoo.models WHERE owner_id = %s AND connection_id = %s", (owner_id, connection_id))
        count = cursor.fetchone()["count"]
        if count:
            raise ConnectionInUseError({PostgresModelRepository.dependency_name: count})

    def create(self, owner_id, request):
        request = ModelCreate.model_validate(request)
        _check_documents(request.model_dump(mode="json"), self._maximum_document_bytes)
        with self._transaction() as cursor:
            self._lock_creation(cursor, owner_id, request)
            model_id = f"model_{secrets.token_hex(16)}"
            return self._insert_model(cursor, owner_id, model_id, request)

    def _lock_creation(self, cursor, owner_id, request):
        # Keep the same lock order for every creation, including duplication.
        cursor.execute("SELECT id FROM metadata.users WHERE id = %s FOR UPDATE", (owner_id,))
        cursor.execute("SELECT database_name FROM metadata.postgres_connections WHERE owner_id = %s AND id = %s FOR UPDATE", (owner_id, request.connection_id))
        connection = cursor.fetchone()
        if connection is None or connection["database_name"] != request.database:
            raise ModelNotFoundError("The selected connection no longer exists or its database changed")
        cursor.execute("SELECT count(*) AS count FROM schemoo.models WHERE owner_id = %s", (owner_id,))
        if cursor.fetchone()["count"] >= self._maximum:
            raise ModelLimitError(self._maximum)

    @staticmethod
    def _insert_model(cursor, owner_id, model_id, request):
        cursor.execute("""INSERT INTO schemoo.models
                (id, owner_id, connection_id, database, namespace, name, definition, layout, explore, catalog_fingerprint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (model_id, owner_id, request.connection_id, request.database, request.namespace, request.name,
                 Jsonb(request.definition.model_dump(mode="json")), Jsonb(request.layout.model_dump(mode="json")),
                 Jsonb(request.explore.model_dump(mode="json")), request.catalog_fingerprint))
        return SchemooModel.model_validate(cursor.fetchone())

    def duplicate(self, owner_id, model_id, request):
        request = ModelDuplicate.model_validate(request)
        with self._transaction() as cursor:
            source = self._row(cursor, owner_id, model_id)
            self._lock_creation(cursor, owner_id, source)
            # Every document and preview writer takes this parent row lock.
            # Re-read after acquiring it so the copied documents and previews
            # belong to one snapshot, even when another worker is saving them.
            source = self._row(cursor, owner_id, model_id, lock=True)
            model, previews = _duplicate_snapshot(source, self._preview_rows(cursor, owner_id, model_id),
                request, self._maximum_document_bytes)
            saved = self._insert_model(cursor, owner_id, model.id, model)
            for preview in previews:
                cursor.execute("""INSERT INTO schemoo.model_previews
                    (id, owner_id, model_id, name, explore, created_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""", (preview.id, owner_id, model.id,
                    preview.name, Jsonb(preview.explore.model_dump(mode="json")),
                    preview.created_at, preview.updated_at))
            return saved

    def _update(self, owner_id, model_id, expected, revision_key, changes):
        _check_documents(changes, self._maximum_document_bytes)
        # Column names are internal fixed constants, never caller-controlled SQL.
        with self._transaction() as cursor:
            model = self._row(cursor, owner_id, model_id, lock=True)
            if getattr(model, revision_key) != expected:
                raise ModelConflictError(getattr(model, revision_key))
            names = list(changes)
            assignments = ", ".join(f"{name} = %s" for name in names)
            values = [Jsonb(changes[name]) if name in {"definition", "layout", "explore"} else changes[name] for name in names]
            cursor.execute(f"UPDATE schemoo.models SET {assignments}, {revision_key} = {revision_key} + 1, updated_at = clock_timestamp() WHERE owner_id = %s AND id = %s RETURNING *", (*values, owner_id, model_id))
            return SchemooModel.model_validate(cursor.fetchone())

    def update(self, owner_id, model_id, request):
        request = ModelUpdate.model_validate(request)
        return self._update(owner_id, model_id, request.expected_revision, "revision", request.model_dump(mode="json", exclude={"expected_revision"}))

    def update_layout(self, owner_id, model_id, request):
        request = LayoutUpdate.model_validate(request)
        return self._update(owner_id, model_id, request.expected_revision, "layout_revision", {"layout": request.layout.model_dump(mode="json")})

    def update_explore(self, owner_id, model_id, request):
        request = ExploreUpdate.model_validate(request)
        return self._update(owner_id, model_id, request.expected_revision, "explore_revision", {"explore": request.explore.model_dump(mode="json")})

    def delete(self, owner_id, model_id, expected_revision):
        with self._transaction() as cursor:
            model = self._row(cursor, owner_id, model_id, lock=True)
            if model.revision != expected_revision:
                raise ModelConflictError(model.revision)
            cursor.execute("DELETE FROM schemoo.models WHERE owner_id = %s AND id = %s", (owner_id, model_id))

    @staticmethod
    def _preview_rows(cursor, owner_id, model_id):
        columns = ", ".join(SavedPreview.model_fields)
        cursor.execute(f"SELECT {columns} FROM schemoo.model_previews WHERE owner_id = %s AND model_id = %s ORDER BY created_at, id", (owner_id, model_id))
        return [SavedPreview.model_validate(row) for row in cursor.fetchall()]

    def list_previews(self, owner_id, model_id):
        with self._transaction() as cursor:
            self._row(cursor, owner_id, model_id)
            return self._preview_rows(cursor, owner_id, model_id)

    def create_preview(self, owner_id, model_id, request):
        request = PreviewCreate.model_validate(request)
        with self._transaction() as cursor:
            # Parent lock serializes the aggregate byte budget and model deletion.
            self._row(cursor, owner_id, model_id, lock=True)
            current = self._preview_rows(cursor, owner_id, model_id)
            _check_preview_name(current, request.name)
            now = datetime.now(timezone.utc)
            preview = SavedPreview(**request.model_dump(), id=f"preview_{secrets.token_hex(16)}",
                model_id=model_id, created_at=now, updated_at=now)
            _check_previews([*current, preview], self._maximum_document_bytes)
            cursor.execute("""INSERT INTO schemoo.model_previews
                (id, owner_id, model_id, name, explore, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""", (preview.id, owner_id, model_id,
                preview.name, Jsonb(preview.explore.model_dump(mode="json")), now, now))
            return preview

    def update_preview(self, owner_id, model_id, preview_id, request):
        request = PreviewUpdate.model_validate(request)
        with self._transaction() as cursor:
            self._row(cursor, owner_id, model_id, lock=True)
            current = self._preview_rows(cursor, owner_id, model_id)
            preview = next((item for item in current if item.id == preview_id), None)
            if preview is None:
                raise ModelNotFoundError("Saved preview was not found")
            if preview.revision != request.expected_revision:
                raise PreviewConflictError(preview.revision)
            _check_preview_name(current, request.name, preview_id)
            updated = SavedPreview.model_validate({**preview.model_dump(),
                **request.model_dump(exclude={"expected_revision"}), "revision": preview.revision + 1,
                "updated_at": datetime.now(timezone.utc)})
            _check_previews([updated if item.id == preview_id else item for item in current], self._maximum_document_bytes)
            cursor.execute("""UPDATE schemoo.model_previews SET name = %s, explore = %s,
                revision = %s, updated_at = %s WHERE owner_id = %s AND model_id = %s AND id = %s""",
                (updated.name, Jsonb(updated.explore.model_dump(mode="json")), updated.revision,
                 updated.updated_at, owner_id, model_id, preview_id))
            return updated

    def delete_preview(self, owner_id, model_id, preview_id, expected_revision):
        with self._transaction() as cursor:
            self._row(cursor, owner_id, model_id, lock=True)
            current = self._preview_rows(cursor, owner_id, model_id)
            preview = next((item for item in current if item.id == preview_id), None)
            if preview is None:
                raise ModelNotFoundError("Saved preview was not found")
            if preview.revision != expected_revision:
                raise PreviewConflictError(preview.revision)
            cursor.execute("DELETE FROM schemoo.model_previews WHERE owner_id = %s AND model_id = %s AND id = %s", (owner_id, model_id, preview_id))
