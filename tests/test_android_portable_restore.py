"""Portable format guards and opt-in, socket-only PostgreSQL restore rehearsals."""

import base64
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
from tempfile import TemporaryDirectory

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from dev.android import recovery
from schemii.common.metadata.crypto import CredentialCipher, EncryptedCredential
from schemii.common.metadata.secrets import read_encryption_key

ROOT = Path(__file__).resolve().parents[1]
APP_MIGRATIONS = ROOT / "src/schemii/schemii/metadata/migrations"
COMMON_MIGRATIONS = ROOT / "src/schemii/common/metadata/migrations"
USER_ID = "fixture-user"
WORKSPACE_ID = "ws_" + "a" * 32
CONNECTION_ID = "pg_" + "b" * 32
LAYOUT = [{"objectId": "fixture-order", "x": 144, "y": -52}]
LINUX_SECRET_NAMES = {
    "metadata_bootstrap_password": "metadata_password",
    "target_bootstrap_password": "demo_admin_password",
    "target_password": "demo_target_password",
}


def identities(profile):
    database = "schemii_test" if profile == "linux" else "schemii"
    return {
        "metadata": {
            "bootstrap_user": "schemii" if profile == "linux" else "schemii_bootstrap",
            "app_user": "schemii_metadata_app",
            "primary_database": database,
            "databases": [database],
        },
        "target": {
            "bootstrap_user": "schemii_demo_admin"
            if profile == "linux"
            else "schemii_bootstrap",
            "app_user": "schemii" if profile == "linux" else "schemii_target",
            "primary_database": database,
            "databases": sorted([database, "schemii_migration_demo", "organization"]),
        },
    }


def write_private(path, content):
    path.write_bytes(content)
    path.chmod(0o600)


def new_bundle(root):
    root.mkdir(mode=0o700)
    for name in ("secrets", *recovery.PORTABLE_CLUSTERS):
        (root / name).mkdir(mode=0o700)


@pytest.fixture
def sealed_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    new_bundle(bundle)
    clusters = identities("linux")
    for name in recovery.SECRETS:
        value = (
            base64.b64encode(bytes(32))
            if name == "metadata_encryption_key"
            else b"fixture"
        )
        write_private(bundle / "secrets" / name, value + b"\n")
    for plane, identity in clusters.items():
        write_private(bundle / plane / "roles.sql", b"-- fixture")
        for name in identity["databases"]:
            write_private(bundle / plane / f"{name}.dump", b"PGDMPfixture")
    recovery.seal_portable(bundle, clusters)
    return bundle


def test_portable_contract_preserves_distinct_cluster_identities(sealed_bundle):
    manifest = recovery.verify_portable(sealed_bundle)
    assert manifest["clusters"] == identities("linux")
    assert set(manifest["sha256"]) == {
        "metadata/roles.sql",
        "metadata/schemii_test.dump",
        "target/roles.sql",
        "target/schemii_test.dump",
        "target/organization.dump",
        "target/schemii_migration_demo.dump",
        *(f"secrets/{name}" for name in recovery.SECRETS),
    }


def test_roles_replay_omits_only_the_initial_create():
    create = b'CREATE ROLE "schemii";\n'
    other_sql = (
        b'ALTER ROLE "schemii" SUPERUSER;\nCREATE ROLE "schemii_metadata_app";\n'
    )
    assert (
        recovery.portable_roles_for_restore(create + other_sql, "schemii") == other_sql
    )
    for ambiguous in (other_sql, create + other_sql + create):
        with pytest.raises(recovery.RecoveryError, match="bootstrap CREATE ROLE"):
            recovery.portable_roles_for_restore(ambiguous, "schemii")


@pytest.mark.parametrize(
    "case",
    [
        "missing-target",
        "modified-layout-dump",
        "key-replacement",
        "secret-permissions",
        "unexpected-file",
        "symlink",
    ],
)
def test_portable_contract_refuses_bad_payload(sealed_bundle, case):
    key = sealed_bundle / "secrets/metadata_encryption_key"
    metadata = sealed_bundle / "metadata/schemii_test.dump"
    if case == "missing-target":
        (sealed_bundle / "target/organization.dump").unlink()
    elif case == "modified-layout-dump":
        metadata.write_bytes(b"PGDMPaltered")
    elif case == "key-replacement":
        key.write_bytes(base64.b64encode(bytes(range(32))))
    elif case == "secret-permissions":
        key.chmod(0o644)
    elif case == "unexpected-file":
        write_private(sealed_bundle / "secrets/tls.key", b"foreign endpoint key")
    else:
        metadata.rename(sealed_bundle.parent / metadata.name)
        metadata.symlink_to(sealed_bundle.parent / metadata.name)
    with pytest.raises(recovery.RecoveryError):
        recovery.verify_portable(sealed_bundle)


@pytest.mark.parametrize(
    "case",
    [
        "cold-format",
        "wrong-major",
        "metadata-only",
        "postgres-database",
        "dsn-instead-of-name",
        "duplicate-key",
    ],
)
def test_portable_contract_refuses_bad_manifest(sealed_bundle, case):
    path = sealed_bundle / "manifest.json"
    manifest = json.loads(path.read_bytes())
    if case == "cold-format":
        manifest["format"] = recovery.FORMAT
    elif case == "wrong-major":
        manifest["postgres_major"] += 1
    elif case == "metadata-only":
        del manifest["clusters"]["target"]
    elif case == "postgres-database":
        manifest["clusters"]["target"]["databases"].insert(1, "postgres")
    elif case == "dsn-instead-of-name":
        manifest["clusters"]["target"]["primary_database"] = "host=example"
    encoded = json.dumps(manifest)
    if case == "duplicate-key":
        encoded = '{"postgres_major": 17, ' + encoded[1:]
    write_private(path, encoded.encode())
    with pytest.raises(recovery.RecoveryError):
        recovery.verify_portable(sealed_bundle)


@pytest.fixture(scope="module")
def postgres_bin():
    configured = os.environ.get("SCHEMII_PORTABLE_POSTGRES_BIN")
    if not configured:
        pytest.skip(
            "set SCHEMII_PORTABLE_POSTGRES_BIN to a local PostgreSQL 17 bin directory"
        )
    bin_dir = Path(configured)
    assert bin_dir.is_absolute(), "use an absolute PostgreSQL bin directory"
    for binary in (
        "initdb",
        "postgres",
        "pg_ctl",
        "pg_dumpall",
        "pg_dump",
        "pg_restore",
        "psql",
    ):
        version = subprocess.check_output([bin_dir / binary, "--version"], text=True)
        assert f"(PostgreSQL) {recovery.PORTABLE_POSTGRES_MAJOR}." in version, version
    return bin_dir


@contextmanager
def private_cluster(bin_dir, bootstrap_user, bootstrap_password):
    # A short private Unix socket path fits sun_path and exposes no TCP port.
    with TemporaryDirectory(prefix="schemii-pg-", dir="/tmp") as directory:
        root = Path(directory)
        data = root / "data"
        password_file = root / "password"
        write_private(password_file, bootstrap_password.encode() + b"\n")
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("PG")
        }
        environment.update(
            PGHOST=directory,
            PGPORT="5432",
            PGDATABASE="postgres",
            PGUSER=bootstrap_user,
            PGPASSWORD=bootstrap_password,
            PGCONNECT_TIMEOUT="5",
        )

        def run(tool, *args, input_sql=None):
            return subprocess.run(
                [bin_dir / tool, *args],
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                input=input_sql,
                umask=0o077,
                check=True,
            )

        run(
            "initdb",
            "--pgdata",
            data,
            "--username",
            bootstrap_user,
            "--pwfile",
            password_file,
            "--auth-local=scram-sha-256",
            "--auth-host=reject",
            "--locale=C",
            "--encoding=UTF8",
        )
        password_file.unlink()
        try:
            run(
                "pg_ctl",
                "-D",
                data,
                "-l",
                root / "log",
                "-w",
                "-t",
                "20",
                "-o",
                f"-c listen_addresses='' -c unix_socket_directories={directory}",
                "start",
            )
            yield directory, run
        finally:
            if (data / "postmaster.pid").exists():
                run("pg_ctl", "-D", data, "-w", "-t", "20", "-m", "fast", "stop")


def connect(socket, database, username, password):
    return psycopg.connect(
        host=socket,
        port=5432,
        dbname=database,
        user=username,
        password=password,
        connect_timeout=5,
        autocommit=True,
    )


def seed_metadata(connection, target, password, encryption_key):
    connection.execute("CREATE SCHEMA metadata")
    for migration in (
        COMMON_MIGRATIONS / "0001_connections.sql",
        APP_MIGRATIONS / "0002_schemii_workspaces.sql",
        APP_MIGRATIONS / "0003_schemii_designs.sql",
        APP_MIGRATIONS / "0037_accounts_roles.sql",
    ):
        connection.execute(migration.read_text())
    connection.execute(
        "INSERT INTO metadata.users (id, display_name) VALUES (%s, 'Fixture')",
        (USER_ID,),
    )
    connection.execute(
        "INSERT INTO metadata.auth_accounts (user_id, username, password_hash) VALUES (%s, 'fixture', 'fixture-hash')",
        (USER_ID,),
    )
    connection.execute(
        "INSERT INTO metadata.postgres_connections (id, owner_id, name, host, port, database_name, username, ssl_mode, connect_timeout) "
        "VALUES (%s, %s, 'Fixture', 'source-fixture', 5432, %s, %s, 'prefer', 5)",
        (CONNECTION_ID, USER_ID, target["primary_database"], target["app_user"]),
    )
    credential = CredentialCipher(encryption_key).encrypt(
        USER_ID, CONNECTION_ID, password
    )
    connection.execute(
        "INSERT INTO metadata.postgres_connection_credentials (connection_id, owner_id, ciphertext, nonce, key_version) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            CONNECTION_ID,
            USER_ID,
            credential.ciphertext,
            credential.nonce,
            credential.key_version,
        ),
    )
    connection.execute(
        "INSERT INTO schemii.workspaces (id, owner_id, name) VALUES (%s, %s, 'Fixture')",
        (WORKSPACE_ID, USER_ID),
    )
    connection.execute(
        "INSERT INTO schemii.workspace_targets (workspace_id, owner_id, connection_id, database_name, namespace) "
        "VALUES (%s, %s, %s, %s, 'inventory')",
        (WORKSPACE_ID, USER_ID, CONNECTION_ID, target["primary_database"]),
    )
    connection.execute(
        "INSERT INTO schemii.workspace_design_layouts (workspace_id, owner_id, revision, design_revision, objects) "
        "VALUES (%s, %s, 9, 3, %s)",
        (WORKSPACE_ID, USER_ID, Jsonb(LAYOUT)),
    )


def export_fixture(bin_dir, bundle, profile):
    clusters = identities(profile)
    new_bundle(bundle)
    mapping = LINUX_SECRET_NAMES if profile == "linux" else {}
    source_secrets = bundle.parent / f"{profile}-secrets"
    source_secrets.mkdir(mode=0o700)
    encryption_key = secrets.token_bytes(32)
    values = {name: secrets.token_hex(16) for name in recovery.SECRETS}
    values["metadata_encryption_key"] = base64.b64encode(encryption_key).decode()
    for name, value in values.items():
        original = source_secrets / mapping.get(name, name)
        write_private(original, value.encode() + b"\n")
        write_private(bundle / "secrets" / name, original.read_bytes())
    for plane, identity in clusters.items():
        admin = identity["bootstrap_user"]
        login = identity["app_user"]
        admin_pw = values[f"{plane}_bootstrap_password"]
        login_pw = values[
            "metadata_app_password" if plane == "metadata" else "target_password"
        ]
        with private_cluster(bin_dir, admin, admin_pw) as (socket, run):
            with connect(socket, "postgres", admin, admin_pw) as conn:
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN INHERIT PASSWORD {}").format(
                        sql.Identifier(login), sql.Literal(login_pw)
                    )
                )
                conn.execute("CREATE ROLE fixture_reader")
                conn.execute(
                    sql.SQL("GRANT fixture_reader TO {}").format(sql.Identifier(login))
                )
                for database in identity["databases"]:
                    conn.execute(
                        sql.SQL("CREATE DATABASE {} OWNER {}").format(
                            sql.Identifier(database), sql.Identifier(login)
                        )
                    )
            for database in identity["databases"]:
                with connect(socket, database, login, login_pw) as conn:
                    if plane == "metadata":
                        seed_metadata(
                            conn,
                            clusters["target"],
                            values["target_password"],
                            encryption_key,
                        )
                    else:
                        conn.execute("CREATE SCHEMA inventory")
                        conn.execute(
                            "CREATE TABLE inventory.items (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name text NOT NULL)"
                        )
                        conn.execute(
                            "INSERT INTO inventory.items (name) VALUES (%s)",
                            (database,),
                        )
                        conn.execute(
                            "GRANT SELECT ON inventory.items TO fixture_reader"
                        )
            # All writers are now closed. Archive globals separately for each plane.
            roles = bundle / plane / "roles.sql"
            run(
                "pg_dumpall",
                "--roles-only",
                "--quote-all-identifiers",
                "--encoding=UTF8",
                "--file",
                roles,
            )
            roles.chmod(0o600)
            for database in identity["databases"]:
                dump = bundle / plane / f"{database}.dump"
                run("pg_dump", "--format=custom", "--dbname", database, "--file", dump)
                dump.chmod(0o600)
    recovery.seal_portable(bundle, clusters)
    return source_secrets, mapping


@contextmanager
def restore_fixture(bin_dir, bundle):
    manifest = recovery.verify_portable(bundle)
    with ExitStack() as stage:
        restored = {}
        for plane, identity in manifest["clusters"].items():
            admin = identity["bootstrap_user"]
            password = (
                (bundle / "secrets" / f"{plane}_bootstrap_password")
                .read_text()
                .rstrip("\n")
            )
            socket, run = stage.enter_context(private_cluster(bin_dir, admin, password))
            root = bundle / plane
            run(
                "psql",
                "-X",
                "--set",
                "ON_ERROR_STOP=1",
                "--single-transaction",
                "--file",
                "-",
                input_sql=recovery.portable_roles_for_restore(
                    (root / "roles.sql").read_bytes(), admin
                ).decode("utf-8"),
            )
            for database in identity["databases"]:
                run(
                    "pg_restore",
                    "--create",
                    "--exit-on-error",
                    "--dbname",
                    "postgres",
                    root / f"{database}.dump",
                )
            with connect(socket, "postgres", admin, password) as connection:
                rows = connection.execute(
                    "SELECT datname, pg_get_userbyid(datdba) FROM pg_database "
                    "WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY datname"
                ).fetchall()
                assert rows == [
                    (database, identity["app_user"])
                    for database in identity["databases"]
                ]
                grantor = connection.execute(
                    "SELECT pg_get_userbyid(grantor) FROM pg_auth_members "
                    "WHERE roleid = 'fixture_reader'::regrole AND member = %s::regrole",
                    (identity["app_user"],),
                ).fetchone()
                assert grantor == (admin,)
            restored[plane] = (socket, identity)
        yield restored


@pytest.mark.parametrize(
    "source,destination", [("linux", "android"), ("android", "linux")]
)
def test_postgres_portable_round_trip(postgres_bin, tmp_path, source, destination):
    bundle = tmp_path / "transfer"
    originals, source_mapping = export_fixture(postgres_bin, bundle, source)
    destination_mapping = LINUX_SECRET_NAMES if destination == "linux" else {}
    material = tmp_path / f"{destination}-secrets"
    material.mkdir(mode=0o700)

    with restore_fixture(postgres_bin, bundle) as restored:
        for name in recovery.SECRETS:
            payload = (bundle / "secrets" / name).read_bytes()
            assert payload == (originals / source_mapping.get(name, name)).read_bytes()
            write_private(material / destination_mapping.get(name, name), payload)

        def secret(name):
            return (
                (material / destination_mapping.get(name, name))
                .read_text()
                .rstrip("\n")
            )

        metadata_socket, metadata = restored["metadata"]
        target_socket, target = restored["target"]
        assert metadata_socket != target_socket
        assert {plane: pair[1] for plane, pair in restored.items()} == identities(
            source
        )
        with connect(
            metadata_socket,
            metadata["primary_database"],
            metadata["app_user"],
            secret("metadata_app_password"),
        ) as conn:
            assert conn.execute(
                "SELECT user_id, username FROM metadata.auth_accounts"
            ).fetchone() == (USER_ID, "fixture")
            assert conn.execute(
                "SELECT workspace_id, owner_id, revision, design_revision, objects FROM schemii.workspace_design_layouts"
            ).fetchone() == (WORKSPACE_ID, USER_ID, 9, 3, LAYOUT)
            assert conn.execute(
                "SELECT connection_id, database_name, namespace FROM schemii.workspace_targets"
            ).fetchone() == (CONNECTION_ID, target["primary_database"], "inventory")
            owner, connection_id, ciphertext, nonce, version = conn.execute(
                "SELECT owner_id, connection_id, ciphertext, nonce, key_version FROM metadata.postgres_connection_credentials"
            ).fetchone()
        key = read_encryption_key(str(material / "metadata_encryption_key"))
        recovered_password = CredentialCipher(key).decrypt(
            owner, connection_id, EncryptedCredential(ciphertext, nonce, version)
        )
        assert recovered_password == secret("target_password")

        for database in target["databases"]:
            with connect(
                target_socket, database, target["app_user"], recovered_password
            ) as conn:
                assert conn.execute(
                    "SELECT id, name FROM inventory.items"
                ).fetchall() == [(1, database)]
                assert conn.execute(
                    "SELECT tableowner, has_table_privilege('fixture_reader', 'inventory.items', 'SELECT'), "
                    "pg_has_role(current_user, 'fixture_reader', 'MEMBER') "
                    "FROM pg_tables WHERE schemaname='inventory' AND tablename='items'"
                ).fetchone() == (target["app_user"], True, True)
                assert conn.execute(
                    "INSERT INTO inventory.items (name) VALUES ('new') RETURNING id"
                ).fetchone() == (2,)

        for plane, (socket, identity) in restored.items():
            with connect(
                socket,
                "postgres",
                identity["bootstrap_user"],
                secret(f"{plane}_bootstrap_password"),
            ) as conn:
                assert conn.execute("SELECT current_user").fetchone() == (
                    identity["bootstrap_user"],
                )


def test_target_restore_failure_keeps_staged_pair_private(postgres_bin, tmp_path):
    bundle = tmp_path / "transfer"
    export_fixture(postgres_bin, bundle, "linux")
    # This models an internally consistent bundle whose target archive is
    # unusable. Metadata restores first, but the stage is never yielded.
    archive = bundle / "target/organization.dump"
    write_private(archive, b"PGDMPbroken")
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["sha256"]["target/organization.dump"] = hashlib.sha256(
        archive.read_bytes()
    ).hexdigest()
    write_private(path, json.dumps(manifest).encode())
    with pytest.raises(subprocess.CalledProcessError) as caught:
        with restore_fixture(postgres_bin, bundle):
            pytest.fail("a partial restore must not be eligible for cutover")
    assert Path(caught.value.cmd[0]).name == "pg_restore"
    assert caught.value.cmd[-1] == archive
