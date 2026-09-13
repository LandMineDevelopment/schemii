"""Raw SQL protocol, transaction policies and owner transaction review regressions."""
from collections import OrderedDict
from types import SimpleNamespace
import threading

import pytest
from psycopg import pq

from schemii.common.api.errors import ApiProblem
from schemii.common.postgres.console.raw import RawSession
from schemii.schemii.console.raw_session import RawSessionService, SqlCreate


class PgResult:
    def __init__(self, status, rows=(), command=b"SELECT 1", error=None):
        self.status, self.rows, self.command_status = status, rows, command
        self.nfields = 1 if rows or status == pq.ExecStatus.TUPLES_OK else 0
        self.ntuples = len(rows)
        self.command_tuples = 1
        self.error_message = b"error"
        self.error = error
    def fname(self, index): return b"value"
    def ftype(self, index): return 25
    def get_value(self, row, col): return self.rows[row][col]
    def error_field(self, key):
        return self.error[0] if key == pq.DiagnosticField.MESSAGE_PRIMARY else self.error[1]


def wire_session(monkeypatch, responses, row_limit=2, byte_limit=256):
    from schemii.common.postgres.console import raw as module
    monkeypatch.setattr(module.generators, "send", lambda pg: "send")
    monkeypatch.setattr(module.generators, "fetch", lambda pg: "fetch")
    pending = iter(responses + [None])
    sent = []
    connection = SimpleNamespace(
        info=SimpleNamespace(encoding="utf-8", transaction_status=SimpleNamespace(name="IDLE")),
        pgconn=SimpleNamespace(send_query=sent.append, set_single_row_mode=lambda: None),
        wait=lambda action: next(pending) if action == "fetch" else None,
    )
    session = RawSession.__new__(RawSession)
    session.connection, session.row_limit, session.byte_limit = connection, row_limit, byte_limit
    return session, sent


def test_raw_wire_preserves_sql_and_consumes_all_returning_rows(monkeypatch):
    responses = [PgResult(pq.ExecStatus.SINGLE_TUPLE, [(str(i).encode(),)]) for i in range(5)]
    responses += [PgResult(pq.ExecStatus.TUPLES_OK)]
    session, sent = wire_session(monkeypatch, responses)
    sql = "/* untouched */ UPDATE things SET x=x+1 RETURNING x;"
    result = session.execute(sql, lambda _: None)
    assert sent == [sql.encode()]
    assert result["errorMessage"] is None
    assert result["results"][0]["rows"] == [["0"], ["1"]]
    assert result["results"][0]["rowCount"] == 5
    assert result["results"][0]["truncated"] is True


def test_raw_wire_keeps_server_error_and_prior_command_receipts(monkeypatch):
    session, _ = wire_session(monkeypatch, [PgResult(pq.ExecStatus.COMMAND_OK, command=b"CREATE TABLE"),
        PgResult(pq.ExecStatus.FATAL_ERROR, error=(b"permission denied", b"42501"))])
    result = session.execute("CREATE TABLE x(a int); DROP TABLE private", lambda _: None)
    assert result["sqlstate"] == "42501"
    assert result["errorMessage"] == "permission denied"
    assert result["results"][0]["command"] == "CREATE TABLE"


def test_protocol_copy_does_not_execute_any_part_without_transfer_endpoint(monkeypatch):
    session, sent = wire_session(monkeypatch, [])
    result = session.execute("CREATE TABLE x(a int); COPY x TO STDOUT", lambda _: None)
    assert sent == []
    assert "no commands" in result["errorMessage"]


class PolicyRaw:
    def __init__(self):
        self.transaction_status = "idle"
        self.sent = []
        self.byte_limit = 4096
        self.connection = SimpleNamespace(add_notice_handler=lambda fn: None, remove_notice_handler=lambda fn: None)
    def execute(self, sql, publish):
        self.sent.append(sql)
        command = sql.split()[0].upper()
        if command == "BEGIN": self.transaction_status = "intrans"
        if command in {"COMMIT", "ROLLBACK"}: self.transaction_status = "idle"
        failure = "fail" in sql
        if failure and self.transaction_status == "intrans": self.transaction_status = "inerror"
        return {"results": [] if failure else [{"command": command, "rows": [], "columns": []}],
                "errorMessage": "deliberate failure" if failure else None,
                "sqlstate": "XX000" if failure else None, "transactionStatus": self.transaction_status}


def policy_fixture():
    console = SimpleNamespace(_repository=SimpleNamespace(record_history=lambda *args: None), _query_history_limit=20)
    service = RawSessionService(console, None, None)
    session = dict(id="raw_test", owner="me", workspaceId="ws_test", raw=PolicyRaw(),
                   status="open", revision=1, operation=threading.Lock(), signalLock=threading.RLock(),
                   executions=OrderedDict(), tickets=OrderedDict(), currentExecutionId=None,
                   pendingStatements=[], pendingStatementsTruncated=False, transactionStartedAt=None)
    return service, session


@pytest.mark.parametrize(("mode", "sql", "expected", "state"), [
    ("manual", "SELECT 1", ["BEGIN", "SELECT 1"], "intrans"),
    ("manual", "BEGIN", ["BEGIN"], "intrans"),
    ("each_statement", "VACUUM; SELECT 1", ["VACUUM", "SELECT 1"], "idle"),
    ("whole_run", "SELECT 1; SELECT 2", ["BEGIN", "SELECT 1; SELECT 2", "COMMIT"], "idle"),
    ("whole_run", "SELECT fail", ["BEGIN", "SELECT fail", "ROLLBACK"], "idle"),
])
def test_explicit_commit_policies(mode, sql, expected, state):
    service, session = policy_fixture()
    body = SqlCreate(sql=sql, commit_mode=mode)
    receipt = service.reserve(session, body)
    service.run(session, receipt, body)
    assert session["raw"].sent == expected
    assert receipt["transactionStatus"] == state
    assert not session["operation"].locked()


def test_manual_commit_clears_pending_history_and_new_run_opens_transaction():
    service, session = policy_fixture()
    for sql in ("SELECT 1", "COMMIT", "SELECT 2"):
        body = SqlCreate(sql=sql)
        receipt = service.reserve(session, body)
        service.run(session, receipt, body)
    assert [entry["sql"] for entry in session["pendingStatements"]] == ["SELECT 2"]
    assert session["raw"].sent == ["BEGIN", "SELECT 1", "COMMIT", "BEGIN", "SELECT 2"]


def test_automatic_policy_never_commits_preexisting_transaction():
    service, session = policy_fixture()
    session["raw"].transaction_status = "intrans"
    with pytest.raises(ApiProblem):
        service.reserve(session, SqlCreate(sql="SELECT 1", commit_mode="whole_run"))
    assert session["raw"].sent == []


def test_reviewed_transaction_revision_fences_unseen_changes():
    service, session = policy_fixture()
    body = SqlCreate(sql="SELECT 1")
    receipt = service.reserve(session, body)
    service.run(session, receipt, body)
    with pytest.raises(ApiProblem):
        service.reserve(session, SqlCreate(sql="COMMIT", expected_revision=1))
    assert "COMMIT" not in session["raw"].sent


def test_manual_script_reopens_after_explicit_commit_and_leaves_next_statement_pending():
    service, session = policy_fixture()
    body = SqlCreate(sql="INSERT INTO items VALUES (1); COMMIT; INSERT INTO items VALUES (2)")
    receipt = service.reserve(session, body)
    service.run(session, receipt, body)
    assert session["raw"].sent == ["BEGIN", "INSERT INTO items VALUES (1)", "COMMIT", "BEGIN", "INSERT INTO items VALUES (2)"]
    assert receipt["transactionStatus"] == "intrans"
    assert [item["sql"] for item in session["pendingStatements"]] == ["INSERT INTO items VALUES (2)"]


def test_manual_explicit_transaction_controls_are_sent_without_rewriting():
    service, session = policy_fixture()
    body = SqlCreate(sql="BEGIN; SAVEPOINT mark; SELECT 1; ROLLBACK TO mark; COMMIT AND CHAIN; SELECT 2")
    receipt = service.reserve(session, body)
    service.run(session, receipt, body)
    # Fake connection only models plain transaction commands; verify exact typed controls.
    sent = session["raw"].sent
    assert sent[0:4] == ["BEGIN", "SAVEPOINT mark", "SELECT 1", "ROLLBACK TO mark"]
    assert "COMMIT AND CHAIN" in sent
    assert receipt["status"] == "succeeded"


def test_session_idle_deadline_is_disclosed_and_running_work_has_no_idle_deadline():
    service, session = policy_fixture()
    session.update(consoleId="con_test", createdAt="2026-09-12T12:00:00+00:00",
                   lastUsedAt="2026-09-12T12:01:00+00:00")
    session["raw"].backend_pid = 123
    view = service.view(session)
    assert view["idleTimeoutSeconds"] == 1800
    assert view["expiresAt"] == "2026-09-12T12:31:00+00:00"
    session["status"] = "running"
    assert service.view(session)["expiresAt"] is None


def test_explicit_begin_publishes_open_transaction_before_next_statement_finishes():
    service, session = policy_fixture()
    execute = session["raw"].execute
    def observe(sql, publish):
        if sql == "SELECT 1":
            assert session["status"] == "running"
            assert session["transactionStartedAt"] is not None
        return execute(sql, publish)
    session["raw"].execute = observe
    body = SqlCreate(sql="BEGIN; SELECT 1")
    receipt = service.reserve(session, body)
    service.run(session, receipt, body)
    assert receipt["status"] == "succeeded"


def test_idle_reaper_uses_disclosed_timeout_and_never_expires_running_work(monkeypatch):
    from schemii.schemii.console import raw_session as module
    service, idle = policy_fixture()
    _, running = policy_fixture()
    monkeypatch.setattr(module.time, "monotonic", lambda: 10000)
    closed = []
    idle.update(id="raw_idle", used=8199)
    idle["raw"].close = lambda: closed.append("idle")
    running.update(id="raw_running", status="running", used=1)
    running["raw"].close = lambda: closed.append("running")
    service.sessions.update({idle["id"]: idle, running["id"]: running})
    service.reap()
    assert closed == ["idle"]
    assert list(service.sessions) == ["raw_running"]
