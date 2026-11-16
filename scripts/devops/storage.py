"""Explicit, fail-closed PostgreSQL analytics migration contract (not an importer)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, NoReturn
import uuid

import psycopg

MIGRATIONS = Path(__file__).with_name("migrations")
ROLES = ("owner", "migrator", "collector", "reader", "maintenance")


class Refusal(Exception):
    def __init__(self, reason: str, next_action: str):
        self.reason = reason
        self.next_action = next_action


def refuse(reason: str, next_action: str) -> NoReturn:
    raise Refusal(reason, next_action)


def connection_config(path: Path) -> dict[str, Any]:
    """No ambient libpq defaults, options, DSNs, or credential values in output."""
    if any(p.is_symlink() for p in (path, *path.parents)):
        refuse("unsafe-profile", "Use an owned private regular file without symlinks.")
    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        refuse(
            "unsafe-profile", "Set the owned connection profile permissions to 0600."
        )
    data = json.loads(path.read_text())
    required = {"host", "port", "dbname", "user", "passfile"}
    if (
        not isinstance(data, dict)
        or not required <= data.keys()
        or data.keys() - required - {"passfile", "sslrootcert"}
    ):
        refuse(
            "invalid-profile",
            "Use only host, port, dbname, user, passfile and sslrootcert.",
        )
    if (
        any(not isinstance(data[k], str) or not data[k] for k in data if k != "port")
        or type(data["port"]) is not int
        or not 1 <= data["port"] <= 65535
    ):
        refuse(
            "invalid-profile", "Supply explicit connection strings and an integer port."
        )
    if not re.fullmatch(r"devops_analytics_[a-z0-9_]{1,48}", data["dbname"]):
        refuse(
            "wrong-target", "Use a newly provisioned devops_analytics_NAME database."
        )
    if not (
        data["host"].startswith("/") or re.fullmatch(r"[A-Za-z0-9.-]+", data["host"])
    ):
        refuse("invalid-profile", "Use one Unix socket directory or one TLS hostname.")
    # An explicit private passfile prevents the default ~/.pgpass lookup.
    passfile = Path(data["passfile"])
    if (
        not passfile.is_absolute()
        or passfile.is_symlink()
        or not passfile.is_file()
        or passfile.stat().st_uid != os.getuid()
        or passfile.stat().st_mode & 0o077
    ):
        refuse(
            "unsafe-profile",
            "Use an owned 0600 absolute passfile (empty for private socket trust).",
        )
    data.update(
        sslmode="disable" if data["host"].startswith("/") else "verify-full",
        connect_timeout=5,
        application_name="devops-storage-v1",
    )
    return data


def migration_files() -> list[tuple[int, str, str]]:
    result = []
    for index, path in enumerate(sorted(MIGRATIONS.glob("*.sql")), 1):
        if not re.fullmatch(rf"{index:03}_[a-z_]+\.sql", path.name):
            refuse(
                "invalid-migration-set",
                "Restore the contiguous reviewed migration files.",
            )
        raw = path.read_bytes()
        result.append((index, hashlib.sha256(raw).hexdigest(), raw.decode("utf-8")))
    if not result:
        refuse("invalid-migration-set", "Restore the reviewed migration files.")
    return result


def catalog_snapshot(conn: psycopg.Connection) -> list:
    """Definitions, owners, ACLs, columns, constraints, triggers, domains and functions."""
    queries = [
        "SELECT nspname, pg_get_userbyid(nspowner), ARRAY(SELECT x::text FROM unnest(nspacl) x ORDER BY x::text) FROM pg_namespace WHERE nspname IN ('devops','devops_control') ORDER BY 1",
        "SELECT n.nspname,c.relname,c.relkind,pg_get_userbyid(c.relowner),ARRAY(SELECT x::text FROM unnest(c.relacl) x ORDER BY x::text),c.relrowsecurity,c.relforcerowsecurity,c.reloptions,c.relpersistence,c.relreplident FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('devops','devops_control') ORDER BY 1,2",
        "SELECT n.nspname,c.relname,a.attname,a.attnum,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated,ARRAY(SELECT x::text FROM unnest(a.attacl) x ORDER BY x::text) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname IN ('devops','devops_control') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,4",
        "SELECT n.nspname,conname,conrelid::regclass::text,contypid::regtype::text,pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname IN ('devops','devops_control') ORDER BY 1,2,3,4",
        "SELECT n.nspname,c.relname,pg_get_viewdef(c.oid,true) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='devops' AND c.relkind='v' ORDER BY 1,2",
        "SELECT pg_get_triggerdef(t.oid),t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='devops' AND NOT t.tgisinternal ORDER BY 1",
        "SELECT pg_get_functiondef(p.oid),pg_get_userbyid(p.proowner),ARRAY(SELECT x::text FROM unnest(p.proacl) x ORDER BY x::text),p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('devops','devops_control') ORDER BY 1",
        "SELECT n.nspname,t.typname,t.typtype,format_type(t.typbasetype,t.typtypmod),t.typnotnull,t.typdefault,ARRAY(SELECT x::text FROM unnest(t.typacl) x ORDER BY x::text),pg_get_userbyid(t.typowner) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname IN ('devops','devops_control') ORDER BY 1,2",
        "SELECT n.nspname,c.relname,i.indisvalid,i.indisready,i.indislive FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('devops','devops_control') ORDER BY 1,2",
        "SELECT indexdef FROM pg_indexes WHERE schemaname IN ('devops','devops_control') ORDER BY 1",
        "SELECT schemaname,tablename,policyname,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname IN ('devops','devops_control') ORDER BY 1,2,3",
        "SELECT pg_get_userbyid(defaclrole),defaclnamespace::regnamespace::text,defaclobjtype,ARRAY(SELECT x::text FROM unnest(defaclacl) x ORDER BY x::text) FROM pg_default_acl WHERE defaclrole=(SELECT oid FROM pg_roles WHERE rolname='devops_owner') ORDER BY 1,2,3",
    ]
    return [conn.execute(query).fetchall() for query in queries]


def catalog_digest(conn: psycopg.Connection) -> str:
    rows = catalog_snapshot(conn)
    return hashlib.sha256(
        json.dumps(rows, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def preflight(conn: psycopg.Connection, name: str, identity: uuid.UUID) -> None:
    if not re.fullmatch(r"devops_analytics_[a-z0-9_]{1,48}", name):
        refuse(
            "wrong-target", "Use the explicit dedicated devops_analytics_NAME database."
        )
    row = conn.execute(
        "SELECT current_database(), shobj_description(oid,'pg_database'), pg_get_userbyid(datdba), datacl::text FROM pg_database WHERE datname=current_database()"
    ).fetchone()
    if (
        not row
        or row[0] != name
        or row[1] != f"schemii-devops-v1:{identity}"
        or row[2] != "devops_maintenance"
    ):
        refuse(
            "wrong-target",
            "Verify the explicit database name, UUID marker and maintenance owner with its provisioner.",
        )
    if conn.info.server_version < 150000 or conn.info.server_version >= 190000:
        refuse(
            "unsupported-server",
            "Use reviewed PostgreSQL 15 through 18; only the reported test version is verified.",
        )
    unexpected = conn.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('public','devops','devops_control','information_schema') AND nspname !~ '^pg_' UNION ALL SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' UNION ALL SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' UNION ALL SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' UNION ALL SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public' UNION ALL SELECT pg_describe_object(d.classid,d.objid,d.objsubid) FROM pg_depend d JOIN pg_namespace n ON n.oid=d.refobjid WHERE d.refclassid='pg_namespace'::regclass AND n.nspname='public'"
    ).fetchall()
    if unexpected:
        refuse(
            "non-dedicated-target",
            "Use a dedicated empty analytics database; preserve foreign objects.",
        )
    roles = conn.execute(
        "SELECT rolname,rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication,rolinherit FROM pg_roles WHERE rolname = ANY(%s) ORDER BY rolname",
        ([f"devops_{r}" for r in ROLES],),
    ).fetchall()
    if (
        len(roles) != 5
        or any(any(r[1:-1]) for r in roles)
        or any(r[0] == "devops_migrator" and r[-1] for r in roles)
    ):
        refuse(
            "unsafe-roles",
            "Restore the five NOLOGIN nonprivileged analytics authority roles.",
        )
    # Check actual membership edges, not pg_has_role(superuser,...) shortcuts.
    edges = conn.execute(
        "SELECT parent.rolname,child.rolname FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles child ON child.oid=m.member"
    ).fetchall()
    parents: dict[str, set[str]] = {}
    for parent, child in edges:
        parents.setdefault(child, set()).add(parent)

    def ancestors(role: str) -> set[str]:
        found: set[str] = set()
        todo = list(parents.get(role, set()))
        while todo:
            item = todo.pop()
            if item not in found:
                found.add(item)
                todo.extend(parents.get(item, set()))
        return found

    if ancestors("devops_migrator") != {"devops_owner"} or any(
        ancestors(f"devops_{r}")
        for r in ("owner", "collector", "reader", "maintenance")
    ):
        refuse("unsafe-roles", "Remove unexpected authority-role memberships.")
    for actor, dangerous in conn.execute(
        "SELECT rolname,rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication FROM pg_roles"
    ):
        memberships = ancestors(actor)
        if (
            memberships & {"devops_collector", "devops_reader"}
            and conn.execute(
                "SELECT has_database_privilege(%s,current_database(),'CREATE') OR has_database_privilege(%s,current_database(),'TEMP') OR has_schema_privilege(%s,'public','CREATE') OR has_schema_privilege(%s,'public','USAGE')",
                (actor, actor, actor, actor),
            ).fetchall()[0][0]
        ):
            refuse(
                "unsafe-runtime-actor",
                "Revoke runtime login database CREATE/TEMP and public-schema CREATE/USAGE privileges.",
            )
        if memberships & {"devops_collector", "devops_reader"} and (
            dangerous or memberships - {"devops_collector", "devops_reader"}
        ):
            refuse(
                "unsafe-runtime-actor",
                "Remove runtime login privileges or authority memberships.",
            )
    session = conn.execute(
        "SELECT rolname,rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication FROM pg_roles WHERE rolname=session_user"
    ).fetchone()
    if not session or session[1] or "devops_migrator" not in ancestors(session[0]):
        refuse(
            "unsafe-migration-actor",
            "Connect through a dedicated non-superuser login granted only devops_migrator.",
        )
    if ancestors(session[0]) != {"devops_migrator", "devops_owner"}:
        refuse(
            "unsafe-migration-actor",
            "Remove unrelated memberships from the migration login.",
        )
    # PUBLIC has no connect/create/temp; public schema has no writable grants.
    public_grants = conn.execute(
        "SELECT privilege_type FROM pg_database d, aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a WHERE d.datname=current_database() AND a.grantee=0 UNION ALL SELECT privilege_type FROM pg_namespace n, aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a WHERE n.nspname='public' AND (a.grantee=0 OR a.grantee IN (SELECT oid FROM pg_roles WHERE rolname IN ('devops_collector','devops_reader')))"
    ).fetchall()
    if public_grants:
        refuse(
            "unsafe-target-grants",
            "Revoke PUBLIC database and public-schema privileges using the provisioner.",
        )
    for role in ("devops_collector", "devops_reader"):
        if conn.execute(
            "SELECT has_database_privilege(%s,current_database(),'CREATE') OR has_database_privilege(%s,current_database(),'TEMP')",
            (role, role),
        ).fetchall()[0][0]:
            refuse(
                "unsafe-target-grants",
                "Revoke runtime database CREATE/TEMP privileges.",
            )


def operate(
    conn: psycopg.Connection, action: str, name: str, identity: uuid.UUID
) -> dict:
    files = migration_files()
    with conn.transaction():
        # Transaction-scoped serialization; no persistent target writes before guards.
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SELECT pg_advisory_xact_lock(278, 1)")
        preflight(conn, name, identity)
        conn.execute("SET LOCAL ROLE devops_owner")
        installed = conn.execute(
            "SELECT to_regclass('devops_control.identity'),to_regclass('devops_control.migrations')"
        ).fetchone()
        assert installed is not None
        if not any(installed):
            if conn.execute(
                "SELECT count(*) FROM pg_namespace WHERE nspname IN ('devops','devops_control')"
            ).fetchall()[0][0]:
                refuse(
                    "divergent-schema",
                    "Preserve and investigate the unexpected unversioned analytics schemas.",
                )
            applied = []
        elif not all(installed):
            refuse(
                "divergent-schema",
                "Restore the complete reviewed control schema from private backup.",
            )
        else:
            records = conn.execute(
                "SELECT singleton,database_id,contract_version FROM devops_control.identity"
            ).fetchall()
            if records != [(True, identity, 1)]:
                refuse(
                    "wrong-schema-identity",
                    "Verify the stored UUID and contract version; never overwrite another identity.",
                )
            applied = conn.execute(
                "SELECT version,checksum,schema_digest FROM devops_control.migrations ORDER BY version"
            ).fetchall()
            if (
                not applied
                or [(r[0], r[1]) for r in applied]
                != [(v, c) for v, c, _ in files[: len(applied)]]
                or len(applied) > len(files)
            ):
                refuse(
                    "divergent-version",
                    "Restore reviewed migration files; investigate unknown versions or checksum changes.",
                )
            if applied[-1][2] != catalog_digest(conn):
                refuse(
                    "divergent-schema",
                    "Investigate schema/ACL drift and restore the reviewed schema; do not restamp it.",
                )
        pending = files[len(applied) :]
        if action == "migrate":
            for version, checksum, body in pending:
                conn.execute(body, prepare=False)
                if version == 1:
                    conn.execute(
                        "INSERT INTO devops_control.identity(database_id,contract_version) VALUES (%s,1)",
                        (identity,),
                    )
                conn.execute(
                    "INSERT INTO devops_control.migrations VALUES (%s,%s,%s)",
                    (version, checksum, catalog_digest(conn)),
                )
        return {
            "contract_version": 1,
            "database_id": str(identity),
            "schema_version": len(files) if action == "migrate" else len(applied),
            "pending_versions": []
            if action == "migrate"
            else [v for v, _, _ in pending],
            "applied_versions": [v for v, _, _ in pending]
            if action == "migrate"
            else [],
            "migration_checksums": {str(v): c for v, c, _ in files},
            "server_version": conn.info.server_version,
        }


class Parser(argparse.ArgumentParser):
    json_errors = False

    def error(self, message: str) -> NoReturn:
        result = {
            "schema": 1,
            "ok": False,
            "reason": "invalid-arguments",
            "exit_code": 2,
            "next_action": "Use --help; supply plan/migrate/status, --profile, --database-name and --database-id.",
        }
        if self.json_errors:
            print(json.dumps(result, sort_keys=True))
        else:
            print(result["next_action"], file=sys.stderr)
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = Parser(
        description=__doc__,
        epilog="Provision separately with provision.sql. No default database, schema reset, importer or down migration. See docs/devops-metrics.md.",
    )
    parser.add_argument("action", choices=("plan", "migrate", "status"))
    parser.add_argument(
        "--profile",
        type=Path,
        required=True,
        help="Owned 0600 JSON connection profile; never a DSN",
    )
    parser.add_argument("--database-name", required=True)
    parser.add_argument(
        "--database-id", required=True, help="Expected provisioned database UUID"
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the v1 result contract"
    )
    parser.json_errors = "--json" in (sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    result: dict[str, Any] = {"schema": 1, "ok": False, "action": args.action}
    code = 2
    connection_attempted = False
    try:
        identity = uuid.UUID(args.database_id)
        config = connection_config(args.profile)
        if config["dbname"] != args.database_name:
            refuse(
                "wrong-target",
                "Match the explicit database name to the provisioned private profile.",
            )
        # Remove *all* ambient libpq parameters; retain ordinary process environment.
        # psycopg/libpq reads these only while opening the synchronous connection.
        ambient = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("PG")}
        try:
            connection_attempted = True
            with psycopg.connect(**config, autocommit=True) as conn:
                detail = operate(conn, args.action, args.database_name, identity)
        finally:
            os.environ.update(ambient)
        result.update(
            ok=True,
            reason="ready" if not detail["pending_versions"] else "migration-pending",
            next_action="Use the collector SQL contract."
            if not detail["pending_versions"]
            else "Review plan, then run migrate with the same explicit identity.",
            **detail,
        )
        code = 0
    except Refusal as error:
        result.update(reason=error.reason, next_action=error.next_action)
    except (ValueError, OSError, psycopg.Error) as error:
        if not connection_attempted and not isinstance(error, psycopg.Error):
            result.update(
                reason="invalid-input",
                next_action="Check the private profile file and UUID; no database connection was attempted.",
            )
        else:
            code = 3
            result.update(
                reason="database-error",
                sqlstate=getattr(error, "sqlstate", None),
                commit_state="unknown",
                next_action="Preserve the original diagnostics. Commit state is unknown; do not replay migrate. Run status with the same explicit database name and UUID, then reconcile migration versions/checksums with the provisioner before any retry.",
            )
    result["exit_code"] = code
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            f"{args.action}: {result['reason']} (exit {code})\n{result['next_action']}"
        )
        if result["ok"]:
            print(
                f"Schema version: {result['schema_version']}; pending: {result['pending_versions']}"
            )
    return code


if __name__ == "__main__":
    sys.exit(main())
