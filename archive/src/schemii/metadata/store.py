from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from .errors import MetadataStoreError
from .migrator import MetadataMigrator, validate_applied_migrations
from ..migration_contract import has_full_schema_completeness_proof
from .validation import bounded_json, identity


_TERMINAL_OPERATION_STATES = {"succeeded", "failed", "uncertain", "cancelled"}
_TERMINAL_MIGRATION_STATES = {"succeeded", "failed", "uncertain"}
_HEX_DIGEST = frozenset("0123456789abcdef")


def _catalog_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MetadataStoreError(
                "metadata_catalog_mismatch", "Server metadata PostgreSQL catalog is malformed", status=503,
            ) from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise MetadataStoreError(
            "metadata_catalog_mismatch", "Server metadata PostgreSQL catalog is malformed", status=503,
        )
    return value


class MetadataStore:
    """Transactional repository for shared server authority metadata."""

    def __init__(
        self, connection_factory: Callable[[], Any], *, max_json_bytes: int = 1024 * 1024,
        expected_application: str = "", expected_role: str = "", expected_owner: str = "",
        expected_admin_owner: str = "",
    ):
        if not callable(connection_factory):
            raise ValueError("connection_factory must be callable")
        if isinstance(max_json_bytes, bool) or not 1024 <= max_json_bytes <= 1024 * 1024:
            raise ValueError("max_json_bytes must be between 1024 and 1048576")
        self.connection_factory = connection_factory
        self.max_json_bytes = max_json_bytes
        self.expected_application = identity(expected_application, "expected_application") if expected_application else ""
        self.expected_role = identity(expected_role, "expected_role") if expected_role else ""
        self.expected_owner = identity(expected_owner, "expected_owner") if expected_owner else ""
        self.expected_admin_owner = identity(expected_admin_owner, "expected_admin_owner") if expected_admin_owner else ""

    def migrate(self) -> int:
        return MetadataMigrator(self.connection_factory).migrate()

    def health(self) -> dict[str, Any]:
        migrator = MetadataMigrator(self.connection_factory)
        expected = migrator.expected_version
        try:
            with self._transaction(write=False) as cursor:
                cursor.execute("SELECT version, name, checksum FROM metadata_schema_migrations ORDER BY version")
                applied = validate_applied_migrations(cursor.fetchall(), migrator.migrations)
                version = len(applied)
                identity_row = None
                if self.expected_application or self.expected_role or self.expected_owner or self.expected_admin_owner:
                    cursor.execute(
                        """SELECT current_user AS current_user, session_user AS session_user,
                                  pg_catalog.pg_get_userbyid(database.datdba) AS database_owner,
                                  pg_catalog.pg_get_userbyid(namespace.nspowner) AS schema_owner,
                                  public.metadata_current_application() AS application_id,
                                  (
                                      SELECT pg_catalog.pg_get_userbyid(admin_namespace.nspowner)
                                      FROM pg_catalog.pg_namespace AS admin_namespace
                                      WHERE admin_namespace.nspname = 'schemii_admin'
                                  ) AS admin_schema_owner,
                                  COALESCE((
                                      SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                                          'name', object_catalog.relname, 'kind', object_catalog.relkind,
                                          'owner', pg_catalog.pg_get_userbyid(object_catalog.relowner)
                                      ) ORDER BY object_catalog.relname)
                                      FROM pg_catalog.pg_class AS object_catalog
                                      WHERE object_catalog.relnamespace = namespace.oid
                                        AND object_catalog.relkind IN ('r', 'p', 'S')
                                        AND object_catalog.relname LIKE 'metadata\\_%' ESCAPE '\\'
                                  ), '[]'::jsonb) AS object_owners,
                                  COALESCE((
                                      SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                                          'name', function_catalog.proname, 'owner', pg_catalog.pg_get_userbyid(function_catalog.proowner)
                                      ) ORDER BY function_catalog.proname, function_catalog.oid)
                                      FROM pg_catalog.pg_proc AS function_catalog
                                      WHERE function_catalog.pronamespace = namespace.oid
                                        AND function_catalog.proname LIKE 'metadata\\_%' ESCAPE '\\'
                                  ), '[]'::jsonb) AS function_owners,
                                  COALESCE((
                                      SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                                          'name', object_catalog.relname, 'enabled', object_catalog.relrowsecurity,
                                          'forced', object_catalog.relforcerowsecurity
                                      ) ORDER BY object_catalog.relname)
                                      FROM pg_catalog.pg_class AS object_catalog
                                      WHERE object_catalog.relnamespace = namespace.oid
                                        AND object_catalog.relkind IN ('r', 'p')
                                        AND object_catalog.relname LIKE 'metadata\\_%' ESCAPE '\\'
                                        AND object_catalog.relname <> 'metadata_schema_migrations'
                                  ), '[]'::jsonb) AS rls_tables,
                                  COALESCE((
                                      SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                                          'name', role_catalog.rolname, 'login', role_catalog.rolcanlogin,
                                           'inherit', role_catalog.rolinherit, 'superuser', role_catalog.rolsuper,
                                           'createRole', role_catalog.rolcreaterole, 'createDatabase', role_catalog.rolcreatedb,
                                           'replication', role_catalog.rolreplication, 'bypassRls', role_catalog.rolbypassrls,
                                          'memberOfOwner', CASE
                                              WHEN role_catalog.rolname = 'schemii_metadata_owner' THEN false
                                              ELSE EXISTS (
                                                  SELECT 1
                                                  FROM pg_catalog.pg_auth_members AS membership
                                                  JOIN pg_catalog.pg_roles AS owner_role ON owner_role.oid = membership.roleid
                                                  WHERE membership.member = role_catalog.oid
                                                    AND owner_role.rolname = 'schemii_metadata_owner'
                                              )
                                          END
                                      ) ORDER BY role_catalog.rolname)
                                       FROM pg_catalog.pg_roles AS role_catalog
                                       WHERE role_catalog.rolname IN (
                                           'schemii_metadata_bootstrap', 'schemii_metadata_owner', 'schemii_metadata_migration',
                                           'schemii_metadata_schemii', 'schemii_metadata_schemer'
                                      )
                                  ), '[]'::jsonb) AS metadata_roles
                           FROM pg_catalog.pg_database AS database
                           JOIN pg_catalog.pg_namespace AS namespace ON namespace.nspname = 'public'
                           WHERE database.datname = current_database()"""
                    )
                    identity_row = cursor.fetchone()
        except MetadataStoreError:
            raise
        except Exception as exc:
            raise MetadataStoreError("metadata_unavailable", "Server metadata PostgreSQL is unavailable", status=503, retryable=True) from exc
        if version != expected:
            raise MetadataStoreError(
                "metadata_schema_outdated",
                "Server metadata schema is not current",
                status=503,
                details={"currentVersion": version, "expectedVersion": expected},
            )
        if (self.expected_application or self.expected_role or self.expected_owner or self.expected_admin_owner) and identity_row is None:
            raise MetadataStoreError(
                "metadata_identity_mismatch",
                "Server metadata PostgreSQL role or ownership identity is unavailable",
                status=503,
            )
        if identity_row is not None:
            actual_role = _row_value(identity_row, "current_user", 0)
            session_role = _row_value(identity_row, "session_user", 1)
            database_owner = _row_value(identity_row, "database_owner", 2)
            schema_owner = _row_value(identity_row, "schema_owner", 3)
            application_id = _row_value(identity_row, "application_id", 4)
            admin_schema_owner = _row_value(identity_row, "admin_schema_owner", 5)
            application_matches = not self.expected_application or application_id == self.expected_application
            role_matches = not self.expected_role or actual_role == self.expected_role == session_role
            owner_matches = not self.expected_owner or database_owner == self.expected_owner == schema_owner
            admin_owner_matches = not self.expected_admin_owner or admin_schema_owner == self.expected_admin_owner
            if not application_matches or not role_matches or not owner_matches or not admin_owner_matches:
                raise MetadataStoreError(
                    "metadata_identity_mismatch",
                    "Server metadata PostgreSQL role or ownership identity is not the configured identity",
                    status=503,
                    details={
                        "expectedApplication": self.expected_application or None,
                        "currentApplication": application_id,
                        "expectedRole": self.expected_role or None, "currentUser": actual_role,
                        "sessionUser": session_role, "expectedOwner": self.expected_owner or None,
                        "databaseOwner": database_owner, "schemaOwner": schema_owner,
                        "expectedAdminOwner": self.expected_admin_owner or None,
                        "adminSchemaOwner": admin_schema_owner,
                    },
                )
            object_owners = _catalog_list(_row_value(identity_row, "object_owners", 6))
            function_owners = _catalog_list(_row_value(identity_row, "function_owners", 7))
            rls_tables = _catalog_list(_row_value(identity_row, "rls_tables", 8))
            roles = _catalog_list(_row_value(identity_row, "metadata_roles", 9))
            ownership_drift = [
                {"kind": "relation", **item} for item in object_owners
                if self.expected_owner and item.get("owner") != self.expected_owner
            ] + [
                {"kind": "function", **item} for item in function_owners
                if self.expected_owner and item.get("owner") != self.expected_owner
            ]
            if not any(item.get("name") == "metadata_schema_migrations" for item in object_owners):
                ownership_drift.append({"kind": "relation", "name": "metadata_schema_migrations", "owner": None})
            if not any(item.get("name") == "metadata_current_application" for item in function_owners):
                ownership_drift.append({"kind": "function", "name": "metadata_current_application", "owner": None})
            expected_rls_tables = {
                item.get("name") for item in object_owners
                if item.get("kind") in {"r", "p"} and item.get("name") != "metadata_schema_migrations"
            }
            rls_by_name = {item.get("name"): item for item in rls_tables}
            rls_drift = [
                table_name for table_name in sorted(expected_rls_tables)
                if table_name not in rls_by_name
                or rls_by_name[table_name].get("enabled") is not True
                or rls_by_name[table_name].get("forced") is not True
            ]
            expected_roles = {
                "schemii_metadata_bootstrap": {
                    "login": False, "memberOfOwner": False, "superuser": True,
                    "createRole": True, "createDatabase": True, "replication": True, "bypassRls": True,
                },
                "schemii_metadata_owner": {"login": False, "memberOfOwner": False},
                "schemii_metadata_migration": {"login": True, "memberOfOwner": True},
                "schemii_metadata_schemii": {"login": True, "memberOfOwner": False},
                "schemii_metadata_schemer": {"login": True, "memberOfOwner": False},
            }
            roles_by_name = {item.get("name"): item for item in roles}
            role_drift = []
            for role_name, expected_role in expected_roles.items():
                role = roles_by_name.get(role_name)
                if (
                    role is None or role.get("login") is not expected_role["login"]
                    or role.get("memberOfOwner") is not expected_role["memberOfOwner"]
                    or role.get("inherit") is not True or any(
                        role.get(field) is not expected_role.get(field, False)
                        for field in ("superuser", "createRole", "createDatabase", "replication", "bypassRls")
                    )
                ):
                    role_drift.append(role_name)
            if ownership_drift or rls_drift or role_drift:
                raise MetadataStoreError(
                    "metadata_catalog_mismatch",
                    "Server metadata PostgreSQL catalog does not match its ownership and isolation contract",
                    status=503,
                    details={
                        "ownershipDrift": ownership_drift,
                        "rowSecurityDrift": rls_drift,
                        "roleDrift": role_drift,
                    },
                )
        return {
            "ok": True, "version": version, "expectedVersion": expected,
            **({"application": self.expected_application} if self.expected_application else {}),
            **({"role": self.expected_role} if self.expected_role else {}),
        }
    def create_agent_settings(self, application_id: str, agent_id: str, policy: Any) -> dict[str, Any]:
        from ..ai_policy import effective_capabilities, policy_digest, validate_policy

        application = identity(application_id, "application_id")
        agent = identity(agent_id, "agent_id")
        document = validate_policy(application, policy)
        capabilities = effective_capabilities(application, document)
        revision_id = uuid.uuid4()
        with self._transaction() as cursor:
            cursor.execute(
                """INSERT INTO metadata_agent_settings (application_id, agent_id, current_revision)
                   VALUES (%s, %s, 1) ON CONFLICT (application_id, agent_id) DO NOTHING""",
                (application, agent),
            )
            if cursor.rowcount:
                self._insert_agent_policy_revision(
                    cursor, revision_id, application, agent, 1, document, capabilities, policy_digest(document),
                )
            return self._get_agent_settings(cursor, application, agent)

    def get_agent_settings(self, application_id: str, agent_id: str) -> dict[str, Any]:
        from ..ai_policy import default_policy

        application = identity(application_id, "application_id")
        agent = identity(agent_id, "agent_id")
        with self._transaction() as cursor:
            cursor.execute(
                "SELECT 1 FROM metadata_agent_settings WHERE application_id = %s AND agent_id = %s",
                (application, agent),
            )
            if cursor.fetchone() is None:
                document = default_policy(application)
                from ..ai_policy import effective_capabilities, policy_digest
                revision_id = uuid.uuid4()
                cursor.execute(
                    """INSERT INTO metadata_agent_settings (application_id, agent_id, current_revision)
                       VALUES (%s, %s, 1) ON CONFLICT (application_id, agent_id) DO NOTHING""",
                    (application, agent),
                )
                if cursor.rowcount:
                    self._insert_agent_policy_revision(
                        cursor, revision_id, application, agent, 1, document,
                        effective_capabilities(application, document), policy_digest(document),
                    )
            return self._get_agent_settings(cursor, application, agent)

    def update_agent_settings(
        self, application_id: str, agent_id: str, expected_revision: Any, policy: Any,
    ) -> dict[str, Any]:
        from ..ai_policy import effective_capabilities, policy_digest, validate_policy

        application = identity(application_id, "application_id")
        agent = identity(agent_id, "agent_id")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise MetadataStoreError("invalid_metadata", "expectedRevision is invalid", status=400)
        document = validate_policy(application, policy)
        capabilities = effective_capabilities(application, document)
        digest = policy_digest(document)
        revision_id = uuid.uuid4()
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT current_revision FROM metadata_agent_settings
                   WHERE application_id = %s AND agent_id = %s FOR UPDATE""",
                (application, agent),
            )
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("agent_settings_not_found", "AI agent settings were not found", status=404)
            current = int(_row_value(row, "current_revision", 0))
            if current != expected_revision:
                raise MetadataStoreError(
                    "policy_changed", "AI agent policy changed; refresh before saving", status=409,
                    details={"currentRevision": current},
                )
            revision = current + 1
            self._insert_agent_policy_revision(
                cursor, revision_id, application, agent, revision, document, capabilities, digest,
            )
            cursor.execute(
                """UPDATE metadata_agent_settings SET current_revision = %s, updated_at = clock_timestamp()
                   WHERE application_id = %s AND agent_id = %s""",
                (revision, application, agent),
            )
            grant_compatible_capabilities = [
                name for name, item in capabilities.items()
                if item["effectiveMode"] in {"once_per_chat", "automatic"}
            ]
            cursor.execute(
                """UPDATE metadata_grants g SET state = 'revoked', revoked_at = clock_timestamp()
                   FROM metadata_policy_versions v, metadata_agent_policy_revisions r, metadata_chats c
                   WHERE g.chat_id = v.chat_id AND g.policy_revision = v.revision
                     AND v.agent_policy_revision_id = r.agent_policy_revision_id AND c.chat_id = g.chat_id
                     AND r.application_id = %s AND r.agent_id = %s AND c.application_id = %s
                     AND g.state = 'active' AND NOT (g.capability = ANY(%s::text[]))""",
                (application, agent, application, grant_compatible_capabilities),
            )
            evidence = {"agentId": agent, "agentPolicyRevision": revision, "policyDigest": digest}
            cursor.execute(
                """UPDATE metadata_proposals p
                   SET state = 'revoked', revoked_at = clock_timestamp(),
                       revocation_reason = 'agent_policy_changed', revocation_evidence = %s::jsonb
                   FROM metadata_policy_versions v, metadata_agent_policy_revisions r, metadata_chats c
                   WHERE p.chat_id = v.chat_id AND p.policy_revision = v.revision
                     AND v.agent_policy_revision_id = r.agent_policy_revision_id AND c.chat_id = p.chat_id
                      AND r.application_id = %s AND r.agent_id = %s AND c.application_id = %s
                      AND p.state = 'ready'""",
                (_json(evidence), application, agent, application),
            )
            cursor.execute(
                """UPDATE metadata_query_result_references q
                   SET state = 'revoked', revoked_at = clock_timestamp(), revocation_reason = 'agent_policy_changed'
                   FROM metadata_policy_versions v, metadata_agent_policy_revisions r, metadata_chats c
                   WHERE q.chat_id = v.chat_id
                     AND (q.binding -> 'policyBinding' ->> 'policyRevision') ~ '^[0-9]+$'
                     AND (q.binding -> 'policyBinding' ->> 'policyRevision')::bigint = v.revision
                     AND v.agent_policy_revision_id = r.agent_policy_revision_id AND c.chat_id = q.chat_id
                      AND r.application_id = %s AND r.agent_id = %s AND c.application_id = %s
                      AND q.state = 'ready'""",
                (application, agent, application),
            )
            cursor.execute(
                """UPDATE metadata_query_result_payloads p SET payload = '{}'::jsonb, byte_count = 2,
                       scrubbed_at = clock_timestamp()
                   FROM metadata_query_result_references q
                   WHERE q.result_ref_id = p.result_ref_id AND q.state = 'revoked'
                     AND q.revocation_reason = 'agent_policy_changed' AND q.revoked_at >= transaction_timestamp()"""
            )
            return self._get_agent_settings(cursor, application, agent)

    def _insert_agent_policy_revision(
        self, cursor: Any, revision_id: uuid.UUID, application: str, agent: str, revision: int,
        policy: dict[str, Any], capabilities: dict[str, dict[str, str]], digest: str,
    ) -> None:
        cursor.execute(
            """INSERT INTO metadata_agent_policy_revisions
               (agent_policy_revision_id, application_id, agent_id, revision, schema_version, policy, policy_digest)
               VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)""",
            (revision_id, application, agent, revision, policy["schemaVersion"], _json(policy), digest),
        )
        for capability, modes in capabilities.items():
            cursor.execute(
                """INSERT INTO metadata_agent_policy_capabilities
                   (agent_policy_revision_id, capability, configured_mode, effective_mode, safety_floor)
                   VALUES (%s, %s, %s, %s, %s)""",
                (revision_id, capability, modes["configuredMode"], modes["effectiveMode"], modes["safetyFloor"]),
            )
        bounds = policy["bounds"]
        cursor.execute(
            """INSERT INTO metadata_agent_policy_bounds
               (agent_policy_revision_id, rows_disclosed, rows_written, pages_inspected,
                raw_statements, operation_timeout_ms, agent_concurrency)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (revision_id, bounds["rowsDisclosed"], bounds["rowsWritten"], bounds["pagesInspected"],
             bounds["rawStatements"], bounds["operationTimeoutMs"], bounds["agentConcurrency"]),
        )

    def _get_agent_settings(self, cursor: Any, application: str, agent: str) -> dict[str, Any]:
        cursor.execute(
            """SELECT r.agent_policy_revision_id, r.revision, r.schema_version, r.policy, r.policy_digest,
                      r.created_at, s.updated_at
               FROM metadata_agent_settings s JOIN metadata_agent_policy_revisions r
                 ON r.application_id = s.application_id AND r.agent_id = s.agent_id
                AND r.revision = s.current_revision
               WHERE s.application_id = %s AND s.agent_id = %s""",
            (application, agent),
        )
        row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("agent_settings_not_found", "AI agent settings were not found", status=404)
        revision_id = _row_value(row, "agent_policy_revision_id", 0)
        cursor.execute(
            """SELECT capability, configured_mode, effective_mode, safety_floor
               FROM metadata_agent_policy_capabilities
               WHERE agent_policy_revision_id = %s ORDER BY capability""",
            (revision_id,),
        )
        capabilities = {
            _row_value(item, "capability", 0): {
                "configuredMode": _row_value(item, "configured_mode", 1),
                "effectiveMode": _row_value(item, "effective_mode", 2),
                "safetyFloor": _row_value(item, "safety_floor", 3),
            }
            for item in cursor.fetchall()
        }
        from ..ai_policy import APPLICATION_CAPABILITIES, SAFETY_FLOORS, effective_bounds
        # New capabilities never inherit authority from an older immutable policy revision.
        for capability in APPLICATION_CAPABILITIES[application]:
            capabilities.setdefault(capability, {
                "configuredMode": "disabled", "effectiveMode": "disabled",
                "safetyFloor": SAFETY_FLOORS[capability],
            })
        policy = _json_value(_row_value(row, "policy", 3))
        return {
            "application": application, "agentId": agent,
            "revision": int(_row_value(row, "revision", 1)),
            "schemaVersion": int(_row_value(row, "schema_version", 2)),
            "policyRevisionId": str(revision_id), "policyDigest": _row_value(row, "policy_digest", 4),
            "policy": policy, "capabilities": capabilities, "bounds": dict(policy["bounds"]),
            "effectiveBounds": effective_bounds(policy),
            "createdAt": _iso_datetime(_row_value(row, "created_at", 5)),
            "updatedAt": _iso_datetime(_row_value(row, "updated_at", 6)),
        }

    def get_console_settings(self, application_id: str) -> dict[str, Any]:
        application = identity(application_id, "application_id")
        with self._transaction() as cursor:
            cursor.execute(
                """INSERT INTO metadata_console_settings
                   (application_id, revision, write_intent, default_mode, statement_limit, row_page_size)
                   VALUES (%s, 1, 'disabled', 'managed_read', 100, 100)
                   ON CONFLICT (application_id) DO NOTHING""",
                (application,),
            )
            cursor.execute(
                """SELECT application_id, revision, write_intent, default_mode, statement_limit,
                          row_page_size, created_at, updated_at
                   FROM metadata_console_settings WHERE application_id = %s""",
                (application,),
            )
            row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("console_settings_not_found", "Console settings were not found", status=404)
        return _console_settings_record(row)

    def update_console_settings(self, application_id: str, expected_revision: Any, settings: Any) -> dict[str, Any]:
        application = identity(application_id, "application_id")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise MetadataStoreError("invalid_metadata", "expectedRevision is invalid", status=400)
        fields = {"writeIntent", "defaultMode", "statementLimit", "rowPageSize"}
        if not isinstance(settings, dict) or set(settings) != fields:
            raise MetadataStoreError("invalid_metadata", "Console settings fields are invalid", status=400)
        write_intent, default_mode = settings["writeIntent"], settings["defaultMode"]
        statement_limit, row_page_size = settings["statementLimit"], settings["rowPageSize"]
        if write_intent not in {"disabled", "enabled"} or default_mode not in {"managed_read", "managed", "explicit", "autocommit"}:
            raise MetadataStoreError("invalid_metadata", "Console settings values are invalid", status=400)
        invalid_limits = (
            isinstance(statement_limit, bool) or not isinstance(statement_limit, int) or not 1 <= statement_limit <= 100
            or isinstance(row_page_size, bool) or not isinstance(row_page_size, int) or not 1 <= row_page_size <= 500
        )
        if invalid_limits:
            raise MetadataStoreError("invalid_metadata", "Console settings limits exceed operator safety maxima", status=400)
        with self._transaction() as cursor:
            cursor.execute(
                """UPDATE metadata_console_settings
                   SET revision = revision + 1, write_intent = %s, default_mode = %s,
                       statement_limit = %s, row_page_size = %s, updated_at = clock_timestamp()
                   WHERE application_id = %s AND revision = %s
                   RETURNING application_id, revision, write_intent, default_mode, statement_limit,
                             row_page_size, created_at, updated_at""",
                (write_intent, default_mode, statement_limit, row_page_size, application, expected_revision),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute("SELECT revision FROM metadata_console_settings WHERE application_id = %s", (application,))
                current = cursor.fetchone()
                if current is None:
                    raise MetadataStoreError("console_settings_not_found", "Console settings were not found", status=404)
                raise MetadataStoreError(
                    "console_settings_conflict", "Console settings changed; refresh before saving", status=409,
                    details={"currentRevision": int(_row_value(current, "revision", 0))},
                )
        return _console_settings_record(row)

    def put_console_execution_receipt(self, receipt: dict[str, Any]) -> dict[str, Any]:
        fields = {"executionId", "applicationId", "sessionBinding", "serverId", "profileId", "profileFingerprint",
                  "database", "namespace", "consoleId", "mode", "settingsRevision", "state", "outcome", "completedStatementIndexes",
                  "errorCode", "postgresEvidence", "reconciliationEvidence"}
        if not isinstance(receipt, dict) or set(receipt) != fields:
            raise MetadataStoreError("invalid_metadata", "Console execution receipt fields are invalid", status=400)
        execution, console = _uuid(receipt["executionId"], "execution_id"), _uuid(receipt["consoleId"], "console_id")
        application = identity(receipt["applicationId"], "application_id")
        binding_hash = hashlib.sha256(_bounded_text(receipt["sessionBinding"], "session_binding", 4096).encode()).hexdigest()
        server = _bounded_text(receipt["serverId"], "server_id", 256)
        profile = _bounded_text(receipt["profileId"], "profile_id", 256)
        fingerprint = _digest(receipt["profileFingerprint"], "profile_fingerprint")
        database, namespace = _bounded_text(receipt["database"], "database", 63), _bounded_text(receipt["namespace"], "namespace", 63)
        mode, state, outcome = receipt["mode"], receipt["state"], receipt["outcome"]
        settings_revision = receipt["settingsRevision"]
        if settings_revision is not None and (isinstance(settings_revision, bool) or not isinstance(settings_revision, int) or settings_revision < 1):
            raise MetadataStoreError("invalid_metadata", "Console settings revision is invalid", status=400)
        valid = (mode in {"managed_read", "managed", "explicit", "autocommit"}
                  and state in {"reserved", "running", "succeeded", "failed", "cancelled", "uncertain"}
                 and outcome in {"rolled_back", "committed", "partial_committed", "transaction_open", "not_started", "uncertain"})
        indexes = receipt["completedStatementIndexes"]
        if not valid or not isinstance(indexes, list) or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in indexes):
            raise MetadataStoreError("invalid_metadata", "Console execution receipt state is invalid", status=400)
        error_code = None if receipt["errorCode"] is None else identity(receipt["errorCode"], "error_code")
        postgres = None if receipt["postgresEvidence"] is None else bounded_json(receipt["postgresEvidence"], "postgres_evidence", 16384)
        reconciliation = None if receipt["reconciliationEvidence"] is None else bounded_json(receipt["reconciliationEvidence"], "reconciliation_evidence", 16384)
        params = (execution, application, binding_hash, server, profile, fingerprint, database, namespace, console,
                  mode, settings_revision, state, outcome, indexes, error_code, None if postgres is None else _json(postgres),
                  None if reconciliation is None else _json(reconciliation))
        with self._transaction() as cursor:
            if state == "reserved":
                cursor.execute("""INSERT INTO metadata_console_execution_receipts
                    (execution_id, application_id, session_binding_hash, server_id, profile_id, profile_fingerprint,
                     database_name, namespace_name, console_id, mode, settings_revision, state, outcome,
                     completed_statement_indexes, error_code, postgres_evidence, reconciliation_evidence)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                    ON CONFLICT (execution_id) DO NOTHING""", params)
                if cursor.rowcount == 0:
                    raise MetadataStoreError(
                        "execution_conflict", "Execution ID is already reserved", status=409,
                    )
            else:
                cursor.execute("""UPDATE metadata_console_execution_receipts
                    SET state = %s, outcome = %s, completed_statement_indexes = %s, error_code = %s,
                        postgres_evidence = %s::jsonb, reconciliation_evidence = %s::jsonb,
                        updated_at = clock_timestamp()
                    WHERE execution_id = %s AND application_id = %s AND session_binding_hash = %s
                      AND server_id = %s AND profile_id = %s AND profile_fingerprint = %s
                      AND database_name = %s AND namespace_name = %s AND console_id = %s
                      AND mode = %s AND settings_revision IS NOT DISTINCT FROM %s
                      AND state IN ('reserved', 'running')""",
                    (state, outcome, indexes, error_code, None if postgres is None else _json(postgres),
                     None if reconciliation is None else _json(reconciliation), execution, *params[1:11]),
                )
                if cursor.rowcount == 0:
                    raise MetadataStoreError(
                        "execution_conflict", "Execution ID cannot be transitioned", status=409,
                    )
        return self.get_console_execution_receipt(str(execution), application, receipt["sessionBinding"], server,
                                                  profile, fingerprint, database, namespace, str(console))

    def get_console_execution_receipt(self, execution_id: str, application_id: str, session_binding: str,
                                      server_id: str, profile_id: str, profile_fingerprint: str,
                                      database: str, namespace: str, console_id: str) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        owner = (identity(application_id, "application_id"), hashlib.sha256(_bounded_text(session_binding, "session_binding", 4096).encode()).hexdigest(),
                 _bounded_text(server_id, "server_id", 256), _bounded_text(profile_id, "profile_id", 256),
                 _digest(profile_fingerprint, "profile_fingerprint"), _bounded_text(database, "database", 63),
                 _bounded_text(namespace, "namespace", 63), _uuid(console_id, "console_id"))
        with self._transaction(write=False) as cursor:
            cursor.execute("""SELECT application_id, session_binding_hash, server_id, profile_id, profile_fingerprint,
                database_name, namespace_name, console_id, mode, settings_revision, state, outcome, completed_statement_indexes,
                error_code, postgres_evidence, reconciliation_evidence, created_at, updated_at
                FROM metadata_console_execution_receipts WHERE execution_id = %s""", (execution,))
            row = cursor.fetchone()
        owner_names = ("application_id", "session_binding_hash", "server_id", "profile_id", "profile_fingerprint", "database_name", "namespace_name", "console_id")
        if row is None or tuple(_row_value(row, name, index) for index, name in enumerate(owner_names)) != owner:
            raise MetadataStoreError("execution_not_found", "Console execution status was not found", status=404)
        return {"executionId": str(execution), "mode": _row_value(row, "mode", 8),
                "settingsRevision": _row_value(row, "settings_revision", 9), "state": _row_value(row, "state", 10),
                "outcome": _row_value(row, "outcome", 11), "completedStatementIndexes": list(_row_value(row, "completed_statement_indexes", 12)),
                "errorCode": _row_value(row, "error_code", 13), "postgresEvidence": _json_value(_row_value(row, "postgres_evidence", 14)),
                "reconciliationEvidence": _json_value(_row_value(row, "reconciliation_evidence", 15)),
                "createdAt": _iso_datetime(_row_value(row, "created_at", 16)), "updatedAt": _iso_datetime(_row_value(row, "updated_at", 17))}

    def provision_chat(
        self,
        application_id: str,
        resource_kind: str,
        resource_id: str,
        *,
        external_session_id: str | None = None,
    ) -> dict[str, Any]:
        application = identity(application_id, "application_id")
        kind = identity(resource_kind, "resource_kind")
        resource = _bounded_text(resource_id, "resource_id", 256)
        external = None if external_session_id is None else _bounded_text(external_session_id, "external_session_id", 512)
        chat = uuid.uuid4()
        with self._transaction() as cursor:
            if external is not None:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{application}\0{external}",))
                cursor.execute(
                    """SELECT chat_id, resource_kind, resource_id, state FROM metadata_chats
                       WHERE application_id = %s AND external_session_id = %s FOR UPDATE""",
                    (application, external),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    if (_row_value(existing, "resource_kind", 1), _row_value(existing, "resource_id", 2)) != (kind, resource):
                        raise MetadataStoreError("external_session_conflict", "External session belongs to another resource", status=409)
                    return {"chatId": str(_row_value(existing, "chat_id", 0)),
                            "state": _row_value(existing, "state", 3), "provisioningOwner": False}
            cursor.execute(
                """INSERT INTO metadata_chats
                   (chat_id, application_id, resource_kind, resource_id, external_session_id, display_title, state)
                   VALUES (%s, %s, %s, %s, %s, 'Untitled chat', 'provisioning')""",
                (chat, application, kind, resource, external),
            )
            self._audit(cursor, application, "chat", chat, None, "provisioning", "provision_requested")
        return {"chatId": str(chat), "state": "provisioning", "provisioningOwner": True}

    def bind_chat_external_session(self, chat_id: str, external_session_id: str, display_title: str) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        external = _bounded_text(external_session_id, "external_session_id", 512)
        title = _bounded_text(display_title, "display_title", 256)
        with self._transaction() as cursor:
            row = self._lock_chat(cursor, chat)
            if _row_value(row, "state", 1) != "provisioning":
                raise MetadataStoreError("chat_transition_invalid", "External session can only be bound while provisioning", status=409)
            cursor.execute(
                "SELECT external_session_id, display_title FROM metadata_chats WHERE chat_id = %s FOR UPDATE",
                (chat,),
            )
            current = cursor.fetchone()
            current_external = _row_value(current, "external_session_id", 0)
            if current_external is not None and current_external != external:
                raise MetadataStoreError("external_session_conflict", "Chat is bound to another external session", status=409)
            cursor.execute(
                """UPDATE metadata_chats SET external_session_id = %s, display_title = %s,
                          updated_at = clock_timestamp() WHERE chat_id = %s""",
                (external, title, chat),
            )
        return {"chatId": str(chat), "externalSessionId": external, "displayTitle": title}

    def set_chat_conversation_title(self, chat_id: str, conversation_title: str, *, overwrite: bool = False) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        title = _chat_title(conversation_title)
        with self._transaction() as cursor:
            row = self._lock_chat(cursor, chat)
            if _row_value(row, "state", 1) != "active":
                raise MetadataStoreError("chat_inactive", "AI chat is not active", status=409)
            cursor.execute(
                """UPDATE metadata_chats
                   SET conversation_title = %s, updated_at = clock_timestamp()
                   WHERE chat_id = %s AND (%s OR conversation_title IS NULL)
                   RETURNING conversation_title""",
                (title, chat, overwrite),
            )
            updated = cursor.fetchone()
            if updated is None:
                cursor.execute("SELECT conversation_title FROM metadata_chats WHERE chat_id = %s", (chat,))
                updated = cursor.fetchone()
        return {"chatId": str(chat), "conversationTitle": _row_value(updated, "conversation_title", 0)}

    def activate_chat(
        self,
        chat_id: str,
        target: dict[str, Any] | None,
        *,
        policy: dict[str, Any] | None = None,
        capabilities: dict[str, str] | None = None,
        agent_policy_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        safe = None if target is None else _target(target)
        document = None if policy is None else bounded_json(policy, "policy", self.max_json_bytes)
        modes = None if capabilities is None else {identity(name, "capability"): mode for name, mode in capabilities.items()}
        if (document is None) != (modes is None):
            raise MetadataStoreError("invalid_metadata", "Initial policy and capabilities must be supplied together", status=400)
        agent_binding = _agent_policy_binding(agent_policy_binding)
        if agent_binding is not None and document is None:
            raise MetadataStoreError("invalid_metadata", "Agent policy binding requires a chat policy snapshot", status=400)
        if modes is not None and any(mode not in {"deny", "approval", "once_per_chat", "automatic"} for mode in modes.values()):
            raise MetadataStoreError("invalid_metadata", "capability grant mode is invalid", status=400)
        with self._transaction() as cursor:
            row = self._lock_chat(cursor, chat)
            state = _row_value(row, "state", 1)
            if state == "active":
                cursor.execute(
                    """SELECT target_id, profile_id, database_name, namespace_name,
                              profile_fingerprint, connected_target_fingerprint
                       FROM metadata_targets WHERE chat_id = %s""",
                    (chat,),
                )
                existing = cursor.fetchone()
                stored = None if existing is None else {
                    "profileId": _row_value(existing, "profile_id", 1),
                    "databaseName": _row_value(existing, "database_name", 2),
                    "namespaceName": _row_value(existing, "namespace_name", 3),
                    "profileFingerprint": _row_value(existing, "profile_fingerprint", 4),
                    "connectedTargetFingerprint": _row_value(existing, "connected_target_fingerprint", 5),
                }
                if stored != safe:
                    raise MetadataStoreError("target_conflict", "Chat is active for a different immutable target", status=409)
                return {"chatId": str(chat), "state": "active", "activationOwner": False}
            if state != "provisioning":
                raise MetadataStoreError("chat_transition_invalid", "Chat cannot be activated from its current state", status=409)
            cursor.execute("SELECT external_session_id FROM metadata_chats WHERE chat_id = %s", (chat,))
            if _row_value(cursor.fetchone(), "external_session_id", 0) is None:
                raise MetadataStoreError("external_session_required", "Chat has no external provider session", status=409)
            target_id = None
            if safe is not None:
                target_id = uuid.uuid4()
                cursor.execute(
                    """INSERT INTO metadata_targets
                       (target_id, chat_id, profile_id, database_name, namespace_name,
                        profile_fingerprint, connected_target_fingerprint)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (target_id, chat, safe["profileId"], safe["databaseName"], safe["namespaceName"],
                     safe["profileFingerprint"], safe["connectedTargetFingerprint"]),
                )
            if document is not None:
                if agent_binding is not None:
                    self._validate_agent_policy_link(cursor, _row_value(row, "application_id", 0), agent_binding)
                policy_id = uuid.uuid4()
                cursor.execute(
                    """INSERT INTO metadata_policy_versions
                       (policy_version_id, chat_id, revision, policy, agent_policy_revision_id, agent_policy_schema_version)
                       VALUES (%s, %s, 1, %s::jsonb, %s, %s)""",
                    (policy_id, chat, _json(document),
                     None if agent_binding is None else agent_binding["policyRevisionId"],
                     None if agent_binding is None else agent_binding["schemaVersion"]),
                )
                for capability, mode in sorted(modes.items()):
                    cursor.execute(
                        "INSERT INTO metadata_capabilities (capability_id, policy_version_id, capability, grant_mode) VALUES (%s, %s, %s, %s)",
                        (uuid.uuid4(), policy_id, capability, mode),
                    )
            cursor.execute("UPDATE metadata_chats SET state = 'active', updated_at = clock_timestamp() WHERE chat_id = %s", (chat,))
            self._audit(cursor, _row_value(row, "application_id", 0), "chat", chat, state, "active", "provision_succeeded")
        return {"chatId": str(chat), "targetId": None if target_id is None else str(target_id), "state": "active", "activationOwner": True}

    def fail_chat(self, chat_id: str, reason: str) -> dict[str, Any]:
        return self._transition_chat(chat_id, {"provisioning"}, "failed", reason)

    def begin_chat_deletion(self, chat_id: str, reason: str = "delete_requested") -> dict[str, Any]:
        return self._transition_chat(chat_id, {"active", "failed"}, "deleting", reason, idempotent=True)

    def mark_chat_deleted(self, chat_id: str, reason: str = "provider_deleted") -> dict[str, Any]:
        return self._transition_chat(chat_id, {"deleting"}, "deleted", reason, idempotent=True)

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT c.chat_id, c.application_id, c.resource_kind, c.resource_id,
                          c.external_session_id, c.state, c.created_at, c.updated_at, c.deleted_at,
                          t.target_id, t.profile_id, t.database_name, t.namespace_name,
                           t.profile_fingerprint, t.connected_target_fingerprint, c.display_title,
                           c.conversation_title
                   FROM metadata_chats c LEFT JOIN metadata_targets t USING (chat_id)
                   WHERE c.chat_id = %s""",
                (chat,),
            )
            row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("chat_not_found", "Chat was not found", status=404)
        return _chat_record(row)

    def list_chats(
        self,
        *,
        resource_kind: str | None = None,
        resource_id: str | None = None,
        states: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        count = _limit(limit)
        kind = None if resource_kind is None else identity(resource_kind, "resource_kind")
        resource = None if resource_id is None else _bounded_text(resource_id, "resource_id", 256)
        allowed_states = [identity(state, "state") for state in (states or [])]
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT c.chat_id, c.application_id, c.resource_kind, c.resource_id,
                          c.external_session_id, c.state, c.created_at, c.updated_at, c.deleted_at,
                          t.target_id, t.profile_id, t.database_name, t.namespace_name,
                           t.profile_fingerprint, t.connected_target_fingerprint, c.display_title,
                           c.conversation_title
                   FROM metadata_chats c LEFT JOIN metadata_targets t USING (chat_id)
                   WHERE (%s::text IS NULL OR c.resource_kind = %s::text)
                      AND (%s::text IS NULL OR c.resource_id = %s::text)
                     AND (cardinality(%s::text[]) = 0 OR c.state = ANY(%s::text[]))
                   ORDER BY c.created_at DESC, c.chat_id DESC LIMIT %s""",
                (kind, kind, resource, resource, allowed_states, allowed_states, count),
            )
            rows = cursor.fetchall()
        return [_chat_record(row) for row in rows]

    def get_current_policy(self, chat_id: str) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT v.policy_version_id, v.revision, v.policy, v.created_at,
                          v.agent_policy_revision_id, v.agent_policy_schema_version
                   FROM metadata_policy_versions v WHERE v.chat_id = %s
                   ORDER BY v.revision DESC LIMIT 1""",
                (chat,),
            )
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("policy_not_found", "Chat policy was not found", status=404)
            policy_id = _row_value(row, "policy_version_id", 0)
            cursor.execute(
                "SELECT capability, grant_mode FROM metadata_capabilities WHERE policy_version_id = %s ORDER BY capability",
                (policy_id,),
            )
            capabilities = {_row_value(item, "capability", 0): _row_value(item, "grant_mode", 1) for item in cursor.fetchall()}
        return {"policyVersionId": str(policy_id), "revision": int(_row_value(row, "revision", 1)),
                 "policy": _json_value(_row_value(row, "policy", 2)), "capabilities": capabilities,
                 "createdAt": _iso_datetime(_row_value(row, "created_at", 3)),
                 "agentPolicyRevisionId": None if _row_optional(row, "agent_policy_revision_id", 4) is None else str(_row_optional(row, "agent_policy_revision_id", 4)),
                 "agentPolicySchemaVersion": _row_optional(row, "agent_policy_schema_version", 5)}

    def list_grants(self, chat_id: str, *, active_only: bool = False) -> list[dict[str, Any]]:
        chat = _uuid(chat_id, "chat_id")
        if type(active_only) is not bool:
            raise MetadataStoreError("invalid_metadata", "active_only must be a boolean", status=400)
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT grant_id, capability, policy_revision, state, expires_at, created_at, revoked_at
                   FROM metadata_grants WHERE chat_id = %s AND (NOT %s OR state = 'active')
                   ORDER BY created_at DESC, grant_id DESC""",
                (chat, active_only),
            )
            rows = cursor.fetchall()
        return [{"grantId": str(_row_value(row, "grant_id", 0)), "capability": _row_value(row, "capability", 1),
                 "policyRevision": int(_row_value(row, "policy_revision", 2)), "state": _row_value(row, "state", 3),
                 "expiresAt": _iso_datetime(_row_value(row, "expires_at", 4)),
                 "createdAt": _iso_datetime(_row_value(row, "created_at", 5)),
                 "revokedAt": _iso_datetime(_row_value(row, "revoked_at", 6))} for row in rows]

    def update_policy(
        self,
        chat_id: str,
        expected_revision: int,
        policy: dict[str, Any],
        capabilities: dict[str, str],
        agent_policy_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise MetadataStoreError("invalid_metadata", "expected_revision is invalid", status=400)
        if not isinstance(capabilities, dict) or len(capabilities) > 1000:
            raise MetadataStoreError("invalid_metadata", "capabilities must be a bounded object", status=400)
        document = bounded_json(policy, "policy", self.max_json_bytes)
        modes = {identity(name, "capability"): mode for name, mode in capabilities.items()}
        if any(mode not in {"deny", "approval", "once_per_chat", "automatic"} for mode in modes.values()):
            raise MetadataStoreError("invalid_metadata", "capability grant mode is invalid", status=400)
        policy_id = uuid.uuid4()
        agent_binding = _agent_policy_binding(agent_policy_binding)
        revision = expected_revision + 1
        with self._transaction() as cursor:
            cursor.execute("SELECT state, application_id FROM metadata_chats WHERE chat_id = %s FOR UPDATE", (chat,))
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("chat_not_found", "Chat was not found", status=404)
            if _row_value(row, "state", 0) != "active":
                raise MetadataStoreError("chat_inactive", "Chat is not active", status=409)
            cursor.execute("SELECT COALESCE(MAX(revision), 0) AS revision FROM metadata_policy_versions WHERE chat_id = %s", (chat,))
            current = int(_row_value(cursor.fetchone(), "revision", 0))
            if current != expected_revision:
                raise MetadataStoreError("policy_changed", "Chat policy changed; refresh required", status=409, details={"currentRevision": current})
            if agent_binding is not None:
                self._validate_agent_policy_link(cursor, _row_value(row, "application_id", 1), agent_binding)
            cursor.execute(
                """INSERT INTO metadata_policy_versions
                   (policy_version_id, chat_id, revision, policy, agent_policy_revision_id, agent_policy_schema_version)
                   VALUES (%s, %s, %s, %s::jsonb, %s, %s)""",
                (policy_id, chat, revision, _json(document),
                 None if agent_binding is None else agent_binding["policyRevisionId"],
                 None if agent_binding is None else agent_binding["schemaVersion"]),
            )
            for capability, mode in sorted(modes.items()):
                cursor.execute(
                    "INSERT INTO metadata_capabilities (capability_id, policy_version_id, capability, grant_mode) VALUES (%s, %s, %s, %s)",
                    (uuid.uuid4(), policy_id, capability, mode),
                )
            cursor.execute(
                """UPDATE metadata_grants SET state = 'revoked', revoked_at = clock_timestamp()
                   WHERE chat_id = %s AND state = 'active'
                     AND (policy_revision <> %s OR capability <> ALL(%s))""",
                (chat, revision, list(name for name, mode in modes.items() if mode == "once_per_chat")),
            )
            self._audit(cursor, self._application_for_chat(cursor, chat), "grant", policy_id, str(expected_revision), str(revision), "policy_updated")
        return {"chatId": str(chat), "revision": revision, "policyVersionId": str(policy_id)}

    def create_proposal(
        self,
        chat_id: str,
        capability: str,
        policy_revision: int,
        binding: dict[str, Any],
        action: dict[str, Any],
        *,
        ttl_seconds: int = 300,
    ) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        capability_name = identity(capability, "capability")
        revision = _positive_int(policy_revision, "policy_revision")
        ttl = _seconds(ttl_seconds, "ttl_seconds", maximum=86400)
        safe_binding = bounded_json(binding, "binding", self.max_json_bytes)
        safe_action = bounded_json(action, "action", self.max_json_bytes)
        proposal = uuid.uuid4()
        with self._transaction() as cursor:
            chat_row = self._lock_chat(cursor, chat)
            if _row_value(chat_row, "state", 1) != "active":
                raise MetadataStoreError("chat_inactive", "Chat is not active", status=409)
            cursor.execute(
                """SELECT 1 FROM metadata_policy_versions v JOIN metadata_capabilities c USING (policy_version_id)
                   WHERE v.chat_id = %s AND v.revision = %s AND c.capability = %s
                     AND v.revision = (SELECT MAX(revision) FROM metadata_policy_versions WHERE chat_id = %s)""",
                (chat, revision, capability_name, chat),
            )
            if cursor.fetchone() is None:
                raise MetadataStoreError("policy_changed", "Proposal must bind the current policy capability", status=409)
            if safe_binding.get("policyBinding", {}).get("snapshot", {}).get("version") == 2:
                self._validate_effective_proposal_binding(cursor, chat, capability_name, revision, safe_binding)
            cursor.execute(
                """INSERT INTO metadata_proposals
                   (proposal_id, chat_id, capability, policy_revision, binding, action, expires_at)
                   VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb,
                           clock_timestamp() + (%s * interval '1 second'))""",
                (proposal, chat, capability_name, revision, _json(safe_binding), _json(safe_action), ttl),
            )
            self._audit(cursor, _row_value(chat_row, "application_id", 0), "proposal", proposal, None, "ready", "proposal_created")
        return {"proposalId": str(proposal), "chatId": str(chat), "state": "ready", "policyRevision": revision}

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        proposal = _uuid(proposal_id, "proposal_id")
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT proposal_id, chat_id, capability, policy_revision, binding, action,
                           state, created_at, expires_at, revoked_at, revocation_reason, revocation_evidence,
                           cancellation_requested_at
                   FROM metadata_proposals WHERE proposal_id = %s""",
                (proposal,),
            )
            row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("proposal_not_found", "Proposal was not found", status=404)
        return _proposal_record(row)

    def list_proposals(self, chat_id: str, *, states: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        chat = _uuid(chat_id, "chat_id")
        allowed_states = [identity(state, "state") for state in (states or [])]
        count = _limit(limit)
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT proposal_id, chat_id, capability, policy_revision, binding, action,
                           state, created_at, expires_at, revoked_at, revocation_reason, revocation_evidence,
                           cancellation_requested_at
                   FROM metadata_proposals WHERE chat_id = %s
                     AND (cardinality(%s::text[]) = 0 OR state = ANY(%s::text[]))
                   ORDER BY created_at DESC, proposal_id DESC LIMIT %s""",
                (chat, allowed_states, allowed_states, count),
            )
            rows = cursor.fetchall()
        return [_proposal_record(row) for row in rows]

    def request_proposal_cancellation(
        self, proposal_id: str, chat_id: str, *, expected_application: str, expected_resource_kind: str,
    ) -> dict[str, Any]:
        proposal = _uuid(proposal_id, "proposal_id")
        chat = _uuid(chat_id, "chat_id")
        application = identity(expected_application, "expected_application")
        resource_kind = identity(expected_resource_kind, "expected_resource_kind")
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT p.chat_id, p.state, p.cancellation_requested_at, c.application_id, c.resource_kind
                   FROM metadata_proposals p JOIN metadata_chats c USING (chat_id)
                   WHERE p.proposal_id = %s FOR UPDATE OF p""",
                (proposal,),
            )
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("proposal_not_found", "Proposal was not found", status=404)
            if _row_value(row, "chat_id", 0) != chat:
                raise MetadataStoreError("authority_binding_mismatch", "Authority record belongs to another chat", status=403)
            if (_row_value(row, "application_id", 3), _row_value(row, "resource_kind", 4)) != (application, resource_kind):
                raise MetadataStoreError("authority_binding_mismatch", "Authority record belongs to another application resource", status=403)
            proposal_state = _row_value(row, "state", 1)
            cursor.execute(
                "SELECT operation_id, state FROM metadata_operations WHERE proposal_id = %s FOR UPDATE",
                (proposal,),
            )
            operation = cursor.fetchone()
            if operation is None:
                if proposal_state == "cancelled":
                    return {"requested": True, "proposalState": "cancelled", "operationId": None, "operationState": None}
                if proposal_state != "ready":
                    return {"requested": False, "proposalState": proposal_state, "operationId": None, "operationState": None}
                cursor.execute(
                    "UPDATE metadata_proposals SET state = 'cancelled', cancellation_requested_at = clock_timestamp() WHERE proposal_id = %s",
                    (proposal,),
                )
                self._audit(cursor, self._application_for_chat(cursor, chat), "proposal", proposal, "ready", "cancelled", "query_cancellation_requested")
                return {"requested": True, "proposalState": "cancelled", "operationId": None, "operationState": None}
            operation_id = _row_value(operation, "operation_id", 0)
            operation_state = _row_value(operation, "state", 1)
            if operation_state not in {"ready", "running"}:
                return {
                    "requested": False, "proposalState": proposal_state,
                    "operationId": str(operation_id), "operationState": operation_state,
                }
            cursor.execute(
                "UPDATE metadata_proposals SET cancellation_requested_at = COALESCE(cancellation_requested_at, clock_timestamp()) WHERE proposal_id = %s",
                (proposal,),
            )
            if operation_state == "ready":
                error = {"code": "execution_cancelled", "message": "AI query was cancelled before execution started"}
                cursor.execute(
                    "INSERT INTO metadata_operation_outcomes (outcome_id, operation_id, state, error) VALUES (%s, %s, 'cancelled', %s::jsonb)",
                    (uuid.uuid4(), operation_id, _json(error)),
                )
                cursor.execute(
                    "UPDATE metadata_operations SET state = 'cancelled', updated_at = clock_timestamp() WHERE operation_id = %s",
                    (operation_id,),
                )
                self._audit(cursor, self._application_for_chat(cursor, chat), "operation", operation_id, "ready", "cancelled", "query_cancellation_requested")
                return {
                    "requested": True, "proposalState": proposal_state,
                    "operationId": str(operation_id), "operationState": "cancelled",
                }
            self._audit(cursor, self._application_for_chat(cursor, chat), "operation", operation_id, operation_state, operation_state, "query_cancellation_requested")
            return {
                "requested": True, "proposalState": proposal_state,
                "operationId": str(operation_id), "operationState": operation_state,
            }

    def authorize_and_create_operation(
        self,
        proposal_id: str,
        *,
        expected_policy_revision: int,
        approved: bool = False,
        required_effective_mode: str | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 60,
    ) -> dict[str, Any]:
        proposal = _uuid(proposal_id, "proposal_id")
        if isinstance(expected_policy_revision, bool) or not isinstance(expected_policy_revision, int) or expected_policy_revision <= 0:
            raise MetadataStoreError("invalid_metadata", "expected_policy_revision is invalid", status=400)
        if type(approved) is not bool:
            raise MetadataStoreError("invalid_metadata", "approved must be a boolean", status=400)
        if required_effective_mode is not None and required_effective_mode not in {"every_action", "once_per_chat", "automatic"}:
            raise MetadataStoreError("invalid_metadata", "required_effective_mode is invalid", status=400)
        worker = None if worker_id is None else identity(worker_id, "worker_id")
        lease = _seconds(lease_seconds, "lease_seconds", maximum=3600)
        claim_token = secrets.token_urlsafe(32) if worker is not None else None
        attempt_id = uuid.uuid4() if worker is not None else None
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT chat_id, capability, policy_revision, state, binding,
                           expires_at > clock_timestamp() AS current, cancellation_requested_at
                   FROM metadata_proposals WHERE proposal_id = %s FOR UPDATE""",
                (proposal,),
            )
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("proposal_not_found", "Proposal was not found", status=404)
            state = _row_value(row, "state", 3)
            if state == "cancelled" or _row_optional(row, "cancellation_requested_at", 6) is not None:
                raise MetadataStoreError("proposal_cancelled", "Proposal execution was cancelled", status=409)
            if state == "authorized":
                cursor.execute("SELECT operation_id, state FROM metadata_operations WHERE proposal_id = %s", (proposal,))
                existing = cursor.fetchone()
                if existing is None:
                    raise MetadataStoreError("metadata_invariant", "Authorized proposal has no operation")
                return {"operationId": str(_row_value(existing, "operation_id", 0)), "state": _row_value(existing, "state", 1), "executionOwner": False}
            if state != "ready":
                raise MetadataStoreError("proposal_unavailable", "Proposal is not ready", status=409)
            if not _row_value(row, "current", 5):
                raise MetadataStoreError("proposal_expired", "Proposal has expired", status=409)
            chat_id = _row_value(row, "chat_id", 0)
            capability = _row_value(row, "capability", 1)
            cursor.execute("SELECT state FROM metadata_chats WHERE chat_id = %s FOR UPDATE", (chat_id,))
            chat_row = cursor.fetchone()
            if chat_row is None or _row_value(chat_row, "state", 0) != "active":
                raise MetadataStoreError("chat_inactive", "Chat is not active", status=409)
            cursor.execute(
                """SELECT c.grant_mode,
                          (SELECT MAX(current.revision) FROM metadata_policy_versions current WHERE current.chat_id = v.chat_id) AS current_revision,
                          (SELECT g.grant_id FROM metadata_grants g
                           WHERE g.chat_id = v.chat_id AND g.capability = c.capability AND g.state = 'active'
                             AND (g.expires_at IS NULL OR g.expires_at > clock_timestamp())) AS grant_id
                   FROM metadata_policy_versions v
                   JOIN metadata_capabilities c ON c.policy_version_id = v.policy_version_id
                   WHERE v.chat_id = %s AND v.revision = %s AND c.capability = %s""",
                (chat_id, _row_value(row, "policy_revision", 2), capability),
            )
            authority = cursor.fetchone()
            if authority is None:
                raise MetadataStoreError("policy_changed", "Proposal policy binding is stale", status=409)
            revision = int(_row_value(row, "policy_revision", 2))
            current_revision = int(_row_value(authority, "current_revision", 1))
            if revision != expected_policy_revision or revision != current_revision:
                raise MetadataStoreError("policy_changed", "Proposal policy binding is stale", status=409)
            mode = _row_value(authority, "grant_mode", 0)
            grant_id = _row_value(authority, "grant_id", 2)
            binding = _json_value(_row_value(row, "binding", 4))
            if binding.get("policyBinding", {}).get("snapshot", {}).get("version") == 2:
                self._validate_effective_proposal_binding(cursor, chat_id, capability, revision, binding)
                snapshot = binding["policyBinding"]["snapshot"]
                concurrency = snapshot["bounds"]["agentConcurrency"] or 16
                cursor.execute(
                    "SELECT current_revision FROM metadata_agent_settings WHERE application_id = %s AND agent_id = %s FOR UPDATE",
                    (snapshot["application"], snapshot["agentId"]),
                )
                settings_row = cursor.fetchone()
                if settings_row is None or int(_row_value(settings_row, "current_revision", 0)) != snapshot["agentPolicyRevision"]:
                    raise MetadataStoreError("agent_policy_changed", "AI agent settings changed; request a fresh proposal", status=409)
                cursor.execute(
                    """SELECT count(*) AS active_count FROM metadata_operations o
                       JOIN metadata_proposals p ON p.proposal_id = o.proposal_id
                       WHERE o.state = 'running'
                         AND p.binding -> 'policyBinding' ->> 'application' = %s
                         AND p.binding -> 'policyBinding' ->> 'agentId' = %s""",
                    (snapshot["application"], snapshot["agentId"]),
                )
                if int(_row_value(cursor.fetchone(), "active_count", 0)) >= concurrency:
                    raise MetadataStoreError(
                        "agent_concurrency_exhausted", "AI agent concurrency bound is exhausted", status=409,
                        details={"agentId": snapshot["agentId"], "maximum": concurrency},
                    )
            effective = binding.get("policyBinding", {}).get("effectiveMode") if isinstance(binding, dict) else None
            if effective not in {"every_action", "once_per_chat", "automatic"}:
                raise MetadataStoreError("policy_changed", "Proposal approval policy binding is invalid", status=409)
            if required_effective_mode is not None:
                effective = required_effective_mode
            compatible = (
                effective == "every_action" or
                (effective == "once_per_chat" and mode == "once_per_chat") or
                (effective == "automatic" and mode == "automatic")
            )
            requires_approval = effective == "every_action" or (effective == "once_per_chat" and grant_id is None)
            if mode == "deny" or not compatible or (requires_approval and not approved):
                raise MetadataStoreError("approval_required", "Proposal requires explicit approval", status=403)
            decision = "automatic" if effective == "automatic" else "grant" if grant_id is not None else "explicit"
            if effective == "once_per_chat" and grant_id is None:
                cursor.execute(
                    "INSERT INTO metadata_grants (grant_id, chat_id, capability, policy_revision) VALUES (%s, %s, %s, %s)",
                    (uuid.uuid4(), chat_id, capability, revision),
                )
            operation_id = uuid.uuid4()
            cursor.execute(
                "INSERT INTO metadata_operations (operation_id, proposal_id, chat_id, capability) VALUES (%s, %s, %s, %s)",
                (operation_id, proposal, chat_id, capability),
            )
            cursor.execute(
                "INSERT INTO metadata_operation_approvals (approval_id, operation_id, policy_revision, decision) VALUES (%s, %s, %s, %s)",
                (uuid.uuid4(), operation_id, revision, decision),
            )
            cursor.execute("UPDATE metadata_proposals SET state = 'authorized' WHERE proposal_id = %s", (proposal,))
            if worker is not None:
                cursor.execute(
                    """INSERT INTO metadata_operation_attempts
                       (attempt_id, operation_id, worker_id, claim_token_hash, lease_expires_at)
                       VALUES (%s, %s, %s, %s, clock_timestamp() + (%s * interval '1 second'))""",
                    (attempt_id, operation_id, worker, _token_hash(claim_token), lease),
                )
                cursor.execute("UPDATE metadata_operations SET state = 'running', updated_at = clock_timestamp() WHERE operation_id = %s", (operation_id,))
            self._audit(cursor, self._application_for_chat(cursor, chat_id), "proposal", proposal, "ready", "authorized", "proposal_authorized")
            self._audit(cursor, self._application_for_chat(cursor, chat_id), "operation", operation_id, None, "ready", "operation_created")
        return {"operationId": str(operation_id), "state": "running" if worker is not None else "ready", "executionOwner": True,
                **({"attemptId": str(attempt_id), "claimToken": claim_token} if worker is not None else {})}

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        operation = _uuid(operation_id, "operation_id")
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT o.operation_id, o.proposal_id, o.chat_id, o.capability, o.state,
                           o.created_at, o.updated_at, a.attempt_id, a.worker_id, a.lease_expires_at,
                           x.state AS outcome_state, x.result, x.error, p.cancellation_requested_at
                    FROM metadata_operations o JOIN metadata_proposals p USING (proposal_id)
                   LEFT JOIN metadata_operation_attempts a ON a.operation_id = o.operation_id AND a.state = 'running'
                   LEFT JOIN metadata_operation_outcomes x ON x.operation_id = o.operation_id
                   WHERE o.operation_id = %s""",
                (operation,),
            )
            row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("operation_not_found", "Operation was not found", status=404)
        return _operation_record(row)

    def consume_operation_bound(self, operation_id: str, bound_name: str, amount: int, evidence: Any) -> dict[str, Any]:
        operation = _uuid(operation_id, "operation_id")
        if bound_name not in {"rowsDisclosed", "rowsWritten", "pagesInspected"}:
            raise MetadataStoreError("invalid_metadata", "AI operation bound name is invalid", status=400)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise MetadataStoreError("invalid_metadata", "AI operation bound amount is invalid", status=400)
        safe_evidence = bounded_json(evidence, "evidence", 8192)
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT o.state, p.binding -> 'policyBinding' -> 'snapshot' -> 'bounds' ->> %s AS maximum
                   FROM metadata_operations o JOIN metadata_proposals p USING (proposal_id)
                   WHERE o.operation_id = %s FOR UPDATE OF o""",
                (bound_name, operation),
            )
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("operation_not_found", "Operation was not found", status=404)
            if _row_value(row, "state", 0) != "running":
                raise MetadataStoreError("operation_not_running", "AI operation is not running", status=409)
            raw_maximum = _row_value(row, "maximum", 1)
            maximum = None if raw_maximum is None else int(raw_maximum)
            cursor.execute(
                "SELECT used, evidence FROM metadata_ai_operation_usage WHERE operation_id = %s AND bound_name = %s FOR UPDATE",
                (operation, bound_name),
            )
            usage = cursor.fetchone()
            used = 0 if usage is None else int(_row_value(usage, "used", 0))
            updated = used + amount
            if maximum is not None and updated > maximum:
                raise MetadataStoreError(
                    "policy_bound_exceeded", f"AI operation exceeds the {bound_name} bound", status=422,
                    details={"bound": bound_name, "maximum": maximum, "used": used, "requested": amount},
                )
            history = [] if usage is None else _json_value(_row_value(usage, "evidence", 1))
            history.append(safe_evidence)
            cursor.execute(
                """INSERT INTO metadata_ai_operation_usage (operation_id, bound_name, used, evidence)
                   VALUES (%s, %s, %s, %s::jsonb)
                   ON CONFLICT (operation_id, bound_name) DO UPDATE
                   SET used = EXCLUDED.used, evidence = EXCLUDED.evidence, updated_at = clock_timestamp()""",
                (operation, bound_name, updated, _json(history)),
            )
        return {"operationId": str(operation), "bound": bound_name, "used": updated, "maximum": maximum}

    def list_operations(self, chat_id: str, *, states: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        chat = _uuid(chat_id, "chat_id")
        allowed_states = [identity(state, "state") for state in (states or [])]
        count = _limit(limit)
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT o.operation_id, o.proposal_id, o.chat_id, o.capability, o.state,
                           o.created_at, o.updated_at, a.attempt_id, a.worker_id, a.lease_expires_at,
                           x.state AS outcome_state, x.result, x.error, p.cancellation_requested_at
                    FROM metadata_operations o JOIN metadata_proposals p USING (proposal_id)
                   LEFT JOIN metadata_operation_attempts a ON a.operation_id = o.operation_id AND a.state = 'running'
                   LEFT JOIN metadata_operation_outcomes x ON x.operation_id = o.operation_id
                   WHERE o.chat_id = %s AND (cardinality(%s::text[]) = 0 OR o.state = ANY(%s::text[]))
                   ORDER BY o.created_at DESC, o.operation_id DESC LIMIT %s""",
                (chat, allowed_states, allowed_states, count),
            )
            rows = cursor.fetchall()
        return [_operation_record(row) for row in rows]

    def claim_operation(self, operation_id: str, worker_id: str, *, lease_seconds: int = 60) -> dict[str, Any]:
        operation = _uuid(operation_id, "operation_id")
        worker = identity(worker_id, "worker_id")
        token = secrets.token_urlsafe(32)
        attempt = uuid.uuid4()
        lease = _seconds(lease_seconds, "lease_seconds", maximum=3600)
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT p.cancellation_requested_at FROM metadata_proposals p
                   JOIN metadata_operations o USING (proposal_id) WHERE o.operation_id = %s FOR UPDATE OF p""",
                (operation,),
            )
            proposal_row = cursor.fetchone()
            if proposal_row is None:
                raise MetadataStoreError("operation_not_found", "Operation was not found", status=404)
            cursor.execute("SELECT state, chat_id FROM metadata_operations WHERE operation_id = %s FOR UPDATE", (operation,))
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("operation_not_found", "Operation was not found", status=404)
            if _row_value(row, "state", 0) != "ready":
                raise MetadataStoreError("operation_not_claimable", "Operation is not ready for execution", status=409)
            if _row_optional(proposal_row, "cancellation_requested_at", 0) is not None:
                raise MetadataStoreError("proposal_cancelled", "Proposal execution was cancelled", status=409)
            cursor.execute(
                """INSERT INTO metadata_operation_attempts
                   (attempt_id, operation_id, worker_id, claim_token_hash, lease_expires_at)
                   VALUES (%s, %s, %s, %s, clock_timestamp() + (%s * interval '1 second'))""",
                (attempt, operation, worker, _token_hash(token), lease),
            )
            cursor.execute("UPDATE metadata_operations SET state = 'running', updated_at = clock_timestamp() WHERE operation_id = %s", (operation,))
            self._audit(cursor, self._application_for_chat(cursor, _row_value(row, "chat_id", 1)), "operation", operation, "ready", "running", "operation_claimed")
        return {"attemptId": str(attempt), "claimToken": token, "state": "running"}

    def heartbeat_operation(self, attempt_id: str, claim_token: str, *, lease_seconds: int = 60) -> dict[str, Any]:
        return self._touch_attempt(attempt_id, claim_token, finish_state=None, result=None, error=None,
                                   lease_seconds=_seconds(lease_seconds, "lease_seconds", maximum=3600))

    def finish_operation(
        self,
        attempt_id: str,
        claim_token: str,
        state: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if state not in _TERMINAL_OPERATION_STATES:
            raise MetadataStoreError("invalid_metadata", "operation finish state is invalid", status=400)
        safe_result = None if result is None else bounded_json(result, "result", self.max_json_bytes)
        safe_error = None if error is None else bounded_json(error, "error", self.max_json_bytes)
        if (state == "succeeded") != (safe_result is not None and safe_error is None):
            raise MetadataStoreError("invalid_metadata", "operation outcome payload does not match state", status=400)
        if state != "succeeded" and safe_error is None:
            raise MetadataStoreError("invalid_metadata", "failed or uncertain operation requires an error", status=400)
        return self._touch_attempt(attempt_id, claim_token, finish_state=state, result=safe_result, error=safe_error, lease_seconds=None)

    def abandon_stale_operations(self, *, stale_before: datetime, limit: int = 100) -> list[str]:
        cutoff = _aware_datetime(stale_before, "stale_before")
        count = _limit(limit)
        error = {"code": "lease_expired", "message": "Execution lease expired; reconcile without replay"}
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT a.attempt_id, a.operation_id, o.chat_id
                   FROM metadata_operation_attempts a JOIN metadata_operations o USING (operation_id)
                   WHERE a.state = 'running' AND a.lease_expires_at < %s
                   ORDER BY a.lease_expires_at FOR UPDATE OF a, o SKIP LOCKED LIMIT %s""",
                (cutoff, count),
            )
            rows = cursor.fetchall()
            for row in rows:
                attempt = _row_value(row, "attempt_id", 0)
                operation = _row_value(row, "operation_id", 1)
                cursor.execute("UPDATE metadata_operation_attempts SET state = 'abandoned', finished_at = clock_timestamp() WHERE attempt_id = %s", (attempt,))
                cursor.execute(
                    """INSERT INTO metadata_operation_outcomes (outcome_id, operation_id, state, error)
                       VALUES (%s, %s, 'uncertain', %s::jsonb) ON CONFLICT (operation_id) DO NOTHING""",
                    (uuid.uuid4(), operation, _json(error)),
                )
                cursor.execute("UPDATE metadata_operations SET state = 'uncertain', updated_at = clock_timestamp() WHERE operation_id = %s AND state = 'running'", (operation,))
                self._scrub_operation_results(cursor, operation, "operation_abandoned")
                self._audit(cursor, self._application_for_chat(cursor, _row_value(row, "chat_id", 2)), "operation", operation, "running", "uncertain", "lease_abandoned_reconcile_only")
        return [str(_row_value(row, "operation_id", 1)) for row in rows]

    def abandon_operation_attempt(self, attempt_id: str, claim_token: str) -> dict[str, Any]:
        """Make an exact formerly-owned attempt uncertain without making it claimable again."""
        attempt = _uuid(attempt_id, "attempt_id")
        error = {"code": "lease_lost", "message": "Execution lease was lost; reconcile without replay"}
        with self._transaction() as cursor:
            cursor.execute(
                "SELECT operation_id, state, claim_token_hash FROM metadata_operation_attempts WHERE attempt_id = %s FOR UPDATE",
                (attempt,),
            )
            row = cursor.fetchone()
            if row is None or not secrets.compare_digest(str(_row_value(row, "claim_token_hash", 2)), _token_hash(claim_token)):
                raise MetadataStoreError("invalid_claim", "Execution claim is invalid", status=409)
            operation = _row_value(row, "operation_id", 0)
            if _row_value(row, "state", 1) != "running":
                cursor.execute("SELECT state FROM metadata_operations WHERE operation_id = %s", (operation,))
                return {"operationId": str(operation), "state": _row_value(cursor.fetchone(), "state", 0), "resolutionOwner": False}
            cursor.execute("UPDATE metadata_operation_attempts SET state = 'abandoned', finished_at = clock_timestamp() WHERE attempt_id = %s", (attempt,))
            cursor.execute(
                "INSERT INTO metadata_operation_outcomes (outcome_id, operation_id, state, error) VALUES (%s, %s, 'uncertain', %s::jsonb) ON CONFLICT (operation_id) DO NOTHING",
                (uuid.uuid4(), operation, _json(error)),
            )
            cursor.execute("UPDATE metadata_operations SET state = 'uncertain', updated_at = clock_timestamp() WHERE operation_id = %s AND state = 'running'", (operation,))
            self._scrub_operation_results(cursor, operation, "operation_abandoned")
            cursor.execute("SELECT chat_id FROM metadata_operations WHERE operation_id = %s", (operation,))
            chat_id = _row_value(cursor.fetchone(), "chat_id", 0)
            self._audit(cursor, self._application_for_chat(cursor, chat_id), "operation", operation, "running", "uncertain", "lease_lost_reconcile_only")
        return {"operationId": str(operation), "state": "uncertain", "resolutionOwner": True}

    def resolve_uncertain_operation(
        self,
        operation_id: str,
        state: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operation = _uuid(operation_id, "operation_id")
        if state not in {"succeeded", "failed"}:
            raise MetadataStoreError("invalid_metadata", "resolution state is invalid", status=400)
        safe_result = None if result is None else bounded_json(result, "result", self.max_json_bytes)
        safe_error = None if error is None else bounded_json(error, "error", self.max_json_bytes)
        if (state == "succeeded") != (safe_result is not None and safe_error is None):
            raise MetadataStoreError("invalid_metadata", "resolution payload does not match state", status=400)
        if state == "failed" and safe_error is None:
            raise MetadataStoreError("invalid_metadata", "failed resolution requires an error", status=400)
        with self._transaction() as cursor:
            cursor.execute("SELECT state, chat_id FROM metadata_operations WHERE operation_id = %s FOR UPDATE", (operation,))
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("operation_not_found", "Operation was not found", status=404)
            current = _row_value(row, "state", 0)
            cursor.execute("SELECT state, result, error FROM metadata_operation_outcomes WHERE operation_id = %s FOR UPDATE", (operation,))
            outcome = cursor.fetchone()
            if current == state:
                if outcome is None or _json_value(_row_value(outcome, "result", 1)) != safe_result or _json_value(_row_value(outcome, "error", 2)) != safe_error:
                    raise MetadataStoreError("resolution_conflict", "Operation was resolved with a different outcome", status=409)
                return {"operationId": str(operation), "state": state, "resolutionOwner": False}
            if current != "uncertain":
                raise MetadataStoreError("operation_not_uncertain", "Only uncertain operations can be reconciled", status=409)
            cursor.execute("UPDATE metadata_operation_outcomes SET state = %s, result = %s::jsonb, error = %s::jsonb WHERE operation_id = %s",
                           (state, None if safe_result is None else _json(safe_result), None if safe_error is None else _json(safe_error), operation))
            cursor.execute("UPDATE metadata_operations SET state = %s, updated_at = clock_timestamp() WHERE operation_id = %s", (state, operation))
            self._audit(cursor, self._application_for_chat(cursor, _row_value(row, "chat_id", 1)), "operation", operation, "uncertain", state, "operation_reconciled")
        return {"operationId": str(operation), "state": state, "resolutionOwner": True}

    def create_result(self, chat_id: str, binding: dict[str, Any], payload: dict[str, Any], *, ttl_seconds: int = 300) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        if isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= 86400:
            raise MetadataStoreError("invalid_metadata", "ttl_seconds is invalid", status=400)
        safe_binding = bounded_json(binding, "binding", self.max_json_bytes)
        safe_payload = bounded_json(payload, "payload", self.max_json_bytes)
        operation = None if safe_binding.get("operationId") is None else _uuid(safe_binding["operationId"], "operationId")
        byte_count = len(_json(safe_payload).encode("utf-8"))
        result_ref = uuid.uuid4()
        with self._transaction() as cursor:
            if operation is not None:
                cursor.execute(
                    """SELECT p.chat_id, p.cancellation_requested_at FROM metadata_proposals p
                       JOIN metadata_operations o USING (proposal_id)
                       WHERE o.operation_id = %s FOR UPDATE OF p""",
                    (operation,),
                )
                proposal_row = cursor.fetchone()
                if proposal_row is None or str(_row_value(proposal_row, "chat_id", 0)) != str(chat):
                    raise MetadataStoreError("authority_binding_mismatch", "Query result operation binding is invalid", status=403)
                if _row_optional(proposal_row, "cancellation_requested_at", 1) is not None:
                    raise MetadataStoreError("proposal_cancelled", "Cancelled operation cannot create a query result", status=409)
                cursor.execute(
                    """SELECT o.state, o.chat_id, EXISTS (
                           SELECT 1 FROM metadata_operation_attempts a
                           WHERE a.operation_id = o.operation_id AND a.state = 'running'
                             AND a.lease_expires_at >= clock_timestamp()
                       ) AS active_claim
                       FROM metadata_operations o WHERE o.operation_id = %s FOR UPDATE""",
                    (operation,),
                )
                operation_row = cursor.fetchone()
                if operation_row is None or str(_row_value(operation_row, "chat_id", 1)) != str(chat):
                    raise MetadataStoreError("authority_binding_mismatch", "Query result operation binding is invalid", status=403)
                if _row_value(operation_row, "state", 0) != "running" or not _row_value(operation_row, "active_claim", 2):
                    raise MetadataStoreError("operation_not_running", "Query result operation no longer owns an active execution claim", status=409)
            cursor.execute("SELECT state FROM metadata_chats WHERE chat_id = %s FOR UPDATE", (chat,))
            chat_row = cursor.fetchone()
            if chat_row is None:
                raise MetadataStoreError("chat_not_found", "Chat was not found", status=404)
            if _row_value(chat_row, "state", 0) != "active":
                raise MetadataStoreError("chat_inactive", "Chat is not active", status=409)
            cursor.execute(
                """INSERT INTO metadata_query_result_references
                   (result_ref_id, chat_id, binding, expires_at)
                   VALUES (%s, %s, %s::jsonb, clock_timestamp() + (%s * interval '1 second'))""",
                (result_ref, chat, _json(safe_binding), ttl_seconds),
            )
            cursor.execute(
                "INSERT INTO metadata_query_result_payloads (result_ref_id, payload, byte_count) VALUES (%s, %s::jsonb, %s)",
                (result_ref, _json(safe_payload), byte_count),
            )
            self._audit(cursor, self._application_for_chat(cursor, chat), "result", result_ref, None, "ready", "result_created")
        return {"resultRefId": str(result_ref), "state": "ready"}

    def reserve_result(self, result_ref_id: str, chat_id: str, binding: dict[str, Any]) -> dict[str, Any]:
        result_ref = _uuid(result_ref_id, "result_ref_id")
        chat = _uuid(chat_id, "chat_id")
        safe_binding = bounded_json(binding, "binding", self.max_json_bytes)
        token = secrets.token_urlsafe(32)
        delivery = uuid.uuid4()
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT r.chat_id, r.binding, r.state, r.expires_at > clock_timestamp() AS current, p.payload
                   FROM metadata_query_result_references r JOIN metadata_query_result_payloads p USING (result_ref_id)
                   WHERE r.result_ref_id = %s FOR UPDATE OF r""",
                (result_ref,),
            )
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("result_not_found", "Query result was not found", status=404)
            stored_binding = _json_value(_row_value(row, "binding", 1))
            comparable_binding = stored_binding
            if isinstance(stored_binding, dict):
                server_only = {
                    key for key in ("policyBinding", "operationId")
                    if key in stored_binding and key not in safe_binding
                }
                if server_only:
                    comparable_binding = {key: value for key, value in stored_binding.items() if key not in server_only}
            if str(_row_value(row, "chat_id", 0)) != str(chat) or comparable_binding != safe_binding:
                raise MetadataStoreError("result_binding_mismatch", "Query result binding does not match", status=403)
            if _row_value(row, "state", 2) != "ready" or not _row_value(row, "current", 3):
                raise MetadataStoreError("result_unavailable", "Query result is not available", status=409)
            cursor.execute(
                "INSERT INTO metadata_query_result_deliveries (delivery_id, result_ref_id, reservation_token_hash) VALUES (%s, %s, %s)",
                (delivery, result_ref, _token_hash(token)),
            )
            cursor.execute("UPDATE metadata_query_result_references SET state = 'reserved' WHERE result_ref_id = %s", (result_ref,))
            self._audit(cursor, self._application_for_chat(cursor, chat), "result", result_ref, "ready", "reserved", "delivery_reserved")
        return {"deliveryId": str(delivery), "reservationToken": token, "payload": _json_value(_row_value(row, "payload", 4)), "state": "reserved"}

    def begin_result_delivery(self, delivery_id: str, reservation_token: str) -> dict[str, Any]:
        return self._result_transition(delivery_id, reservation_token, "reserved", "delivering")

    def consume_result(self, delivery_id: str, reservation_token: str) -> dict[str, Any]:
        return self._result_transition(delivery_id, reservation_token, "delivering", "consumed", scrub=True)

    def release_result(self, delivery_id: str, reservation_token: str) -> dict[str, Any]:
        return self._result_transition(delivery_id, reservation_token, "reserved", "released")

    def mark_result_uncertain(self, delivery_id: str, reservation_token: str) -> dict[str, Any]:
        return self._result_transition(delivery_id, reservation_token, "delivering", "uncertain", scrub=True)

    def recover_stale_results(self, *, reserved_before: datetime, delivering_before: datetime, limit: int = 100) -> dict[str, list[str]]:
        reserved_cutoff = _aware_datetime(reserved_before, "reserved_before")
        delivering_cutoff = _aware_datetime(delivering_before, "delivering_before")
        count = _limit(limit)
        released: list[str] = []
        uncertain: list[str] = []
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT d.delivery_id, d.result_ref_id, d.state
                   FROM metadata_query_result_deliveries d
                   WHERE (d.state = 'reserved' AND d.reserved_at < %s)
                      OR (d.state = 'delivering' AND d.dispatch_started_at < %s)
                   ORDER BY d.reserved_at FOR UPDATE OF d SKIP LOCKED LIMIT %s""",
                (reserved_cutoff, delivering_cutoff, count),
            )
            rows = cursor.fetchall()
            for row in rows:
                delivery = _row_value(row, "delivery_id", 0)
                result_ref = _row_value(row, "result_ref_id", 1)
                cursor.execute("SELECT c.application_id FROM metadata_query_result_references r JOIN metadata_chats c USING (chat_id) WHERE r.result_ref_id = %s", (result_ref,))
                application = _row_value(cursor.fetchone(), "application_id", 0)
                if _row_value(row, "state", 2) == "reserved":
                    cursor.execute("UPDATE metadata_query_result_deliveries SET state = 'released', finished_at = clock_timestamp() WHERE delivery_id = %s", (delivery,))
                    cursor.execute("UPDATE metadata_query_result_references SET state = 'ready' WHERE result_ref_id = %s", (result_ref,))
                    self._audit(cursor, application, "result", result_ref, "reserved", "ready", "stale_reservation_released")
                    released.append(str(delivery))
                else:
                    cursor.execute("UPDATE metadata_query_result_deliveries SET state = 'uncertain', finished_at = clock_timestamp() WHERE delivery_id = %s", (delivery,))
                    cursor.execute("UPDATE metadata_query_result_references SET state = 'uncertain' WHERE result_ref_id = %s", (result_ref,))
                    cursor.execute("UPDATE metadata_query_result_payloads SET payload = '{}'::jsonb, byte_count = 2, scrubbed_at = clock_timestamp() WHERE result_ref_id = %s", (result_ref,))
                    self._audit(cursor, application, "result", result_ref, "delivering", "uncertain", "stale_delivery_uncertain")
                    uncertain.append(str(delivery))
        return {"released": released, "uncertain": uncertain}

    def create_migration_plan(
        self,
        application_id: str,
        resource_kind: str,
        resource_id: str,
        resource_revision: int,
        layout_token: str,
        target: dict[str, Any],
        live_fingerprint: str,
        desired_fingerprint: str,
        private_payload: dict[str, Any],
        review_payload: dict[str, Any],
        review_digest: str,
        destructive: bool,
        *,
        adapter_kind: str,
        source_kind: str,
        ttl_seconds: int = 900,
        retention_seconds: int = 30 * 86400,
    ) -> dict[str, Any]:
        application = identity(application_id, "application_id")
        kind = identity(resource_kind, "resource_kind")
        if kind not in {"schema", "view", "materialized_view"}:
            raise MetadataStoreError("invalid_metadata", "resource_kind is invalid", status=400)
        adapter = identity(adapter_kind, "adapter_kind")
        if adapter not in {"full_schema", "view_mutation", "insert_rows"}:
            raise MetadataStoreError("invalid_metadata", "adapter_kind is invalid", status=400)
        source = identity(source_kind, "source_kind")
        if source not in {"normal", "ai"}:
            raise MetadataStoreError("invalid_metadata", "source_kind is invalid", status=400)
        resource = _bounded_text(resource_id, "resource_id", 256)
        revision = _nonnegative_int(resource_revision, "resource_revision")
        layout = _bounded_text(layout_token, "layout_token", 256)
        safe_target = _target(target)
        live = _digest(live_fingerprint, "live_fingerprint")
        desired = _digest(desired_fingerprint, "desired_fingerprint")
        private = bounded_json(private_payload, "private_payload", self.max_json_bytes)
        review = bounded_json(review_payload, "review_payload", self.max_json_bytes)
        digest = _digest(review_digest, "review_digest")
        if not secrets.compare_digest(digest, canonical_review_digest(review)):
            raise MetadataStoreError("review_digest_mismatch", "Review digest does not match the canonical review payload", status=400)
        if type(destructive) is not bool:
            raise MetadataStoreError("invalid_metadata", "destructive must be a boolean", status=400)
        if adapter == "full_schema" and not has_full_schema_completeness_proof(private, review, live, desired):
            raise MetadataStoreError("migration_plan_incomplete", "Durable full-schema plans require explicit completeness proof", status=409)
        ttl = _seconds(ttl_seconds, "ttl_seconds", maximum=86400)
        retention = _seconds(retention_seconds, "retention_seconds", maximum=365 * 86400)
        plan = uuid.uuid4()
        with self._transaction() as cursor:
            cursor.execute(
                """INSERT INTO metadata_migration_plans
                   (plan_id, application_id, resource_kind, resource_id, resource_revision, layout_token,
                    profile_id, database_name, namespace_name, profile_fingerprint,
                    connected_target_fingerprint, live_fingerprint, desired_fingerprint,
                     private_payload, review_payload, review_digest, destructive, expires_at,
                     adapter_kind, source_kind, retain_until)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s::jsonb, %s::jsonb, %s, %s,
                            clock_timestamp() + (%s * interval '1 second'), %s, %s,
                            clock_timestamp() + (%s * interval '1 second'))""",
                (plan, application, kind, resource, revision, layout, safe_target["profileId"],
                 safe_target["databaseName"], safe_target["namespaceName"], safe_target["profileFingerprint"],
                 safe_target["connectedTargetFingerprint"], live, desired, _json(private), _json(review), digest,
                  destructive, ttl, adapter, source, retention),
            )
        return {"planId": str(plan), "state": "ready", "reviewDigest": digest, "expiresInSeconds": ttl}

    def fail_migration_execution_before_mutation(
        self, execution_id: str, evidence: dict[str, Any],
    ) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        safe_evidence = bounded_json(evidence, "evidence", self.max_json_bytes)
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            current = _row_value(row, "state", 0)
            if current == "failed":
                return {"executionId": str(execution), "state": "failed", "transitionOwner": False}
            if current != "ready":
                raise MetadataStoreError("execution_transition_invalid", "Execution is not awaiting target validation", status=409)
            cursor.execute(
                "UPDATE metadata_migration_executions SET state = 'failed', commit_outcome = 'rolled_back', updated_at = clock_timestamp() WHERE execution_id = %s",
                (execution,),
            )
            self._migration_transition(cursor, execution, "ready", "failed", safe_evidence)
        return {"executionId": str(execution), "state": "failed", "commitOutcome": "rolled_back", "transitionOwner": True}

    def get_migration_status(self, plan_id: str) -> dict[str, Any]:
        plan = self.get_migration_plan(plan_id)
        plan_uuid = _uuid(plan_id, "plan_id")
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT e.*, s.sync_id, s.state AS sync_state, s.receipt AS sync_receipt
                   FROM metadata_migration_executions e
                   LEFT JOIN metadata_migration_syncs s USING (execution_id)
                   WHERE e.plan_id = %s""",
                (plan_uuid,),
            )
            row = cursor.fetchone()
        return {"plan": plan, "execution": None if row is None else _execution_record(row)}

    def get_migration_execution_context(self, execution_id: str) -> dict[str, Any]:
        execution = self.get_migration_execution(execution_id)
        return {"execution": execution, "plan": self.get_migration_plan(execution["planId"], include_private=True)}

    def get_migration_plan(self, plan_id: str, *, include_private: bool = False) -> dict[str, Any]:
        plan = _uuid(plan_id, "plan_id")
        if type(include_private) is not bool:
            raise MetadataStoreError("invalid_metadata", "include_private must be a boolean", status=400)
        with self._transaction(write=False) as cursor:
            cursor.execute("SELECT * FROM metadata_migration_plans WHERE plan_id = %s", (plan,))
            row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("plan_not_found", "Migration plan was not found", status=404)
        return _plan_record(row, include_private=include_private)

    def list_migration_plans(self, *, states: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        allowed_states = [identity(state, "state") for state in (states or [])]
        count = _limit(limit)
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT * FROM metadata_migration_plans
                   WHERE cardinality(%s::text[]) = 0 OR state = ANY(%s::text[])
                   ORDER BY created_at DESC, plan_id DESC LIMIT %s""",
                (allowed_states, allowed_states, count),
            )
            rows = cursor.fetchall()
        return [_plan_record(row, include_private=False) for row in rows]

    def create_migration_execution(
        self,
        plan_id: str,
        confirmed_review_digest: str,
        destructive_confirmed: bool,
    ) -> dict[str, Any]:
        plan = _uuid(plan_id, "plan_id")
        digest = _digest(confirmed_review_digest, "confirmed_review_digest")
        if type(destructive_confirmed) is not bool:
            raise MetadataStoreError("invalid_metadata", "destructive_confirmed must be a boolean", status=400)
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT review_payload, review_digest, destructive, state,
                          expires_at > clock_timestamp() AS current, adapter_kind,
                          private_payload, live_fingerprint, desired_fingerprint
                   FROM metadata_migration_plans WHERE plan_id = %s FOR UPDATE""",
                (plan,),
            )
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("plan_not_found", "Migration plan was not found", status=404)
            stored_digest = _row_value(row, "review_digest", 1)
            canonical = canonical_review_digest(_json_value(_row_value(row, "review_payload", 0)))
            if not secrets.compare_digest(stored_digest, canonical) or not secrets.compare_digest(digest, stored_digest):
                raise MetadataStoreError("review_digest_mismatch", "Confirmed review does not match the durable plan", status=409)
            if _row_value(row, "adapter_kind", 5) == "full_schema" and not has_full_schema_completeness_proof(
                _json_value(_row_value(row, "private_payload", 6)),
                _json_value(_row_value(row, "review_payload", 0)),
                _row_value(row, "live_fingerprint", 7), _row_value(row, "desired_fingerprint", 8),
            ):
                raise MetadataStoreError("migration_plan_incomplete", "Full-schema migration plan lacks explicit completeness proof", status=409)
            if _row_value(row, "destructive", 2) and not destructive_confirmed:
                raise MetadataStoreError("destructive_confirmation_required", "Destructive plan requires explicit confirmation", status=403)
            cursor.execute("SELECT execution_id, state FROM metadata_migration_executions WHERE plan_id = %s", (plan,))
            existing = cursor.fetchone()
            if existing is not None:
                return {"executionId": str(_row_value(existing, "execution_id", 0)), "state": _row_value(existing, "state", 1), "executionOwner": False}
            if _row_value(row, "state", 3) != "ready" or not _row_value(row, "current", 4):
                raise MetadataStoreError("plan_expired", "Migration plan has expired", status=409)
            execution = uuid.uuid4()
            cursor.execute(
                """INSERT INTO metadata_migration_executions
                   (execution_id, plan_id, confirmed_review_digest, destructive_confirmed)
                   VALUES (%s, %s, %s, %s)""",
                (execution, plan, digest, destructive_confirmed),
            )
            self._migration_transition(cursor, execution, None, "ready", {"reviewDigest": digest, "destructiveConfirmed": destructive_confirmed})
        return {"executionId": str(execution), "state": "ready", "executionOwner": True}

    def begin_migration_execution(self, execution_id: str, target_xid: str, target_identity: dict[str, Any]) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        xid = _bounded_text(target_xid, "target_xid", 128)
        target = bounded_json(target_identity, "target_identity", self.max_json_bytes)
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            state = _row_value(row, "state", 0)
            if state == "applying":
                if _row_value(row, "target_xid", 1) != xid or _json_value(_row_value(row, "target_identity", 2)) != target:
                    raise MetadataStoreError("execution_evidence_conflict", "Execution already has different target evidence", status=409)
                return {"executionId": str(execution), "state": state, "transitionOwner": False}
            if state != "ready":
                raise MetadataStoreError("execution_transition_invalid", "Execution cannot begin from its current state", status=409)
            cursor.execute(
                """UPDATE metadata_migration_executions SET state = 'applying', target_xid = %s,
                          target_identity = %s::jsonb, updated_at = clock_timestamp()
                   WHERE execution_id = %s""",
                (xid, _json(target), execution),
            )
            self._migration_transition(cursor, execution, "ready", "applying", {"targetXid": xid, "targetIdentity": target})
        return {"executionId": str(execution), "state": "applying", "transitionOwner": True}

    def record_migration_intended_result(self, execution_id: str, intended_result: dict[str, Any]) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        intended = bounded_json(intended_result, "intended_result", self.max_json_bytes)
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            if _row_value(row, "state", 0) != "applying":
                raise MetadataStoreError("execution_transition_invalid", "Intended result requires an applying execution", status=409)
            existing = _json_value(_row_value(row, "intended_result", 3))
            if existing is not None:
                if existing != intended:
                    raise MetadataStoreError("execution_evidence_conflict", "Execution already has a different intended result", status=409)
                return {"executionId": str(execution), "state": "applying", "recordOwner": False}
            cursor.execute("UPDATE metadata_migration_executions SET intended_result = %s::jsonb, updated_at = clock_timestamp() WHERE execution_id = %s",
                           (_json(intended), execution))
        return {"executionId": str(execution), "state": "applying", "recordOwner": True}

    def prepare_migration_reconciliation(
        self, execution_id: str, evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Move an interrupted applying execution to reconcile-only ownership."""
        execution = _uuid(execution_id, "execution_id")
        safe_evidence = bounded_json(evidence, "evidence", self.max_json_bytes)
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            current = _row_value(row, "state", 0)
            xid = _row_value(row, "target_xid", 1)
            identity_document = _json_value(_row_value(row, "target_identity", 2))
            intended = _json_value(_row_value(row, "intended_result", 3))
            reconciliation = _row_value(row, "reconciliation_status", 5)
            if current == "uncertain":
                return {
                    "executionId": str(execution), "state": current, "transitionOwner": False,
                    "targetXid": xid, "targetIdentity": identity_document,
                    "intendedResultPresent": intended is not None,
                    "manualRequired": reconciliation == "failed",
                }
            if current != "applying":
                raise MetadataStoreError("reconciliation_not_required", "Execution is not applying", status=409)
            manual = xid is None or identity_document is None
            status = "failed" if manual else "required"
            cursor.execute(
                """UPDATE metadata_migration_executions
                   SET state = 'uncertain', commit_outcome = 'uncertain', reconciliation_status = %s,
                       reconciliation_evidence = %s::jsonb, updated_at = clock_timestamp()
                   WHERE execution_id = %s""",
                (status, _json(safe_evidence), execution),
            )
            self._migration_transition(cursor, execution, "applying", "uncertain", safe_evidence)
        return {
            "executionId": str(execution), "state": "uncertain", "transitionOwner": True,
            "targetXid": xid, "targetIdentity": identity_document,
            "intendedResultPresent": intended is not None, "manualRequired": manual,
        }

    def require_manual_migration_reconciliation(
        self, execution_id: str, evidence: dict[str, Any],
    ) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        safe_evidence = bounded_json(evidence, "evidence", self.max_json_bytes)
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            if _row_value(row, "state", 0) != "uncertain":
                raise MetadataStoreError("reconciliation_not_required", "Execution is not uncertain", status=409)
            if _row_value(row, "reconciliation_status", 5) == "failed":
                return {"executionId": str(execution), "state": "uncertain", "manualRequired": True, "transitionOwner": False}
            cursor.execute(
                """UPDATE metadata_migration_executions
                   SET reconciliation_status = 'failed', reconciliation_evidence = %s::jsonb,
                       updated_at = clock_timestamp() WHERE execution_id = %s""",
                (_json(safe_evidence), execution),
            )
        return {"executionId": str(execution), "state": "uncertain", "manualRequired": True, "transitionOwner": True}

    def finish_migration_execution(
        self,
        execution_id: str,
        state: str,
        commit_outcome: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        if state not in _TERMINAL_MIGRATION_STATES or commit_outcome not in {"committed", "rolled_back", "uncertain"}:
            raise MetadataStoreError("invalid_metadata", "migration outcome is invalid", status=400)
        if (state, commit_outcome) not in {("succeeded", "committed"), ("failed", "rolled_back"), ("uncertain", "uncertain")}:
            raise MetadataStoreError("invalid_metadata", "migration state does not match commit outcome", status=400)
        safe_evidence = None if evidence is None else bounded_json(evidence, "evidence", self.max_json_bytes)
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            current = _row_value(row, "state", 0)
            current_outcome = _row_value(row, "commit_outcome", 4)
            if current == state:
                if current_outcome != commit_outcome:
                    raise MetadataStoreError("execution_outcome_conflict", "Execution has a different terminal outcome", status=409)
                return {"executionId": str(execution), "state": state, "transitionOwner": False}
            if current != "applying":
                raise MetadataStoreError("execution_transition_invalid", "Execution is not applying", status=409)
            if state == "succeeded" and _row_value(row, "intended_result", 3) is None:
                raise MetadataStoreError("intended_result_required", "Committed execution requires a durable intended result", status=409)
            reconciliation = "required" if state == "uncertain" else "not_required"
            cursor.execute(
                """UPDATE metadata_migration_executions SET state = %s, commit_outcome = %s,
                          reconciliation_status = %s, updated_at = clock_timestamp()
                   WHERE execution_id = %s""",
                (state, commit_outcome, reconciliation, execution),
            )
            self._migration_transition(cursor, execution, "applying", state, safe_evidence)
        return {"executionId": str(execution), "state": state, "commitOutcome": commit_outcome, "transitionOwner": True}

    def reconcile_migration_execution(
        self,
        execution_id: str,
        commit_outcome: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        if commit_outcome not in {"committed", "rolled_back"}:
            raise MetadataStoreError("invalid_metadata", "reconciled commit outcome is invalid", status=400)
        safe_evidence = bounded_json(evidence, "evidence", self.max_json_bytes)
        target_state = "succeeded" if commit_outcome == "committed" else "failed"
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            current = _row_value(row, "state", 0)
            status = _row_value(row, "reconciliation_status", 5)
            if current == target_state and status == "reconciled":
                if _row_value(row, "commit_outcome", 4) != commit_outcome or _json_value(_row_value(row, "reconciliation_evidence", 6)) != safe_evidence:
                    raise MetadataStoreError("reconciliation_conflict", "Execution was reconciled with different evidence", status=409)
                return {"executionId": str(execution), "state": target_state, "commitOutcome": commit_outcome, "reconciliationOwner": False}
            if current != "uncertain" or status not in {"required", "reconciling"}:
                raise MetadataStoreError("reconciliation_not_required", "Execution is not awaiting reconciliation", status=409)
            if commit_outcome == "committed" and _row_value(row, "intended_result", 3) is None:
                raise MetadataStoreError("intended_result_required", "Committed reconciliation requires a durable intended result", status=409)
            cursor.execute(
                """UPDATE metadata_migration_executions SET state = %s, commit_outcome = %s,
                          reconciliation_status = 'reconciled', reconciliation_evidence = %s::jsonb,
                          updated_at = clock_timestamp() WHERE execution_id = %s""",
                (target_state, commit_outcome, _json(safe_evidence), execution),
            )
            self._migration_transition(cursor, execution, "uncertain", target_state, safe_evidence)
        return {"executionId": str(execution), "state": target_state, "commitOutcome": commit_outcome, "reconciliationOwner": True}

    def record_migration_sync(
        self,
        execution_id: str,
        state: str,
        *,
        receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        if state not in {"pending", "succeeded", "conflict", "failed"}:
            raise MetadataStoreError("invalid_metadata", "sync state is invalid", status=400)
        safe_receipt = None if receipt is None else bounded_json(receipt, "receipt", self.max_json_bytes)
        if state != "pending" and safe_receipt is None:
            raise MetadataStoreError("invalid_metadata", "terminal sync requires a receipt", status=400)
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            if _row_value(row, "state", 0) != "succeeded" or _row_value(row, "commit_outcome", 4) != "committed":
                raise MetadataStoreError("sync_not_allowed", "Only committed executions can synchronize resources", status=409)
            cursor.execute("SELECT sync_id, state, receipt FROM metadata_migration_syncs WHERE execution_id = %s FOR UPDATE", (execution,))
            existing = cursor.fetchone()
            if existing is None:
                sync = uuid.uuid4()
                cursor.execute("INSERT INTO metadata_migration_syncs (sync_id, execution_id, state, receipt) VALUES (%s, %s, %s, %s::jsonb)",
                               (sync, execution, state, None if safe_receipt is None else _json(safe_receipt)))
                return {"syncId": str(sync), "executionId": str(execution), "state": state, "transitionOwner": True}
            sync = _row_value(existing, "sync_id", 0)
            current = _row_value(existing, "state", 1)
            if current == state:
                if _json_value(_row_value(existing, "receipt", 2)) != safe_receipt:
                    raise MetadataStoreError("sync_conflict", "Sync state already has a different receipt", status=409)
                return {"syncId": str(sync), "executionId": str(execution), "state": state, "transitionOwner": False}
            if current != "pending" or state == "pending":
                raise MetadataStoreError("sync_transition_invalid", "Sync cannot transition from its current state", status=409)
            cursor.execute("UPDATE metadata_migration_syncs SET state = %s, receipt = %s::jsonb, updated_at = clock_timestamp() WHERE sync_id = %s",
                           (state, _json(safe_receipt), sync))
        return {"syncId": str(sync), "executionId": str(execution), "state": state, "transitionOwner": True}

    def get_migration_execution(self, execution_id: str) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT e.*, s.sync_id, s.state AS sync_state, s.receipt AS sync_receipt
                   FROM metadata_migration_executions e
                   LEFT JOIN metadata_migration_syncs s USING (execution_id)
                   WHERE e.execution_id = %s""",
                (execution,),
            )
            row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("execution_not_found", "Migration execution was not found", status=404)
        return _execution_record(row)

    def list_migration_executions(self, *, states: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        allowed_states = [identity(state, "state") for state in (states or [])]
        count = _limit(limit)
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT e.*, s.sync_id, s.state AS sync_state, s.receipt AS sync_receipt
                   FROM metadata_migration_executions e LEFT JOIN metadata_migration_syncs s USING (execution_id)
                   WHERE cardinality(%s::text[]) = 0 OR e.state = ANY(%s::text[])
                   ORDER BY e.created_at DESC, e.execution_id DESC LIMIT %s""",
                (allowed_states, allowed_states, count),
            )
            rows = cursor.fetchall()
        return [_execution_record(row) for row in rows]

    def list_transitions(self, aggregate_kind: str, aggregate_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        kind = identity(aggregate_kind, "aggregate_kind")
        aggregate = _uuid(aggregate_id, "aggregate_id")
        count = _limit(limit)
        with self._transaction(write=False) as cursor:
            if kind == "migration":
                cursor.execute("SELECT transition_id, from_state, to_state, evidence, created_at FROM metadata_migration_transitions WHERE execution_id = %s ORDER BY transition_id DESC LIMIT %s", (aggregate, count))
            elif kind in {"chat", "grant", "proposal", "operation", "result"}:
                cursor.execute("SELECT transition_id, from_state, to_state, reason, created_at FROM metadata_authority_transitions WHERE aggregate_kind = %s AND aggregate_id = %s ORDER BY transition_id DESC LIMIT %s", (kind, aggregate, count))
            else:
                raise MetadataStoreError("invalid_metadata", "aggregate_kind is invalid", status=400)
            rows = cursor.fetchall()
        if kind == "migration":
            return [{"transitionId": int(_row_value(row, "transition_id", 0)), "fromState": _row_value(row, "from_state", 1),
                     "toState": _row_value(row, "to_state", 2), "evidence": _json_value(_row_value(row, "evidence", 3)),
                     "createdAt": _iso_datetime(_row_value(row, "created_at", 4))} for row in rows]
        return [{"transitionId": int(_row_value(row, "transition_id", 0)), "fromState": _row_value(row, "from_state", 1),
                 "toState": _row_value(row, "to_state", 2), "reason": _row_value(row, "reason", 3),
                 "createdAt": _iso_datetime(_row_value(row, "created_at", 4))} for row in rows]

    def cleanup(self, *, before: datetime, limit: int = 1000) -> dict[str, int]:
        cutoff = _aware_datetime(before, "before")
        count = _limit(limit, maximum=10000)
        deleted: dict[str, int] = {}
        with self._transaction() as cursor:
            cursor.execute(
                """UPDATE metadata_migration_plans p
                   SET private_payload = '{}'::jsonb, private_payload_redacted_at = clock_timestamp()
                   WHERE p.plan_id IN (
                     SELECT p2.plan_id FROM metadata_migration_plans p2
                     JOIN metadata_migration_executions e ON e.plan_id = p2.plan_id
                     WHERE (e.state = 'failed' OR (e.state = 'succeeded' AND EXISTS (
                          SELECT 1 FROM metadata_migration_syncs s
                          WHERE s.execution_id = e.execution_id AND s.state IN ('succeeded', 'conflict', 'failed')
                       ))) AND p2.private_payload_redacted_at IS NULL AND p2.retain_until < %s
                     ORDER BY p2.retain_until LIMIT %s FOR UPDATE OF p2 SKIP LOCKED
                   )""",
                (cutoff, count),
            )
            deleted["planPayloadsRedacted"] = max(0, int(cursor.rowcount))
            # Chat-owned authority and result rows cascade atomically. Application-scoped
            # authority transitions deliberately have no aggregate FK and remain as audit evidence.
            for name, sql in (
                ("results", "DELETE FROM metadata_query_result_references WHERE result_ref_id IN (SELECT result_ref_id FROM metadata_query_result_references WHERE state IN ('consumed', 'uncertain', 'expired', 'revoked') AND expires_at < %s ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED)"),
                ("plans", "DELETE FROM metadata_migration_plans WHERE plan_id IN (SELECT p.plan_id FROM metadata_migration_plans p LEFT JOIN metadata_migration_executions e USING (plan_id) WHERE e.execution_id IS NULL AND p.expires_at < %s ORDER BY p.expires_at LIMIT %s FOR UPDATE OF p SKIP LOCKED)"),
                ("chats", "DELETE FROM metadata_chats WHERE chat_id IN (SELECT chat_id FROM metadata_chats WHERE state = 'deleted' AND deleted_at < %s ORDER BY deleted_at LIMIT %s FOR UPDATE SKIP LOCKED)"),
            ):
                cursor.execute(sql, (cutoff, count))
                deleted[name] = max(0, int(cursor.rowcount))
        return deleted

    def _transition_chat(self, chat_id: str, allowed: set[str], target: str, reason: str, *, idempotent: bool = False) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        safe_reason = _bounded_text(reason, "reason", 256)
        with self._transaction() as cursor:
            row = self._lock_chat(cursor, chat)
            current = _row_value(row, "state", 1)
            if idempotent and current == target:
                return {"chatId": str(chat), "state": target, "transitionOwner": False}
            if current not in allowed:
                raise MetadataStoreError("chat_transition_invalid", "Chat cannot transition from its current state", status=409)
            cursor.execute(
                """UPDATE metadata_chats SET state = %s, updated_at = clock_timestamp(),
                          deleted_at = CASE WHEN %s = 'deleted' THEN clock_timestamp() ELSE deleted_at END
                   WHERE chat_id = %s""",
                (target, target, chat),
            )
            self._audit(cursor, _row_value(row, "application_id", 0), "chat", chat, current, target, safe_reason)
        return {"chatId": str(chat), "state": target, "transitionOwner": True}

    def _lock_chat(self, cursor: Any, chat_id: uuid.UUID) -> Any:
        cursor.execute("SELECT application_id, state FROM metadata_chats WHERE chat_id = %s FOR UPDATE", (chat_id,))
        row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("chat_not_found", "Chat was not found", status=404)
        return row

    def _application_for_chat(self, cursor: Any, chat_id: Any) -> str:
        cursor.execute("SELECT application_id FROM metadata_chats WHERE chat_id = %s", (chat_id,))
        row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("metadata_invariant", "Authority aggregate has no chat")
        return str(_row_value(row, "application_id", 0))

    def _validate_agent_policy_link(self, cursor: Any, application: str, binding: dict[str, Any] | None) -> None:
        if binding is None:
            return
        cursor.execute(
            """SELECT 1 FROM metadata_agent_policy_revisions
               WHERE agent_policy_revision_id = %s AND application_id = %s AND schema_version = %s""",
            (binding["policyRevisionId"], application, binding["schemaVersion"]),
        )
        if cursor.fetchone() is None:
            raise MetadataStoreError("agent_policy_changed", "Chat policy must link an application-owned agent policy revision", status=409)

    def _validate_effective_proposal_binding(
        self, cursor: Any, chat_id: Any, capability: str, revision: int, binding: dict[str, Any],
    ) -> None:
        policy_binding = binding.get("policyBinding")
        if not isinstance(policy_binding, dict):
            raise MetadataStoreError("authority_binding_mismatch", "Proposal policy binding is incomplete", status=409)
        cursor.execute(
            """SELECT c.application_id, c.resource_kind, c.resource_id, v.policy,
                      v.agent_policy_revision_id, v.agent_policy_schema_version,
                      t.profile_id, t.database_name, t.namespace_name, t.profile_fingerprint
               FROM metadata_chats c JOIN metadata_policy_versions v ON v.chat_id = c.chat_id
               LEFT JOIN metadata_targets t ON t.chat_id = c.chat_id
               WHERE c.chat_id = %s AND v.revision = %s""",
            (chat_id, revision),
        )
        row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("policy_changed", "Proposal chat policy no longer exists", status=409)
        snapshot = _json_value(_row_value(row, "policy", 3))
        resource = policy_binding.get("resource")
        target = policy_binding.get("target")
        durable_target = {} if _row_optional(row, "profile_id", 6) is None else {
            "profileId": _row_optional(row, "profile_id", 6), "database": _row_optional(row, "database_name", 7),
            "namespace": _row_optional(row, "namespace_name", 8), "profileFingerprint": _row_optional(row, "profile_fingerprint", 9),
        }
        authority = snapshot.get("capabilities", {}).get(capability)
        expected = {
            "application": _row_value(row, "application_id", 0), "agentId": snapshot.get("agentId"),
            "agentPolicyRevision": snapshot.get("agentPolicyRevision"),
            "agentPolicyRevisionId": str(_row_value(row, "agent_policy_revision_id", 4)),
            "agentPolicySchemaVersion": _row_value(row, "agent_policy_schema_version", 5),
            "chatPolicyRevision": revision, "policyRevision": revision,
            "canonicalCapability": capability, "capability": capability,
            "configuredMode": None if authority is None else authority.get("configuredMode"),
            "effectiveMode": None if authority is None else authority.get("effectiveMode"),
            "safetyFloorReason": None if authority is None else authority.get("safetyFloorReason"),
            "snapshot": snapshot, "disclosureClass": snapshot.get("disclosureClass"),
        }
        allowed_fields = set(expected) | {"origin", "resource", "target"}
        if set(policy_binding) != allowed_fields or any(policy_binding.get(key) != value for key, value in expected.items()):
            raise MetadataStoreError("authority_binding_mismatch", "Proposal policy binding does not match durable authority", status=409)
        if not isinstance(resource, dict) or resource.get("kind") != _row_value(row, "resource_kind", 1) or resource.get("id") != _row_value(row, "resource_id", 2):
            raise MetadataStoreError("authority_binding_mismatch", "Proposal resource binding does not match durable authority", status=409)
        if target != durable_target or binding.get("authorizationTarget") != durable_target:
            raise MetadataStoreError("authority_binding_mismatch", "Proposal target binding does not match durable authority", status=409)
        if resource.get("revision") != binding.get("schemaConcurrency", {}).get("revision") or resource.get("layoutToken") != binding.get("schemaConcurrency", {}).get("layoutToken"):
            raise MetadataStoreError("authority_binding_mismatch", "Proposal resource revision binding is inconsistent", status=409)
        cursor.execute(
            "SELECT current_revision FROM metadata_agent_settings WHERE application_id = %s AND agent_id = %s",
            (snapshot.get("application"), snapshot.get("agentId")),
        )
        current = cursor.fetchone()
        if current is None or int(_row_value(current, "current_revision", 0)) != snapshot.get("agentPolicyRevision"):
            raise MetadataStoreError("agent_policy_changed", "AI agent settings changed; start a new chat", status=409)

    def _audit(self, cursor: Any, application_id: str, kind: str, aggregate_id: Any, from_state: str | None, to_state: str, reason: str) -> None:
        cursor.execute(
            """INSERT INTO metadata_authority_transitions
               (application_id, aggregate_kind, aggregate_id, from_state, to_state, reason)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (application_id, kind, aggregate_id, from_state, to_state, reason),
        )

    def _lock_execution(self, cursor: Any, execution_id: uuid.UUID) -> Any:
        cursor.execute(
            """SELECT state, target_xid, target_identity, intended_result, commit_outcome,
                      reconciliation_status, reconciliation_evidence
               FROM metadata_migration_executions WHERE execution_id = %s FOR UPDATE""",
            (execution_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("execution_not_found", "Migration execution was not found", status=404)
        return row

    def _migration_transition(self, cursor: Any, execution_id: uuid.UUID, from_state: str | None, to_state: str, evidence: Any) -> None:
        cursor.execute(
            "INSERT INTO metadata_migration_transitions (execution_id, from_state, to_state, evidence) VALUES (%s, %s, %s, %s::jsonb)",
            (execution_id, from_state, to_state, None if evidence is None else _json(evidence)),
        )

    def _touch_attempt(self, attempt_id: str, token: str, *, finish_state: str | None, result: Any, error: Any, lease_seconds: int | None) -> dict[str, Any]:
        attempt = _uuid(attempt_id, "attempt_id")
        with self._transaction() as cursor:
            cursor.execute("SELECT operation_id, state, claim_token_hash, lease_expires_at FROM metadata_operation_attempts WHERE attempt_id = %s FOR UPDATE", (attempt,))
            row = cursor.fetchone()
            if row is None or not secrets.compare_digest(str(_row_value(row, "claim_token_hash", 2)), _token_hash(token)):
                raise MetadataStoreError("invalid_claim", "Execution claim is invalid", status=409)
            operation_id = _row_value(row, "operation_id", 0)
            if finish_state in {"succeeded", "failed"}:
                cursor.execute(
                    """SELECT p.cancellation_requested_at FROM metadata_operations o
                       JOIN metadata_proposals p USING (proposal_id) WHERE o.operation_id = %s FOR UPDATE OF p""",
                    (operation_id,),
                )
                cancellation = cursor.fetchone()
                if cancellation is not None and _row_optional(cancellation, "cancellation_requested_at", 0) is not None:
                    finish_state = "cancelled"
                    result = None
                    error = {"code": "execution_cancelled", "message": "AI query was cancelled"}
                    self._scrub_operation_results(cursor, operation_id, "operation_cancelled")
            if _row_value(row, "state", 1) != "running":
                if finish_state is not None and _row_value(row, "state", 1) == finish_state:
                    operation_id = _row_value(row, "operation_id", 0)
                    cursor.execute("SELECT state, result, error FROM metadata_operation_outcomes WHERE operation_id = %s", (operation_id,))
                    outcome = cursor.fetchone()
                    if outcome is not None and _json_value(_row_value(outcome, "result", 1)) == result and _json_value(_row_value(outcome, "error", 2)) == error:
                        return {"operationId": str(operation_id), "attemptId": str(attempt), "state": finish_state,
                                "result": result, "error": error, "resolutionOwner": False}
                raise MetadataStoreError("operation_not_running", "Execution attempt is no longer running", status=409)
            if finish_state is None:
                cursor.execute(
                    """UPDATE metadata_operation_attempts SET heartbeat_at = clock_timestamp(),
                              lease_expires_at = clock_timestamp() + (%s * interval '1 second')
                       WHERE attempt_id = %s AND lease_expires_at >= clock_timestamp()""",
                    (lease_seconds, attempt),
                )
                if cursor.rowcount != 1:
                    raise MetadataStoreError("operation_lease_expired", "Execution lease expired; reconcile without replay", status=409)
                return {"attemptId": str(attempt), "state": "running"}
            cursor.execute(
                """UPDATE metadata_operation_attempts SET state = %s, heartbeat_at = clock_timestamp(),
                          finished_at = clock_timestamp() WHERE attempt_id = %s
                       AND lease_expires_at >= clock_timestamp()""",
                (finish_state, attempt),
            )
            if cursor.rowcount != 1:
                raise MetadataStoreError("operation_lease_expired", "Execution lease expired; reconcile without replay", status=409)
            cursor.execute(
                "INSERT INTO metadata_operation_outcomes (outcome_id, operation_id, state, result, error) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)",
                (uuid.uuid4(), operation_id, finish_state, None if result is None else _json(result), None if error is None else _json(error)),
            )
            cursor.execute("UPDATE metadata_operations SET state = %s, updated_at = clock_timestamp() WHERE operation_id = %s", (finish_state, operation_id))
            cursor.execute("SELECT chat_id FROM metadata_operations WHERE operation_id = %s", (operation_id,))
            chat_id = _row_value(cursor.fetchone(), "chat_id", 0)
            self._audit(cursor, self._application_for_chat(cursor, chat_id), "operation", operation_id, "running", finish_state, "operation_finished")
        return {"operationId": str(operation_id), "attemptId": str(attempt), "state": finish_state,
                "result": result, "error": error}

    @staticmethod
    def _scrub_operation_results(cursor: Any, operation_id: uuid.UUID, reason: str) -> None:
        cursor.execute(
            """UPDATE metadata_query_result_references SET state = 'revoked', revoked_at = clock_timestamp(),
                      revocation_reason = %s
               WHERE state = 'ready' AND binding ? 'operationId' AND binding ->> 'operationId' = %s""",
            (reason, str(operation_id)),
        )
        cursor.execute(
            """UPDATE metadata_query_result_payloads p SET payload = '{}'::jsonb, byte_count = 2,
                      scrubbed_at = clock_timestamp()
               FROM metadata_query_result_references r
               WHERE p.result_ref_id = r.result_ref_id AND r.binding ? 'operationId'
                 AND r.binding ->> 'operationId' = %s""",
            (str(operation_id),),
        )

    def _result_transition(self, delivery_id: str, token: str, required: str, target: str, *, scrub: bool = False) -> dict[str, Any]:
        delivery = _uuid(delivery_id, "delivery_id")
        with self._transaction() as cursor:
            cursor.execute("SELECT result_ref_id, state, reservation_token_hash FROM metadata_query_result_deliveries WHERE delivery_id = %s FOR UPDATE", (delivery,))
            row = cursor.fetchone()
            if row is None or not secrets.compare_digest(str(_row_value(row, "reservation_token_hash", 2)), _token_hash(token)):
                raise MetadataStoreError("invalid_result_reservation", "Query result reservation is invalid", status=409)
            if _row_value(row, "state", 1) != required:
                raise MetadataStoreError("result_delivery_changed", "Query result delivery state changed", status=409)
            result_ref = _row_value(row, "result_ref_id", 0)
            cursor.execute(
                """UPDATE metadata_query_result_deliveries SET state = %s,
                       dispatch_started_at = CASE WHEN %s = 'delivering' THEN clock_timestamp() ELSE dispatch_started_at END,
                       finished_at = CASE WHEN %s <> 'delivering' THEN clock_timestamp() ELSE finished_at END
                   WHERE delivery_id = %s""",
                (target, target, target, delivery),
            )
            reference_state = "ready" if target == "released" else target
            cursor.execute("UPDATE metadata_query_result_references SET state = %s WHERE result_ref_id = %s", (reference_state, result_ref))
            if scrub:
                cursor.execute("UPDATE metadata_query_result_payloads SET payload = '{}'::jsonb, byte_count = 2, scrubbed_at = clock_timestamp() WHERE result_ref_id = %s", (result_ref,))
            cursor.execute("SELECT c.application_id FROM metadata_query_result_references r JOIN metadata_chats c USING (chat_id) WHERE r.result_ref_id = %s", (result_ref,))
            application = _row_value(cursor.fetchone(), "application_id", 0)
            self._audit(cursor, application, "result", result_ref, required, reference_state, f"delivery_{target}")
        return {"deliveryId": str(delivery), "resultRefId": str(result_ref), "state": target}

    @contextmanager
    def _transaction(self, *, write: bool = True) -> Iterator[Any]:
        connection = None
        cursor = None
        try:
            connection = self.connection_factory()
            cursor = connection.cursor()
            try:
                yield cursor
            except MetadataStoreError:
                connection.rollback()
                raise
            except Exception as exc:
                connection.rollback()
                raise _database_error(exc) from exc
            if write:
                try:
                    connection.commit()
                except Exception as exc:
                    raise MetadataStoreError(
                        "metadata_commit_uncertain",
                        "Server metadata commit outcome is uncertain; reconcile before retrying",
                        status=503,
                        retryable=False,
                    ) from exc
            else:
                connection.rollback()
        except MetadataStoreError:
            raise
        except Exception as exc:
            raise _database_error(exc) from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()


def _uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400) from exc


def _agent_policy_binding(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"policyRevisionId", "schemaVersion"}:
        raise MetadataStoreError("invalid_metadata", "agent policy binding fields are invalid", status=400)
    schema_version = value["schemaVersion"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
        raise MetadataStoreError("invalid_metadata", "agent policy schemaVersion is invalid", status=400)
    return {"policyRevisionId": _uuid(value["policyRevisionId"], "policy_revision_id"), "schemaVersion": schema_version}


def _console_settings_record(row: Any) -> dict[str, Any]:
    return {
        "application": _row_value(row, "application_id", 0),
        "revision": int(_row_value(row, "revision", 1)),
        "writeIntent": _row_value(row, "write_intent", 2),
        "defaultMode": _row_value(row, "default_mode", 3),
        "statementLimit": int(_row_value(row, "statement_limit", 4)),
        "rowPageSize": int(_row_value(row, "row_page_size", 5)),
        "inheritance": "none",
        "maxima": {"statementLimit": 100, "rowPageSize": 500},
        "createdAt": _iso_datetime(_row_value(row, "created_at", 6)),
        "updatedAt": _iso_datetime(_row_value(row, "updated_at", 7)),
    }


def canonical_review_digest(review_payload: dict[str, Any]) -> str:
    """Return the digest of the single canonical JSON representation used for review."""
    if not isinstance(review_payload, dict):
        raise MetadataStoreError("invalid_metadata", "review_payload must be an object", status=400)
    encoded = json.dumps(review_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bounded_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400)
    return value


def _chat_title(value: Any) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or len(value) > 80 or len(value.encode("utf-8")) > 80
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise MetadataStoreError("invalid_metadata", "conversation_title is invalid", status=400)
    return value


def _console_receipt_values(row: Any) -> tuple[Any, ...]:
    names = (
        "application_id", "session_binding_hash", "server_id", "profile_id", "profile_fingerprint",
        "database_name", "namespace_name", "console_id", "mode", "settings_revision", "state", "outcome",
        "completed_statement_indexes", "error_code", "postgres_evidence", "reconciliation_evidence",
    )
    values = tuple(_row_value(row, name, index) for index, name in enumerate(names))
    return (*values[:14], None if values[14] is None else _json(_json_value(values[14])),
            None if values[15] is None else _json(_json_value(values[15])))


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in _HEX_DIGEST for character in value):
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400)
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400)
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400)
    return value


def _seconds(value: Any, field: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400)
    return value


def _limit(value: Any, maximum: int = 1000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise MetadataStoreError("invalid_metadata", "limit is invalid", status=400)
    return value


def _aware_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MetadataStoreError("invalid_metadata", f"{field} must be timezone-aware", status=400)
    return value.astimezone(timezone.utc)


def _target(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "profileId", "databaseName", "namespaceName", "profileFingerprint", "connectedTargetFingerprint",
    }:
        raise MetadataStoreError("invalid_metadata", "target must contain the exact target binding", status=400)
    return {
        "profileId": _bounded_text(value["profileId"], "profile_id", 256),
        "databaseName": _bounded_text(value["databaseName"], "database_name", 63),
        "namespaceName": _bounded_text(value["namespaceName"], "namespace_name", 63),
        "profileFingerprint": _digest(value["profileFingerprint"], "profile_fingerprint"),
        "connectedTargetFingerprint": _digest(value["connectedTargetFingerprint"], "connected_target_fingerprint"),
    }


def _chat_record(row: Any) -> dict[str, Any]:
    target_id = _row_value(row, "target_id", 9)
    target = None if target_id is None else {
        "targetId": str(target_id), "profileId": _row_value(row, "profile_id", 10),
        "databaseName": _row_value(row, "database_name", 11), "namespaceName": _row_value(row, "namespace_name", 12),
        "profileFingerprint": _row_value(row, "profile_fingerprint", 13),
        "connectedTargetFingerprint": _row_value(row, "connected_target_fingerprint", 14),
    }
    return {"chatId": str(_row_value(row, "chat_id", 0)), "applicationId": _row_value(row, "application_id", 1),
            "resourceKind": _row_value(row, "resource_kind", 2), "resourceId": _row_value(row, "resource_id", 3),
            "externalSessionId": _row_value(row, "external_session_id", 4), "state": _row_value(row, "state", 5),
            "createdAt": _iso_datetime(_row_value(row, "created_at", 6)),
            "updatedAt": _iso_datetime(_row_value(row, "updated_at", 7)),
            "deletedAt": _iso_datetime(_row_value(row, "deleted_at", 8)), "target": target,
            "displayTitle": _row_value(row, "display_title", 15),
            "conversationTitle": _row_optional(row, "conversation_title", 16)}


def _proposal_record(row: Any) -> dict[str, Any]:
    return {"proposalId": str(_row_value(row, "proposal_id", 0)), "chatId": str(_row_value(row, "chat_id", 1)),
            "capability": _row_value(row, "capability", 2), "policyRevision": int(_row_value(row, "policy_revision", 3)),
            "binding": _json_value(_row_value(row, "binding", 4)), "action": _json_value(_row_value(row, "action", 5)),
            "state": _row_value(row, "state", 6), "createdAt": _iso_datetime(_row_value(row, "created_at", 7)),
            "expiresAt": _iso_datetime(_row_value(row, "expires_at", 8)),
            "revokedAt": _iso_datetime(_row_optional(row, "revoked_at", 9)),
            "revocationReason": _row_optional(row, "revocation_reason", 10),
            "revocationEvidence": _json_value(_row_optional(row, "revocation_evidence", 11)),
            "cancellationRequestedAt": _iso_datetime(_row_optional(row, "cancellation_requested_at", 12))}


def _operation_record(row: Any) -> dict[str, Any]:
    return {"operationId": str(_row_value(row, "operation_id", 0)),
            "proposalId": None if _row_value(row, "proposal_id", 1) is None else str(_row_value(row, "proposal_id", 1)),
            "chatId": str(_row_value(row, "chat_id", 2)), "capability": _row_value(row, "capability", 3),
            "state": _row_value(row, "state", 4), "createdAt": _iso_datetime(_row_value(row, "created_at", 5)),
            "updatedAt": _iso_datetime(_row_value(row, "updated_at", 6)),
            "attempt": None if _row_value(row, "attempt_id", 7) is None else {
                "attemptId": str(_row_value(row, "attempt_id", 7)), "workerId": _row_value(row, "worker_id", 8),
                "leaseExpiresAt": _iso_datetime(_row_value(row, "lease_expires_at", 9))},
            "outcome": None if _row_value(row, "outcome_state", 10) is None else {
                "state": _row_value(row, "outcome_state", 10), "result": _json_value(_row_value(row, "result", 11)),
                "error": _json_value(_row_value(row, "error", 12))},
            "cancellationRequested": _row_optional(row, "cancellation_requested_at", 13) is not None}


def _plan_record(row: Any, *, include_private: bool) -> dict[str, Any]:
    names = ("plan_id", "application_id", "resource_kind", "resource_id", "resource_revision", "layout_token",
             "profile_id", "database_name", "namespace_name", "profile_fingerprint", "connected_target_fingerprint",
             "live_fingerprint", "desired_fingerprint", "private_payload", "review_payload", "review_digest", "destructive",
              "state", "created_at", "expires_at", "adapter_kind", "source_kind", "retain_until",
              "private_payload_redacted_at")
    values = {name: _row_value(row, name, index) for index, name in enumerate(names)}
    record = {"planId": str(values["plan_id"]), "applicationId": values["application_id"],
              "resourceKind": values["resource_kind"], "resourceId": values["resource_id"],
              "resourceRevision": int(values["resource_revision"]), "layoutToken": values["layout_token"],
              "target": {"profileId": values["profile_id"], "databaseName": values["database_name"],
                         "namespaceName": values["namespace_name"], "profileFingerprint": values["profile_fingerprint"],
                         "connectedTargetFingerprint": values["connected_target_fingerprint"]},
              "liveFingerprint": values["live_fingerprint"], "desiredFingerprint": values["desired_fingerprint"],
              "reviewPayload": _json_value(values["review_payload"]), "reviewDigest": values["review_digest"],
              "destructive": values["destructive"], "state": values["state"],
              "adapterKind": values["adapter_kind"], "sourceKind": values["source_kind"],
              "createdAt": _iso_datetime(values["created_at"]), "expiresAt": _iso_datetime(values["expires_at"]),
              "retainUntil": _iso_datetime(values["retain_until"]),
              "privatePayloadRedactedAt": _iso_datetime(values["private_payload_redacted_at"])}
    if include_private:
        record["privatePayload"] = _json_value(values["private_payload"])
    return record


def _execution_record(row: Any) -> dict[str, Any]:
    def value(name: str, index: int) -> Any:
        return _row_value(row, name, index)
    return {"executionId": str(value("execution_id", 0)), "planId": str(value("plan_id", 1)),
            "state": value("state", 2), "confirmedReviewDigest": value("confirmed_review_digest", 3),
            "destructiveConfirmed": value("destructive_confirmed", 4), "targetXid": value("target_xid", 5),
            "targetIdentity": _json_value(value("target_identity", 6)), "intendedResult": _json_value(value("intended_result", 7)),
            "commitOutcome": value("commit_outcome", 8), "createdAt": _iso_datetime(value("created_at", 9)),
            "updatedAt": _iso_datetime(value("updated_at", 10)),
            "reconciliationStatus": value("reconciliation_status", 11),
            "reconciliationEvidence": _json_value(value("reconciliation_evidence", 12)),
            "sync": None if value("sync_id", 13) is None else {"syncId": str(value("sync_id", 13)),
                    "state": value("sync_state", 14), "receipt": _json_value(value("sync_receipt", 15))}}


def _token_hash(token: Any) -> str:
    if not isinstance(token, str) or not token:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _iso_datetime(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def _row_value(row: Any, name: str, index: int) -> Any:
    return row[name] if isinstance(row, dict) else row[index]


def _row_optional(row: Any, name: str, index: int) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return row[index] if index < len(row) else None


def _database_error(exc: Exception) -> MetadataStoreError:
    try:
        from psycopg import OperationalError
    except ImportError:
        OperationalError = ()
    if isinstance(exc, OperationalError):
        return MetadataStoreError("metadata_unavailable", "Server metadata PostgreSQL is unavailable", status=503, retryable=True)
    return MetadataStoreError("metadata_store_failed", "Server metadata rejected an internal operation", retryable=False)
