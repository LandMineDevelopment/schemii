"""Opt-in, read-only source witness. Config, identities and receipts stay private.

The coordinator must provision a dedicated run role before using this tool. A
borrowed author role, application handles or ended /activity are not authority.
No grants, SQL payloads, queries, cancellation or application writes are exposed.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


@contextmanager
def private_parent(path):
    """Walk from / using directory descriptors, never through a symlink."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or len(path.parts) > 128:
        raise ValueError("absolute_private_source_path_required")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    chain = []
    try:
        for part in path.parts[1:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            # System ancestors may be root-owned. The sole writable exception
            # is a root-owned sticky directory such as /tmp; the private leaf
            # parent must always be owned by this process and mode 0700.
            if info.st_uid not in {0, os.geteuid()} or (
                info.st_mode & 0o022
                and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
            ):
                raise ValueError("unsafe_private_source_ancestor")
            chain.append((info.st_dev, info.st_ino))
        parent = os.fstat(descriptor)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError("private_source_directory_required")
        yield descriptor, path.name, chain
    finally:
        os.close(descriptor)


def private_file(info):
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size > 1024 * 1024
    ):
        raise ValueError("invalid_private_source_file")


def private_read(path):
    with private_parent(path) as (parent, name, chain):
        entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
        private_file(entry)  # Reject FIFO/devices before any potentially blocking open.
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent
        )
        try:
            before = os.fstat(descriptor)
            private_file(before)
            if (before.st_dev, before.st_ino) != (entry.st_dev, entry.st_ino):
                raise ValueError("private_source_file_changed")
            chunks, size = [], 0
            while True:
                chunk = os.read(descriptor, min(65536, 1024 * 1024 + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > 1024 * 1024:
                    raise ValueError("private_source_file_budget")
            after = os.fstat(descriptor)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            private_file(current)
            if (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) != (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("private_source_file_changed")
            with private_parent(path) as (_, _, current_chain):
                if chain != current_chain:
                    raise ValueError("private_source_parent_changed")
            return json.loads(b"".join(chunks))
        finally:
            os.close(descriptor)


def private_write(path, value):
    with private_parent(path) as (parent, name, _):
        try:
            private_file(os.stat(name, dir_fd=parent, follow_symlinks=False))
        except FileNotFoundError:
            pass
        temporary = f"{name}.{secrets.token_hex(8)}.tmp"
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle)
                handle.write("\n")
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def authority(config):
    scope = config.get("authority", {})
    connection = config.get("connection", {})
    endpoint = scope.get("endpoint", {})
    server = scope.get("server", {})
    if (
        config.get("version") != 2
        or not isinstance(config.get("runId"), str)
        or re.fullmatch(r"load_[0-9a-f]{16}", config["runId"]) is None
        or scope.get("kind") != "exclusive_source_role"
        or scope.get("exclusiveToRun") is not True
        or not isinstance(scope.get("provisioningReceipt"), str)
        or not Path(scope["provisioningReceipt"]).is_absolute()
        or scope.get("applicationName") != "schemii"
        or set(scope)
        != {
            "kind",
            "exclusiveToRun",
            "database",
            "role",
            "applicationName",
            "provisioningReceipt",
            "endpoint",
            "server",
        }
        or set(endpoint) != {"host", "port", "sslmode"}
        or endpoint != {key: connection.get(key) for key in ("host", "port", "sslmode")}
        or set(server)
        != {"address", "port", "postmasterStart", "databaseOid", "fixtureId"}
        or not isinstance(server.get("address"), str)
        or not server["address"]
        or type(server.get("port")) is not int
        or not 1 <= server["port"] <= 65535
        or type(server.get("databaseOid")) is not int
        or server["databaseOid"] < 1
        or not isinstance(server.get("fixtureId"), str)
        or re.fullmatch(r"[0-9a-f]{64}", server["fixtureId"]) is None
        or not isinstance(server.get("postmasterStart"), str)
        or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00",
            server["postmasterStart"],
        )
        is None
        or not isinstance(scope.get("database"), str)
        or not isinstance(scope.get("role"), str)
        or not scope["database"]
        or not scope["role"]
        or set(connection) != {"host", "port", "dbname", "user", "password", "sslmode"}
        or connection["dbname"] != scope["database"]
        or connection["user"] != scope["role"]
        or any(
            not isinstance(connection[key], str) or not connection[key]
            for key in ("host", "dbname", "user", "password")
        )
        or type(connection["port"]) is not int
        or not 1 <= connection["port"] <= 65535
        or connection["sslmode"] not in {"require", "verify-ca", "verify-full"}
    ):
        raise ValueError("explicit_exclusive_source_authority_required")
    datetime.fromisoformat(server["postmasterStart"])
    return {"runId": config["runId"], **scope}


def provisioned(scope, receipt):
    if (
        receipt.get("version") != 2
        or receipt.get("status") != "ready"
        or any(
            receipt.get(key) != scope[key]
            for key in (
                "runId",
                "kind",
                "exclusiveToRun",
                "database",
                "role",
                "applicationName",
                "endpoint",
                "server",
            )
        )
    ):
        raise ValueError("provisioned_source_authority_mismatch")


@contextmanager
def monitor_lock(path):
    # The observer and active recovery share this witness. Serialize its ledger,
    # so overlapping samples cannot lose a captured backend identity.
    with private_parent(str(path) + ".lock") as (parent, name, _):
        descriptor = os.open(
            name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
        try:
            private_file(os.fstat(descriptor))
            deadline = time.monotonic() + 3
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise ValueError("source_monitor_busy") from None
                    time.sleep(0.02)
            yield
        finally:
            os.close(descriptor)


def identity(row):
    if (
        type(row.get("pid")) is not int
        or row["pid"] < 1
        or not isinstance(row.get("backend_start"), datetime)
        or row["backend_start"].tzinfo is None
        or any(
            not isinstance(row.get(key), str) or not row[key]
            for key in ("datname", "usename", "application_name", "state")
        )
    ):
        raise ValueError("source_identity_or_visibility_unavailable")
    return {
        "pid": row["pid"],
        "backendStart": row["backend_start"]
        .astimezone(timezone.utc)
        .isoformat(timespec="microseconds"),
        "database": row["datname"],
        "role": row["usename"],
        "applicationName": row["application_name"],
    }


def server_identity(row):
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("server_address"), str)
        or not row["server_address"]
        or type(row.get("server_port")) is not int
        or not 1 <= row["server_port"] <= 65535
        or type(row.get("database_oid")) is not int
        or row["database_oid"] < 1
        or not isinstance(row.get("postmaster_start"), datetime)
        or row["postmaster_start"].tzinfo is None
        or not isinstance(row.get("fixture_marker"), str)
        or re.fullmatch(r"schemii-load:[0-9a-f]{64}", row["fixture_marker"]) is None
    ):
        raise ValueError("source_server_identity_unavailable")
    return {
        "address": row["server_address"],
        "port": row["server_port"],
        "databaseOid": row["database_oid"],
        "postmasterStart": row["postmaster_start"]
        .astimezone(timezone.utc)
        .isoformat(timespec="microseconds"),
        "fixtureId": row["fixture_marker"].removeprefix("schemii-load:"),
    }


def sample(config, previous=None, *, connect=psycopg.connect):
    scope = authority(config)
    if previous and (
        previous.get("version") != 2
        or previous.get("status") != "available"
        or previous.get("authority") != scope
        or previous.get("serverIdentity") != scope["server"]
    ):
        raise ValueError("source_authority_changed")
    captured = list(previous.get("captured", [])) if previous else []
    if len(captured) > 10000:
        raise ValueError("source_identity_budget")
    keys = set()
    for value in captured:
        if (
            set(value) != {"pid", "backendStart", "database", "role", "applicationName"}
            or type(value["pid"]) is not int
            or value["pid"] < 1
            or not isinstance(value["backendStart"], str)
            or re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00",
                value["backendStart"],
            )
            is None
            or value["database"] != scope["database"]
            or value["role"] != scope["role"]
            or value["applicationName"] != scope["applicationName"]
            or (value["pid"], value["backendStart"]) in keys
        ):
            raise ValueError("source_captured_identity_invalid")
        datetime.fromisoformat(value["backendStart"])
        keys.add((value["pid"], value["backendStart"]))
    # All parameters are explicit: no libpq service, author fallback or ambient
    # password file. The same source role sees its own activity without grants.
    with connect(
        **config["connection"],
        application_name="schemii_load_monitor",
        connect_timeout=3,
        passfile="/dev/null",
        options="-c default_transaction_read_only=on -c statement_timeout=1000 -c lock_timeout=500",
        autocommit=True,
        row_factory=dict_row,
    ) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            own = connection.execute(
                "SELECT current_database() AS database, current_user AS role, session_user AS login"
            ).fetchone()
            if own != {
                "database": scope["database"],
                "role": scope["role"],
                "login": scope["role"],
            }:
                raise ValueError("source_monitor_authority_mismatch")
            server = server_identity(
                connection.execute(
                    "SELECT pg_catalog.inet_server_addr()::text AS server_address, "
                    "pg_catalog.inet_server_port() AS server_port, pg_catalog.pg_postmaster_start_time() AS postmaster_start, "
                    "d.oid::bigint AS database_oid, pg_catalog.shobj_description(d.oid, 'pg_database') AS fixture_marker "
                    "FROM pg_catalog.pg_database AS d WHERE d.datname = pg_catalog.current_database()"
                ).fetchone()
            )
            if server != scope["server"]:
                raise ValueError("source_server_identity_changed")
            rows = connection.execute(
                "SELECT pid, backend_start, datname, usename, application_name, state "
                "FROM pg_catalog.pg_stat_activity WHERE datname = %s AND usename = %s "
                "AND application_name = %s ORDER BY pid LIMIT 10001",
                (scope["database"], scope["role"], scope["applicationName"]),
            ).fetchall()
            if len(rows) > 10000:
                raise ValueError("source_observation_budget")
            discovered = [identity(row) for row in rows]
            if len({value["pid"] for value in discovered}) != len(discovered):
                raise ValueError("duplicate_source_identity")
            if any(
                value["database"] != scope["database"]
                or value["role"] != scope["role"]
                or value["applicationName"] != scope["applicationName"]
                for value in discovered
            ):
                raise ValueError("foreign_source_identity")
            if previous is None and discovered:
                raise ValueError("source_baseline_not_empty")
            for value in discovered:
                if value not in captured:
                    captured.append(value)
            if len(captured) > 10000:
                raise ValueError("source_identity_budget")
            # Discovery labels are mutable. Independently inspect every captured
            # PID on this bound server, without database/role/application filters.
            captured_pids = {value["pid"] for value in captured}
            checked = (
                connection.execute(
                    "SELECT pid, backend_start, datname, usename, application_name, state "
                    "FROM pg_catalog.pg_stat_activity WHERE pid = ANY(%s) ORDER BY pid LIMIT 10001",
                    (sorted(captured_pids),),
                ).fetchall()
                if captured
                else []
            )
            if len(checked) > 10000 or len({row["pid"] for row in checked}) != len(
                checked
            ):
                raise ValueError("source_recheck_budget_or_duplicate")
            by_pid = {value["pid"]: value for value in map(identity, checked)}
            if any(pid not in captured_pids for pid in by_pid):
                raise ValueError("source_recheck_foreign_pid")
            checks, live = [], []
            for value in captured:
                observed = by_pid.get(value["pid"])
                if observed is None:
                    checks.append({**value, "status": "absent"})
                elif observed["backendStart"] == value["backendStart"]:
                    if observed != value:
                        raise ValueError("source_captured_identity_label_changed")
                    live.append(value)
                    checks.append({**value, "status": "present"})
                elif observed["backendStart"] > value["backendStart"]:
                    checks.append(
                        {
                            **value,
                            "status": "pid_reused",
                            "observedBackendStart": observed["backendStart"],
                        }
                    )
                else:
                    raise ValueError("source_backend_start_invalid")
    return {
        "version": 2,
        "at": datetime.now(timezone.utc).isoformat(),
        "authority": scope,
        "status": "available",
        "method": "bound_server_discovery_and_captured_pid_start_recheck",
        "serverIdentity": server,
        "capturedChecks": checks,
        "captured": captured,
        "live": live,
        "ownedBackends": len(live),
        "releaseVerified": bool(captured) and not live,
        "samplingLimit": "Only captured backends are witnessed; between-sample sessions are not measured.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    previous = None
    try:
        config = private_read(arguments.config)
        scope = authority(config)
        provisioned(scope, private_read(scope["provisioningReceipt"]))
        path = Path(arguments.output)
        if not path.is_absolute() or str(path) in {
            arguments.config,
            scope["provisioningReceipt"],
        }:
            raise ValueError("disjoint_private_source_output_required")
        parent = path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o077:
            raise ValueError("private_source_directory_required")
        with monitor_lock(path):
            previous = private_read(path) if path.exists() else None
            if previous and previous.get("authority") != scope:
                raise ValueError("source_authority_changed")
            if previous and previous.get("status") != "available":
                raise ValueError("source_monitor_failure_requires_new_run")
            # Direct invocation has the same no-ambient-libpq contract as Node.
            for key in list(os.environ):
                if key.startswith("PG"):
                    del os.environ[key]
            try:
                result = sample(config, previous)
                private_write(path, result)
            except Exception:
                private_write(
                    path,
                    {
                        **(previous or {}),
                        "authority": scope,
                        "version": 2,
                        "status": "unavailable",
                        "at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                raise
        print(json.dumps({"status": "available"}))
        return 0
    except Exception:
        # Never expose libpq errors: they can contain connection identities.
        # Never overwrite a foreign, unreadable or changed-authority receipt.
        # Owned sampling failures are recorded under the lock above.
        print(json.dumps({"status": "unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
