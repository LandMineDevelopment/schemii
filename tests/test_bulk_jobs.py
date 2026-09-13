from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import threading
import time
import pytest
from schemii.common.api.errors import ApiProblem
from schemii.common.postgres.errors import PostgresCommitUncertainError
from schemii.common.query_executions.cancellation import check_query_authority
from schemii.schemii.console.repository import ConsoleTarget
from schemii.schemii.bulk_jobs.models import JobCreate
from schemii.schemii.bulk_jobs.repository import JobRepository
from schemii.schemii.bulk_jobs.service import BulkJobService
from test_console import console_client, target_workspace

OWNER = "owner"
WORKSPACE = "workspace"


class Console:
    def _require_workspace(self, owner, workspace):
        if owner != OWNER or workspace != WORKSPACE:
            raise ApiProblem(404, "workspace_not_found", "Workspace not found")

    def _workspace_target(self, owner, workspace, revision):
        self._require_workspace(owner, workspace)
        assert revision == 1
        return None, ConsoleTarget("connection", 1, "database", "public")

    def _validate_settings_revision(self, owner, revision):
        assert revision == 1

    def settings(self, owner):
        return SimpleNamespace(statement_limit=100)


class Connections:
    @contextmanager
    def use(self, owner, identifier):
        yield SimpleNamespace(revision=1, database="database")


class Transaction:
    def __init__(self, gateway):
        self.gateway = gateway
        self.rollback_called = False
        self.closed = False

    def execute(self, statements):
        self.gateway.executed.append(tuple(statements))
        if self.gateway.execute_hook:
            self.gateway.execute_hook()
        if self.gateway.fail_execute:
            self.gateway.fail_execute = False
            raise RuntimeError("constraint violation")
        check_query_authority()
        return ()

    def commit(self):
        check_query_authority()
        if self.gateway.fail_commit:
            self.gateway.fail_commit = False
            raise PostgresCommitUncertainError()
        self.gateway.commits += 1
        self.closed = True

    def rollback(self):
        self.rollback_called = True
        self.closed = True

    def close(self):
        self.closed = True


class Gateway:
    def __init__(self):
        self.executed = []
        self.commits = 0
        self.fail_commit = False
        self.fail_execute = False
        self.execute_hook = None
        self.transactions = []

    def open_console_transaction(self, target, namespace):
        transaction = Transaction(self)
        self.transactions.append(transaction)
        return transaction


def setup_job(count=2):
    repo = JobRepository()
    gateway = Gateway()
    service = BulkJobService(repo, Console(), Connections(), gateway)
    body = JobCreate(console_id="con_" + "1" * 32, expected_workspace_revision=1,
                     expected_settings_revision=1, name="Import", batches=[{"sql": f"INSERT INTO facts VALUES ({i})"} for i in range(count)])
    job = service.create(OWNER, WORKSPACE, body)
    return service, repo, gateway, job, body


def test_committed_batches_are_never_repeated_on_resume():
    service, repo, gateway, job, body = setup_job()
    count = 0
    def fail_second():
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("constraint violation")
    gateway.execute_hook = fail_second
    service.run(OWNER, WORKSPACE, job["id"])
    failed = service.get(OWNER, WORKSPACE, job["id"])
    assert failed["status"] == "failed"
    assert failed["completedBatches"] == 1
    assert gateway.transactions[-1].rollback_called
    service.command(OWNER, WORKSPACE, job["id"], failed["revision"], "resume", workspace_revision=1, settings_revision=1)
    service.run(OWNER, WORKSPACE, job["id"])
    result = service.get(OWNER, WORKSPACE, job["id"])
    assert result["status"] == "completed"
    assert result["completedBatches"] == gateway.commits == 2
    assert gateway.executed == [("INSERT INTO facts VALUES (0)",), ("INSERT INTO facts VALUES (1)",), ("INSERT INTO facts VALUES (1)",)]


def test_uncertain_commit_requires_explicit_reconciliation():
    service, repo, gateway, job, body = setup_job()
    gateway.fail_commit = True
    service.run(OWNER, WORKSPACE, job["id"])
    result = service.get(OWNER, WORKSPACE, job["id"])
    assert result["status"] == "reconciliation_required"
    with pytest.raises(ApiProblem, match="uncertain"):
        service.command(OWNER, WORKSPACE, job["id"], result["revision"], "resume", workspace_revision=1, settings_revision=1)
    reconciled = service.command(OWNER, WORKSPACE, job["id"], result["revision"], "reconcile", "committed")
    service.command(OWNER, WORKSPACE, job["id"], reconciled["revision"], "resume", workspace_revision=1, settings_revision=1)
    service.run(OWNER, WORKSPACE, job["id"])
    assert len(gateway.executed) == 2
    assert service.get(OWNER, WORKSPACE, job["id"])["completedBatches"] == 2


def test_restart_marks_inflight_uncertain_and_does_not_count_downtime():
    service, repo, gateway, job, body = setup_job()
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    def interrupted(value):
        value.update(status="running", activeSince=start.isoformat(), updatedAt=(start + timedelta(seconds=2)).isoformat(), elapsedMs=1000)
        value["batches"][0]["status"] = "committed"
        value["batches"][1]["status"] = "committing"
    repo.change(OWNER, WORKSPACE, job["id"], interrupted)
    repo.recover()
    result = service.get(OWNER, WORKSPACE, job["id"])
    assert result["status"] == "reconciliation_required"
    assert result["completedBatches"] == 1
    assert result["elapsedMs"] == 3000
    service.run(OWNER, WORKSPACE, job["id"])
    assert gateway.executed == []


def test_cancel_rolls_back_current_batch_and_blocks_resume_while_running():
    service, repo, gateway, job, body = setup_job()
    started, release = threading.Event(), threading.Event()
    gateway.execute_hook = lambda: (started.set(), release.wait(3))
    worker = threading.Thread(target=service.run, args=(OWNER, WORKSPACE, job["id"]))
    worker.start()
    assert started.wait(2)
    current = service.get(OWNER, WORKSPACE, job["id"])
    cancelled = service.command(OWNER, WORKSPACE, job["id"], current["revision"], "cancel")
    assert cancelled["status"] == "cancelling"
    with pytest.raises(ApiProblem, match="active worker"):
        service.command(OWNER, WORKSPACE, job["id"], cancelled["revision"], "resume", workspace_revision=1, settings_revision=1)
    release.set()
    worker.join(3)
    assert not worker.is_alive()
    result = service.get(OWNER, WORKSPACE, job["id"])
    assert result["status"] == "paused"
    assert result["completedBatches"] == gateway.commits == 0
    assert gateway.transactions[0].rollback_called


@pytest.mark.parametrize("sql", ["BEGIN; INSERT INTO t VALUES(1)", "INSERT INTO t VALUES(1); COMMIT", "SAVEPOINT x", "ROLLBACK", "COPY t FROM STDIN"])
def test_batch_rejects_transaction_control_and_copy(sql):
    with pytest.raises(ApiProblem):
        BulkJobService.validate_batch(sql, 100)


def test_bounds_ownership_revision_and_delete():
    service, repo, gateway, job, body = setup_job()
    with pytest.raises(ApiProblem, match="current bulk job"):
        service.create(OWNER, WORKSPACE, body)
    with pytest.raises(ApiProblem):
        service.get("other", WORKSPACE, job["id"])
    with pytest.raises(ApiProblem):
        repo.change("other", WORKSPACE, job["id"])
    with pytest.raises(ApiProblem, match="changed"):
        service.command(OWNER, WORKSPACE, job["id"], 99, "cancel")
    with pytest.raises(ApiProblem):
        service.delete(OWNER, WORKSPACE, job["id"], job["revision"])
    paused = service.command(OWNER, WORKSPACE, job["id"], job["revision"], "cancel")
    service.delete(OWNER, WORKSPACE, job["id"], paused["revision"])
    assert service.list(OWNER, WORKSPACE) == {"jobs": []}


def test_http_routes_execute_two_atomic_batches_and_delete():
    api, postgres = console_client()
    workspace = target_workspace(api)
    url = f'/api/v1/schemii/workspaces/{workspace["id"]}/console/bulk-jobs'
    response = api.post(url, json={"consoleId": "con_" + "2" * 32, "expectedWorkspaceRevision": workspace["revision"], "expectedSettingsRevision": 1, "name": "Write test", "batches": [{"sql": "UPDATE facts SET x=1"}, {"sql": "UPDATE facts SET x=2"}]})
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    for _ in range(100):
        result = api.get(f"{url}/{identifier}").json()
        if result["status"] == "completed":
            break
        time.sleep(0.01)
    assert result["status"] == "completed", result
    assert result["completedBatches"] == 2
    assert all(transaction.committed for transaction in postgres.transactions)
    assert "ownerId" not in result and "target" not in result
    assert api.delete(f'{url}/{identifier}?expectedRevision={result["revision"]}').status_code == 204


def test_resume_requires_reviewed_current_settings_and_workspace():
    service, repo, gateway, job, body = setup_job()
    paused = service.command(OWNER, WORKSPACE, job["id"], job["revision"], "cancel")
    def current_settings(owner, revision):
        if revision != 2:
            raise ApiProblem(409, "settings_changed", "Settings changed")
    service.console._validate_settings_revision = current_settings
    with pytest.raises(ApiProblem, match="Settings changed"):
        service.command(OWNER, WORKSPACE, job["id"], paused["revision"], "resume", workspace_revision=1, settings_revision=1)
    resumed = service.command(OWNER, WORKSPACE, job["id"], paused["revision"], "resume", workspace_revision=1, settings_revision=2)
    service.run(OWNER, WORKSPACE, job["id"])
    assert resumed["status"] == "queued"
    assert service.get(OWNER, WORKSPACE, job["id"])["status"] == "completed"


def test_global_worker_admission_precedes_persistence():
    service, repo, gateway, job, body = setup_job()
    service.capacity.acquire()
    service.capacity.acquire()
    count = len(repo.memory)
    with pytest.raises(ApiProblem, match="Two bulk jobs"):
        service.create_started(OWNER, WORKSPACE, body)
    assert len(repo.memory) == count
    service.capacity.release()
    service.capacity.release()
    service.close()


def test_list_omits_sql_but_detail_retains_reviewed_batches():
    service, repo, gateway, job, body = setup_job()
    assert "sql" not in service.list(OWNER, WORKSPACE)["jobs"][0]["batches"][0]
    assert service.get(OWNER, WORKSPACE, job["id"])["batches"][0]["sql"] == body.batches[0].sql


def test_workspace_deletion_is_blocked_until_queued_job_stops():
    api, postgres = console_client()
    workspace = target_workspace(api)
    service = api.app.state.bulk_jobs
    body = JobCreate(console_id="con_" + "1" * 32, expected_workspace_revision=workspace["revision"], expected_settings_revision=1, name="Paused test", batches=[{"sql": "UPDATE facts SET x=1"}])
    owner = "user_local_prototype"
    job = service.create(owner, workspace["id"], body)
    url = f'/api/v1/schemii/workspaces/{workspace["id"]}?expectedRevision={workspace["revision"]}'
    response = api.delete(url)
    assert response.status_code == 409, response.text
    service.command(owner, workspace["id"], job["id"], job["revision"], "cancel")
    assert api.delete(url).status_code == 204
