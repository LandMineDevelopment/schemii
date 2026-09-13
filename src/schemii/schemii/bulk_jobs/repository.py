"""Bounded durable job documents; SQL and checkpoints only, never result rows."""
import copy
import json
import threading
from datetime import datetime
from contextlib import contextmanager
from schemii.common.api.errors import ApiProblem


class JobRepository:
    def __init__(self, connection_factory=None):
        self.factory = connection_factory
        self.memory = {}
        self.lock = threading.RLock()

    @contextmanager
    def _cursor(self):
        connection = self.factory()
        try:
            with connection.cursor() as cursor:
                yield cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list(self, owner, workspace):
        if self.factory:
            with self._cursor() as cursor:
                cursor.execute("SELECT (document - 'batches') || jsonb_build_object('batches', (SELECT jsonb_agg(batch - 'sql') FROM jsonb_array_elements(document->'batches') AS batch)) AS document FROM schemii.bulk_jobs WHERE owner_id=%s AND workspace_id=%s ORDER BY updated_at DESC LIMIT 50", (owner, workspace))
                return [row["document"] for row in cursor.fetchall()]
        with self.lock:
            return copy.deepcopy([v for v in self.memory.values() if v["ownerId"] == owner and v["workspaceId"] == workspace])

    def create(self, document):
        if self.factory:
            with self._cursor() as cursor:
                cursor.execute("SELECT id FROM schemii.workspaces WHERE owner_id=%s AND id=%s FOR KEY SHARE", (document["ownerId"], document["workspaceId"]))
                if cursor.fetchone() is None:
                    raise ApiProblem(404, "workspace_not_found", "Workspace not found")
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("bulk-jobs:" + document["ownerId"],))
                cursor.execute("SELECT count(*) AS count FROM schemii.bulk_jobs WHERE owner_id=%s", (document["ownerId"],))
                self._check_limit(cursor.fetchone()["count"])
                self._check_active_cursor(cursor, document["ownerId"], document["id"])
                cursor.execute("INSERT INTO schemii.bulk_jobs(id,owner_id,workspace_id,document) VALUES (%s,%s,%s,%s::jsonb)", (document["id"], document["ownerId"], document["workspaceId"], json.dumps(document)))
        else:
            with self.lock:
                self._check_limit(sum(v["ownerId"] == document["ownerId"] for v in self.memory.values()))
                self._check_active_memory(document["ownerId"], document["id"])
                self.memory[document["id"]] = copy.deepcopy(document)
        return document

    @staticmethod
    def _check_limit(count):
        if count >= 50:
            raise ApiProblem(409, "bulk_job_limit", "Delete a finished bulk job before creating another (limit 50 per user).")

    @staticmethod
    def _check_active_cursor(cursor, owner, identifier):
        cursor.execute("SELECT id FROM schemii.bulk_jobs WHERE owner_id=%s AND id<>%s AND document->>'status' IN ('queued','running','cancelling') LIMIT 1", (owner, identifier))
        if cursor.fetchone():
            raise ApiProblem(409, "bulk_job_already_active", "Wait for your current bulk job to stop before starting another.")

    def _check_active_memory(self, owner, identifier):
        if any(v["ownerId"] == owner and v["id"] != identifier and v["status"] in {"queued", "running", "cancelling"} for v in self.memory.values()):
            raise ApiProblem(409, "bulk_job_already_active", "Wait for your current bulk job to stop before starting another.")

    def change(self, owner, workspace, identifier, update=lambda value: None, revision=None):
        if self.factory:
            with self._cursor() as cursor:
                cursor.execute("SELECT id FROM schemii.workspaces WHERE owner_id=%s AND id=%s FOR KEY SHARE", (owner, workspace))
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("bulk-jobs:" + owner,))
                cursor.execute("SELECT document FROM schemii.bulk_jobs WHERE id=%s AND owner_id=%s AND workspace_id=%s FOR UPDATE", (identifier, owner, workspace))
                row = cursor.fetchone()
                value = row["document"] if row else None
                self._validate(value, revision)
                update(value)
                if value["status"] == "queued":
                    self._check_active_cursor(cursor, owner, identifier)
                cursor.execute("UPDATE schemii.bulk_jobs SET document=%s::jsonb,updated_at=clock_timestamp() WHERE id=%s", (json.dumps(value), identifier))
                return value
        with self.lock:
            value = copy.deepcopy(self.memory.get(identifier))
            if value and (value["ownerId"] != owner or value["workspaceId"] != workspace):
                value = None
            self._validate(value, revision)
            update(value)
            if value["status"] == "queued":
                self._check_active_memory(owner, identifier)
            self.memory[identifier] = copy.deepcopy(value)
            return value

    @staticmethod
    def _validate(value, revision):
        if value is None:
            raise ApiProblem(404, "bulk_job_not_found", "Bulk job not found")
        if revision is not None and value["revision"] != revision:
            raise ApiProblem(409, "bulk_job_changed", "The bulk job changed. Refresh its status before continuing.")

    def delete(self, owner, workspace, identifier, revision):
        def check(value):
            self._validate(value, revision)
            if value["status"] in {"running", "queued", "cancelling", "reconciliation_required"}:
                raise ApiProblem(409, "bulk_job_active", "Resolve or stop this job before deleting its checkpoints.")
        if self.factory:
            with self._cursor() as cursor:
                cursor.execute("SELECT id FROM schemii.workspaces WHERE owner_id=%s AND id=%s FOR KEY SHARE", (owner, workspace))
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("bulk-jobs:" + owner,))
                cursor.execute("SELECT document FROM schemii.bulk_jobs WHERE id=%s AND owner_id=%s AND workspace_id=%s FOR UPDATE", (identifier, owner, workspace))
                row = cursor.fetchone()
                check(row["document"] if row else None)
                cursor.execute("DELETE FROM schemii.bulk_jobs WHERE id=%s", (identifier,))
        else:
            with self.lock:
                value = self.memory.get(identifier)
                if value and (value["ownerId"] != owner or value["workspaceId"] != workspace):
                    value = None
                check(value)
                self.memory.pop(identifier)

    def recover(self):
        def recover(value):
            if value["status"] not in {"running", "queued", "cancelling"}:
                return
            batch = next((b for b in value["batches"] if b["status"] in {"running", "committing"}), None)
            value["status"] = "reconciliation_required" if batch else "paused"
            if batch:
                batch["status"] = "uncertain"
            if value["activeSince"]:
                # Last durable heartbeat bounds active time; never count downtime.
                value["elapsedMs"] += max(0, int((datetime.fromisoformat(value["updatedAt"]) - datetime.fromisoformat(value["activeSince"])).total_seconds() * 1000))
            value["activeSince"] = None
            value["revision"] += 1
            value["errorMessage"] = "Server restarted. Verify the interrupted batch's database outcome before resuming." if batch else "Server restarted. Resume the remaining batches when ready."
        if self.factory:
            with self._cursor() as cursor:
                cursor.execute("SELECT id,document FROM schemii.bulk_jobs WHERE document->>'status' IN ('running','queued','cancelling') FOR UPDATE")
                for row in cursor.fetchall():
                    recover(row["document"])
                    cursor.execute("UPDATE schemii.bulk_jobs SET document=%s::jsonb WHERE id=%s", (json.dumps(row["document"]), row["id"]))
        else:
            with self.lock:
                for value in self.memory.values():
                    recover(value)
