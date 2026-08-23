"""Standalone Schemii backend."""

__all__ = ["PostgresService", "PostgresServiceError", "SchemaStore"]


def __getattr__(name):
    if name in {"PostgresService", "PostgresServiceError"}:
        from .postgres_service import PostgresService, PostgresServiceError

        return {"PostgresService": PostgresService, "PostgresServiceError": PostgresServiceError}[name]
    if name == "SchemaStore":
        from .schema_store import SchemaStore

        return SchemaStore
    raise AttributeError(name)
