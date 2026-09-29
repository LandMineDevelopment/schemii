"""PostgreSQL connection and schema migration support for application metadata."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources
import re
from threading import Condition, Lock
from time import monotonic
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row

from schemii.common.errors import MetadataCapacityError, MetadataStorageUnavailableError

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


def packaged_migrations(migration_packages: tuple[str, ...]) -> tuple[Migration, ...]:
    """Load and validate one explicitly composed migration history."""

    if not migration_packages:
        raise MetadataMigrationError("metadata migration composition must not be empty")
    found: list[Migration] = []
    if len(set(migration_packages)) != len(migration_packages):
        raise MetadataMigrationError("metadata migration package is registered twice")
    for migration_package in migration_packages:
        root = resources.files(migration_package)
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
    return _validated_migrations(tuple(found))


def _validated_migrations(migrations: tuple[Migration, ...]) -> tuple[Migration, ...]:
    ordered = tuple(sorted(migrations, key=lambda migration: migration.version))
    if not ordered:
        raise MetadataMigrationError("metadata migration composition must not be empty")
    versions = [migration.version for migration in ordered]
    if len(set(versions)) != len(versions):
        raise MetadataMigrationError("metadata migration versions must be unique")
    names = [migration.name for migration in ordered]
    if len(set(names)) != len(names):
        raise MetadataMigrationError("metadata migration names must be unique")
    if versions != list(range(1, len(ordered) + 1)):
        raise MetadataMigrationError(
            "metadata migrations must form one contiguous history beginning at version 1"
        )
    return ordered


@dataclass(frozen=True)
class MetadataAdmissionSnapshot:
    maximum_connections: int
    active: int
    peak_active: int
    admitted: int
    rejected: int
    connection_failures: int
    connection_seconds: float
    closed: bool


class _AdmittedConnection:
    """A permit belongs to the native connection, never an awaiting asyncio task."""

    def __init__(self, connection: Any, release: Callable[[], None]) -> None:
        self._connection = connection
        self._release = release
        self._close_lock = Lock()
        self._released = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def __enter__(self) -> _AdmittedConnection:
        try:
            self._connection.__enter__()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        try:
            try:
                self._connection.__exit__(exc_type, exc_value, traceback)
            except (psycopg.OperationalError, psycopg.InterfaceError) as error:
                raise MetadataStorageUnavailableError(
                    "Durable metadata is temporarily unavailable"
                ) from error
        finally:
            # A failed commit can leave psycopg's context manager without closing.
            self.close()
        if isinstance(exc_value, (psycopg.OperationalError, psycopg.InterfaceError)):
            raise MetadataStorageUnavailableError(
                "Durable metadata is temporarily unavailable"
            ) from exc_value

    def close(self) -> None:
        with self._close_lock:
            if self._released:
                return
            self._connection.close()
            self._released = True
            self._release()


class MetadataConnectionFactory:
    """Fail-fast process admission with PostgreSQL-enforced native work deadlines."""

    def __init__(
        self,
        config: MetadataConfig,
        connect: Callable[..., Any] | None = None,
        *,
        statement_timeout_ms: int | None = None,
        lock_timeout_ms: int | None = None,
        idle_transaction_timeout_ms: int | None = None,
        maximum_connections: int | None = None,
        connect_timeout: int | None = None,
        application_name: str | None = None,
    ) -> None:
        self._config = config
        self._connect = connect
        self._statement_timeout_ms = (
            config.statement_timeout_ms
            if statement_timeout_ms is None
            else statement_timeout_ms
        )
        self._lock_timeout_ms = (
            config.lock_timeout_ms if lock_timeout_ms is None else lock_timeout_ms
        )
        self._idle_transaction_timeout_ms = (
            config.idle_transaction_timeout_ms
            if idle_transaction_timeout_ms is None
            else idle_transaction_timeout_ms
        )
        self._maximum_connections = (
            config.maximum_connections
            if maximum_connections is None
            else maximum_connections
        )
        self._connect_timeout = (
            config.connect_timeout if connect_timeout is None else connect_timeout
        )
        self._application_name = application_name or config.application_name
        for name, value in (
            ("statement_timeout_ms", self._statement_timeout_ms),
            ("lock_timeout_ms", self._lock_timeout_ms),
            ("idle_transaction_timeout_ms", self._idle_transaction_timeout_ms),
            ("maximum_connections", self._maximum_connections),
            ("connect_timeout", self._connect_timeout),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"metadata {name} must be a positive integer")
        self._condition = Condition()
        self._active = self._peak_active = self._admitted = self._rejected = 0
        self._connection_failures = 0
        self._connection_seconds = 0.0
        self._closed = False

    def __call__(self) -> Any:
        with self._condition:
            if self._closed:
                raise MetadataStorageUnavailableError(
                    "Durable metadata is shutting down"
                )
            if self._active >= self._maximum_connections:
                self._rejected += 1
                raise MetadataCapacityError("Metadata is busy. Try again shortly.")
            self._active += 1
            self._admitted += 1
            self._peak_active = max(self._active, self._peak_active)
        started = monotonic()
        try:
            connect = self._connect or psycopg.connect
            connection = connect(
                self._config.dsn,
                password=read_secret_file(
                    self._config.password_file,
                    "SCHEMII_METADATA_PASSWORD_FILE",
                ),
                connect_timeout=self._connect_timeout,
                application_name=self._application_name,
                row_factory=dict_row,
                options=(
                    f"-c statement_timeout={self._statement_timeout_ms} "
                    f"-c lock_timeout={self._lock_timeout_ms} "
                    f"-c idle_in_transaction_session_timeout={self._idle_transaction_timeout_ms}"
                ),
            )
            return _AdmittedConnection(connection, self._release)
        except BaseException as error:
            with self._condition:
                self._connection_failures += 1
            self._release()
            if isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError)):
                raise MetadataStorageUnavailableError(
                    "Durable metadata is temporarily unavailable"
                ) from error
            raise
        finally:
            with self._condition:
                self._connection_seconds += monotonic() - started

    def _release(self) -> None:
        with self._condition:
            self._active -= 1
            self._condition.notify_all()

    def admission_snapshot(self) -> MetadataAdmissionSnapshot:
        with self._condition:
            return MetadataAdmissionSnapshot(
                self._maximum_connections,
                self._active,
                self._peak_active,
                self._admitted,
                self._rejected,
                self._connection_failures,
                self._connection_seconds,
                self._closed,
            )

    def close(self) -> None:
        """Reject new work and drain leases without freeing live native permits."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            if not self._condition.wait_for(
                lambda: self._active == 0,
                timeout=self._config.shutdown_timeout_seconds,
            ):
                raise MetadataStorageUnavailableError(
                    "Metadata shutdown timed out waiting for active connections"
                )


class MetadataReadinessProbe:
    """Perform one bounded, side-effect-free metadata dependency check."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def __call__(self) -> None:
        connection: Any | None = None
        try:
            connection = self._connection_factory()
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS ready")
                row = cursor.fetchone()
                if row is None or int(row["ready"]) != 1:
                    raise RuntimeError("metadata readiness query returned no row")
        except Exception as error:
            raise MetadataStorageUnavailableError(
                "Durable metadata is temporarily unavailable"
            ) from error
        finally:
            if connection is not None:
                connection.close()


class MetadataMigrator:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        migrations: tuple[Migration, ...],
    ) -> None:
        self._connection_factory = connection_factory
        self._migrations = _validated_migrations(migrations)

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
                        cursor.execute(
                            "SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,)
                        )
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
            raise MetadataMigrationError(
                "metadata schema is newer than this application"
            )
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
