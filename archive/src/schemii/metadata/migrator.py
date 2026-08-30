from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any, Callable

from .errors import MetadataStoreError


_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_LOCK_KEY = 0x534348454D4949  # Stable namespace key: "SCHEMII".


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    sql: str


def packaged_migrations() -> tuple[Migration, ...]:
    found: list[Migration] = []
    root = resources.files("schemii.metadata.migrations")
    for entry in root.iterdir():
        match = _MIGRATION_NAME.fullmatch(entry.name)
        if match is None:
            continue
        sql = entry.read_text(encoding="utf-8")
        found.append(Migration(int(match.group(1)), entry.name, hashlib.sha256(sql.encode("utf-8")).hexdigest(), sql))
    found.sort(key=lambda migration: migration.version)
    if [item.version for item in found] != list(range(1, len(found) + 1)):
        raise MetadataStoreError("metadata_migration_invalid", "Packaged metadata migrations are not contiguous")
    return tuple(found)


class MetadataMigrator:
    def __init__(self, connection_factory: Callable[[], Any], migrations: tuple[Migration, ...] | None = None):
        self.connection_factory = connection_factory
        self.migrations = packaged_migrations() if migrations is None else migrations

    @property
    def expected_version(self) -> int:
        return self.migrations[-1].version if self.migrations else 0

    def migrate(self) -> int:
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS metadata_schema_migrations (
                        version integer PRIMARY KEY CHECK (version > 0),
                        name text NOT NULL UNIQUE,
                        checksum char(64) NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
                        applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
                    )
                """)
                connection.commit()
                cursor.execute("SELECT version, name, checksum FROM metadata_schema_migrations ORDER BY version")
                applied = validate_applied_migrations(cursor.fetchall(), self.migrations)
                for migration in self.migrations:
                    if migration.version in applied:
                        continue
                    cursor.execute(migration.sql)
                    cursor.execute(
                        "INSERT INTO metadata_schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                        (migration.version, migration.name, migration.checksum),
                    )
                    connection.commit()
                return self.expected_version
            except Exception:
                connection.rollback()
                raise
            finally:
                try:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
                    connection.commit()
                finally:
                    cursor.close()
        except MetadataStoreError:
            raise
        except Exception as exc:
            raise MetadataStoreError("metadata_migration_failed", "Metadata migration failed", retryable=True) from exc
        finally:
            connection.close()


def _row_value(row: Any, name: str, index: int) -> Any:
    return row[name] if isinstance(row, dict) else row[index]


def validate_applied_migrations(rows: Any, migrations: tuple[Migration, ...]) -> set[int]:
    applied_rows = list(rows)
    if len(applied_rows) > len(migrations):
        raise MetadataStoreError("metadata_schema_newer", "Metadata schema is newer than this application")
    applied: set[int] = set()
    for index, row in enumerate(applied_rows):
        version = int(_row_value(row, "version", 0))
        if version != index + 1:
            raise MetadataStoreError("metadata_migration_history_invalid", "Metadata migration history is not a contiguous prefix")
        migration = migrations[index]
        name = _row_value(row, "name", 1)
        checksum = _row_value(row, "checksum", 2)
        if (name, checksum) != (migration.name, migration.checksum):
            raise MetadataStoreError("metadata_migration_checksum", "An applied metadata migration checksum does not match")
        applied.add(version)
    return applied
