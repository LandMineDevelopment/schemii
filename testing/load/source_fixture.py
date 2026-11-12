"""Owned datasets inside an already provisioned launcher-held disposable source.

No database/role/runtime creation, COPY, migrations, CASCADE or retained QA data.
The explicit provisioner credential is used only after v2 fixture/server proof.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from testing.load.source_monitor import (
    authority,
    private_read,
    private_write,
    provisioned,
    server_identity,
)


SERVER_SQL = (
    "SELECT pg_catalog.inet_server_addr()::text AS server_address, pg_catalog.inet_server_port() AS server_port, "
    "pg_catalog.pg_postmaster_start_time() AS postmaster_start, d.oid::bigint AS database_oid, "
    "pg_catalog.shobj_description(d.oid, 'pg_database') AS fixture_marker FROM pg_catalog.pg_database d "
    "WHERE d.datname = pg_catalog.current_database()"
)


def validate(config):
    if (
        config.get("version") != 1
        or re.fullmatch(r"load_[a-f0-9]{16}", config.get("tag", "")) is None
        or type(config.get("active")) is not int
        or not 1 <= config["active"] <= 1000
        or type(config.get("wide")) is not bool
        or config.get("rows") != 513
        or config.get("wideRows") != 129
        or config.get("wideCharacters") != 8192
    ):
        raise ValueError("invalid_owned_dataset_spec")
    estimate = config["active"] * (
        513 * 128 + (129 * 4 * 8192 if config["wide"] else 0)
    )
    if (
        type(config.get("maximumBytes")) is not int
        or not estimate <= config["maximumBytes"] <= 1024 * 1024 * 1024
    ):
        raise ValueError("owned_dataset_budget")
    witness = private_read(config["sourceWitnessConfig"])
    scope = authority(witness)
    provisioned(scope, private_read(scope["provisioningReceipt"]))
    admin = config.get("adminConnection", {})
    if (
        scope["runId"] != config["tag"]
        or set(admin) != {"host", "port", "dbname", "user", "password", "sslmode"}
        or any(
            admin.get(key) != witness["connection"][key]
            for key in ("host", "port", "dbname", "sslmode")
        )
        or not isinstance(admin.get("user"), str)
        or not admin["user"]
        or admin["user"] == scope["role"]
        or not admin.get("password")
        or config.get("sourceAdminRole") != admin["user"]
    ):
        raise ValueError("explicit_dataset_provisioner_required")
    return scope, admin


def inspect_schema(connection, item, tag):
    row = connection.execute(
        "SELECT n.oid::bigint AS oid, r.rolname AS owner, pg_catalog.obj_description(n.oid, 'pg_namespace') AS marker "
        "FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles r ON r.oid=n.nspowner WHERE n.nspname=%s",
        (item["name"],),
    ).fetchone()
    if row is None:
        return None
    if (
        row["marker"] != "schemii-load-dataset:" + item["nonce"]
        or row["owner"] != item["owner"]
        or (item.get("oid") is not None and item["oid"] != row["oid"])
    ):
        raise ValueError("dataset_ownership_changed")
    marker = connection.execute(
        sql.SQL("SELECT tag, nonce, owner_index FROM {}._load_ownership").format(
            sql.Identifier(item["name"])
        )
    ).fetchall()
    if marker != [{"tag": tag, "nonce": item["nonce"], "owner_index": item["index"]}]:
        raise ValueError("dataset_nonce_changed")
    relations = connection.execute(
        "SELECT c.relname AS name, c.oid::bigint AS oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN ('r','v','m','p','f','S') ORDER BY c.relname",
        (item["name"],),
    ).fetchall()
    expected = ["_load_ownership", "orders"] + (["wide_rows"] if item["wide"] else [])
    if sorted(value["name"] for value in relations) != sorted(expected) or (
        item.get("relations") is not None and item["relations"] != relations
    ):
        raise ValueError("dataset_relations_changed")
    return {"oid": row["oid"], "relations": relations}


def validate_receipt(receipt, config, scope, admin):
    expected_spec = {
        key: config[key]
        for key in ("active", "wide", "rows", "wideRows", "wideCharacters")
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"version", "tag", "authority", "spec", "schemas"}
        or receipt.get("version") != 1
        or receipt.get("authority") != scope
        or receipt.get("tag") != config["tag"]
        or receipt.get("spec") != expected_spec
        or not isinstance(receipt.get("schemas"), list)
        or len(receipt["schemas"]) > config["active"]
    ):
        raise ValueError("invalid_dataset_receipt")
    seen = set()
    operations = {
        "create_schema",
        "mark_schema",
        "create_marker",
        "insert_marker",
        "create_orders",
        "insert_orders",
        "create_wide",
        "insert_wide",
        "grant_usage",
        "grant_select",
        "drop_table",
        "drop_schema",
    }
    for item in receipt["schemas"]:
        if (
            not isinstance(item, dict)
            or set(item)
            - {
                "index",
                "name",
                "owner",
                "nonce",
                "wide",
                "phase",
                "steps",
                "oid",
                "relations",
            }
            or type(item.get("index")) is not int
            or not 1 <= item["index"] <= config["active"]
            or item["index"] in seen
            or item.get("name") != f"{config['tag']}_u{item['index']:04d}"
            or item.get("owner") != admin["user"]
            or type(item.get("wide")) is not bool
            or item["wide"] != config["wide"]
            or not isinstance(item.get("nonce"), str)
            or re.fullmatch(r"[a-f0-9]{32}", item["nonce"]) is None
            or item.get("phase")
            not in {"create_pending", "ready", "delete_pending", "deleted"}
            or not isinstance(item.get("steps"), list)
            or len(item["steps"]) > 128
        ):
            raise ValueError("invalid_dataset_intent")
        seen.add(item["index"])
        tables = {"orders", "_load_ownership"} | (
            {"wide_rows"} if item["wide"] else set()
        )
        for step in item["steps"]:
            if (
                not isinstance(step, dict)
                or set(step) - {"operation", "phase", "table"}
                or step.get("operation") not in operations
                or step.get("phase") != "pending"
                or (
                    step["operation"] == "drop_table"
                    and step.get("table") not in tables
                )
                or (step["operation"] != "drop_table" and "table" in step)
            ):
                raise ValueError("invalid_dataset_step")
        if "oid" in item and (type(item["oid"]) is not int or item["oid"] < 1):
            raise ValueError("invalid_dataset_oid")
        if "relations" in item:
            relations = item["relations"]
            if (
                not isinstance(relations, list)
                or len(relations) != len(tables)
                or {entry.get("name") for entry in relations if isinstance(entry, dict)}
                != tables
                or any(
                    not isinstance(entry, dict)
                    or set(entry) != {"name", "oid"}
                    or type(entry["oid"]) is not int
                    or entry["oid"] < 1
                    for entry in relations
                )
            ):
                raise ValueError("invalid_dataset_relations")
        if item["phase"] == "ready" and ("oid" not in item or "relations" not in item):
            raise ValueError("incomplete_ready_dataset_identity")


def apply(config, path, *, cleanup=False, connect=psycopg.connect):
    scope, admin = validate(config)
    try:
        receipt = private_read(path)
    except FileNotFoundError:
        if cleanup:
            raise ValueError("dataset_receipt_required") from None
        receipt = {
            "version": 1,
            "tag": config["tag"],
            "authority": scope,
            "spec": {
                key: config[key]
                for key in ("active", "wide", "rows", "wideRows", "wideCharacters")
            },
            "schemas": [],
        }
        private_write(path, receipt)
    validate_receipt(receipt, config, scope, admin)
    for key in list(os.environ):
        if key.startswith("PG"):
            del os.environ[key]
    with connect(
        **admin,
        application_name="schemii_load_dataset_admin",
        passfile="/dev/null",
        connect_timeout=3,
        options="-c statement_timeout=10000 -c lock_timeout=1000",
        autocommit=True,
        row_factory=dict_row,
    ) as connection:
        own = connection.execute(
            "SELECT current_user AS role, session_user AS login"
        ).fetchone()
        if (
            own != {"role": admin["user"], "login": admin["user"]}
            or server_identity(connection.execute(SERVER_SQL).fetchone())
            != scope["server"]
        ):
            raise ValueError("dataset_server_or_role_changed")
        for index in range(1, config["active"] + 1):
            name = f"{config['tag']}_u{index:04d}"
            item = next(
                (value for value in receipt["schemas"] if value["index"] == index), None
            )
            if item is None:
                if cleanup:
                    continue
                # Absence check and durable nonce intent precede every mutation.
                if connection.execute(
                    "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s", (name,)
                ).fetchone():
                    raise ValueError("unowned_dataset_name_collision")
                item = {
                    "index": index,
                    "name": name,
                    "owner": admin["user"],
                    "nonce": secrets.token_hex(16),
                    "wide": config["wide"],
                    "phase": "create_pending",
                    "steps": [],
                }
                receipt["schemas"].append(item)
                private_write(path, receipt)
            if (
                item["name"] != name
                or item["owner"] != admin["user"]
                or re.fullmatch(r"[a-f0-9]{32}", item["nonce"]) is None
            ):
                raise ValueError("invalid_dataset_intent")
            with connection.transaction():
                found = inspect_schema(connection, item, config["tag"])
                if cleanup:
                    if found:
                        item["phase"] = "delete_pending"
                        private_write(path, receipt)
                        for table in (["wide_rows"] if item["wide"] else []) + [
                            "orders",
                            "_load_ownership",
                        ]:
                            item["steps"].append(
                                {
                                    "operation": "drop_table",
                                    "table": table,
                                    "phase": "pending",
                                }
                            )
                            private_write(path, receipt)
                            connection.execute(
                                sql.SQL("DROP TABLE {}.{}").format(
                                    sql.Identifier(name), sql.Identifier(table)
                                )
                            )
                        item["steps"].append(
                            {"operation": "drop_schema", "phase": "pending"}
                        )
                        private_write(path, receipt)
                        connection.execute(
                            sql.SQL("DROP SCHEMA {}").format(sql.Identifier(name))
                        )
                    item["phase"] = "deleted"
                elif found:
                    item.update(
                        found, phase="ready"
                    )  # Exact nonce, owner and object identities; never name-only adoption.
                else:
                    if item["phase"] not in {"create_pending"}:
                        raise ValueError("owned_dataset_disappeared")
                    operations = [
                        (
                            "create_schema",
                            sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                                sql.Identifier(name), sql.Identifier(admin["user"])
                            ),
                            None,
                        ),
                        (
                            "mark_schema",
                            sql.SQL("COMMENT ON SCHEMA {} IS {}").format(
                                sql.Identifier(name),
                                sql.Literal("schemii-load-dataset:" + item["nonce"]),
                            ),
                            None,
                        ),
                        (
                            "create_marker",
                            sql.SQL(
                                "CREATE TABLE {}._load_ownership(tag text, nonce text, owner_index integer)"
                            ).format(sql.Identifier(name)),
                            None,
                        ),
                        (
                            "insert_marker",
                            sql.SQL(
                                "INSERT INTO {}._load_ownership VALUES (%s,%s,%s)"
                            ).format(sql.Identifier(name)),
                            (config["tag"], item["nonce"], index),
                        ),
                        (
                            "create_orders",
                            sql.SQL(
                                "CREATE TABLE {}.orders(id bigint PRIMARY KEY, owner_nonce text NOT NULL)"
                            ).format(sql.Identifier(name)),
                            None,
                        ),
                        (
                            "insert_orders",
                            sql.SQL(
                                "INSERT INTO {}.orders SELECT n,%s FROM generate_series(1,%s) n"
                            ).format(sql.Identifier(name)),
                            (item["nonce"], config["rows"]),
                        ),
                    ]
                    if item["wide"]:
                        operations.extend(
                            [
                                (
                                    "create_wide",
                                    sql.SQL(
                                        "CREATE TABLE {}.wide_rows(id bigint PRIMARY KEY, owner_nonce text, c1 text,c2 text,c3 text,c4 text)"
                                    ).format(sql.Identifier(name)),
                                    None,
                                ),
                                (
                                    "insert_wide",
                                    sql.SQL(
                                        "INSERT INTO {}.wide_rows SELECT n,%s,repeat('W',8192),repeat('X',8192),repeat('Y',8192),repeat('Z',8192) FROM generate_series(1,129) n"
                                    ).format(sql.Identifier(name)),
                                    (item["nonce"],),
                                ),
                            ]
                        )
                    operations.append(
                        (
                            "grant_usage",
                            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                                sql.Identifier(name), sql.Identifier(scope["role"])
                            ),
                            None,
                        )
                    )
                    for table in ["orders"] + (["wide_rows"] if item["wide"] else []):
                        operations.append(
                            (
                                "grant_select",
                                sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
                                    sql.Identifier(name),
                                    sql.Identifier(table),
                                    sql.Identifier(scope["role"]),
                                ),
                                None,
                            )
                        )
                    for kind, query, parameters in operations:
                        item["steps"].append({"operation": kind, "phase": "pending"})
                        private_write(path, receipt)
                        connection.execute(query, parameters)
                    item.update(
                        inspect_schema(connection, item, config["tag"]), phase="ready"
                    )
            private_write(
                path, receipt
            )  # After commit; uncertain commit remains a nonce-bound intent.
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "cleanup"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    try:
        result = apply(
            private_read(args.config), args.receipt, cleanup=args.action == "cleanup"
        )
        print(
            json.dumps(
                {
                    "status": "ready" if args.action == "prepare" else "cleaned",
                    "schemas": len(result["schemas"]),
                }
            )
        )
        return 0
    except Exception:
        print(json.dumps({"status": "unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
