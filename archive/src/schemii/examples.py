from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_json import write_json
from .postgres_service import PostgresService, PostgresServiceError
from .schema_store import SchemaStore, SchemaStoreError


LOCAL_SCHEMA_ID = "schemii_example_local"
POSTGRES_SCHEMA_ID = "schemii_example_postgres"
POSTGRES_PROFILE_ID = "schemii_example_postgres"
EXAMPLE_VERSION = 1
EXAMPLE_MODES = {"off", "local", "all"}
COLORS = ["#f4b942", "#65a9ff", "#9b82f4", "#59c894", "#ef7c8e", "#e58d4c"]

POSTGRES_LAYOUT = {
    "publishers": (80, 80, COLORS[0]),
    "authors": (80, 560, COLORS[0]),
    "books": (500, 220, COLORS[1]),
    "book_authors": (500, 720, COLORS[2]),
    "inventory": (920, 40, COLORS[3]),
    "reviews": (920, 500, COLORS[3]),
    "order_items": (1340, 220, COLORS[4]),
    "orders": (1760, 60, COLORS[5]),
    "customers": (1760, 560, COLORS[5]),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _column(table: str, name: str, data_type: str, **values: Any) -> dict[str, Any]:
    return {
        "id": f"local_column_{table}_{name}",
        "name": name,
        "type": data_type,
        "primary": values.pop("primary", False),
        "nullable": values.pop("nullable", True),
        "unique": values.pop("unique", False),
        "default": values.pop("default", ""),
        **values,
    }


def _table(name: str, columns: list[dict[str, Any]], x: int, y: int, color: str, **values: Any) -> dict[str, Any]:
    primary_ids = [column["id"] for column in columns if column["primary"]]
    table = {
        "id": f"local_table_{name}",
        "name": name,
        "columns": columns,
        "primaryKey": {
            "id": f"local_pk_{name}", "name": f"{name}_pkey", "columnIds": primary_ids,
            "definition": "PRIMARY KEY (" + ", ".join(column["name"] for column in columns if column["primary"]) + ")",
        } if primary_ids else None,
        "uniqueConstraints": values.pop("uniqueConstraints", []),
        "checks": values.pop("checks", []),
        "indexes": values.pop("indexes", []),
        "triggers": [],
        **values,
    }
    table["_layout"] = {"x": x, "y": y, "color": color, "namespace": None, "name": name, "liveOid": None}
    return table


def _relationship(name: str, source_table: str, source_column: str, target_table: str, target_column: str, *, on_delete: str = "NO ACTION") -> dict[str, Any]:
    return {
        "id": f"local_fk_{name}", "name": name, "constraintName": name,
        "fromTableId": f"local_table_{source_table}", "fromColumnId": f"local_column_{source_table}_{source_column}",
        "toTableId": f"local_table_{target_table}", "toColumnId": f"local_column_{target_table}_{target_column}",
        "onUpdate": "NO ACTION", "onDelete": on_delete, "matchType": "SIMPLE",
        "validated": True, "deferrable": False, "initiallyDeferred": False,
    }


def local_example_record() -> dict[str, Any]:
    tables = [
        _table("venues", [
            _column("venues", "id", "bigint", primary=True, nullable=False, unique=True),
            _column("venues", "name", "varchar(160)", nullable=False, unique=True),
            _column("venues", "city", "varchar(100)", nullable=False),
            _column("venues", "capacity", "integer", nullable=False),
        ], 80, 80, COLORS[0], checks=[{
            "id": "local_check_venues_capacity", "name": "venues_capacity_check",
            "columnIds": ["local_column_venues_capacity"], "definition": "CHECK (capacity > 0)", "validated": True,
        }]),
        _table("events", [
            _column("events", "id", "bigint", primary=True, nullable=False, unique=True),
            _column("events", "venue_id", "bigint", nullable=False),
            _column("events", "name", "varchar(180)", nullable=False),
            _column("events", "starts_on", "date", nullable=False),
            _column("events", "status", "varchar(20)", nullable=False, default="'draft'"),
        ], 470, 80, COLORS[1], checks=[{
            "id": "local_check_events_status", "name": "events_status_check",
            "columnIds": ["local_column_events_status"], "definition": "CHECK (status IN ('draft', 'published', 'complete'))", "validated": True,
        }], indexes=[{
            "id": "local_index_events_starts_on", "name": "events_starts_on_idx",
            "definition": "CREATE INDEX events_starts_on_idx ON events (starts_on)", "unique": False, "method": "btree",
        }]),
        _table("sessions", [
            _column("sessions", "id", "bigint", primary=True, nullable=False, unique=True),
            _column("sessions", "event_id", "bigint", nullable=False),
            _column("sessions", "title", "varchar(200)", nullable=False),
            _column("sessions", "starts_at", "timestamp with time zone", nullable=False),
            _column("sessions", "room", "varchar(80)", nullable=False),
        ], 860, 80, COLORS[2]),
        _table("speakers", [
            _column("speakers", "id", "bigint", primary=True, nullable=False, unique=True),
            _column("speakers", "name", "varchar(140)", nullable=False),
            _column("speakers", "email", "varchar(255)", nullable=False, unique=True),
            _column("speakers", "bio", "text"),
        ], 1260, 40, COLORS[3]),
        _table("session_speakers", [
            _column("session_speakers", "session_id", "bigint", primary=True, nullable=False),
            _column("session_speakers", "speaker_id", "bigint", primary=True, nullable=False),
            _column("session_speakers", "role", "varchar(30)", nullable=False, default="'speaker'"),
        ], 1260, 500, COLORS[3]),
        _table("attendees", [
            _column("attendees", "id", "bigint", primary=True, nullable=False, unique=True),
            _column("attendees", "name", "varchar(140)", nullable=False),
            _column("attendees", "email", "varchar(255)", nullable=False, unique=True),
            _column("attendees", "registered_at", "timestamp with time zone", nullable=False, default="now()"),
        ], 80, 600, COLORS[4]),
        _table("registrations", [
            _column("registrations", "attendee_id", "bigint", primary=True, nullable=False),
            _column("registrations", "session_id", "bigint", primary=True, nullable=False),
            _column("registrations", "checked_in", "boolean", nullable=False, default="false"),
            _column("registrations", "rating", "integer"),
        ], 470, 600, COLORS[5], checks=[{
            "id": "local_check_registrations_rating", "name": "registrations_rating_check",
            "columnIds": ["local_column_registrations_rating"], "definition": "CHECK (rating BETWEEN 1 AND 5)", "validated": True,
        }]),
    ]
    layout = {table["id"]: table.pop("_layout") for table in tables}
    relationships = [
        _relationship("events_venue_id_fkey", "events", "venue_id", "venues", "id", on_delete="RESTRICT"),
        _relationship("sessions_event_id_fkey", "sessions", "event_id", "events", "id", on_delete="CASCADE"),
        _relationship("session_speakers_session_id_fkey", "session_speakers", "session_id", "sessions", "id", on_delete="CASCADE"),
        _relationship("session_speakers_speaker_id_fkey", "session_speakers", "speaker_id", "speakers", "id", on_delete="CASCADE"),
        _relationship("registrations_attendee_id_fkey", "registrations", "attendee_id", "attendees", "id", on_delete="CASCADE"),
        _relationship("registrations_session_id_fkey", "registrations", "session_id", "sessions", "id", on_delete="CASCADE"),
    ]
    return {
        "id": LOCAL_SCHEMA_ID,
        "updatedAt": _utc_now(),
        "schema": {
            "projectName": "Event Studio: Local design example",
            "tables": tables,
            "relationships": relationships,
            "functions": [{
                "id": "local_function_session_registration_count", "name": "session_registration_count",
                "kind": "function", "identityArguments": "target_session_id bigint",
                "arguments": "target_session_id bigint", "returnType": "bigint", "language": "sql",
                "definition": "CREATE FUNCTION session_registration_count(target_session_id bigint) RETURNS bigint LANGUAGE sql STABLE AS $$ SELECT count(*) FROM registrations WHERE session_id = target_session_id $$",
            }],
            "views": [],
            "layout": {"version": 1, "tables": layout, "view": {"x": 35, "y": 35, "zoom": 0.72}},
        },
    }


class ExampleInstaller:
    def __init__(
        self,
        service: PostgresService,
        store: SchemaStore,
        config_dir: str | os.PathLike[str],
        mode: str,
        postgres_profile: dict[str, Any] | None = None,
        *,
        manage_postgres_profile: bool = True,
    ):
        if mode not in EXAMPLE_MODES:
            raise ValueError("SCHEMII_EXAMPLES must be off, local, or all")
        self.service = service
        self.store = store
        self.config_dir = Path(config_dir)
        self.mode = mode
        self.postgres_profile = postgres_profile
        self.manage_postgres_profile = manage_postgres_profile
        self.marker_path = self.config_dir / "examples_initialized.json"

    def expected_components(self) -> list[str]:
        if self.mode == "off":
            return []
        return ["local", "postgres"] if self.mode == "all" else ["local"]

    def initialize_once(self) -> dict[str, Any]:
        completed = self._read_marker()
        pending = [item for item in self.expected_components() if item not in completed]
        result = self.restore(pending)
        completed.update(result["completed"])
        if pending:
            self._write_marker(completed)
        return result

    def restore(self, components: list[str] | None = None) -> dict[str, Any]:
        requested = self.expected_components() if components is None else components
        result: dict[str, Any] = {"installed": [], "preserved": [], "completed": [], "errors": []}
        for component in requested:
            try:
                complete = self._install_local(result) if component == "local" else self._install_postgres(result)
                if complete:
                    result["completed"].append(component)
            except PostgresServiceError as error:
                result["errors"].append({"component": component, "message": error.message})
            except SchemaStoreError as error:
                result["errors"].append({"component": component, "message": error.payload["error"]["message"]})
            except (OSError, ValueError) as error:
                result["errors"].append({"component": component, "message": str(error)[:300]})
        return result

    def _install_local(self, result: dict[str, Any]) -> bool:
        if self._schema_exists(LOCAL_SCHEMA_ID):
            result["preserved"].append(LOCAL_SCHEMA_ID)
            return True
        self.store.save(LOCAL_SCHEMA_ID, local_example_record(), expected_layout_token=None, layout_protocol=None)
        result["installed"].append(LOCAL_SCHEMA_ID)
        return True

    def _install_postgres(self, result: dict[str, Any]) -> bool:
        if not self.postgres_profile:
            result["errors"].append({"component": "postgres", "message": "The included PostgreSQL example is not enabled in this launch mode"})
            return False
        profiles = {profile["id"]: profile for profile in self.service.list_profiles()}
        expected_redacted = {key: value for key, value in self.postgres_profile.items() if key != "password"}
        existing = profiles.get(POSTGRES_PROFILE_ID)
        if existing is None:
            if not self.manage_postgres_profile:
                result["errors"].append({"component": "postgres", "message": "The shared tutorial connection was not initialized"})
                return False
            self.service.save_profile(POSTGRES_PROFILE_ID, self.postgres_profile)
            result["installed"].append(POSTGRES_PROFILE_ID)
        elif any(existing.get(key) != value for key, value in expected_redacted.items()):
            result["errors"].append({"component": "postgres", "message": "The reserved tutorial connection ID contains different settings"})
            return False
        elif self.manage_postgres_profile:
            self.service.save_profile(POSTGRES_PROFILE_ID, self.postgres_profile)
            result["preserved"].append(POSTGRES_PROFILE_ID)
        else:
            result["preserved"].append(POSTGRES_PROFILE_ID)

        connection = self.service.test_profile(POSTGRES_PROFILE_ID)
        if connection.get("database") != self.postgres_profile["dbname"]:
            result["errors"].append({"component": "postgres", "message": "The tutorial connection reached an unexpected database"})
            return False
        namespace = "bookstore"
        if not self.service.namespace_exists(POSTGRES_PROFILE_ID, self.postgres_profile["dbname"], namespace):
            result["errors"].append({"component": "postgres", "message": "The bookstore tutorial namespace is unavailable"})
            return False
        if self._schema_exists(POSTGRES_SCHEMA_ID):
            result["preserved"].append(POSTGRES_SCHEMA_ID)
            return True
        schema = self.service.introspect(POSTGRES_PROFILE_ID, namespace)
        schema["projectName"] = "Mercury Books: PostgreSQL tutorial"
        layout = {}
        for index, table in enumerate(schema["tables"]):
            x, y, color = POSTGRES_LAYOUT.get(table["name"], (80 + (index % 4) * 420, 80 + (index // 4) * 440, COLORS[index % len(COLORS)]))
            layout[table["id"]] = {
                "x": x, "y": y, "color": color, "namespace": namespace, "name": table["name"],
                "liveOid": table.get("postgres", {}).get("liveOid"),
            }
            table.pop("x", None)
            table.pop("y", None)
            table.pop("color", None)
        schema["layout"] = {"version": 1, "tables": layout, "view": {"x": 35, "y": 35, "zoom": 0.58}}
        record = {"id": POSTGRES_SCHEMA_ID, "updatedAt": _utc_now(), "schema": schema}
        self.store.save(POSTGRES_SCHEMA_ID, record, expected_layout_token=None, layout_protocol=None)
        result["installed"].append(POSTGRES_SCHEMA_ID)
        return True

    def _schema_exists(self, schema_id: str) -> bool:
        return any(record.get("id") == schema_id for record in self.store.list())

    def _read_marker(self) -> set[str]:
        if not self.marker_path.exists():
            return set()
        try:
            payload = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if payload.get("version") != EXAMPLE_VERSION or not isinstance(payload.get("components"), list):
            return set()
        return {item for item in payload["components"] if item in {"local", "postgres"}}

    def _write_marker(self, components: set[str]) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        write_json(
            self.marker_path,
            {"version": EXAMPLE_VERSION, "components": sorted(components)},
            mode=0o600,
        )


def postgres_example_profile_from_environment() -> dict[str, Any]:
    try:
        port = int(os.environ.get("SCHEMII_EXAMPLE_POSTGRES_PORT", "5432"))
    except ValueError as error:
        raise ValueError("SCHEMII_EXAMPLE_POSTGRES_PORT must be an integer") from error
    return {
        "name": "Mercury Books: Included PostgreSQL",
        "host": os.environ.get("SCHEMII_EXAMPLE_POSTGRES_HOST", "postgres"),
        "port": port,
        "dbname": os.environ.get("SCHEMII_EXAMPLE_POSTGRES_DB", "schemii"),
        "user": os.environ.get("SCHEMII_EXAMPLE_POSTGRES_USER", "schemii"),
        "password": os.environ.get("SCHEMII_EXAMPLE_POSTGRES_PASSWORD", "schemii-local"),
        "sslmode": "disable",
        "timeout": 10,
    }


def initialize_postgres_example_profile(service: PostgresService, profile: dict[str, Any]) -> dict[str, Any]:
    profiles = {item["id"]: item for item in service.list_profiles()}
    existing = profiles.get(POSTGRES_PROFILE_ID)
    expected = {key: value for key, value in profile.items() if key != "password"}
    if existing is not None and any(existing.get(key) != value for key, value in expected.items()):
        raise ValueError("The reserved tutorial connection ID contains different settings")
    return service.save_profile(POSTGRES_PROFILE_ID, profile)


def installer_from_environment(service: PostgresService, store: SchemaStore, config_dir: str | os.PathLike[str]) -> ExampleInstaller:
    mode = os.environ.get("SCHEMII_EXAMPLES", "off")
    owner = os.environ.get("SCHEMII_EXAMPLE_PROFILE_OWNER", "application")
    if owner not in {"application", "initializer"}:
        raise ValueError("SCHEMII_EXAMPLE_PROFILE_OWNER must be application or initializer")
    profile = postgres_example_profile_from_environment() if mode == "all" else None
    return ExampleInstaller(service, store, config_dir, mode, profile, manage_postgres_profile=owner == "application")
