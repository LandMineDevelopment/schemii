"""One explicit transaction per batch, with a durable pre-commit uncertainty fence."""
from dataclasses import asdict
from datetime import datetime, timezone
import secrets
import threading
import logging
from concurrent.futures import ThreadPoolExecutor
from pglast import parse_sql
from schemii.common.api.errors import ApiProblem
from schemii.common.postgres.console.execution import validate_transaction_statements, ConsoleStatementValidationError
from schemii.common.query_executions.cancellation import QueryCancellationRegistry


def now():
    return datetime.now(timezone.utc).isoformat()


def touch(value):
    value["revision"] += 1
    value["updatedAt"] = now()


def stop_clock(value):
    if value["activeSince"]:
        value["elapsedMs"] += max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(value["activeSince"])).total_seconds() * 1000))
        value["activeSince"] = None


class BulkJobService:
    def __init__(self, repository, console, connections, postgres):
        self.repository = repository
        self.console = console
        self.connections = connections
        self.postgres = postgres
        self.cancellation = QueryCancellationRegistry()
        self.lock = threading.RLock()
        self.active = {}
        self.active_owners = {}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bulk-job")
        self.capacity = threading.BoundedSemaphore(2)
        self.closed = False

    def _admit(self):
        if self.closed or not self.capacity.acquire(blocking=False):
            raise ApiProblem(409, "bulk_job_capacity", "Two bulk jobs are already active. Wait for one to finish before starting another.")

    def create_started(self, owner, workspace, body):
        with self.lock:
            self._admit()
            try:
                value = self.create(owner, workspace, body)
                self.executor.submit(self._run_admitted, owner, workspace, value["id"])
                return value
            except Exception:
                self.capacity.release()
                raise

    def resume_started(self, owner, workspace, identifier, body):
        with self.lock:
            self._admit()
            try:
                value = self.command(owner, workspace, identifier, body.expected_revision, "resume",
                                     workspace_revision=body.expected_workspace_revision,
                                     settings_revision=body.expected_settings_revision)
                self.executor.submit(self._run_admitted, owner, workspace, identifier)
                return value
            except Exception:
                self.capacity.release()
                raise

    def _run_admitted(self, owner, workspace, identifier):
        try:
            self.run(owner, workspace, identifier)
        finally:
            self.capacity.release()

    def close(self):
        with self.lock:
            self.closed = True
            for stopped in self.active.values():
                stopped.set()
            active = [(self.active_owners[key], key) for key in self.active]
        for owner, identifier in active:
            self.cancellation.cancel(owner, identifier)
        self.executor.shutdown(wait=True)


    def _authorize(self, owner, workspace):
        self.console._require_workspace(owner, workspace)

    def _target(self, owner, workspace, revision):
        return self.console._workspace_target(owner, workspace, revision)[1]

    def create(self, owner, workspace, body):
        self.console._validate_settings_revision(owner, body.expected_settings_revision)
        target = self._target(owner, workspace, body.expected_workspace_revision)
        # Validate the entire plan before any SQL is submitted.
        limit = self.console.settings(owner).statement_limit
        for batch in body.batches:
            self.validate_batch(batch.sql, limit)
        timestamp = now()
        value = dict(id="bjob_" + secrets.token_hex(16), ownerId=owner, workspaceId=workspace,
                     consoleId=body.console_id, workspaceRevision=body.expected_workspace_revision,
                     settingsRevision=body.expected_settings_revision, target=asdict(target),
                     name=body.name.strip(), revision=1, status="queued", createdAt=timestamp,
                     updatedAt=timestamp, elapsedMs=0, activeSince=None, errorMessage=None,
                     batches=[dict(sql=b.sql, status="pending", errorMessage=None, executionId=None) for b in body.batches])
        return self.public(self.repository.create(value))

    @staticmethod
    def validate_batch(sql, limit):
        try:
            script = validate_transaction_statements([sql], statement_limit=limit)
            if any(type(raw.stmt).__name__ == "TransactionStmt" for raw in parse_sql(sql)):
                raise ApiProblem(422, "bulk_transaction_control", "Each batch owns one transaction. Remove BEGIN, COMMIT, ROLLBACK and savepoint commands.")
            return script.statements
        except ConsoleStatementValidationError as error:
            raise ApiProblem(422, error.code, str(error)) from error

    def public(self, value):
        result = {k: v for k, v in value.items() if k not in {"ownerId", "target", "workspaceRevision", "settingsRevision", "activeSince"}}
        if value["activeSince"]:
            result["elapsedMs"] += max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(value["activeSince"])).total_seconds() * 1000))
        result["completedBatches"] = sum(b["status"] == "committed" for b in value["batches"])
        result["totalBatches"] = len(value["batches"])
        result["currentBatchIndex"] = next((i for i,b in enumerate(value["batches"]) if b["status"] != "committed"), None)
        return result

    def get(self, owner, workspace, identifier):
        self._authorize(owner, workspace)
        return self.public(self.repository.change(owner, workspace, identifier))

    def list(self, owner, workspace):
        self._authorize(owner, workspace)
        jobs = []
        for value in self.repository.list(owner, workspace):
            summary = self.public(value)
            summary["batches"] = [{k: v for k, v in batch.items() if k != "sql"} for batch in summary["batches"]]
            jobs.append(summary)
        return {"jobs": jobs}

    def command(self, owner, workspace, identifier, revision, action, outcome=None, workspace_revision=None, settings_revision=None):
        self._authorize(owner, workspace)
        reviewed_target = None
        if action == "resume" and workspace_revision is not None and settings_revision is not None:
            reviewed_target = self._target(owner, workspace, workspace_revision)
            self.console._validate_settings_revision(owner, settings_revision)
        with self.lock:
            def update(value):
                status = value["status"]
                if action == "cancel":
                    if status not in {"queued", "running", "cancelling"}:
                        raise ApiProblem(409, "bulk_job_not_running", "This job is not running.")
                    value["status"] = "cancelling" if identifier in self.active else "paused"
                else:
                    if identifier in self.active:
                        raise ApiProblem(409, "bulk_job_active", "Wait for the active worker to stop before continuing.")
                    if action == "resume":
                        if status not in {"paused", "failed"}:
                            raise ApiProblem(409, "bulk_job_not_resumable", "Resolve any uncertain batch before resuming.")
                        if workspace_revision is None or settings_revision is None:
                            raise ApiProblem(422, "bulk_resume_review_required", "Review current workspace and settings revisions before resuming.")
                        if asdict(reviewed_target) != value["target"]:
                            raise ApiProblem(409, "bulk_target_changed", "The saved database target changed. Create a newly reviewed job.")
                        value["workspaceRevision"] = workspace_revision
                        value["settingsRevision"] = settings_revision
                        for batch in value["batches"]:
                            if batch["status"] == "failed":
                                batch["status"] = "pending"
                        value["status"] = "queued"
                        value["errorMessage"] = None
                    elif action == "reconcile":
                        if status != "reconciliation_required":
                            raise ApiProblem(409, "bulk_reconciliation_not_required", "This job has no uncertain batch.")
                        batch = next(b for b in value["batches"] if b["status"] == "uncertain")
                        batch["status"] = "committed" if outcome == "committed" else "pending"
                        batch["reconciledAt"] = now()
                        batch["reconciledOutcome"] = outcome
                        batch["errorMessage"] = None
                        value["status"] = "completed" if all(b["status"] == "committed" for b in value["batches"]) else "paused"
                        value["errorMessage"] = None
                touch(value)
            value = self.repository.change(owner, workspace, identifier, update, revision)
            if action == "cancel" and identifier in self.active:
                self.active[identifier].set()
        if action == "cancel":
            self.cancellation.cancel(owner, identifier)
        return self.public(value)

    def delete(self, owner, workspace, identifier, revision):
        self._authorize(owner, workspace)
        with self.lock:
            if identifier in self.active:
                raise ApiProblem(409, "bulk_job_active", "Wait for the worker to stop before deleting this job.")
            self.repository.delete(owner, workspace, identifier, revision)

    def run(self, owner, workspace, identifier):
        with self.lock:
            if identifier in self.active:
                return
            value = self.repository.change(owner, workspace, identifier)
            if value["status"] != "queued":
                return
            if self.closed:
                self.command(owner, workspace, identifier, value["revision"], "cancel")
                return
            def begin(value):
                if value["status"] != "queued":
                    raise ApiProblem(409, "bulk_job_changed", "Job already claimed")
                value["status"] = "running"
                value["activeSince"] = now()
                touch(value)
            value = self.repository.change(owner, workspace, identifier, begin, value["revision"])
            stopped = self.active[identifier] = threading.Event()
            self.active_owners[identifier] = owner
        heartbeat_stop = threading.Event()
        def heartbeat():
            while not heartbeat_stop.wait(1):
                try:
                    def tick(value):
                        if value["activeSince"]:
                            stop_clock(value)
                            value["activeSince"] = now()
                            value["updatedAt"] = now()
                    self.repository.change(owner, workspace, identifier, tick)
                except Exception:
                    logging.getLogger(__name__).exception("Bulk job heartbeat failed")
                    stopped.set()
                    self.cancellation.cancel(owner, identifier)
                    return
        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            with self.cancellation.scope(owner, identifier, identifier, lambda: not stopped.is_set()):
                self._run_batches(owner, workspace, identifier, stopped)
        except Exception:
            # Scope cancellation can race worker startup, before a transaction exists.
            def interrupted(value):
                batch = next((b for b in value["batches"] if b["status"] in {"running", "committing"}), None)
                value["status"] = "reconciliation_required" if batch else "paused"
                if batch:
                    batch["status"] = "uncertain"
                stop_clock(value)
                touch(value)
            self.repository.change(owner, workspace, identifier, interrupted)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)
            with self.lock:
                self.active.pop(identifier, None)
                self.active_owners.pop(identifier, None)

    def _run_batches(self, owner, workspace, identifier, stopped):
        while True:
            transaction = None
            committing = False
            index = None
            try:
                value = self.repository.change(owner, workspace, identifier)
                index = next((i for i,b in enumerate(value["batches"]) if b["status"] != "committed"), None)
                if index is None or stopped.is_set():
                    def finish(value):
                        value["status"] = "completed" if index is None else "paused"
                        stop_clock(value)
                        touch(value)
                    self.repository.change(owner, workspace, identifier, finish)
                    return
                target = self._target(owner, workspace, value["workspaceRevision"])
                if asdict(target) != value["target"]:
                    raise ApiProblem(409, "bulk_target_changed", "The saved database target changed.")
                self.console._validate_settings_revision(owner, value["settingsRevision"])
                statements = self.validate_batch(value["batches"][index]["sql"], self.console.settings(owner).statement_limit)
                with self.connections.use(owner, target.connection_id) as resolved:
                    if resolved.revision != target.connection_revision or resolved.database != target.database:
                        raise ApiProblem(409, "bulk_target_changed", "The saved database target changed.")
                    transaction = self.postgres.open_console_transaction(resolved, target.namespace)
                    def started(value):
                        value["batches"][index]["status"] = "running"
                        touch(value)
                    self.repository.change(owner, workspace, identifier, started)
                    transaction.execute(statements)
                    if stopped.is_set():
                        raise RuntimeError("Batch cancelled before commit")
                    def committing_batch(value):
                        value["batches"][index]["status"] = "committing"
                        touch(value)
                    self.repository.change(owner, workspace, identifier, committing_batch)
                    committing = True
                    transaction.commit()
                    transaction = None
                    def committed(value):
                        value["batches"][index]["status"] = "committed"
                        value["batches"][index]["committedAt"] = now()
                        value["batches"][index]["errorMessage"] = None
                        # Checkpoint elapsed time without including later pauses.
                        stop_clock(value)
                        value["activeSince"] = now()
                        touch(value)
                    self.repository.change(owner, workspace, identifier, committed)
            except Exception as error:
                rollback_ok = transaction is None
                if transaction is not None:
                    try:
                        transaction.rollback()
                        rollback_ok = True
                    except Exception:
                        transaction.close()
                uncertain = committing or not rollback_ok
                def failed(value):
                    value["status"] = "reconciliation_required" if uncertain else ("paused" if stopped.is_set() else "failed")
                    message = "Commit outcome is uncertain. Verify this batch in PostgreSQL before reconciling; it will not be retried automatically." if uncertain else ("Cancelled; the current transaction was rolled back. Previously committed batches remain committed." if stopped.is_set() else str(error)[:2048])
                    value["errorMessage"] = message
                    if index is not None:
                        value["batches"][index]["status"] = "uncertain" if uncertain else "failed"
                        value["batches"][index]["errorMessage"] = message
                    stop_clock(value)
                    touch(value)
                self.repository.change(owner, workspace, identifier, failed)
                return
