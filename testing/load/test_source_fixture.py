"""Offline safety controls for the disposable source dataset provisioner.

All credentials are synthetic. SQL execution uses a strict transactional fake;
the actual private-file and v2 authority validators remain in the exercised path.
"""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

import pytest
from psycopg import sql
from psycopg.rows import dict_row

from testing.load import source_fixture as fixture


class LostAcknowledgment(RuntimeError):
    pass


def quoted(value):
    return '"' + value.replace('"', '""') + '"'


def identifier(value):
    assert value.startswith('"') and value.endswith('"')
    return value[1:-1].replace('""', '"')


class Rows:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def fetchone(self):
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Transaction:
    def __init__(self, server):
        self.server = server

    def __enter__(self):
        self.before = deepcopy(self.server.schemas)
        self.server.events.append("begin")

    def __exit__(self, error_type, _error, _traceback):
        server = self.server
        if error_type:
            server.events.append("rollback")
            if server.rollback_applies:
                server.schemas = self.before
            if server.rollback_error:
                server.rollback_error = False
                raise LostAcknowledgment("rollback_ack_sentinel")
        elif server.commit_error:
            mode, server.commit_error = server.commit_error, None
            server.events.append("commit_ack_lost:" + mode)
            if mode == "before":
                server.schemas = self.before
            raise LostAcknowledgment("commit_ack_sentinel")
        else:
            server.events.append("commit")


class StrictPostgres:
    """Reject unknown SQL and inspect the durable intent before every mutation."""

    token = r'"(?:[^"]|"")*"'

    def __init__(self, config, server_row, receipt):
        self.config, self.server_row, self.receipt = config, server_row, receipt
        self.schemas = {}
        self.calls, self.statements, self.mutations, self.events = [], [], [], []
        self.next_oid = 40001
        self.role = {
            "role": config["sourceAdminRole"],
            "login": config["sourceAdminRole"],
        }
        self.commit_error = None
        self.rollback_error, self.rollback_applies = False, True
        self.fail_operation = None

    def connect(self, **options):
        expected = dict(
            self.config["adminConnection"],
            application_name="schemii_load_dataset_admin",
            passfile="/dev/null",
            connect_timeout=3,
            options="-c statement_timeout=10000 -c lock_timeout=1000",
            autocommit=True,
            row_factory=dict_row,
        )
        assert options == expected
        assert not any(key.startswith("PG") for key in os.environ)
        self.calls.append(options)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.events.append("connection_closed")

    def transaction(self):
        return Transaction(self)

    def oid(self):
        self.next_oid += 1
        return self.next_oid

    def execute(self, query, parameters=None):
        text = query.as_string() if isinstance(query, sql.Composable) else query
        self.statements.append((text, deepcopy(parameters)))
        assert "CASCADE" not in text.upper()
        if text == "SELECT current_user AS role, session_user AS login":
            return Rows([self.role])
        if text == fixture.SERVER_SQL:
            return Rows([self.server_row])
        if text == "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s":
            value = self.schemas.get(parameters[0])
            return Rows([{"oid": value["oid"]}] if value else [])
        if text.startswith("SELECT n.oid::bigint AS oid, r.rolname AS owner,"):
            value = self.schemas.get(parameters[0])
            return Rows(
                [{key: value[key] for key in ("oid", "owner", "marker")}]
                if value
                else []
            )
        if text.startswith(
            "SELECT c.relname AS name, c.oid::bigint AS oid FROM pg_catalog.pg_class"
        ):
            value = self.schemas[parameters[0]]
            return Rows(
                [
                    {"name": name, "oid": oid}
                    for name, oid in sorted(value["relations"].items())
                ]
            )
        match = re.fullmatch(
            rf"SELECT tag, nonce, owner_index FROM ({self.token})\._load_ownership",
            text,
        )
        if match:
            return Rows(self.schemas[identifier(match[1])]["ownership"])
        return self.mutate(text, parameters)

    def mutate(self, text, parameters):
        patterns = [
            (
                "create_schema",
                rf"CREATE SCHEMA ({self.token}) AUTHORIZATION ({self.token})",
            ),
            (
                "mark_schema",
                rf"COMMENT ON SCHEMA ({self.token}) IS '(schemii-load-dataset:[a-f0-9]{{32}})'",
            ),
            (
                "create_marker",
                rf"CREATE TABLE ({self.token})\._load_ownership\(tag text, nonce text, owner_index integer\)",
            ),
            (
                "insert_marker",
                rf"INSERT INTO ({self.token})\._load_ownership VALUES \(%s,%s,%s\)",
            ),
            (
                "create_orders",
                rf"CREATE TABLE ({self.token})\.orders\(id bigint PRIMARY KEY, owner_nonce text NOT NULL\)",
            ),
            (
                "insert_orders",
                rf"INSERT INTO ({self.token})\.orders SELECT n,%s FROM generate_series\(1,%s\) n",
            ),
            (
                "create_wide",
                rf"CREATE TABLE ({self.token})\.wide_rows\(id bigint PRIMARY KEY, owner_nonce text, c1 text,c2 text,c3 text,c4 text\)",
            ),
            (
                "insert_wide",
                rf"INSERT INTO ({self.token})\.wide_rows SELECT n,%s,repeat\('W',8192\),repeat\('X',8192\),repeat\('Y',8192\),repeat\('Z',8192\) FROM generate_series\(1,129\) n",
            ),
            ("grant_usage", rf"GRANT USAGE ON SCHEMA ({self.token}) TO ({self.token})"),
            (
                "grant_select",
                rf"GRANT SELECT ON ({self.token})\.({self.token}) TO ({self.token})",
            ),
            ("drop_table", rf"DROP TABLE ({self.token})\.({self.token})"),
            ("drop_schema", rf"DROP SCHEMA ({self.token})"),
        ]
        matches = [
            (kind, match)
            for kind, pattern in patterns
            if (match := re.fullmatch(pattern, text))
        ]
        assert len(matches) == 1, f"Unadvertised SQL: {text}"
        kind, match = matches[0]
        name = identifier(match[1])
        receipt = json.loads(self.receipt.read_text())
        (item,) = [item for item in receipt["schemas"] if item["name"] == name]
        assert item["owner"] == self.config["sourceAdminRole"]
        assert item["phase"] == (
            "delete_pending" if kind.startswith("drop_") else "create_pending"
        )
        assert item["steps"][-1]["operation"] == kind
        assert item["steps"][-1]["phase"] == "pending"
        self.events.append(("durable_intent", name, kind, deepcopy(item)))
        if kind == self.fail_operation:
            self.fail_operation = None
            raise RuntimeError("mutation_sentinel:" + kind)
        value = self.schemas.get(name)
        if kind == "create_schema":
            assert value is None
            assert identifier(match[2]) == item["owner"]
            self.schemas[name] = {
                "oid": self.oid(),
                "owner": item["owner"],
                "marker": None,
                "relations": {},
                "ownership": [],
                "rows": {},
                "grants": [],
            }
        elif kind == "mark_schema":
            assert match[2] == "schemii-load-dataset:" + item["nonce"]
            value["marker"] = match[2]
        elif kind.startswith("create_"):
            table = {
                "create_marker": "_load_ownership",
                "create_orders": "orders",
                "create_wide": "wide_rows",
            }[kind]
            assert table not in value["relations"]
            value["relations"][table] = self.oid()
        elif kind == "insert_marker":
            assert parameters == (self.config["tag"], item["nonce"], item["index"])
            value["ownership"] = [
                dict(zip(("tag", "nonce", "owner_index"), parameters))
            ]
        elif kind == "insert_orders":
            assert parameters == (item["nonce"], 513)
            value["rows"]["orders"] = (1, 513, item["nonce"])
        elif kind == "insert_wide":
            assert parameters == (item["nonce"],)
            value["rows"]["wide_rows"] = (1, 129, 4, 8192, "WXYZ", item["nonce"])
        elif kind == "grant_usage":
            value["grants"].append(("USAGE", None, identifier(match[2])))
        elif kind == "grant_select":
            value["grants"].append(
                ("SELECT", identifier(match[2]), identifier(match[3]))
            )
        elif kind == "drop_table":
            table = identifier(match[2])
            assert item["steps"][-1]["table"] == table
            del value["relations"][table]
        elif kind == "drop_schema":
            assert not value["relations"]
            del self.schemas[name]
        self.mutations.append((kind, name))
        return Rows([])


@pytest.fixture
def owned(tmp_path):
    tmp_path.chmod(0o700)
    tag = "load_1234567890abcdef"
    server = {
        "address": "172.21.0.8",
        "port": 5432,
        "postmasterStart": "2026-11-12T10:11:12.000013+00:00",
        "databaseOid": 71234,
        "fixtureId": "b" * 64,
    }
    endpoint = {"host": "127.0.0.19", "port": 6543, "sslmode": "verify-full"}
    source_role, admin_role = 'fixture_Source_"Reader', 'fixture_Admin_"Owner'
    proof = tmp_path / "provisioned.json"
    scope = {
        "kind": "exclusive_source_role",
        "exclusiveToRun": True,
        "database": "fixture_owned_database",
        "role": source_role,
        "applicationName": "schemii",
        "provisioningReceipt": str(proof),
        "endpoint": endpoint,
        "server": server,
    }
    witness = {
        "version": 2,
        "runId": tag,
        "authority": scope,
        "connection": dict(
            endpoint,
            dbname=scope["database"],
            user=source_role,
            password="synthetic-reader-password",
        ),
    }
    fixture.private_write(
        str(proof), {"version": 2, "status": "ready", "runId": tag, **scope}
    )
    witness_path = tmp_path / "witness.json"
    fixture.private_write(str(witness_path), witness)
    config = {
        "version": 1,
        "tag": tag,
        "active": 1,
        "wide": False,
        "rows": 513,
        "wideRows": 129,
        "wideCharacters": 8192,
        "maximumBytes": 1024 * 1024 * 1024,
        "sourceWitnessConfig": str(witness_path),
        "sourceAdminRole": admin_role,
        "adminConnection": dict(
            endpoint,
            dbname=scope["database"],
            user=admin_role,
            password="synthetic-admin-password",
        ),
    }
    row = {
        "server_address": server["address"],
        "server_port": server["port"],
        "postmaster_start": datetime.fromisoformat(server["postmasterStart"]),
        "database_oid": server["databaseOid"],
        "fixture_marker": "schemii-load:" + server["fixtureId"],
    }
    receipt = tmp_path / "dataset.json"
    pg = StrictPostgres(config, row, receipt)
    return config, pg, receipt, witness


def apply(owned, *, cleanup=False):
    config, pg, receipt, _ = owned
    return fixture.apply(config, str(receipt), cleanup=cleanup, connect=pg.connect)


def write_receipt(path, value):
    fixture.private_write(str(path), value)


def test_create_persists_each_intent_before_sql_and_grants_only_read_access(
    owned, monkeypatch
):
    config, pg, path, witness = owned
    config.update(active=2, wide=True)
    monkeypatch.setenv("PGHOST", "forbidden-ambient-host")
    monkeypatch.setenv("PGSERVICE", "forbidden-ambient-service")
    result = apply(owned)
    assert result == fixture.private_read(str(path))
    assert result["authority"] == {"runId": config["tag"], **witness["authority"]}
    assert path.stat().st_mode & 0o777 == 0o600
    assert (
        pg.calls[0]["host"]
        == witness["authority"]["endpoint"]["host"]
        != pg.server_row["server_address"]
    )
    assert len(result["schemas"]) == 2
    for item in result["schemas"]:
        assert item["phase"] == "ready" and item["oid"] > 40000
        value = pg.schemas[item["name"]]
        assert item["relations"] == [
            {"name": name, "oid": oid}
            for name, oid in sorted(value["relations"].items())
        ]
        assert value["rows"]["orders"] == (1, 513, item["nonce"])
        assert value["rows"]["wide_rows"] == (1, 129, 4, 8192, "WXYZ", item["nonce"])
        assert value["grants"] == [
            ("USAGE", None, witness["authority"]["role"]),
            ("SELECT", "orders", witness["authority"]["role"]),
            ("SELECT", "wide_rows", witness["authority"]["role"]),
        ]
        assert len(item["steps"]) == 11
    assert pg.events.count("commit") == 2
    assert (
        len([event for event in pg.events if isinstance(event, tuple)])
        == len(pg.mutations)
        == 22
    )
    before = list(pg.mutations)
    assert apply(owned) == result
    assert pg.mutations == before


@pytest.mark.parametrize(
    "change",
    [
        {"active": 0},
        {"active": 1001},
        {"active": True},
        {"active": 1.0},
        {"wide": 1},
        {"rows": 512},
        {"wideRows": 130},
        {"wideCharacters": 8191},
        {"tag": "retained_database"},
        {"maximumBytes": 513 * 128 - 1},
        {"maximumBytes": 1024 * 1024 * 1024 + 1},
        {"maximumBytes": True},
    ],
)
def test_invalid_population_or_budget_fails_before_connection(owned, change):
    owned[0].update(change)
    with pytest.raises(ValueError):
        apply(owned)
    assert not owned[1].calls and not owned[2].exists()


def test_advertised_thousand_normal_datasets_fit_only_their_actual_config_budget(owned):
    config, pg, _, _ = owned
    config.update(active=1000, maximumBytes=1000 * 513 * 128)
    scope, admin = fixture.validate(config)
    assert scope["runId"] == config["tag"] and admin == config["adminConnection"]
    config["maximumBytes"] -= 1
    with pytest.raises(ValueError, match="owned_dataset_budget"):
        fixture.validate(config)
    config.update(wide=True, maximumBytes=1024 * 1024 * 1024)
    with pytest.raises(ValueError, match="owned_dataset_budget"):
        fixture.validate(config)
    assert not pg.calls


@pytest.mark.parametrize(
    "key,value",
    [
        ("host", "192.0.2.99"),
        ("port", 6544),
        ("dbname", "foreign_db"),
        ("sslmode", "disable"),
        ("user", "wrong_admin"),
        ("password", ""),
    ],
)
def test_held_source_endpoint_and_explicit_admin_mapping_cannot_drift(
    owned, key, value
):
    owned[0]["adminConnection"][key] = value
    with pytest.raises(ValueError, match="explicit_dataset_provisioner_required"):
        apply(owned)
    assert not owned[1].calls and not owned[2].exists()


@pytest.mark.parametrize("misconfiguration", ["reader_as_admin", "libpq_service"])
def test_reader_role_and_ambient_connection_parameters_cannot_provision(
    owned, misconfiguration
):
    config, pg, path, witness = owned
    if misconfiguration == "reader_as_admin":
        config["sourceAdminRole"] = config["adminConnection"]["user"] = witness[
            "authority"
        ]["role"]
    else:
        config["adminConnection"]["service"] = "ambient-retained-target"
    with pytest.raises(ValueError, match="explicit_dataset_provisioner_required"):
        apply(owned)
    assert not pg.calls and not path.exists()


@pytest.mark.parametrize(
    "proof", ["legacy", "wrong_role", "wrong_nonce", "retargeted_endpoint"]
)
def test_v2_provisioned_authority_is_required_before_any_connection(owned, proof):
    config, pg, path, witness = owned
    if proof == "legacy":
        witness["version"] = 1
    elif proof == "retargeted_endpoint":
        witness["authority"]["endpoint"]["host"] = "192.0.2.77"
    else:
        receipt_path = witness["authority"]["provisioningReceipt"]
        receipt = fixture.private_read(receipt_path)
        if proof == "wrong_role":
            receipt["role"] = "retained_foreign_role"
        else:
            receipt["server"]["fixtureId"] = "c" * 64
        fixture.private_write(receipt_path, receipt)
    fixture.private_write(config["sourceWitnessConfig"], witness)
    with pytest.raises(ValueError):
        apply(owned)
    assert not pg.calls and not path.exists()


@pytest.mark.parametrize(
    "key,value",
    [
        ("server_address", "192.0.2.8"),
        ("server_port", 6545),
        ("database_oid", 99999),
        ("fixture_marker", "schemii-load:" + "d" * 64),
        ("postmaster_start", datetime(2026, 11, 11, tzinfo=timezone.utc)),
    ],
)
def test_actual_server_identity_mismatch_never_reaches_ddl(owned, key, value):
    owned[1].server_row[key] = value
    with pytest.raises(ValueError, match="dataset_server_or_role_changed"):
        apply(owned)
    assert not owned[1].mutations


@pytest.mark.parametrize("key", ["role", "login"])
def test_effective_and_session_admin_roles_must_both_match(owned, key):
    owned[1].role[key] = "foreign_role_sentinel"
    with pytest.raises(ValueError, match="dataset_server_or_role_changed"):
        apply(owned)
    assert not owned[1].mutations


def test_absent_receipt_cannot_adopt_or_remove_existing_same_named_schema(owned):
    config, pg, path, _ = owned
    name = config["tag"] + "_u0001"
    pg.schemas[name] = {
        "oid": 98765,
        "owner": "foreign-owner-sentinel",
        "marker": "foreign-marker",
    }
    before = deepcopy(pg.schemas)
    with pytest.raises(ValueError, match="unowned_dataset_name_collision"):
        apply(owned)
    assert pg.schemas == before and not pg.mutations
    path.unlink()
    with pytest.raises(ValueError, match="dataset_receipt_required"):
        apply(owned, cleanup=True)
    assert pg.schemas == before and len(pg.calls) == 1


@pytest.mark.parametrize("mode", ["before", "after"])
def test_uncertain_create_commit_reconciles_exact_nonce_without_duplicate_ddl(
    owned, mode
):
    config, pg, path, _ = owned
    pg.commit_error = mode
    with pytest.raises(LostAcknowledgment, match="commit_ack_sentinel"):
        apply(owned)
    pending = fixture.private_read(str(path))["schemas"][0]
    assert pending["phase"] == "create_pending" and "oid" not in pending
    assert bool(pg.schemas) is (mode == "after")
    before = len(pg.mutations)
    ready = apply(owned)["schemas"][0]
    assert ready["nonce"] == pending["nonce"] and ready["phase"] == "ready"
    assert len(pg.mutations) == before + (8 if mode == "before" else 0)
    assert list(pg.schemas) == [config["tag"] + "_u0001"]


@pytest.mark.parametrize("rollback_error", [False, True])
def test_failed_transaction_retains_intent_and_rolls_back_before_exact_retry(
    owned, rollback_error
):
    pg, path = owned[1:3]
    pg.fail_operation, pg.rollback_error = "insert_orders", rollback_error
    with pytest.raises(
        RuntimeError,
        match="rollback_ack_sentinel"
        if rollback_error
        else "mutation_sentinel:insert_orders",
    ):
        apply(owned)
    pending = fixture.private_read(str(path))["schemas"][0]
    assert (
        pending["phase"] == "create_pending"
        and pending["steps"][-1]["operation"] == "insert_orders"
    )
    assert pg.schemas == {} and "rollback" in pg.events
    assert apply(owned)["schemas"][0]["nonce"] == pending["nonce"]


def test_uncertain_partial_rollback_cannot_adopt_or_clean_an_incomplete_schema(owned):
    pg = owned[1]
    pg.fail_operation, pg.rollback_error, pg.rollback_applies = (
        "create_orders",
        True,
        False,
    )
    with pytest.raises(LostAcknowledgment, match="rollback_ack_sentinel"):
        apply(owned)
    before, mutations = deepcopy(pg.schemas), list(pg.mutations)
    for cleanup in (False, True):
        with pytest.raises(ValueError, match="dataset_relations_changed"):
            apply(owned, cleanup=cleanup)
        assert pg.schemas == before and pg.mutations == mutations


def test_failed_intent_persistence_blocks_the_corresponding_sql(owned, monkeypatch):
    pg = owned[1]
    real_write = fixture.private_write

    def fail_before_persistence(path, receipt):
        if (
            receipt["schemas"]
            and receipt["schemas"][0]["steps"]
            and receipt["schemas"][0]["steps"][-1]["operation"] == "create_orders"
        ):
            raise OSError("intent_write_sentinel")
        real_write(path, receipt)

    monkeypatch.setattr(fixture, "private_write", fail_before_persistence)
    with pytest.raises(OSError, match="intent_write_sentinel"):
        apply(owned)
    assert all(kind != "create_orders" for kind, _ in pg.mutations)
    assert pg.schemas == {}


def test_missing_final_receipt_after_commit_resumes_from_persisted_nonce(
    owned, monkeypatch
):
    real_write = fixture.private_write

    def fail_after_commit(path, receipt):
        if receipt["schemas"] and receipt["schemas"][0]["phase"] == "ready":
            raise OSError("post_commit_receipt_sentinel")
        real_write(path, receipt)

    with monkeypatch.context() as patch:
        patch.setattr(fixture, "private_write", fail_after_commit)
        with pytest.raises(OSError, match="post_commit_receipt_sentinel"):
            apply(owned)
    pg, path = owned[1:3]
    pending = fixture.private_read(str(path))["schemas"][0]
    assert pending["phase"] == "create_pending" and pg.events.count("commit") == 1
    before = list(pg.mutations)
    assert apply(owned)["schemas"][0]["nonce"] == pending["nonce"]
    assert pg.mutations == before


def test_durable_receipt_scope_cannot_retarget_an_existing_dataset(owned):
    receipt = apply(owned)
    receipt["authority"]["database"] = "retained_foreign_database_sentinel"
    pg, path = owned[1:3]
    write_receipt(path, receipt)
    pg.calls.clear()
    mutations = list(pg.mutations)
    with pytest.raises(ValueError, match="invalid_dataset_receipt"):
        apply(owned, cleanup=True)
    assert not pg.calls and pg.mutations == mutations


@pytest.mark.parametrize(
    "change",
    ["owner", "marker", "nonce", "schema_oid", "relation_oid", "extra_relation"],
)
def test_changed_resource_authority_blocks_reuse_and_cleanup_before_mutation(
    owned, change
):
    item = apply(owned)["schemas"][0]
    pg = owned[1]
    schema = pg.schemas[item["name"]]
    if change == "owner":
        schema["owner"] = "retained-owner-sentinel"
    elif change == "marker":
        schema["marker"] = "schemii-load-dataset:" + "e" * 32
    elif change == "nonce":
        schema["ownership"][0]["nonce"] = "f" * 32
    elif change == "schema_oid":
        schema["oid"] += 9100
    elif change == "relation_oid":
        schema["relations"]["orders"] += 9200
    else:
        schema["relations"]["foreign_table_sentinel"] = 987654
    before, mutations = deepcopy(pg.schemas), list(pg.mutations)
    for cleanup in (False, True):
        with pytest.raises(
            ValueError, match="dataset_(ownership|nonce|relations)_changed"
        ):
            apply(owned, cleanup=cleanup)
        assert pg.schemas == before and pg.mutations == mutations


@pytest.mark.parametrize("mode", ["before", "after"])
def test_uncertain_cleanup_commit_is_restartable_and_never_drops_foreign_objects(
    owned, mode
):
    owned[0]["wide"] = True
    apply(owned)
    pg, path = owned[1:3]
    foreign = {
        "oid": 99991,
        "owner": "retained-peer-sentinel",
        "marker": "retained",
        "relations": {"keep": 99992},
    }
    pg.schemas["retained_peer_schema"] = deepcopy(foreign)
    pg.commit_error = mode
    with pytest.raises(LostAcknowledgment, match="commit_ack_sentinel"):
        apply(owned, cleanup=True)
    pending = fixture.private_read(str(path))["schemas"][0]
    assert (
        pending["phase"] == "delete_pending"
        and pending["steps"][-1]["operation"] == "drop_schema"
    )
    result = apply(owned, cleanup=True)
    assert result["schemas"][0]["phase"] == "deleted"
    assert pg.schemas == {"retained_peer_schema": foreign}
    mutations = list(pg.mutations)
    assert apply(owned, cleanup=True) == result and pg.mutations == mutations
    assert [kind for kind, _ in mutations if kind.startswith("drop_")].count(
        "drop_table"
    ) == (6 if mode == "before" else 3)
    assert all("CASCADE" not in text for text, _ in pg.statements)


def test_cleanup_interrupted_after_table_drops_rolls_back_and_retains_exact_oids(owned):
    item = apply(owned)["schemas"][0]
    pg, path = owned[1:3]
    before = deepcopy(pg.schemas)
    pg.fail_operation = "drop_schema"
    with pytest.raises(RuntimeError, match="mutation_sentinel:drop_schema"):
        apply(owned, cleanup=True)
    assert pg.schemas == before
    pending = fixture.private_read(str(path))["schemas"][0]
    assert pending["phase"] == "delete_pending" and pending["oid"] == item["oid"]
    assert pending["relations"] == item["relations"]
    assert apply(owned, cleanup=True)["schemas"][0]["phase"] == "deleted"
    assert not pg.schemas


@pytest.mark.parametrize("target", ["witness", "provisioning", "receipt"])
@pytest.mark.parametrize(
    "failure", ["malformed", "public", "symlink", "oversize", "fifo"]
)
def test_untrusted_private_files_fail_before_connection(owned, target, failure):
    config, pg, path, witness = owned
    target_path = Path(
        config["sourceWitnessConfig"]
        if target == "witness"
        else witness["authority"]["provisioningReceipt"]
        if target == "provisioning"
        else path
    )
    if target == "receipt":
        apply(owned)
        pg.calls.clear()
    original = target_path.read_bytes()
    if failure == "malformed":
        target_path.write_text("{malformed-fixture-sentinel")
    elif failure == "public":
        target_path.chmod(0o644)
    elif failure == "fifo":
        target_path.unlink()
        os.mkfifo(target_path, mode=0o600)
    elif failure == "oversize":
        target_path.write_text('"' + "S" * (1024 * 1024) + '"')
    else:
        sibling = target_path.with_suffix(".foreign")
        sibling.write_bytes(original)
        sibling.chmod(0o600)
        target_path.unlink()
        target_path.symlink_to(sibling)
    mutations = list(pg.mutations)
    with pytest.raises((ValueError, OSError)):
        apply(owned)
    assert not pg.calls and pg.mutations == mutations


def test_relative_or_symlinked_receipt_parent_fails_before_connection(owned, tmp_path):
    config, pg, _, _ = owned
    with pytest.raises(ValueError, match="absolute_private_source_path_required"):
        fixture.apply(config, "relative-receipt.json", connect=pg.connect)
    target = tmp_path / "actual-parent"
    target.mkdir(mode=0o700)
    link = tmp_path / "linked-parent"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        fixture.apply(config, str(link / "receipt.json"), connect=pg.connect)
    assert not pg.calls and not list(target.iterdir())


@pytest.mark.parametrize(
    "corrupt",
    [
        "version",
        "duplicate_index",
        "unbound_index",
        "unknown_steps",
        "schema_list",
        "step_list",
        "phase",
        "boolean_index",
    ],
)
def test_malformed_intent_journal_is_rejected_before_connection(owned, corrupt):
    receipt = apply(owned)
    if corrupt == "version":
        receipt["version"] = 99
    elif corrupt == "duplicate_index":
        receipt["schemas"].append(deepcopy(receipt["schemas"][0]))
    elif corrupt == "unbound_index":
        receipt["schemas"].append(
            {**receipt["schemas"][0], "index": 2, "name": "foreign-schema-sentinel"}
        )
    elif corrupt == "unknown_steps":
        receipt["schemas"][0]["steps"] = [
            {"operation": "foreign-unknown-ddl", "phase": "pending"}
        ]
    elif corrupt == "schema_list":
        receipt["schemas"] = {"foreign-shape": "not-an-intent-list"}
    elif corrupt == "step_list":
        receipt["schemas"][0]["steps"] = {
            "operation": "drop_schema",
            "phase": "pending",
        }
    elif corrupt == "phase":
        receipt["schemas"][0]["phase"] = "untrusted_phase_sentinel"
    else:
        receipt["schemas"][0]["index"] = True
    pg, path = owned[1:3]
    write_receipt(path, receipt)
    before, mutations = deepcopy(pg.schemas), list(pg.mutations)
    pg.calls.clear()
    with pytest.raises(ValueError):
        apply(owned, cleanup=True)
    assert not pg.calls and pg.schemas == before and pg.mutations == mutations
