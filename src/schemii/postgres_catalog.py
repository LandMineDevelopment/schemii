from __future__ import annotations

import re
from typing import Any

from .catalog_pagination import catalog_page_size, decode_catalog_cursor, encode_catalog_cursor
from .postgres_common import NotFoundError, PostgresServiceError, ValidationError, canonical_fingerprint, postgres_error_details
from .postgres_concurrency import postgres_execution
from .query_type_capabilities import catalog_capabilities
from .view_provenance import (
    derive_column_provenance,
    derive_join_provenance,
    derive_sql_stages,
    unavailable_sql_stages,
)


FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_RELATION_DEFINITION_BYTES = 64 * 1024
RELATION_KINDS = {"table": "r", "partitioned_table": "p", "view": "v", "materialized_view": "m", "foreign_table": "f"}


class PostgresCatalogMixin:
    @postgres_execution("catalog")
    def list_relations(
        self, profile_id: str, database: str, namespace: str, *, kind: str | None = None,
        search: str | None = None, page_size: Any = None, cursor: Any = None,
    ) -> dict[str, Any]:
        database = self._validate_database(database)
        namespace = self._validate_namespace(namespace)
        if kind is not None and kind not in RELATION_KINDS:
            raise ValidationError("kind must be table, partitioned_table, view, materialized_view, or foreign_table")
        if search is None:
            search = ""
        if not isinstance(search, str) or len(search) > 128 or "\x00" in search or search != search.strip():
            raise ValidationError("search must be a trimmed string up to 128 characters")
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
            self._require_namespace(connection, namespace)
            filters = ["n.nspname = %s", "c.relkind IN ('r', 'p', 'v', 'm', 'f')"]
            filter_params: list[Any] = [namespace]
            if kind is not None:
                filters.append("c.relkind = %s")
                filter_params.append(RELATION_KINDS[kind])
            if search:
                filters.append("pg_catalog.strpos(pg_catalog.lower(c.relname), pg_catalog.lower(%s)) > 0")
                filter_params.append(search)
            where = " AND ".join(filters)
            fingerprint_row = self._execute_rows(connection, f"""
                /* relation_catalog_fingerprint */
                SELECT pg_catalog.md5(COALESCE(pg_catalog.string_agg(
                    c.relkind::text || pg_catalog.length(c.relname)::text || ':' || c.relname, '' ORDER BY c.relname, c.relkind
                ), '')) AS first_hash,
                pg_catalog.md5('relation:' || COALESCE(pg_catalog.string_agg(
                    c.relkind::text || pg_catalog.length(c.relname)::text || ':' || c.relname, '' ORDER BY c.relname, c.relkind
                ), '')) AS second_hash
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE {where}
            """, tuple(filter_params))[0]
            fingerprint = fingerprint_row["first_hash"] + fingerprint_row["second_hash"]
            context = {
                "type": "relations", "profileFingerprint": profile_fingerprint,
                "database": current, "namespace": namespace, "scope": "namespace", "filter": {"kind": kind, "search": search},
                "sort": "name_kind", "pageSize": size, "catalogFingerprint": fingerprint,
            }
            after = decode_catalog_cursor(self._catalog_cursor_secret, cursor, context)
            keyset = "AND (c.relname, c.relkind) > (%s, %s)" if after else ""
            page_params = filter_params + (after if after else []) + [size + 1]
            rows = self._execute_rows(connection, f"""
                /* relation_catalog_page */
                SELECT c.relname AS relation_name,
                       c.relkind AS catalog_kind,
                       CASE WHEN c.relkind = 'r' THEN 'table'
                            WHEN c.relkind = 'p' THEN 'partitioned_table'
                            WHEN c.relkind = 'v' THEN 'view'
                            WHEN c.relkind = 'm' THEN 'materialized_view'
                            ELSE 'foreign_table' END AS relation_kind
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE {where} {keyset}
                ORDER BY c.relname, c.relkind LIMIT %s
            """, tuple(page_params))
            has_more = len(rows) > size
            entries = [{
                "profileId": profile_id, "database": current, "namespace": namespace,
                "relation": row["relation_name"], "name": row["relation_name"], "kind": row["relation_kind"],
            } for row in rows[:size]]
            next_cursor = encode_catalog_cursor(
                self._catalog_cursor_secret, context, [rows[size - 1]["relation_name"], rows[size - 1]["catalog_kind"]]
            ) if has_more else None
            return {
                "profileId": profile_id,
                "profileFingerprint": context["profileFingerprint"],
                "database": current,
                "namespace": namespace,
                "filter": {"kind": kind, "search": search},
                "catalogFingerprint": fingerprint,
                "entries": entries,
                "relations": [{"name": entry["name"], "kind": entry["kind"]} for entry in entries],
                "page": {"pageSize": size, "returned": len(entries), "hasMore": has_more, "nextCursor": next_cursor},
            }
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL relations could not be read", postgres_error_details(
                exc, phase="catalog", operation="list_relations", rollback={"attempted": True},
            )) from exc
        finally:
            try:
                connection.rollback()
            except Exception:
                pass
            self._close(connection)

    @postgres_execution("catalog")
    def inspect_relation(
        self,
        profile_id: str,
        database: str,
        namespace: str,
        relation: str,
        expected_kind: str | None = None,
        expected_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        database = self._validate_database(database)
        namespace = self._validate_namespace(namespace)
        relation = self._validate_relation_name(relation)
        if expected_kind is not None and expected_kind not in RELATION_KINDS:
            raise ValidationError("expectedKind must be a supported relation kind")
        if expected_fingerprint is not None and (not isinstance(expected_fingerprint, str) or not FINGERPRINT_RE.fullmatch(expected_fingerprint)):
            raise ValidationError("expectedFingerprint must be a 64-character lowercase hexadecimal fingerprint")
        profile_fingerprint = self.profile_context_fingerprint(profile_id)
        connection = self._connect(profile_id)
        try:
            self._execute_statement(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            descriptor = self._inspect_relation_connection(
                connection, profile_id, database, namespace, relation, expected_kind, expected_fingerprint
            )
            if self.profile_context_fingerprint(profile_id) != profile_fingerprint:
                raise PostgresServiceError(409, "profile_changed", "The PostgreSQL profile changed while inspecting the relation")
            return descriptor
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL relation metadata could not be read", postgres_error_details(
                exc, phase="catalog", operation="inspect_relation", rollback={"attempted": True},
            )) from exc
        finally:
            try:
                connection.rollback()
            except Exception:
                pass
            self._close(connection)

    def _inspect_relation_connection(
        self,
        connection: Any,
        profile_id: str,
        database: str,
        namespace: str,
        relation: str,
        expected_kind: str | None,
        expected_fingerprint: str | None,
    ) -> dict[str, Any]:
        connection_row = self._execute_rows(connection, """
            SELECT current_database() AS database,
                   current_setting('server_version_num')::integer AS server_version_num
        """)[0]
        current = connection_row["database"]
        if current != database:
            raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
        self._require_namespace(connection, namespace)
        supports_maintain = int(connection_row.get("server_version_num") or 0) >= 170000
        supports_set_role = int(connection_row.get("server_version_num") or 0) >= 160000
        refresh_capability = (
            "pg_catalog.has_table_privilege(c.oid, 'MAINTAIN')"
            if supports_maintain
            else "pg_catalog.pg_has_role(c.relowner, 'USAGE')"
        )
        set_role_capability = (
            "pg_catalog.pg_has_role(c.relowner, 'SET')"
            if supports_set_role
            else "pg_catalog.pg_has_role(c.relowner, 'MEMBER')"
        )
        relation_rows = self._execute_rows(connection, f"""
                SELECT c.oid AS live_oid,
                       c.relkind AS catalog_kind,
                        CASE WHEN c.relkind = 'r' THEN 'table'
                             WHEN c.relkind = 'p' THEN 'partitioned_table'
                             WHEN c.relkind = 'v' THEN 'view'
                             WHEN c.relkind = 'm' THEN 'materialized_view'
                             ELSE 'foreign_table' END AS relation_kind,
                       CASE WHEN c.relkind IN ('v', 'm') THEN pg_catalog.pg_get_viewdef(c.oid, true) END AS view_definition,
                       pg_catalog.pg_get_userbyid(c.relowner) AS owner_name,
                       current_user AS current_role,
                       current_user = pg_catalog.pg_get_userbyid(c.relowner) AS is_owner,
                       pg_catalog.pg_has_role(c.relowner, 'USAGE') AS inherits_owner,
                       {set_role_capability} AS can_set_role,
                       pg_catalog.has_table_privilege(c.oid, 'SELECT') AS can_select,
                       {refresh_capability} AS can_refresh,
                       c.relispopulated AS materialized_populated
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
        """, (namespace, relation))
        if not relation_rows:
            raise NotFoundError(f"Relation {namespace}.{relation} was not found")
        relation_row = relation_rows[0]
        column_rows = self._execute_rows(connection, """
                /* structured_query_column_types */
                WITH RECURSIVE type_chain AS (
                    SELECT a.attnum, t.oid, t.typnamespace, t.typname, t.typtype, t.typcategory,
                           t.typbasetype, t.typelem, t.typarray, t.xmin::text AS catalog_version, 0 AS depth
                    FROM pg_catalog.pg_attribute a
                    JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
                    WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
                    UNION ALL
                    SELECT chain.attnum, base.oid, base.typnamespace, base.typname, base.typtype,
                           base.typcategory, base.typbasetype, base.typelem, base.typarray,
                           base.xmin::text, chain.depth + 1
                    FROM type_chain chain
                    JOIN pg_catalog.pg_type base ON base.oid = chain.typbasetype
                    WHERE chain.typbasetype <> 0 AND chain.depth < 32
                ), base_types AS (
                    SELECT DISTINCT ON (attnum) * FROM type_chain ORDER BY attnum, depth DESC
                )
                SELECT a.attname AS column_name,
                       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                       NOT a.attnotnull AS nullable,
                       a.attnum AS ordinal,
                       base.typcategory AS type_category,
                       base.typname AS type_name,
                       a.atttypid::integer AS declared_type_oid,
                       declared_namespace.nspname AS declared_type_namespace,
                       attribute_type.typname AS declared_type_name,
                       attribute_type.typtype AS declared_type_kind,
                       attribute_type.typcategory AS declared_type_category,
                       base.oid::integer AS base_type_oid,
                       base_namespace.nspname AS base_type_namespace,
                       base.typname AS base_type_name,
                       base.typtype AS base_type_kind,
                       base.typcategory AS base_type_category,
                       type_dependencies.version AS type_catalog_version,
                       CASE WHEN a.attcollation = 0 THEN NULL ELSE a.attcollation::integer END AS collation_oid,
                       coll_namespace.nspname AS collation_namespace,
                       coll.collname AS collation_name,
                       coll.collprovider AS collation_provider,
                       coll.collisdeterministic AS collation_deterministic,
                       coll.collversion AS collation_version,
                       coll.xmin::text AS collation_catalog_version,
                       CASE WHEN base.typelem = 0 THEN NULL ELSE base.oid::integer END AS array_type_oid,
                       CASE WHEN base.typelem = 0 THEN NULL ELSE base.typelem::integer END AS array_element_type_oid,
                       rng.rngtypid::integer AS range_type_oid,
                       rng.rngsubtype::integer AS range_subtype_oid,
                       NULLIF(rng.rngmultitypid, 0)::integer AS multirange_type_oid,
                       rng.xmin::text AS range_catalog_version
                FROM pg_catalog.pg_attribute a
                JOIN pg_catalog.pg_type attribute_type ON attribute_type.oid = a.atttypid
                JOIN pg_catalog.pg_namespace declared_namespace ON declared_namespace.oid = attribute_type.typnamespace
                JOIN base_types base ON base.attnum = a.attnum
                JOIN pg_catalog.pg_namespace base_namespace ON base_namespace.oid = base.typnamespace
                JOIN LATERAL (
                    SELECT pg_catalog.md5(pg_catalog.string_agg(identity, ',' ORDER BY identity)) AS version
                    FROM (
                        SELECT 'type:' || chain.oid::text || ':' || chain.catalog_version AS identity
                        FROM type_chain chain WHERE chain.attnum = a.attnum
                        UNION ALL
                         SELECT 'constraint:' || con.oid::text || ':' || con.xmin::text || ':' || pg_catalog.pg_get_constraintdef(con.oid, true)
                         FROM type_chain chain
                         JOIN pg_catalog.pg_constraint con ON con.contypid = chain.oid
                        WHERE chain.attnum = a.attnum
                        UNION ALL
                        SELECT 'enum:' || enum.oid::text || ':' || enum.xmin::text || ':' || enum.enumsortorder::text || ':' || enum.enumlabel
                        FROM type_chain chain
                        JOIN pg_catalog.pg_enum enum ON enum.enumtypid = chain.oid
                        WHERE chain.attnum = a.attnum
                        UNION ALL
                        SELECT 'element:' || element.oid::text || ':' || element.xmin::text
                        FROM pg_catalog.pg_type element WHERE element.oid = base.typelem AND base.typelem <> 0
                        UNION ALL
                        SELECT 'element-enum:' || enum.oid::text || ':' || enum.xmin::text || ':' || enum.enumsortorder::text || ':' || enum.enumlabel
                        FROM pg_catalog.pg_enum enum WHERE enum.enumtypid = base.typelem AND base.typelem <> 0
                    ) dependencies
                ) type_dependencies ON true
                LEFT JOIN pg_catalog.pg_collation coll ON coll.oid = a.attcollation
                LEFT JOIN pg_catalog.pg_namespace coll_namespace ON coll_namespace.oid = coll.collnamespace
                LEFT JOIN pg_catalog.pg_range rng ON rng.rngtypid = base.oid
                 WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
                   ORDER BY a.attnum
        """, (relation_row["live_oid"], relation_row["live_oid"]))
        operator_rows = self._execute_rows(connection, """
                /* structured_query_operators */
                WITH RECURSIVE type_chain AS (
                    SELECT a.attnum, t.oid, t.typbasetype, t.typtype, t.typelem, 0 AS depth
                    FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
                    WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
                    UNION ALL
                    SELECT chain.attnum, base.oid, base.typbasetype, base.typtype, base.typelem, chain.depth + 1
                    FROM type_chain chain JOIN pg_catalog.pg_type base ON base.oid = chain.typbasetype
                    WHERE chain.typbasetype <> 0 AND chain.depth < 32
                ), base_types AS (
                    SELECT DISTINCT ON (attnum) attnum, oid, typtype, typelem FROM type_chain ORDER BY attnum, depth DESC
                ), opclass_candidates AS (
                    SELECT base.attnum, base.oid, base.typtype, base.typelem,
                           opclass.oid AS opclass_oid, opclass.opcintype, opclass.opcfamily,
                           opclass.xmin::text AS opclass_version, access_method.oid AS access_method_oid,
                           access_method.amname, binary_cast.oid AS cast_oid,
                           binary_cast.xmin::text AS cast_version,
                           CASE WHEN opclass.opcintype = base.oid THEN 0 ELSE 1 END AS match_rank,
                           opclass_type.typcategory = base_type.typcategory AND opclass_type.typispreferred AS preferred
                    FROM base_types base
                    JOIN pg_catalog.pg_type base_type ON base_type.oid = base.oid
                    JOIN pg_catalog.pg_opclass opclass ON opclass.opcdefault
                    JOIN pg_catalog.pg_type opclass_type ON opclass_type.oid = opclass.opcintype
                    JOIN pg_catalog.pg_am access_method ON access_method.oid = opclass.opcmethod
                         AND access_method.amname IN ('btree', 'hash')
                    LEFT JOIN pg_catalog.pg_cast binary_cast ON binary_cast.castsource = base.oid
                         AND binary_cast.casttarget = opclass.opcintype
                         AND binary_cast.castmethod = 'b' AND binary_cast.castcontext = 'i'
                    WHERE opclass.opcintype = base.oid OR binary_cast.oid IS NOT NULL
                       OR opclass_type.typname IN ('any', 'anyelement', 'anycompatible')
                       OR opclass_type.typname = 'anyenum' AND base.typtype = 'e'
                       OR opclass_type.typname IN ('anyarray', 'anycompatiblearray') AND base.typelem <> 0
                       OR opclass_type.typname IN ('anyrange', 'anycompatiblerange') AND EXISTS (SELECT 1 FROM pg_catalog.pg_range r WHERE r.rngtypid = base.oid)
                       OR opclass_type.typname IN ('anymultirange', 'anycompatiblemultirange') AND EXISTS (SELECT 1 FROM pg_catalog.pg_range r WHERE r.rngmultitypid = base.oid)
                       OR opclass_type.typname = 'anynonarray' AND base.typelem = 0
                ), ranked_opclasses AS (
                    SELECT candidate.*,
                           pg_catalog.count(*) FILTER (WHERE match_rank = 0) OVER candidate_group AS exact_count,
                           pg_catalog.count(*) FILTER (WHERE match_rank = 1) OVER candidate_group AS compatible_count,
                           pg_catalog.count(*) FILTER (WHERE match_rank = 1 AND preferred) OVER candidate_group AS preferred_count
                    FROM opclass_candidates candidate
                    WINDOW candidate_group AS (PARTITION BY attnum, access_method_oid)
                ), selected_opclasses AS (
                    SELECT * FROM ranked_opclasses
                    WHERE match_rank = 0 AND exact_count = 1
                       OR exact_count = 0 AND match_rank = 1 AND preferred AND preferred_count = 1
                       OR exact_count = 0 AND match_rank = 1 AND preferred_count = 0 AND compatible_count = 1
                ), family_operators AS (
                    SELECT selected_opclass.attnum, selected_opclass.oid AS input_type_oid, operator.oid AS operator_oid,
                           operator.oprnamespace, operator.oprname, operator.oprresult,
                           operator.oprnegate, operator.oprcode, operator.xmin::text AS operator_version,
                           implementation.xmin::text AS implementation_version, selected_opclass.opclass_oid,
                           selected_opclass.opclass_version, family.oid AS family_oid,
                           family.xmin::text AS family_version, support.dependencies AS support_versions,
                           COALESCE(selected_opclass.cast_oid::text || ':' || selected_opclass.cast_version, '') AS cast_identity,
                           selected_opclass.amname,
                           member.amopstrategy
                    FROM selected_opclasses selected_opclass
                    JOIN pg_catalog.pg_opfamily family ON family.oid = selected_opclass.opcfamily
                    JOIN pg_catalog.pg_amop member ON member.amopfamily = family.oid
                         AND member.amoplefttype IN (selected_opclass.oid, selected_opclass.opcintype)
                         AND member.amoprighttype IN (selected_opclass.oid, selected_opclass.opcintype)
                    JOIN pg_catalog.pg_operator operator ON operator.oid = member.amopopr
                    JOIN pg_catalog.pg_proc implementation ON implementation.oid = operator.oprcode
                    LEFT JOIN LATERAL (
                        SELECT pg_catalog.string_agg(procedure.oid::text || ':' || procedure.xmin::text, ',' ORDER BY procedure.oid) AS dependencies
                        FROM pg_catalog.pg_amproc support_member
                        JOIN pg_catalog.pg_proc procedure ON procedure.oid = support_member.amproc
                        WHERE support_member.amprocfamily = family.oid
                    ) support ON true
                ), candidates AS (
                    SELECT *, CASE WHEN amname = 'btree' AND amopstrategy = 1 THEN 'lt'
                                   WHEN amname = 'btree' AND amopstrategy = 2 THEN 'lte'
                                   WHEN amopstrategy = 3 THEN 'eq'
                                   WHEN amname = 'btree' AND amopstrategy = 4 THEN 'gte'
                                   WHEN amname = 'btree' AND amopstrategy = 5 THEN 'gt' END AS logical_name
                    FROM family_operators
                    WHERE (amname = 'btree' AND amopstrategy BETWEEN 1 AND 5) OR (amname = 'hash' AND amopstrategy = 1)
                    UNION ALL
                    SELECT family.attnum, family.input_type_oid, negator.oid, negator.oprnamespace,
                           negator.oprname, negator.oprresult, negator.oprnegate, negator.oprcode,
                           negator.xmin::text, implementation.xmin::text, family.opclass_oid,
                           family.opclass_version, family.family_oid, family.family_version,
                           family.support_versions, family.cast_identity, family.amname, family.amopstrategy, 'neq'
                    FROM family_operators family
                    JOIN pg_catalog.pg_operator negator ON negator.oid = family.oprnegate
                    JOIN pg_catalog.pg_proc implementation ON implementation.oid = negator.oprcode
                    WHERE family.amopstrategy = 3 AND family.oprnegate <> 0
                    UNION ALL
                    SELECT base.attnum, base.oid, operator.oid, operator.oprnamespace, operator.oprname,
                           operator.oprresult, operator.oprnegate, operator.oprcode, operator.xmin::text,
                            implementation.xmin::text, 0, '', 0, '', '', '', 'pattern', 0, 'like'
                    FROM base_types base
                    JOIN pg_catalog.pg_operator operator ON operator.oprleft = base.oid AND operator.oprright = base.oid AND operator.oprname = '~~'
                    JOIN pg_catalog.pg_proc implementation ON implementation.oid = operator.oprcode
                ), selected AS (
                    SELECT DISTINCT ON (attnum, logical_name) * FROM candidates WHERE logical_name IS NOT NULL
                    ORDER BY attnum, logical_name, (oprnamespace = 'pg_catalog'::pg_catalog.regnamespace) DESC, operator_oid
                )
                SELECT selected.attnum AS ordinal, selected.logical_name,
                       selected.operator_oid::integer, namespace.nspname AS operator_namespace,
                       selected.oprname AS operator_name, selected.input_type_oid::integer,
                       selected.oprresult::integer AS result_type_oid,
                       pg_catalog.md5(selected.operator_version || ':' || selected.oprcode::text || ':' || selected.implementation_version || ':' ||
                        selected.opclass_oid::text || ':' || selected.opclass_version || ':' || selected.family_oid::text || ':' || selected.family_version || ':' ||
                        COALESCE(selected.support_versions, '') || ':' || selected.cast_identity) AS catalog_version
                FROM selected JOIN pg_catalog.pg_namespace namespace ON namespace.oid = selected.oprnamespace
                ORDER BY selected.attnum, selected.logical_name
        """, (relation_row["live_oid"],))
        aggregate_rows = self._execute_rows(connection, """
                /* structured_query_aggregates */
                WITH RECURSIVE type_chain AS (
                    SELECT a.attnum, t.oid, t.typbasetype, t.typtype, t.typelem, 0 AS depth
                    FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
                    WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
                    UNION ALL
                    SELECT chain.attnum, base.oid, base.typbasetype, base.typtype, base.typelem, chain.depth + 1
                    FROM type_chain chain JOIN pg_catalog.pg_type base ON base.oid = chain.typbasetype
                    WHERE chain.typbasetype <> 0 AND chain.depth < 32
                ), base_types AS (
                    SELECT DISTINCT ON (attnum) attnum, oid, typtype, typelem FROM type_chain ORDER BY attnum, depth DESC
                ), candidates AS (
                    SELECT base.attnum, base.oid AS input_type_oid, aggregate.oid AS aggregate_oid,
                           aggregate.pronamespace, aggregate.proname, aggregate.prorettype,
                           definition.*, aggregate.xmin::text AS aggregate_version,
                           definition.xmin::text AS definition_version,
                           dependencies.versions AS dependency_versions,
                           CASE aggregate.proname WHEN 'avg' THEN 'average' WHEN 'min' THEN 'minimum'
                                WHEN 'max' THEN 'maximum' ELSE aggregate.proname END AS logical_name,
                           CASE WHEN aggregate.proargtypes[0] = base.oid THEN 0 ELSE 1 END AS type_rank
                    FROM base_types base
                    JOIN pg_catalog.pg_proc aggregate ON aggregate.prokind = 'a' AND aggregate.pronargs = 1
                         AND aggregate.proname IN ('count', 'sum', 'avg', 'min', 'max')
                    JOIN pg_catalog.pg_aggregate definition ON definition.aggfnoid = aggregate.oid
                    JOIN pg_catalog.pg_type argument_type ON argument_type.oid = aggregate.proargtypes[0]
                    LEFT JOIN LATERAL (
                        SELECT pg_catalog.string_agg(procedure.oid::text || ':' || procedure.xmin::text, ',' ORDER BY procedure.oid) AS versions
                        FROM pg_catalog.pg_proc procedure
                        WHERE procedure.oid = ANY(ARRAY[
                            definition.aggtransfn, definition.aggfinalfn, definition.aggcombinefn,
                            definition.aggserialfn, definition.aggdeserialfn
                        ]::oid[])
                    ) dependencies ON true
                    WHERE aggregate.proargtypes[0] = base.oid
                       OR argument_type.typname IN ('any', 'anyelement', 'anycompatible')
                       OR argument_type.typname = 'anyenum' AND base.typtype = 'e'
                       OR argument_type.typname IN ('anynonarray', 'anycompatiblenonarray') AND base.typelem = 0
                       OR argument_type.typname IN ('anyarray', 'anycompatiblearray') AND base.typelem <> 0
                       OR argument_type.typname IN ('anyrange', 'anycompatiblerange') AND EXISTS (SELECT 1 FROM pg_catalog.pg_range r WHERE r.rngtypid = base.oid)
                       OR argument_type.typname IN ('anymultirange', 'anycompatiblemultirange') AND EXISTS (SELECT 1 FROM pg_catalog.pg_range r WHERE r.rngmultitypid = base.oid)
                ), selected AS (
                    SELECT DISTINCT ON (attnum, logical_name) * FROM candidates
                    ORDER BY attnum, logical_name, type_rank,
                             (pronamespace = 'pg_catalog'::pg_catalog.regnamespace) DESC, aggregate_oid
                )
                SELECT selected.attnum AS ordinal, selected.logical_name,
                       selected.aggregate_oid::integer, namespace.nspname AS aggregate_namespace,
                       selected.proname AS aggregate_name, selected.input_type_oid::integer,
                       (CASE WHEN result_type.typtype = 'p' THEN selected.input_type_oid ELSE selected.prorettype END)::integer AS result_type_oid,
                       EXISTS (SELECT 1 FROM pg_catalog.pg_opclass output_class
                               JOIN pg_catalog.pg_am output_am ON output_am.oid = output_class.opcmethod
                               JOIN pg_catalog.pg_type output_class_type ON output_class_type.oid = output_class.opcintype
                               WHERE (output_class.opcintype = CASE WHEN result_type.typtype = 'p' THEN selected.input_type_oid ELSE selected.prorettype END
                                      OR result_type.typtype = 'p' AND (
                                          output_class_type.typname IN ('any', 'anyelement', 'anycompatible')
                                          OR output_class_type.typname = 'anyenum' AND input_type.typtype = 'e'
                                          OR output_class_type.typname IN ('anyarray', 'anycompatiblearray') AND input_type.typelem <> 0
                                      ))
                                 AND output_class.opcdefault AND output_am.amname = 'btree') AS output_sortable,
                       (CASE WHEN result_type.typtype = 'p' THEN selected.input_type_oid ELSE selected.prorettype END = 'pg_catalog.int4'::pg_catalog.regtype
                        OR EXISTS (
                            SELECT 1 FROM pg_catalog.pg_cast zero_cast
                            WHERE zero_cast.castsource = 'pg_catalog.int4'::pg_catalog.regtype
                              AND zero_cast.casttarget = CASE WHEN result_type.typtype = 'p' THEN selected.input_type_oid ELSE selected.prorettype END
                              AND zero_cast.castcontext = 'i'
                        )) AS output_zeroable,
                       pg_catalog.md5(selected.aggregate_version || ':' || selected.definition_version || ':' || selected.aggtransfn::text || ':' ||
                       selected.aggfinalfn::text || ':' || selected.aggcombinefn::text || ':' || selected.aggserialfn::text || ':' || selected.aggdeserialfn::text || ':' || COALESCE(selected.dependency_versions, '')) AS catalog_version
                FROM selected
                JOIN pg_catalog.pg_namespace namespace ON namespace.oid = selected.pronamespace
                JOIN pg_catalog.pg_type result_type ON result_type.oid = selected.prorettype
                JOIN pg_catalog.pg_type input_type ON input_type.oid = selected.input_type_oid
                ORDER BY selected.attnum, selected.logical_name
        """, (relation_row["live_oid"],))
        operators_by_ordinal: dict[int, list[dict[str, Any]]] = {}
        aggregates_by_ordinal: dict[int, list[dict[str, Any]]] = {}
        for row in operator_rows:
            operators_by_ordinal.setdefault(int(row["ordinal"]), []).append(row)
        for row in aggregate_rows:
            aggregates_by_ordinal.setdefault(int(row["ordinal"]), []).append(row)
        fingerprint_columns = []
        for row in column_rows:
            column = {
                 "name": row["column_name"],
                 "type": row["data_type"],
                 "nullable": bool(row["nullable"]),
                 "ordinal": int(row["ordinal"]),
            }
            if row.get("declared_type_oid") is not None:
                if row.get("collation_oid") is not None:
                    row["collation_identity"] = {
                        "oid": int(row["collation_oid"]), "namespace": row["collation_namespace"], "name": row["collation_name"],
                        "provider": str(row.get("collation_provider") or ""), "deterministic": bool(row.get("collation_deterministic")),
                        "version": str(row.get("collation_version") or ""), "catalogVersion": str(row.get("collation_catalog_version") or ""),
                    }
                else:
                    row["collation_identity"] = None
                row["array_identity"] = ({"typeOid": int(row["array_type_oid"]), "elementTypeOid": int(row["array_element_type_oid"])}) if row.get("array_type_oid") is not None else None
                row["range_identity"] = ({
                    "typeOid": int(row["range_type_oid"]), "subtypeOid": int(row["range_subtype_oid"]),
                    "multirangeTypeOid": int(row.get("multirange_type_oid") or row["range_type_oid"]),
                    "catalogVersion": str(row.get("range_catalog_version") or ""),
                }) if row.get("range_type_oid") is not None else None
                column["capabilities"] = catalog_capabilities(
                    row, operators_by_ordinal.get(column["ordinal"], []), aggregates_by_ordinal.get(column["ordinal"], []),
                )
            fingerprint_columns.append(column)
        columns = [
            {
                **column,
                "suggestions": self._column_role_suggestions(
                    column["name"], row.get("type_category"), row.get("type_name")
                ),
            }
            for column, row in zip(fingerprint_columns, column_rows)
        ]
        descriptor = {
            "profileId": profile_id,
            "database": current,
            "namespace": namespace,
            "relation": relation,
            "kind": relation_row["relation_kind"],
            "columns": columns,
        }
        if all("capabilities" in column for column in fingerprint_columns):
            descriptor["snapshotVersion"] = 2
        descriptor["fingerprint"] = canonical_fingerprint({
            **descriptor, "columns": fingerprint_columns,
            "catalogKind": relation_row["catalog_kind"],
            "viewDefinition": relation_row.get("view_definition"),
        })
        if expected_kind is not None and descriptor["kind"] != expected_kind:
            raise PostgresServiceError(409, "relation_changed", "The PostgreSQL relation kind changed; reselect the widget source")
        if expected_fingerprint is not None and descriptor["fingerprint"] != expected_fingerprint:
            raise PostgresServiceError(409, "relation_changed", "The PostgreSQL relation definition changed; reselect the widget source")
        view_definition = relation_row.get("view_definition")
        if descriptor["kind"] not in {"view", "materialized_view"}:
            descriptor["definition"] = {"status": "unavailable", "reason": "not_supported"}
        elif not isinstance(view_definition, str) or not view_definition:
            descriptor["definition"] = {"status": "unavailable", "reason": "not_permitted"}
        elif len(view_definition.encode("utf-8")) > MAX_RELATION_DEFINITION_BYTES:
            descriptor["definition"] = {"status": "unavailable", "reason": "too_large"}
        else:
            descriptor["definition"] = {"status": "available", "format": "query", "sql": view_definition}
        owner_name = relation_row.get("owner_name")
        descriptor["owner"] = (
            {"status": "available", "name": owner_name}
            if isinstance(owner_name, str) and owner_name
            else {"status": "unavailable", "reason": "not_permitted"}
        )
        current_role = relation_row.get("current_role")
        descriptor["permissions"] = {
            "status": "available",
            "role": current_role if isinstance(current_role, str) and current_role else None,
            "advisory": True,
            "canSelect": bool(relation_row.get("can_select")),
            "isOwner": bool(relation_row.get("is_owner")),
            "inheritsOwner": bool(relation_row.get("inherits_owner")),
            "canSetRole": bool(relation_row.get("can_set_role")),
            "canAlter": bool(relation_row.get("is_owner") or relation_row.get("can_set_role")),
            "canRefresh": descriptor["kind"] == "materialized_view" and bool(relation_row.get("can_refresh")),
        }
        if descriptor["kind"] == "materialized_view":
            index_rows = self._execute_rows(connection, """
                /* concurrent_refresh_index */
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_index i
                    WHERE i.indrelid = %s
                      AND i.indisunique
                      AND i.indisvalid
                      AND i.indisready
                      AND i.indimmediate
                      AND i.indpred IS NULL
                      AND i.indexprs IS NULL
                ) AS has_refresh_index
            """, (relation_row["live_oid"],))
            populated = bool(relation_row.get("materialized_populated"))
            descriptor["materialized"] = {
                "status": "available",
                "populated": populated,
                "concurrentRefreshEligible": populated and bool(index_rows and index_rows[0]["has_refresh_index"]),
            }
        else:
            descriptor["materialized"] = {"status": "unavailable", "reason": "not_applicable"}
        descriptor["dependencies"] = self._relation_lineage(
            connection, profile_id, current, namespace, relation, descriptor["kind"],
            descriptor["fingerprint"], relation_row["live_oid"], dependents=False,
        )
        descriptor["dependents"] = self._relation_lineage(
            connection, profile_id, current, namespace, relation, descriptor["kind"],
            descriptor["fingerprint"], relation_row["live_oid"], dependents=True,
        )
        descriptor.update(self._view_provenance(connection, descriptor, view_definition))
        return descriptor

    def _view_provenance(
        self, connection: Any, descriptor: dict[str, Any], view_definition: Any,
    ) -> dict[str, dict[str, Any]]:
        if descriptor["kind"] not in {"view", "materialized_view"}:
            return {
                "columnProvenance": {"status": "unavailable", "reason": "not_supported"},
                "joinPredicates": {"status": "unavailable", "reason": "not_supported"},
                "sqlStages": unavailable_sql_stages(descriptor["fingerprint"], "not_supported"),
            }
        if not isinstance(view_definition, str) or not view_definition:
            return {
                "columnProvenance": {"status": "unavailable", "reason": "definition_unavailable"},
                "joinPredicates": {"status": "unavailable", "reason": "definition_unavailable"},
                "sqlStages": unavailable_sql_stages(descriptor["fingerprint"], "definition_unavailable"),
            }
        dependencies = descriptor["dependencies"]
        if dependencies["page"]["hasMore"]:
            return {
                "columnProvenance": {"status": "unavailable", "reason": "too_many_sources"},
                "joinPredicates": {"status": "unavailable", "reason": "too_many_sources"},
                "sqlStages": unavailable_sql_stages(descriptor["fingerprint"], "too_many_sources"),
            }
        identities = dependencies["items"]
        namespaces = [item["namespace"] for item in identities]
        relations = [item["relation"] for item in identities]
        source_rows = self._execute_rows(connection, """
            /* view_provenance_source_columns */
            WITH requested(namespace_name, relation_name) AS (
                SELECT namespace_item.namespace_name, relation_item.relation_name
                FROM pg_catalog.unnest(%s::text[]) WITH ORDINALITY AS namespace_item(namespace_name, position)
                JOIN pg_catalog.unnest(%s::text[]) WITH ORDINALITY AS relation_item(relation_name, position)
                  USING (position)
            )
            SELECT namespace.nspname AS namespace,
                   relation.relname AS relation_name,
                   CASE WHEN relation.relkind = 'r' THEN 'table'
                        WHEN relation.relkind = 'p' THEN 'partitioned_table'
                        WHEN relation.relkind = 'v' THEN 'view'
                        WHEN relation.relkind = 'm' THEN 'materialized_view'
                        ELSE 'foreign_table' END AS relation_kind,
                   attribute.attname AS column_name,
                   attribute.attnum AS ordinal,
                   pg_catalog.format_type(attribute.atttypid, attribute.atttypmod) AS data_type
            FROM requested
            JOIN pg_catalog.pg_namespace namespace ON namespace.nspname = requested.namespace_name
            JOIN pg_catalog.pg_class relation ON relation.relnamespace = namespace.oid
                 AND relation.relname = requested.relation_name
                 AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
            JOIN pg_catalog.pg_attribute attribute ON attribute.attrelid = relation.oid
                 AND attribute.attnum > 0 AND NOT attribute.attisdropped
            ORDER BY namespace.nspname, relation.relname, attribute.attnum
        """, (namespaces, relations)) if identities else []
        rows_by_source: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in source_rows:
            rows_by_source.setdefault((row["namespace"], row["relation_name"]), []).append({
                "name": row["column_name"], "type": row["data_type"], "ordinal": int(row["ordinal"]),
            })
        sources = [{
            **identity,
            "columns": rows_by_source.get((identity["namespace"], identity["relation"]), []),
        } for identity in identities]
        arguments = {
            "current_namespace": descriptor["namespace"],
            "relation_fingerprint": descriptor["fingerprint"],
        }
        return {
            "columnProvenance": derive_column_provenance(
                view_definition, descriptor["columns"], sources, **arguments,
            ),
            "joinPredicates": derive_join_provenance(view_definition, sources, **arguments),
            "sqlStages": derive_sql_stages(view_definition, sources, **arguments),
        }

    def _relation_lineage(
        self, connection: Any, profile_id: str, database: str, namespace: str, relation: str,
        kind: str, relation_fingerprint: str, live_oid: int, *, dependents: bool,
    ) -> dict[str, Any]:
        return self._relation_lineage_page_connection(
            connection, profile_id, database, namespace, relation, kind, relation_fingerprint,
            live_oid, "dependents" if dependents else "dependencies", catalog_page_size(None), None,
        )

    @postgres_execution("catalog")
    def list_relation_lineage(
        self, profile_id: str, database: str, namespace: str, relation: str, direction: str,
        *, expected_kind: str | None = None, expected_fingerprint: str | None = None,
        page_size: Any = None, cursor: Any = None,
    ) -> dict[str, Any]:
        database = self._validate_database(database)
        namespace = self._validate_namespace(namespace)
        relation = self._validate_relation_name(relation)
        if direction not in {"dependencies", "dependents"}:
            raise ValidationError("direction must be dependencies or dependents")
        if expected_kind not in RELATION_KINDS:
            raise ValidationError("expectedKind must be a supported relation kind")
        if not isinstance(expected_fingerprint, str) or not FINGERPRINT_RE.fullmatch(expected_fingerprint):
            raise ValidationError("expectedFingerprint must be a 64-character lowercase hexadecimal fingerprint")
        size = catalog_page_size(page_size)
        profile_fingerprint = self.profile_context_fingerprint(profile_id)
        connection = self._connect(profile_id)
        try:
            self._execute_statement(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            descriptor = self._inspect_relation_connection(
                connection, profile_id, database, namespace, relation, expected_kind, expected_fingerprint,
            )
            identity = self._execute_rows(connection, """
                /* relation_lineage_identity */
                SELECT c.oid AS live_oid
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s AND c.relkind = %s
            """, (namespace, relation, RELATION_KINDS[descriptor["kind"]]))
            if len(identity) != 1:
                raise PostgresServiceError(409, "relation_changed", "The PostgreSQL relation changed while reading lineage")
            if self.profile_context_fingerprint(profile_id) != profile_fingerprint:
                raise PostgresServiceError(409, "profile_changed", "The PostgreSQL profile changed while reading lineage")
            return self._relation_lineage_page_connection(
                connection, profile_id, database, namespace, relation, descriptor["kind"],
                descriptor["fingerprint"], identity[0]["live_oid"], direction, size, cursor,
            )
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL relation lineage could not be read", postgres_error_details(
                exc, phase="catalog", operation="relation_lineage", rollback={"attempted": True},
            )) from exc
        finally:
            try:
                connection.rollback()
            except Exception:
                pass
            self._close(connection)

    def _relation_lineage_page_connection(
        self, connection: Any, profile_id: str, database: str, namespace: str, relation: str,
        kind: str, relation_fingerprint: str, live_oid: int, direction: str, size: int,
        cursor: Any,
    ) -> dict[str, Any]:
        dependents = direction == "dependents"
        if dependents:
            identity_join = "c.oid = rw.ev_class"
            object_filter = "d.refobjid = %s"
        else:
            identity_join = "c.oid = d.refobjid"
            object_filter = "rw.ev_class = %s"
        lineage_sql = f"""
            SELECT DISTINCT n.nspname AS namespace,
                   c.relname AS relation_name,
                    c.relkind AS catalog_kind,
                    CASE WHEN c.relkind = 'r' THEN 'table'
                         WHEN c.relkind = 'p' THEN 'partitioned_table'
                         WHEN c.relkind = 'v' THEN 'view'
                         WHEN c.relkind = 'm' THEN 'materialized_view'
                         ELSE 'foreign_table' END AS relation_kind
            FROM pg_catalog.pg_rewrite rw
            JOIN pg_catalog.pg_depend d
              ON d.classid = 'pg_catalog.pg_rewrite'::pg_catalog.regclass
             AND d.objid = rw.oid
             AND d.refclassid = 'pg_catalog.pg_class'::pg_catalog.regclass
             AND d.deptype = 'n'
            JOIN pg_catalog.pg_class c ON {identity_join}
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE {object_filter}
              AND c.oid <> %s
              AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
        """
        fingerprint_rows = self._execute_rows(connection, f"""
            /* relation_{direction}_fingerprint */
            WITH lineage AS ({lineage_sql})
            SELECT pg_catalog.md5(COALESCE(pg_catalog.string_agg(
                       pg_catalog.length(namespace)::text || ':' || namespace ||
                        pg_catalog.length(relation_name)::text || ':' || relation_name || catalog_kind::text,
                       '' ORDER BY namespace, relation_name, catalog_kind
                   ), '')) AS first_hash,
                   pg_catalog.md5('lineage:' || COALESCE(pg_catalog.string_agg(
                       pg_catalog.length(namespace)::text || ':' || namespace ||
                        pg_catalog.length(relation_name)::text || ':' || relation_name || catalog_kind::text,
                       '' ORDER BY namespace, relation_name, catalog_kind
                   ), '')) AS second_hash
            FROM lineage
        """, (live_oid, live_oid))
        fingerprint_row = fingerprint_rows[0] if fingerprint_rows else {}
        if isinstance(fingerprint_row.get("first_hash"), str) and isinstance(fingerprint_row.get("second_hash"), str):
            fingerprint = fingerprint_row["first_hash"] + fingerprint_row["second_hash"]
        else:
            fingerprint = canonical_fingerprint({"relationFingerprint": relation_fingerprint, "items": fingerprint_rows})
        context = {
            "type": "relation_lineage", "profileFingerprint": self.profile_context_fingerprint(profile_id),
            "database": database, "namespace": namespace, "relation": relation, "kind": kind,
            "relationFingerprint": relation_fingerprint, "direction": direction,
            "filter": "", "sort": "namespace_name_kind", "pageSize": size,
            "catalogFingerprint": fingerprint,
        }
        after = decode_catalog_cursor(self._catalog_cursor_secret, cursor, context)
        keyset = "WHERE (namespace, relation_name, catalog_kind) > (%s, %s, %s)" if after else ""
        params = (live_oid, live_oid) + (tuple(after) if after else ()) + (size + 1,)
        rows = self._execute_rows(connection, f"""
            /* relation_{direction}_page */
            WITH lineage AS ({lineage_sql})
            SELECT namespace, relation_name, catalog_kind, relation_kind
            FROM lineage {keyset}
            ORDER BY namespace, relation_name, catalog_kind
            LIMIT %s
        """, params)
        rows = [row for row in rows if all(key in row for key in ("namespace", "relation_name", "relation_kind"))]
        catalog_kinds = {name: value for name, value in RELATION_KINDS.items()}
        for row in rows:
            row.setdefault("catalog_kind", catalog_kinds.get(row["relation_kind"], ""))
        has_more = len(rows) > size
        visible = rows[:size]
        items = [
            {
                "profileId": profile_id, "database": database,
                "namespace": row["namespace"], "relation": row["relation_name"], "kind": row["relation_kind"],
            }
            for row in visible
        ]
        next_cursor = encode_catalog_cursor(
            self._catalog_cursor_secret, context,
            [visible[-1]["namespace"], visible[-1]["relation_name"], visible[-1]["catalog_kind"]],
        ) if has_more else None
        return {
            "status": "available",
            "profileId": profile_id, "database": database, "namespace": namespace,
            "relation": relation, "kind": kind, "relationFingerprint": relation_fingerprint,
            "direction": direction, "catalogFingerprint": fingerprint, "items": items,
            "truncated": has_more,
            "page": {"pageSize": size, "returned": len(items), "hasMore": has_more, "nextCursor": next_cursor},
        }
