from __future__ import annotations

import hashlib
import json
from typing import Any

from .catalog_pagination import catalog_page_size, decode_catalog_cursor, encode_catalog_cursor
from .postgres_common import PostgresServiceError, ValidationError, postgres_error_details
from .postgres_concurrency import postgres_execution
from .result_limits import ResultLimitError


MAX_CATALOG_ROWS = 5000
MAX_QUERY_SPECIFIC_CATALOG_ROWS = 20_000
CATALOG_FETCH_BATCH_SIZE = 500


def _json_cell(value: Any) -> Any:
    # Kept as a compatibility hook for callers; the service-owned limiter is authoritative.
    from .result_limits import ResultLimiter
    return ResultLimiter().cell(value)


class PostgresConnectionMixin:
    def _connect(self, profile_id: str):
        profile = self._profile(profile_id)
        observed = {**profile, "id": profile_id}
        try:
            connection = self._connect_profile(profile)
        except PostgresServiceError:
            self._record_target_connection(observed, False)
            raise
        self._record_target_connection(observed, True)
        return connection

    def _connect_profile(self, profile: dict[str, Any]):
        kwargs = {
            "host": profile["host"], "port": profile["port"], "dbname": profile["dbname"],
            "user": profile["user"], "password": profile["password"], "sslmode": profile["sslmode"],
            "connect_timeout": profile["timeout"], "application_name": self._application_name,
        }
        try:
            if self._connect_factory is not None:
                connection = self._connect_factory(**kwargs)
            else:
                import psycopg
                from psycopg.rows import dict_row
                connection = psycopg.connect(**kwargs, row_factory=dict_row)
        except Exception as exc:
            self._record_target_connection(profile, False)
            raise PostgresServiceError(502, "connection_failed", "PostgreSQL connection failed", postgres_error_details(
                exc, phase="connect", operation="profile_connection", retry={"safe": True, "writeAttempted": False},
            )) from exc
        self._record_target_connection(profile, True)
        return connection

    @staticmethod
    def _profile_fingerprint(profile: dict[str, Any]) -> str:
        encoded = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _close(connection: Any) -> None:
        close = getattr(connection, "close", None)
        if close:
            close()

    @staticmethod
    def _execute_rows(
        connection: Any,
        query: str,
        params: tuple[Any, ...] = (),
        *,
        max_rows: int = MAX_CATALOG_ROWS,
    ) -> list[dict[str, Any]]:
        if (
            isinstance(max_rows, bool) or not isinstance(max_rows, int)
            or not 1 <= max_rows <= MAX_QUERY_SPECIFIC_CATALOG_ROWS
        ):
            raise ValueError("catalog row bound must be an integer from 1 to 20000")
        cursor = connection.cursor()
        try:
            cursor.execute(query, params)
            fetchmany = getattr(cursor, "fetchmany", None)
            rows = fetchmany(max_rows + 1) if fetchmany else cursor.fetchall()
            if len(rows) > max_rows:
                raise PostgresServiceError(
                    422, "catalog_result_too_large", "PostgreSQL catalog result exceeds the item limit",
                    {"policy": "reject", "path": "$", "limit": max_rows, "actual": len(rows)},
                )
            if not rows:
                return []
            if isinstance(rows[0], dict):
                return [dict(row) for row in rows]
            names = [item.name if hasattr(item, "name") else item[0] for item in cursor.description]
            return [dict(zip(names, row)) for row in rows]
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    @staticmethod
    def _execute_all_rows(connection: Any, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Read a complete catalog projection without a generic collection cap."""
        cursor = connection.cursor()
        rows: list[dict[str, Any]] = []
        try:
            cursor.execute(query, params)
            names = [item.name if hasattr(item, "name") else item[0] for item in cursor.description]
            while True:
                batch = cursor.fetchmany(CATALOG_FETCH_BATCH_SIZE)
                if not batch:
                    break
                if isinstance(batch[0], dict):
                    rows.extend(dict(row) for row in batch)
                else:
                    rows.extend(dict(zip(names, row)) for row in batch)
            return rows
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    @staticmethod
    def _execute_statement(connection: Any, query: str, params: tuple[Any, ...] = ()) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(query, params)
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    def _require_namespace(self, connection: Any, namespace: str) -> None:
        rows = self._execute_rows(connection, """
            SELECT EXISTS (
                SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s
            ) AS namespace_exists
        """, (namespace,))
        if not rows or not rows[0].get("namespace_exists"):
            raise PostgresServiceError(404, "namespace_not_found", f"PostgreSQL namespace {namespace} was not found")

    _json_cell = staticmethod(_json_cell)

    def _bounded_cell(self, value: Any, *, path: str, events: list[dict[str, Any]]) -> Any:
        try:
            return self._result_limiter.cell(value, path=path, events=events)
        except ResultLimitError as exc:
            raise PostgresServiceError(422, exc.code, exc.message, exc.details) from exc

    @postgres_execution("catalog")
    def test_profile(self, profile_id: str) -> dict[str, Any]:
        connection = self._connect(profile_id)
        try:
            rows = self._execute_rows(connection, "SELECT current_database() AS database, version() AS version")
            row = rows[0]
            return {"ok": True, "database": row["database"], "serverVersion": row["version"]}
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "query_failed", "PostgreSQL connection test failed", postgres_error_details(
                exc, phase="execute", operation="profile_test", rollback={"required": False},
            )) from exc
        finally:
            self._close(connection)

    @staticmethod
    def _namespace_scope(value: Any) -> str:
        scope = "user" if value is None else value
        if scope not in {"user", "all"}:
            raise ValidationError("scope must be user or all")
        return scope

    @postgres_execution("catalog")
    def namespace_exists(self, profile_id: str, database: str, namespace: str) -> bool:
        database = self._validate_database(database)
        namespace = self._validate_namespace(namespace)
        connection = self._connect(profile_id)
        try:
            self._execute_statement(connection, "SET TRANSACTION READ ONLY")
            current = self._execute_rows(connection, "SELECT current_database() AS database")[0]["database"]
            if current != database:
                raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
            rows = self._execute_rows(connection, """
                SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s) AS namespace_exists
            """, (namespace,))
            return bool(rows and rows[0].get("namespace_exists"))
        finally:
            try:
                connection.rollback()
            except Exception:
                pass
            self._close(connection)

    @postgres_execution("catalog")
    def list_namespace_page(
        self, profile_id: str, database: str, *, scope: str = "user", page_size: Any = None,
        cursor: Any = None,
    ) -> dict[str, Any]:
        database = self._validate_database(database)
        scope = self._namespace_scope(scope)
        size = catalog_page_size(page_size)
        profile_fingerprint = self.profile_context_fingerprint(profile_id)
        connection = self._connect(profile_id)
        try:
            self._execute_statement(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            current = self._execute_rows(connection, "SELECT current_database() AS database")[0]["database"]
            if current != database:
                raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
            if self.profile_context_fingerprint(profile_id) != profile_fingerprint:
                raise PostgresServiceError(409, "profile_changed", "The PostgreSQL profile changed while reading the catalog")
            user_predicate = "AND nspname <> 'information_schema' AND nspname !~ '^pg_'" if scope == "user" else ""
            fingerprint_row = self._execute_rows(connection, f"""
                /* namespace_catalog_fingerprint */
                SELECT pg_catalog.md5(COALESCE(pg_catalog.string_agg(
                    pg_catalog.length(nspname)::text || ':' || nspname, '' ORDER BY nspname
                ), '')) AS first_hash,
                pg_catalog.md5('namespace:' || COALESCE(pg_catalog.string_agg(
                    pg_catalog.length(nspname)::text || ':' || nspname, '' ORDER BY nspname
                ), '')) AS second_hash
                FROM pg_catalog.pg_namespace WHERE true {user_predicate}
            """)[0]
            fingerprint = fingerprint_row["first_hash"] + fingerprint_row["second_hash"]
            context = {
                "type": "namespaces", "profileFingerprint": profile_fingerprint,
                "database": current, "scope": scope, "filter": "", "sort": "name", "pageSize": size,
                "catalogFingerprint": fingerprint,
            }
            after = decode_catalog_cursor(self._catalog_cursor_secret, cursor, context)
            keyset = "AND nspname > %s" if after else ""
            params = ((after[0],) if after else ()) + (size + 1,)
            rows = self._execute_rows(connection, f"""
                /* namespace_catalog_page */
                SELECT nspname AS namespace,
                       CASE WHEN nspname = 'pg_catalog' THEN 'pg_catalog'
                            WHEN nspname = 'information_schema' THEN 'information_schema'
                            WHEN nspname ~ '^pg_(toast_)?temp_[0-9]+$' THEN 'temporary'
                            WHEN nspname = 'pg_toast' OR nspname ~ '^pg_toast_' THEN 'toast'
                            WHEN nspname ~ '^pg_' THEN 'other_system'
                            ELSE 'user' END AS classification
                FROM pg_catalog.pg_namespace
                WHERE true {user_predicate} {keyset}
                ORDER BY nspname LIMIT %s
            """, params)
            has_more = len(rows) > size
            entries = [{"name": row["namespace"], "classification": row["classification"], "system": row["classification"] != "user"} for row in rows[:size]]
            next_cursor = encode_catalog_cursor(self._catalog_cursor_secret, context, [entries[-1]["name"]]) if has_more else None
            return {
                "profileId": profile_id, "profileFingerprint": context["profileFingerprint"], "database": current,
                "scope": scope, "catalogFingerprint": fingerprint, "entries": entries,
                "namespaces": [entry["name"] for entry in entries],
                "page": {"pageSize": size, "returned": len(entries), "hasMore": has_more, "nextCursor": next_cursor},
            }
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL namespaces could not be read", postgres_error_details(
                exc, phase="catalog", operation="list_namespaces", rollback={"attempted": True},
            )) from exc
        finally:
            try:
                connection.rollback()
            except Exception:
                pass
            self._close(connection)

    def list_namespaces(self, profile_id: str) -> list[str]:
        profile = self._profile(profile_id)
        cursor = None
        names = []
        while True:
            page = self.list_namespace_page(profile_id, profile["dbname"], scope="user", cursor=cursor)
            names.extend(page["namespaces"])
            cursor = page["page"]["nextCursor"]
            if cursor is None:
                return names
