from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .migration_contract import has_full_schema_completeness_proof

from .atomic_json import write_json
from .file_lock import RefCountedKeyedFileGuard


SCHEMA_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
LAYOUT_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SchemaStoreError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: Any):
        super().__init__(message)
        self.status = status
        self.payload = {"error": {"code": code, "message": message, **details}}


def schema_layout_token(record: dict[str, Any]) -> str:
    schema = record.get("schema", {}) if isinstance(record, dict) else {}
    tables = schema.get("tables", []) if isinstance(schema, dict) else []
    legacy = {
        table.get("id"): {field: table.get(field) for field in ("x", "y", "color") if field in table}
        for table in tables if isinstance(table, dict) and isinstance(table.get("id"), str)
    }
    layout = {"layout": schema.get("layout", {}), "legacyTables": legacy}
    encoded = json.dumps(layout, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def postgres_view_id(namespace: str, relation: str) -> str:
    encoded = json.dumps((namespace, relation), ensure_ascii=True, separators=(",", ":"))
    return f"pg_view_{hashlib.sha256(encoded.encode()).hexdigest()[:20]}"


def is_wholesale_layout_change(existing_record: dict[str, Any], incoming_record: dict[str, Any]) -> bool:
    def layers(record: dict[str, Any]) -> dict[str, tuple[dict[str, Any], Any]]:
        layout = record.get("schema", {}).get("layout", {})
        if not isinstance(layout, dict):
            return {}
        configured = layout.get("layers")
        if isinstance(configured, dict):
            result = {}
            for name in ("tables", "views"):
                layer = configured.get(name, {})
                if isinstance(layer, dict):
                    objects = layer.get("objects", {})
                    result[name] = (objects if isinstance(objects, dict) else {}, layer.get("viewport"))
            return result
        tables = layout.get("tables", {})
        return {"tables": (tables if isinstance(tables, dict) else {}, layout.get("view"))}

    existing_layers = layers(existing_record)
    incoming_layers = layers(incoming_record)
    if any(
        existing_layers[name][1] != incoming_layers.get(name, ({}, None))[1]
        for name in existing_layers
        if existing_layers[name][1] is not None
    ):
        return True
    visual_fields = ("x", "y", "color")
    established = 0
    changed = 0
    for name, (existing, _) in existing_layers.items():
        incoming = incoming_layers.get(name, ({}, None))[0]
        established += len(existing)
        layer_changed = 0
        for object_id, current in existing.items():
            candidate = incoming.get(object_id)
            if not isinstance(current, dict) or not isinstance(candidate, dict):
                layer_changed += 1
            elif any(current.get(field) != candidate.get(field) for field in visual_fields):
                layer_changed += 1
        changed += layer_changed
    return established >= 8 and changed >= max(8, (established + 1) // 2)


class SchemaStore:
    def __init__(self, schema_dir: str | os.PathLike[str], *, read_only: bool = False):
        self.schema_dir = Path(schema_dir).expanduser()
        self.read_only = read_only
        self._lock = threading.RLock()
        self.lock_dir = self.schema_dir / ".locks"
        self._guards = RefCountedKeyedFileGuard(lambda schema_id: self.lock_dir / f"{schema_id}.lock")
        if not read_only:
            self.schema_dir.mkdir(parents=True, exist_ok=True)
            self.lock_dir.mkdir(mode=0o700, exist_ok=True)

    @contextmanager
    def _schema_guard(self, schema_id: str):
        """Serialize schema writes across threads and server processes."""
        if self.read_only:
            raise SchemaStoreError(403, "schema_store_read_only", "This schema store is read-only")
        with self._guards.exclusive(schema_id):
            yield

    @contextmanager
    def _schema_read_guard(self, schema_id: str):
        if not self.read_only:
            with self._schema_guard(schema_id):
                yield
            return
        with self._guards.thread(schema_id):
            yield

    @staticmethod
    def validate_id(schema_id: Any) -> str:
        if not isinstance(schema_id, str) or not SCHEMA_ID_PATTERN.fullmatch(schema_id):
            raise SchemaStoreError(404, "not_found", "Unknown schema path")
        return schema_id

    @staticmethod
    def _validate_record(record: Any, schema_id: str | None = None) -> dict[str, Any]:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise SchemaStoreError(400, "invalid_schema", "Invalid schema record")
        if schema_id is not None and record["id"] != schema_id:
            raise SchemaStoreError(400, "invalid_schema", "Invalid schema record")
        schema = record.get("schema")
        if not (
            isinstance(schema, dict)
            and isinstance(schema.get("projectName"), str)
            and isinstance(schema.get("tables"), list)
            and isinstance(schema.get("relationships"), list)
            and ("functions" not in schema or isinstance(schema["functions"], list))
        ):
            raise SchemaStoreError(400, "invalid_schema", "Invalid schema record")
        return record

    def _records(self) -> list[tuple[Path, dict[str, Any]]]:
        records = []
        if not self.read_only:
            self.schema_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.schema_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                records.append((path, self._validate_record(record)))
            except (OSError, json.JSONDecodeError, SchemaStoreError):
                continue
        return records

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{**record, "layoutToken": schema_layout_token(record)} for _, record in self._records()]

    def list_summaries(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{
                "id": record["id"], "revision": record.get("revision", 0),
                "updatedAt": record.get("updatedAt"), "layoutToken": schema_layout_token(record),
                "projectName": record["schema"].get("projectName", ""),
                "tableCount": len(record["schema"].get("tables", [])),
                "postgres": {
                    key: record["schema"].get("postgres", {}).get(key)
                    for key in ("sourceProfileId", "database", "namespace")
                },
            } for _, record in self._records()]

    def get(self, schema_id: str) -> dict[str, Any]:
        schema_id = self.validate_id(schema_id)
        with self._schema_read_guard(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            return {**json.loads(json.dumps(found[1])), "layoutToken": schema_layout_token(found[1])}

    @contextmanager
    def guard_revision(self, schema_id: str, expected_revision: Any):
        schema_id = self.validate_id(schema_id)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise SchemaStoreError(400, "invalid_schema_binding", "expectedRevision is invalid")
        with self._schema_read_guard(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            if found[1].get("revision", 0) != expected_revision:
                raise SchemaStoreError(409, "schema_conflict", "Schema changed in another session; reload before continuing", currentRevision=found[1].get("revision", 0))
            yield json.loads(json.dumps(found[1]))

    @contextmanager
    def reserve_ai_binding(self, schema_id: str, expected_revision: Any, layout_token: Any):
        schema_id = self.validate_id(schema_id)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise SchemaStoreError(400, "invalid_schema_binding", "expectedRevision is invalid")
        if not isinstance(layout_token, str) or not LAYOUT_TOKEN_PATTERN.fullmatch(layout_token):
            raise SchemaStoreError(400, "invalid_schema_binding", "layoutToken is invalid")
        with self._schema_guard(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            if found[1].get("revision", 0) != expected_revision:
                raise SchemaStoreError(409, "schema_conflict", "Schema changed; reload before continuing")
            if schema_layout_token(found[1]) != layout_token:
                raise SchemaStoreError(409, "layout_conflict", "Saved layout changed; hard-refresh before continuing")
            yield json.loads(json.dumps(found[1]))

    def require_migration_binding(
        self, schema_id: str, expected_revision: Any, layout_token: Any,
        profile_id: str, database: str, namespace: str,
    ) -> dict[str, Any]:
        """Load the exact server-owned desired schema for a migration preview."""
        schema_id = self.validate_id(schema_id)
        with self._schema_read_guard(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            record = found[1]
            self._require_view_binding(
                record, expected_revision, layout_token, profile_id, database, namespace,
            )
            return json.loads(json.dumps(record))

    def _find(self, schema_id: str) -> tuple[Path, dict[str, Any]] | None:
        for path, record in self._records():
            if record["id"] == schema_id:
                return path, record
        return None

    @staticmethod
    def _require_view_binding(
        record: dict[str, Any], expected_revision: Any, layout_token: Any,
        profile_id: str, database: str, namespace: str,
    ) -> None:
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise SchemaStoreError(400, "invalid_schema_binding", "expectedSchemaRevision is invalid")
        if not isinstance(layout_token, str) or not LAYOUT_TOKEN_PATTERN.fullmatch(layout_token):
            raise SchemaStoreError(400, "invalid_schema_binding", "layoutToken is invalid")
        revision = record.get("revision", 0)
        if revision != expected_revision:
            raise SchemaStoreError(409, "schema_conflict", "Schema changed in another session; reload before continuing", currentRevision=revision)
        if schema_layout_token(record) != layout_token:
            raise SchemaStoreError(409, "layout_conflict", "Saved layout changed; hard-refresh before continuing")
        postgres = record.get("schema", {}).get("postgres")
        expected = (profile_id, database, namespace)
        actual = (
            postgres.get("sourceProfileId"), postgres.get("database"), postgres.get("namespace")
        ) if isinstance(postgres, dict) else (None, None, None)
        if actual != expected:
            raise SchemaStoreError(409, "schema_target_changed", "Saved schema is not bound to the requested PostgreSQL target")

    def require_view_mutation_binding(
        self, schema_id: str, expected_revision: Any, layout_token: Any,
        profile_id: str, database: str, namespace: str, relation: str,
        operation: str, expectation: dict[str, Any], saved_view_id: str | None = None,
    ) -> dict[str, Any]:
        if operation not in {"upsert", "delete"}:
            raise SchemaStoreError(400, "invalid_schema_binding", "View operation is invalid")
        schema_id = self.validate_id(schema_id)
        with self._schema_read_guard(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            record = found[1]
            self._require_view_binding(record, expected_revision, layout_token, profile_id, database, namespace)
            matches = [
                item for item in record["schema"].get("views", [])
                if isinstance(item, dict) and item.get("namespace") == namespace and item.get("name") == relation
            ]
            expected_absent = isinstance(expectation, dict) and expectation == {"absent": True}
            if operation == "delete" and expected_absent:
                raise SchemaStoreError(400, "invalid_schema_binding", "Delete requires an existing saved view")
            if expected_absent:
                if matches:
                    raise SchemaStoreError(409, "schema_view_changed", "Saved schema view collides with the expected new view")
                matched_id = None
            else:
                if len(matches) != 1:
                    message = "Saved schema contains ambiguous matching view items" if len(matches) > 1 else "Saved schema view changed after editing began"
                    raise SchemaStoreError(409, "schema_view_changed", message)
                matched_id = matches[0].get("id")
                if not isinstance(matched_id, str) or not matched_id:
                    raise SchemaStoreError(409, "schema_view_changed", "Saved schema view has no stable identity")
                if saved_view_id is not None and matched_id != saved_view_id:
                    raise SchemaStoreError(409, "schema_view_changed", "Saved schema view identity changed after preview")
                expected_kind = expectation.get("kind") if isinstance(expectation, dict) else None
                if expected_kind in {"view", "materialized_view"} and bool(matches[0].get("materialized")) != (expected_kind == "materialized_view"):
                    raise SchemaStoreError(409, "schema_view_changed", "Saved schema view kind changed after editing began")
            return {"record": json.loads(json.dumps(record)), "savedViewId": matched_id}

    @contextmanager
    def reserve_view_mutation_binding(
        self, schema_id: str, expected_revision: Any, layout_token: Any,
        profile_id: str, database: str, namespace: str, relation: str,
        operation: str, expectation: dict[str, Any], saved_view_id: str | None,
    ):
        """Reserve one schema from binding validation through narrow sync."""
        schema_id = self.validate_id(schema_id)
        with self._schema_guard(schema_id):
            self.require_view_mutation_binding(
                schema_id, expected_revision, layout_token,
                profile_id, database, namespace, relation, operation, expectation, saved_view_id,
            )
            yield

    def sync_view_after_mutation(
        self, schema_id: str, expected_revision: int, layout_token: str,
        profile_id: str, database: str, namespace: str, relation: str,
        kind: str | None, definition: str | None, query_definition: str | None, fingerprint: str | None,
        *, operation: str, expected_absent: bool, saved_view_id: str | None, receipt_id: str | None = None,
    ) -> dict[str, Any]:
        if operation not in {"upsert", "delete"} or not isinstance(expected_absent, bool):
            raise SchemaStoreError(400, "invalid_schema_binding", "expectedAbsent is invalid")
        schema_id = self.validate_id(schema_id)
        with self._schema_guard(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            path, record = found
            receipts = record.get("aiViewMutationReceipts", {})
            if receipt_id is not None and isinstance(receipts, dict) and isinstance(receipts.get(receipt_id), dict):
                return json.loads(json.dumps(receipts[receipt_id]))
            self._require_view_binding(record, expected_revision, layout_token, profile_id, database, namespace)
            views = record["schema"].get("views", [])
            indexes = [
                index for index, item in enumerate(views)
                if isinstance(item, dict) and item.get("namespace") == namespace and item.get("name") == relation
            ]
            if expected_absent and indexes:
                message = "Saved schema contains ambiguous matching view items" if len(indexes) > 1 else "Saved schema view collides with the newly created view"
                raise SchemaStoreError(409, "schema_view_changed", message)
            if not expected_absent and len(indexes) != 1:
                raise SchemaStoreError(409, "schema_view_changed", "Saved schema view changed after preview")
            stored_id = views[indexes[0]].get("id") if indexes else None
            if not expected_absent and stored_id:
                if stored_id != saved_view_id:
                    raise SchemaStoreError(409, "schema_view_changed", "Saved schema view identity changed after preview")
            stored = json.loads(json.dumps(record))
            if operation == "delete":
                if expected_absent or len(indexes) != 1 or not saved_view_id:
                    raise SchemaStoreError(409, "schema_view_changed", "Saved schema view changed after preview")
                del stored["schema"]["views"][indexes[0]]
            elif expected_absent:
                item = {"id": postgres_view_id(namespace, relation)}
                stored["schema"].setdefault("views", []).append(item)
            else:
                item = stored["schema"]["views"][indexes[0]]
            if operation == "upsert":
                item.update({
                    "name": relation,
                    "namespace": namespace,
                    "materialized": kind == "materialized_view",
                    "definition": definition,
                    "queryDefinition": query_definition,
                    "fingerprint": fingerprint,
                })
            stored["revision"] = expected_revision + 1
            stored["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            result = {
                "status": "saved", "revision": stored["revision"],
                "updatedAt": stored["updatedAt"], "layoutToken": schema_layout_token(stored),
            }
            if receipt_id is not None:
                if not isinstance(receipt_id, str) or not (
                    re.fullmatch(r"ai_plan_[A-Za-z0-9_-]{1,120}", receipt_id)
                    or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", receipt_id)
                ):
                    raise SchemaStoreError(400, "invalid_schema_binding", "View mutation receipt is invalid")
                receipts = stored.setdefault("aiViewMutationReceipts", {})
                receipts[receipt_id] = result
            try:
                write_json(path, stored)
            except OSError as exc:
                raise SchemaStoreError(500, "schema_store_error", "Schema file could not be saved") from exc
            return result

    def save(
        self,
        schema_id: str,
        record: Any,
        *,
        expected_layout_token: str | None,
        layout_protocol: str | None,
    ) -> dict[str, Any]:
        schema_id = self.validate_id(schema_id)
        record = self._validate_record(record, schema_id)
        with self._schema_guard(schema_id):
            found = self._find(schema_id)
            current_revision = 0
            existing_path = None
            if found:
                existing_path, existing_record = found
                current_revision = existing_record.get("revision", 0)
                if record.get("revision", 0) != current_revision:
                    raise SchemaStoreError(
                        409,
                        "schema_conflict",
                        "Schema changed in another session; reload before saving",
                        currentRevision=current_revision,
                    )
                layout_changed = schema_layout_token(record) != schema_layout_token(existing_record)
                if layout_changed and (
                    layout_protocol != "2"
                    or (
                        expected_layout_token != schema_layout_token(existing_record)
                        and is_wholesale_layout_change(existing_record, record)
                    )
                ):
                    raise SchemaStoreError(
                        409,
                        "layout_conflict",
                        "A stale client attempted to change the saved layout; hard-refresh before saving",
                    )

            stored = dict(record)
            if found:
                for key in ("aiOperationReceipts", "aiViewMutationReceipts", "migrationSyncReceipts", "lastAiMigrationSync"):
                    if key in existing_record:
                        stored[key] = json.loads(json.dumps(existing_record[key]))
            stored.pop("layoutToken", None)
            stored["revision"] = current_revision + 1
            stored["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            destination = self.schema_dir / f"{schema_id}.json"
            try:
                write_json(destination, stored)
                if existing_path and existing_path != destination:
                    existing_path.unlink()
            except OSError as exc:
                raise SchemaStoreError(500, "schema_store_error", "Schema file could not be saved") from exc
            return {
                "saved": schema_id,
                "revision": stored["revision"],
                "updatedAt": stored["updatedAt"],
                "layoutToken": schema_layout_token(stored),
            }

    def apply_ai_mutation(
        self,
        schema_id: str,
        operation_id: str,
        expected_revision: Any,
        expected_layout_token: Any,
        transform,
    ) -> dict[str, Any]:
        """Apply one idempotent AI transform and persist its receipt atomically."""
        schema_id = self.validate_id(schema_id)
        if not isinstance(operation_id, str) or not SCHEMA_ID_PATTERN.fullmatch(operation_id):
            raise SchemaStoreError(400, "invalid_operation", "Operation identity is invalid")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise SchemaStoreError(400, "invalid_schema_binding", "expectedRevision is invalid")
        if not isinstance(expected_layout_token, str) or not LAYOUT_TOKEN_PATTERN.fullmatch(expected_layout_token):
            raise SchemaStoreError(400, "invalid_schema_binding", "layoutToken is invalid")
        with self._schema_guard(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            path, record = found
            receipts = record.get("aiOperationReceipts", {})
            if not isinstance(receipts, dict):
                raise SchemaStoreError(500, "schema_store_error", "Schema operation receipts are invalid")
            if operation_id in receipts:
                return json.loads(json.dumps(receipts[operation_id]))
            if record.get("revision", 0) != expected_revision:
                raise SchemaStoreError(409, "schema_conflict", "Schema changed in another session; reload before continuing", currentRevision=record.get("revision", 0))
            if schema_layout_token(record) != expected_layout_token:
                raise SchemaStoreError(409, "layout_conflict", "Saved layout changed; hard-refresh before continuing")
            stored, result = transform(json.loads(json.dumps(record)))
            self._validate_record(stored, schema_id)
            stored["revision"] = expected_revision + 1
            stored["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            receipt = {
                **result,
                "kind": "schema_saved",
                "schemaId": schema_id,
                "revision": stored["revision"],
                "updatedAt": stored["updatedAt"],
                "layoutToken": schema_layout_token(stored),
            }
            stored.setdefault("aiOperationReceipts", {})[operation_id] = receipt
            try:
                write_json(path, stored)
            except OSError as exc:
                raise SchemaStoreError(500, "schema_store_error", "Schema file could not be saved") from exc
            return json.loads(json.dumps(receipt))

    def preview_ai_mutation(self, schema_id: str, expected_revision: Any, expected_layout_token: Any, transform) -> dict[str, Any]:
        """Apply an AI transform to an owned copy without persisting it."""
        schema_id = self.validate_id(schema_id)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise SchemaStoreError(400, "invalid_schema_binding", "expectedRevision is invalid")
        if not isinstance(expected_layout_token, str) or not LAYOUT_TOKEN_PATTERN.fullmatch(expected_layout_token):
            raise SchemaStoreError(400, "invalid_schema_binding", "layoutToken is invalid")
        with self._schema_read_guard(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            record = found[1]
            if record.get("revision", 0) != expected_revision:
                raise SchemaStoreError(409, "schema_conflict", "Schema changed in another session; reload before continuing", currentRevision=record.get("revision", 0))
            if schema_layout_token(record) != expected_layout_token:
                raise SchemaStoreError(409, "layout_conflict", "Saved layout changed; hard-refresh before continuing")
            candidate, result = transform(json.loads(json.dumps(record)))
            self._validate_record(candidate, schema_id)
            return {"record": json.loads(json.dumps(candidate)), "mutation": json.loads(json.dumps(result))}

    def create_ai_project(self, operation_id: str, project_name: str) -> dict[str, Any]:
        if not isinstance(operation_id, str) or not SCHEMA_ID_PATTERN.fullmatch(operation_id):
            raise SchemaStoreError(400, "invalid_operation", "Operation identity is invalid")
        digest = hashlib.sha256(f"project:{operation_id}".encode()).hexdigest()[:20]
        schema_id = f"schema_{digest}"
        with self._schema_guard(schema_id):
            found = self._find(schema_id)
            if found is not None:
                receipt = found[1].get("aiOperationReceipts", {}).get(operation_id)
                if isinstance(receipt, dict):
                    return json.loads(json.dumps(receipt))
                raise SchemaStoreError(409, "schema_conflict", "Generated project identity is already in use")
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            record = {
                "id": schema_id, "revision": 1, "updatedAt": now,
                "schema": {"projectName": project_name, "tables": [], "relationships": [], "functions": []},
            }
            receipt = {
                "kind": "project_created", "schemaId": schema_id, "projectName": project_name,
                "revision": 1, "updatedAt": now, "layoutToken": schema_layout_token(record),
            }
            record["aiOperationReceipts"] = {operation_id: receipt}
            try:
                write_json(self.schema_dir / f"{schema_id}.json", record)
            except OSError as exc:
                raise SchemaStoreError(500, "schema_store_error", "Schema file could not be saved") from exc
            return json.loads(json.dumps(receipt))

    def sync_ai_migration_result(
        self, schema_id: str, expected_revision: Any, layout_token: Any, refreshed_schema: Any,
    ) -> dict[str, Any]:
        schema_id = self.validate_id(schema_id)
        if not isinstance(refreshed_schema, dict):
            raise SchemaStoreError(400, "invalid_schema", "Refreshed PostgreSQL schema is invalid")
        with self._schema_guard(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            path, current = found
            if current.get("revision", 0) == expected_revision + 1:
                sync = current.get("lastAiMigrationSync")
                if isinstance(sync, dict) and sync.get("sourceRevision") == expected_revision:
                    return json.loads(json.dumps(sync["result"]))
            if current.get("revision", 0) != expected_revision:
                raise SchemaStoreError(409, "schema_conflict", "Saved design changed after migration preview; reload and reconcile")
            if schema_layout_token(current) != layout_token:
                raise SchemaStoreError(409, "layout_conflict", "Saved layout changed after migration preview; hard-refresh and reconcile")
            stored = json.loads(json.dumps(current))
            semantic = json.loads(json.dumps(refreshed_schema))
            semantic["projectName"] = current["schema"]["projectName"]
            if "layout" in current["schema"]:
                semantic["layout"] = json.loads(json.dumps(current["schema"]["layout"]))
            existing_tables = {item.get("id"): item for item in current["schema"].get("tables", []) if isinstance(item, dict)}
            existing_by_oid = {str(item.get("postgres", {}).get("liveOid")): item for item in existing_tables.values() if item.get("postgres", {}).get("liveOid") is not None}
            existing_by_name = {(item.get("namespace") or current["schema"].get("postgres", {}).get("namespace"), item.get("name")): item for item in existing_tables.values()}
            id_map = {}
            for table in semantic.get("tables", []):
                previous = existing_tables.get(table.get("id"))
                if previous is None and table.get("postgres", {}).get("liveOid") is not None:
                    previous = existing_by_oid.get(str(table["postgres"]["liveOid"]))
                if previous is None:
                    previous = existing_by_name.get((table.get("namespace") or semantic.get("postgres", {}).get("namespace"), table.get("name")))
                if previous:
                    id_map[table["id"]] = previous["id"]
                    table["id"] = previous["id"]
                    old_columns = {item.get("name"): item for item in previous.get("columns", []) if isinstance(item, dict)}
                    for column in table.get("columns", []):
                        old_column = old_columns.get(column.get("name"))
                        if old_column:
                            id_map[column["id"]] = old_column["id"]
                            column["id"] = old_column["id"]
                    for key in ("uniqueConstraints", "checks", "indexes", "triggers"):
                        old_objects = {item.get("name"): item for item in previous.get(key, []) if isinstance(item, dict)}
                        for item in table.get(key, []):
                            old_item = old_objects.get(item.get("name"))
                            if old_item:
                                item["id"] = old_item["id"]
                    if isinstance(table.get("primaryKey"), dict) and isinstance(previous.get("primaryKey"), dict):
                        table["primaryKey"]["id"] = previous["primaryKey"]["id"]
                    for field in ("x", "y", "color"):
                        if field in previous:
                            table[field] = previous[field]
            for relationship in semantic.get("relationships", []):
                for field in ("fromTableId", "toTableId", "fromColumnId", "toColumnId"):
                    if relationship.get(field) in id_map:
                        relationship[field] = id_map[relationship[field]]
                for field in ("fromColumnIds", "toColumnIds"):
                    if isinstance(relationship.get(field), list):
                        relationship[field] = [id_map.get(value, value) for value in relationship[field]]
                old_relationship = next((item for item in current["schema"].get("relationships", []) if item.get("constraintName") == relationship.get("constraintName") or item.get("name") == relationship.get("name")), None)
                if old_relationship:
                    relationship["id"] = old_relationship["id"]
            old_views = {(item.get("namespace"), item.get("name")): item for item in current["schema"].get("views", []) if isinstance(item, dict)}
            for view in semantic.get("views", []):
                previous = old_views.get((view.get("namespace"), view.get("name")))
                if previous:
                    view["id"] = previous["id"]
            old_functions = {(item.get("namespace"), item.get("kind"), item.get("name"), item.get("identityArguments")): item for item in current["schema"].get("functions", []) if isinstance(item, dict)}
            for function in semantic.get("functions", []):
                previous = old_functions.get((function.get("namespace"), function.get("kind"), function.get("name"), function.get("identityArguments")))
                if previous:
                    function["id"] = previous["id"]
            stored["schema"] = semantic
            stored["revision"] = expected_revision + 1
            stored["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            result = {"status": "saved", "schemaId": schema_id, "revision": stored["revision"], "updatedAt": stored["updatedAt"], "layoutToken": schema_layout_token(stored)}
            stored["lastAiMigrationSync"] = {"sourceRevision": expected_revision, "result": result}
            try:
                write_json(path, stored)
            except OSError as exc:
                raise SchemaStoreError(500, "schema_store_error", "Schema file could not be saved after migration") from exc
            return result

    def sync_full_migration_result(
        self, schema_id: str, expected_revision: Any, layout_token: Any,
        refreshed_schema: Any, execution_id: str, completeness_proof: Any = None,
        live_fingerprint: Any = None, desired_fingerprint: Any = None,
    ) -> dict[str, Any]:
        """Synchronize full semantic state while proving every established layout value is unchanged."""
        review_proof = {
            "complete": True, "applyCapable": True, "blockingDifferences": [],
            "completenessProof": completeness_proof,
        }
        if not has_full_schema_completeness_proof(
            {"completenessProof": completeness_proof}, review_proof,
            live_fingerprint, desired_fingerprint,
        ):
            raise SchemaStoreError(409, "migration_plan_incomplete", "Full-schema synchronization requires explicit completeness proof")
        if not isinstance(execution_id, str):
            raise SchemaStoreError(400, "invalid_schema_binding", "Execution identity is invalid")
        current = self.get(schema_id)
        receipts = current.get("migrationSyncReceipts", {})
        if isinstance(receipts, dict) and isinstance(receipts.get(execution_id), dict):
            return json.loads(json.dumps(receipts[execution_id]))
        before_layout = json.loads(json.dumps(current.get("schema", {}).get("layout"))) if "layout" in current.get("schema", {}) else None
        before_legacy = {
            item.get("id"): {key: json.loads(json.dumps(item[key])) for key in ("x", "y", "color") if key in item}
            for item in current.get("schema", {}).get("tables", []) if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        result = self.sync_ai_migration_result(schema_id, expected_revision, layout_token, refreshed_schema)
        with self._schema_guard(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            path, stored = found
            after_layout = stored.get("schema", {}).get("layout") if "layout" in stored.get("schema", {}) else None
            after_tables = {item.get("id"): item for item in stored.get("schema", {}).get("tables", []) if isinstance(item, dict)}
            after_legacy = {
                table_id: {key: json.loads(json.dumps(after_tables.get(table_id, {}).get(key))) for key in values}
                for table_id, values in before_legacy.items()
            }
            if after_layout != before_layout or after_legacy != before_legacy:
                raise SchemaStoreError(500, "layout_preservation_failed", "Migration synchronization changed established layout")
            stored.setdefault("migrationSyncReceipts", {})[execution_id] = result
            try:
                write_json(path, stored)
            except OSError as exc:
                raise SchemaStoreError(500, "schema_store_error", "Schema sync receipt could not be saved") from exc
        return result

    def delete(self, schema_id: str, expected_revision: Any, layout_token: Any) -> dict[str, str]:
        schema_id = self.validate_id(schema_id)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise SchemaStoreError(400, "invalid_schema_binding", "expectedRevision is invalid")
        if not isinstance(layout_token, str) or not LAYOUT_TOKEN_PATTERN.fullmatch(layout_token):
            raise SchemaStoreError(400, "invalid_schema_binding", "layoutToken is invalid")
        with self._schema_guard(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            if found[1].get("revision", 0) != expected_revision:
                raise SchemaStoreError(409, "schema_conflict", "Schema changed before it could be deleted", currentRevision=found[1].get("revision", 0))
            if schema_layout_token(found[1]) != layout_token:
                raise SchemaStoreError(409, "layout_conflict", "Saved layout changed before the schema could be deleted")
            try:
                found[0].unlink()
            except OSError as exc:
                raise SchemaStoreError(500, "schema_store_error", "Schema file could not be deleted") from exc
        return {"deleted": schema_id}
