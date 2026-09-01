"""PostgreSQL connection and schema migration support for application metadata."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources
import re
from typing import Any, Callable

from psycopg.rows import dict_row

from .config import MetadataConfig
from .secrets import read_secret_file


_MIGRATION_FILE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_MIGRATION_LOCK = 0x534348454D4949


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    sql: str


class MetadataMigrationError(RuntimeError):
    """The durable metadata schema cannot be trusted or migrated."""


def packaged_migrations() -> tuple[Migration, ...]:
    found: list[Migration] = []
    root = resources.files("schemii.common.metadata.migrations")
    for entry in root.iterdir():
        match = _MIGRATION_FILE.fullmatch(entry.name)
        if match is None:
            continue
        sql = entry.read_text(encoding="utf-8")
        found.append(
            Migration(
                version=int(match.group(1)),
                name=entry.name,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                sql=sql,
            )
        )
    found.sort(key=lambda migration: migration.version)
    if [migration.version for migration in found] != list(range(1, len(found) + 1)):
        raise MetadataMigrationError("metadata migrations must be contiguous")
    return tuple(found)


class MetadataConnectionFactory:
    def __init__(
        self,
        config: MetadataConfig,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        self._config = config
        self._connect = connect

    def __call__(self) -> Any:
        import psycopg

        connect = self._connect or psycopg.connect
        return connect(
            self._config.dsn,
            password=read_secret_file(
                self._config.password_file,
                "SCHEMII_METADATA_PASSWORD_FILE",
            ),
            connect_timeout=self._config.connect_timeout,
            application_name=self._config.application_name,
            row_factory=dict_row,
        )


class MetadataMigrator:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        migrations: tuple[Migration, ...] | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._migrations = packaged_migrations() if migrations is None else migrations

    def migrate(self) -> int:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
                try:
                    cursor.execute("CREATE SCHEMA IF NOT EXISTS metadata")
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS metadata.schema_migrations (
                            version integer PRIMARY KEY CHECK (version > 0),
                            name text NOT NULL UNIQUE,
                            checksum char(64) NOT NULL CHECK (
                                checksum ~ '^[0-9a-f]{64}$'
                            ),
                            applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
                        )
                        """
                    )
                    connection.commit()
                    cursor.execute(
                        """
                        SELECT version, name, checksum
                        FROM metadata.schema_migrations
                        ORDER BY version
                        """
                    )
                    applied = self._validate_applied(cursor.fetchall())
                    for migration in self._migrations:
                        if migration.version in applied:
                            continue
                        cursor.execute(migration.sql)
                        cursor.execute(
                            """
                            INSERT INTO metadata.schema_migrations
                                (version, name, checksum)
                            VALUES (%s, %s, %s)
                            """,
                            (migration.version, migration.name, migration.checksum),
                        )
                        connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    try:
                        cursor.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
                        connection.commit()
                    except Exception:
                        connection.rollback()
        except MetadataMigrationError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise MetadataMigrationError("metadata migration failed") from error
        finally:
            connection.close()
        return len(self._migrations)

    def _validate_applied(self, rows: list[dict[str, Any]]) -> set[int]:
        if len(rows) > len(self._migrations):
            raise MetadataMigrationError("metadata schema is newer than this application")
        applied: set[int] = set()
        for index, row in enumerate(rows):
            version = int(row["version"])
            if version != index + 1:
                raise MetadataMigrationError(
                    "metadata migration history is not a contiguous prefix"
                )
            migration = self._migrations[index]
            if (row["name"], row["checksum"]) != (
                migration.name,
                migration.checksum,
            ):
                raise MetadataMigrationError(
                    "an applied metadata migration does not match this application"
                )
            applied.add(version)
        return applied
