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


def private_read(path):
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        entry = path.lstat()
        if (
            not path.is_absolute()
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or entry.st_ino != info.st_ino
            or entry.st_dev != info.st_dev
            or stat.S_ISLNK(entry.st_mode)
            or info.st_size > 1024 * 1024
        ):
            raise ValueError("invalid_private_source_file")
        return json.load(handle)


def private_write(path, value):
    path = Path(path)
    parent = path.parent.lstat()
    if (
        not path.is_absolute()
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_mode & 0o077
    ):
        raise ValueError("private_source_directory_required")
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def authority(config):
    scope = config.get("authority", {})
    connection = config.get("connection", {})
    if (
        config.get("version") != 1
        or not isinstance(config.get("runId"), str)
        or re.fullmatch(r"load_[0-9a-f]{16}", config["runId"]) is None
        or scope.get("kind") != "exclusive_source_role"
        or scope.get("exclusiveToRun") is not True
        or not isinstance(scope.get("provisioningReceipt"), str)
        or not Path(scope["provisioningReceipt"]).is_absolute()
        or scope.get("applicationName") != "schemii"
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
    return {"runId": config["runId"], **scope}


def provisioned(scope, receipt):
    if (
        receipt.get("version") != 1
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
            )
        )
    ):
        raise ValueError("provisioned_source_authority_mismatch")


@contextmanager
def monitor_lock(path):
    # The observer and active recovery share this witness. Serialize its ledger,
    # so overlapping samples cannot lose a captured backend identity.
    descriptor = os.open(
        str(path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("private_monitor_lock_required")
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


def sample(config, previous=None, *, connect=psycopg.connect):
    scope = authority(config)
    if previous and previous.get("authority") != scope:
        raise ValueError("source_authority_changed")
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
            rows = connection.execute(
                "SELECT pid, backend_start, datname, usename, application_name, state "
                "FROM pg_stat_activity WHERE datname = %s AND usename = %s "
                "AND application_name = %s ORDER BY pid LIMIT 10001",
                (scope["database"], scope["role"], scope["applicationName"]),
            ).fetchall()
    if len(rows) > 10000:
        raise ValueError("source_observation_budget")
    live = [identity(row) for row in rows]
    if len({value["pid"] for value in live}) != len(live):
        raise ValueError("duplicate_source_identity")
    if any(
        value["database"] != scope["database"]
        or value["role"] != scope["role"]
        or value["applicationName"] != scope["applicationName"]
        for value in live
    ):
        raise ValueError("foreign_source_identity")
    if previous is None and live:
        raise ValueError("source_baseline_not_empty")
    captured = list(previous.get("captured", [])) if previous else []
    for value in live:
        if any(old["pid"] == value["pid"] and old != value for old in captured):
            raise ValueError("source_backend_replaced")
        if value not in captured:
            captured.append(value)
    if len(captured) > 10000:
        raise ValueError("source_identity_budget")
    return {
        "version": 1,
        "at": datetime.now(timezone.utc).isoformat(),
        "authority": scope,
        "status": "available",
        "method": "independent_pg_stat_activity_exact_role_database_application_pid_start",
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
                        "version": 1,
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
