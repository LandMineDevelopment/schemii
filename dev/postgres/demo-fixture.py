"""Rebuild the application-owned half of one isolated demo scenario."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import psycopg

from schemii.common.metadata.config import MetadataConfig
from schemii.common.metadata.crypto import CredentialCipher
from schemii.common.metadata.factory import MetadataRepositories
from schemii.common.metadata.models import LOCAL_PROTOTYPE_USER_ID
from schemii.common.metadata.secrets import read_encryption_key, read_secret_file
from schemii.common.metadata.users import ensure_local_metadata_user
from schemii.main import create_services
from schemii.schemii.designs.importer import import_postgres_catalog
from schemii.schemii.designs.models import SchemiiDesignContent, SchemiiDesignReplace
from schemii.schemii.workspaces.models import WorkspaceCreateRecord


FIXTURE_CONNECTION_ID = "pg_" + hashlib.sha256(
    b"schemii:developer-fixture:migration-demo:connection:v1"
).hexdigest()[:32]
SCENARIO_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SOURCE_REVISION = re.compile(r"^(?:[0-9a-f]{40}|unknown)(?:\+dirty)?$")


class FixtureError(RuntimeError):
    """The requested fixture is invalid or could not be rebuilt safely."""


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise FixtureError(f"{name} is required")
    return value


def _scenario() -> tuple[Path, dict[str, Any]]:
    root = Path(os.environ.get("SCHEMII_DEMO_FIXTURE_ROOT", "/fixture")).resolve()
    scenario_id = _required_environment("SCHEMII_DEMO_SCENARIO")
    if SCENARIO_ID.fullmatch(scenario_id) is None:
        raise FixtureError("SCHEMII_DEMO_SCENARIO must be a safe kebab-case identifier")
    directory = (root / "demo-scenarios" / scenario_id).resolve()
    if directory.parent != (root / "demo-scenarios").resolve():
        raise FixtureError("demo scenario escaped the fixture root")
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FixtureError(f"could not read {manifest_path}") from error
    expected_keys = {
        "id",
        "title",
        "description",
        "sourceRevision",
        "designAlterations",
        "targetAlteration",
    }
    if (
        not isinstance(manifest, dict)
        or not expected_keys <= set(manifest)
        or set(manifest) != expected_keys
    ):
        raise FixtureError(f"{manifest_path} has an invalid contract")
    if manifest["id"] != scenario_id or manifest["sourceRevision"] != "runtime":
        raise FixtureError(f"{manifest_path} has inconsistent provenance")
    if not all(
        isinstance(manifest[field], str) and manifest[field].strip()
        for field in ("title", "description", "targetAlteration")
    ):
        raise FixtureError(f"{manifest_path} has empty descriptive fields")
    alterations = manifest["designAlterations"]
    if not isinstance(alterations, list) or not all(
        isinstance(name, str) and Path(name).name == name and name.endswith(".json")
        for name in alterations
    ):
        raise FixtureError(f"{manifest_path} lists invalid design alterations")
    target_name = manifest["targetAlteration"]
    if Path(target_name).name != target_name or not target_name.endswith(".sql"):
        raise FixtureError(f"{manifest_path} lists an invalid target alteration")
    return directory, manifest


def _fixture_digest(root: Path, directory: Path, manifest: dict[str, Any]) -> str:
    paths = [root / "migration-demo.sql", directory / "manifest.json"]
    paths.extend(directory / name for name in manifest["designAlterations"])
    paths.append(directory / manifest["targetAlteration"])
    digest = hashlib.sha256()
    for path in paths:
        try:
            content = path.read_bytes()
        except OSError as error:
            raise FixtureError(f"could not read fixture input {path}") from error
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _metadata_config() -> MetadataConfig:
    config = MetadataConfig.from_env()
    if config is None:
        raise FixtureError("durable metadata configuration is required")
    return config


def _cleanup_and_create_connection(
    config: MetadataConfig,
    repositories: MetadataRepositories,
    *,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> None:
    if repositories.connection_factory is None:
        raise FixtureError("durable metadata repositories are required")
    cipher = CredentialCipher(read_encryption_key(config.encryption_key_file))
    encrypted = cipher.encrypt(
        LOCAL_PROTOTYPE_USER_ID,
        FIXTURE_CONNECTION_ID,
        password,
    )
    connection = repositories.connection_factory()
    try:
        with connection.cursor() as cursor:
            ensure_local_metadata_user(cursor, LOCAL_PROTOTYPE_USER_ID)
            cursor.execute(
                """
                SELECT id
                FROM metadata.postgres_connections
                WHERE owner_id = %s
                  AND (
                    id = %s
                    OR (
                      host = %s AND port = %s AND database_name = %s
                      AND username = %s
                    )
                  )
                """,
                (
                    LOCAL_PROTOTYPE_USER_ID,
                    FIXTURE_CONNECTION_ID,
                    host,
                    port,
                    database,
                    username,
                ),
            )
            connection_ids = [row["id"] for row in cursor.fetchall()]
            if connection_ids:
                cursor.execute(
                    """
                    DELETE FROM schemii.workspaces AS workspace
                    USING schemii.workspace_targets AS target
                    WHERE workspace.owner_id = %s
                      AND target.owner_id = workspace.owner_id
                      AND target.workspace_id = workspace.id
                      AND target.connection_id = ANY(%s)
                    """,
                    (LOCAL_PROTOTYPE_USER_ID, connection_ids),
                )
                cursor.execute(
                    """
                    DELETE FROM metadata.postgres_connections
                    WHERE owner_id = %s AND id = ANY(%s)
                    """,
                    (LOCAL_PROTOTYPE_USER_ID, connection_ids),
                )
            cursor.execute(
                """
                INSERT INTO metadata.postgres_connections (
                    id, owner_id, name, host, port, database_name, username,
                    ssl_mode, connect_timeout
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'disable', 10)
                """,
                (
                    FIXTURE_CONNECTION_ID,
                    LOCAL_PROTOTYPE_USER_ID,
                    "Schemii resettable demo",
                    host,
                    port,
                    database,
                    username,
                ),
            )
            cursor.execute(
                """
                INSERT INTO metadata.postgres_connection_credentials (
                    connection_id, owner_id, ciphertext, nonce, key_version
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    FIXTURE_CONNECTION_ID,
                    LOCAL_PROTOTYPE_USER_ID,
                    encrypted.ciphertext,
                    encrypted.nonce,
                    encrypted.key_version,
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _apply_design_alteration(services: Any, workspace_id: str, path: Path) -> None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FixtureError(f"could not read design alteration {path}") from error
    if not isinstance(document, dict) or set(document) != {"changes"}:
        raise FixtureError(f"{path} must contain only a changes array")
    changes = document["changes"]
    if not isinstance(changes, list) or not changes:
        raise FixtureError(f"{path} must contain at least one design change")

    design = services.designs.get(LOCAL_PROTOTYPE_USER_ID, workspace_id)
    content = design.content.model_dump(mode="json")
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("operation"), str):
            raise FixtureError(f"{path} contains an unsupported design change")
        if change["operation"] == "addView":
            expected = {
                "operation",
                "name",
                "kind",
                "definition",
                "populateOnCreate",
            }
            if (
                set(change) != expected
                or change["kind"] not in {"view", "materialized_view"}
                or not isinstance(change["name"], str)
                or not isinstance(change["definition"], str)
                or (
                    change["kind"] == "view"
                    and change["populateOnCreate"] is not None
                )
                or (
                    change["kind"] == "materialized_view"
                    and not isinstance(change["populateOnCreate"], bool)
                )
            ):
                raise FixtureError(f"{path} contains an invalid addView change")
            relation_names = {
                item["name"] for item in [*content["tables"], *content["views"]]
            }
            if change["name"] in relation_names:
                raise FixtureError(
                    f"{path} cannot add existing relation {change['name']}"
                )
            identifier = "view_" + hashlib.sha256(
                f"{path.name}\0{change['name']}".encode("utf-8")
            ).hexdigest()[:32]
            content["views"].append(
                {
                    "id": identifier,
                    "name": change["name"],
                    "kind": change["kind"],
                    "definition": change["definition"],
                    "populate_on_create": change["populateOnCreate"],
                }
            )
            continue
        if change["operation"] not in {"addColumn", "setColumnType"}:
            raise FixtureError(f"{path} uses an unsupported design operation")
        if not isinstance(change.get("table"), str):
            raise FixtureError(f"{path} contains a design change without a table")
        table = next(
            (item for item in content["tables"] if item["name"] == change["table"]),
            None,
        )
        if table is None:
            raise FixtureError(f"{path} references unknown table {change['table']}")
        if change["operation"] == "addColumn":
            expected = {"operation", "table", "column", "dataType", "nullable"}
            if set(change) != expected or not isinstance(change["nullable"], bool):
                raise FixtureError(f"{path} contains an invalid addColumn change")
            if any(item["name"] == change["column"] for item in table["columns"]):
                raise FixtureError(
                    f"{path} cannot add existing column {change['table']}.{change['column']}"
                )
            identifier = "column_" + hashlib.sha256(
                f"{path.name}\0{change['table']}\0{change['column']}".encode("utf-8")
            ).hexdigest()[:32]
            table["columns"].append(
                {
                    "id": identifier,
                    "name": change["column"],
                    "data_type": change["dataType"],
                    "nullable": change["nullable"],
                    "default_expression": None,
                    "identity": None,
                    "generated_expression": None,
                    "generated_source_column_ids": [],
                }
            )
            continue
        expected = {"operation", "table", "column", "dataType"}
        if set(change) != expected:
            raise FixtureError(f"{path} uses an unsupported design operation")
        column = next(
            (item for item in table["columns"] if item["name"] == change["column"]),
            None,
        )
        if column is None:
            raise FixtureError(
                f"{path} references unknown column {change['table']}.{change['column']}"
            )
        column["data_type"] = change["dataType"]

    replacement = SchemiiDesignContent.model_validate(content)
    group_id = "dgrp_" + hashlib.sha256(path.read_bytes()).hexdigest()[:32]
    services.designs.replace(
        LOCAL_PROTOTYPE_USER_ID,
        workspace_id,
        SchemiiDesignReplace(
            expected_design_revision=design.revision,
            content=replacement,
            history_group_id=group_id,
        ),
    )


def _apply_target_alteration_and_record(
    *,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    sql: str,
    scenario: str,
    source_revision: str,
    fixture_digest: str,
    workspace_id: str,
) -> None:
    with psycopg.connect(
        host=host,
        port=port,
        dbname=database,
        user=username,
        password=password,
        connect_timeout=10,
        application_name="schemii-demo-fixture",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, prepare=False)
            cursor.execute(
                """
                INSERT INTO schemii_fixture.fixture_state (
                    singleton, scenario, source_revision, fixture_digest, workspace_id
                ) VALUES (true, %s, %s, %s, %s)
                ON CONFLICT (singleton) DO UPDATE
                SET scenario = EXCLUDED.scenario,
                    source_revision = EXCLUDED.source_revision,
                    fixture_digest = EXCLUDED.fixture_digest,
                    workspace_id = EXCLUDED.workspace_id,
                    reset_at = clock_timestamp()
                """,
                (scenario, source_revision, fixture_digest, workspace_id),
            )


def main() -> None:
    directory, manifest = _scenario()
    root = directory.parent.parent
    source_revision = _required_environment("SCHEMII_DEMO_SOURCE_REVISION")
    if SOURCE_REVISION.fullmatch(source_revision) is None:
        raise FixtureError("SCHEMII_DEMO_SOURCE_REVISION has an invalid format")
    host = os.environ.get("SCHEMII_DEMO_POSTGRES_HOST", "postgres").strip()
    port = int(os.environ.get("SCHEMII_DEMO_POSTGRES_PORT", "5432"))
    database = os.environ.get(
        "SCHEMII_DEMO_POSTGRES_DATABASE", "schemii_migration_demo"
    ).strip()
    username = _required_environment("SCHEMII_DEMO_POSTGRES_USER")
    config = _metadata_config()
    password = read_secret_file(
        os.environ.get(
            "SCHEMII_DEMO_POSTGRES_PASSWORD_FILE", config.password_file
        ),
        "SCHEMII_DEMO_POSTGRES_PASSWORD_FILE",
    )
    fixture_digest = _fixture_digest(root, directory, manifest)

    services = create_services()
    _cleanup_and_create_connection(
        config,
        services.metadata,
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
    )
    with services.connections.use(
        LOCAL_PROTOTYPE_USER_ID, FIXTURE_CONNECTION_ID
    ) as target:
        catalog = services.postgres.introspect(target, "public")
        workspace_request = WorkspaceCreateRecord(
            name=f"Demo · {manifest['title']}",
            connection_id=FIXTURE_CONNECTION_ID,
            database=database,
            namespace="public",
        )
        imported = import_postgres_catalog(catalog)
        assert services.migrations is not None
        workspace = services.migrations.create_import_workspace(
            LOCAL_PROTOTYPE_USER_ID,
            workspace_request,
            imported,
            target.revision,
            catalog,
        )
    for alteration in manifest["designAlterations"]:
        _apply_design_alteration(services, workspace.id, directory / alteration)

    target_path = directory / manifest["targetAlteration"]
    _apply_target_alteration_and_record(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        sql=target_path.read_text(encoding="utf-8"),
        scenario=manifest["id"],
        source_revision=source_revision,
        fixture_digest=fixture_digest,
        workspace_id=workspace.id,
    )
    print(f"Demo scenario: {manifest['id']} — {manifest['title']}")
    print(f"Source revision: {source_revision}")
    print(f"Fixture digest: {fixture_digest}")
    print(f"SCHEMII_DEMO_WORKSPACE_ID={workspace.id}")


if __name__ == "__main__":
    main()
