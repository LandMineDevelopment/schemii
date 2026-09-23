"""Owner-bound raw human PostgreSQL sessions; AI uses separate managed execution."""
from __future__ import annotations

import anyio
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import secrets
import threading
import time
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pglast import split
from pydantic import Field
from psycopg import InterfaceError, OperationalError

from schemii.common.api.errors import ApiProblem
from schemii.common.api.models import ApiModel
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.postgres.console.raw import RawSession
from schemii.common.postgres.errors import PostgresGatewayError
from .service import ConsoleServiceError


RAW_SESSION_IDLE_SECONDS = 1800

class SessionCreate(ApiModel):
    console_id: str = Field(pattern=r"^con_[0-9a-f]{32}$")
    expected_workspace_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_settings_revision: Annotated[int, Field(strict=True, ge=1)]


class SqlCreate(ApiModel):
    expected_revision: Annotated[int, Field(strict=True, ge=1)] | None = None
    sql: str = Field(min_length=1, max_length=1024 * 1024)
    commit_mode: Literal["manual", "each_statement", "whole_run"] = "manual"


class ExplainCreate(ApiModel):
    commit_mode: Literal["manual", "each_statement", "whole_run"] = "manual"
    sql: str = Field(min_length=1, max_length=1024 * 1024)
    analyze: Annotated[bool, Field(strict=True)] = False


def now():
    return datetime.now(timezone.utc).isoformat()


class CopyDownload:
    """Close COPY and release its lease even if iteration never starts."""

    def __init__(self, manager, session, ticket):
        self.manager, self.session, self.ticket = manager, session, ticket
        self.released = False
        self.iterator = self.stream()

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.iterator)

    def finish(self):
        with self.session["signalLock"]:
            if not self.released:
                self.released = True
                if self.ticket["status"] == "running":
                    self.ticket.update(status="failed", errorMessage="COPY download interrupted")
                self.manager.release(self.session)

    def cancel(self):
        with self.session["signalLock"]:
            if not self.released:
                self.session["raw"].cancel()

    def close(self):
        try:
            self.iterator.close()
        finally:
            self.finish()

    def stream(self):
        try:
            self.manager.authorize_ticket(self.ticket)
            self.manager.prepare_policy(self.session, SqlCreate(
                sql=self.ticket["sql"], commit_mode=("each_statement" if self.ticket["commitMode"] == "whole_run" else self.ticket["commitMode"])))
            for chunk in self.session["raw"].copy_download(self.ticket["sql"]):
                self.manager.authorize_ticket(self.ticket)
                yield chunk
            self.ticket["status"] = "succeeded"
        except BaseException as error:
            self.ticket.update(status="failed", errorMessage=str(error)[:2048] or "COPY download interrupted")
            raise
        finally:
            try:
                self.manager.track_pending(self.session, self.ticket["sql"], self.ticket["status"])
            finally:
                self.finish()


class CopyStreamingResponse(StreamingResponse):
    """Disconnects cancel PostgreSQL before waiting for blocking COPY reads."""

    def __init__(self, download, **kwargs):
        self.download = download
        super().__init__(download, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            async with anyio.create_task_group() as group:
                async def stream():
                    try:
                        await self.stream_response(send)
                    finally:
                        group.cancel_scope.cancel()

                group.start_soon(stream)
                await self.listen_for_disconnect(receive)
                await anyio.to_thread.run_sync(self.download.cancel)
                group.cancel_scope.cancel()
        finally:
            # The thread running next() has finished before the task group exits.
            # Shield cleanup from the disconnected request's cancellation scope.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self.download.close)


class RawSessionService:
    """Bounded volatile sessions and receipts, never durable result row storage."""
    def __init__(self, console, connections, postgres):
        self.console = console
        self.connections = connections
        self.postgres = postgres
        self.sessions = OrderedDict()
        self.lock = threading.RLock()
        self.opening = set()

    def create(self, owner, workspace, body):
        _, target = self.console._workspace_target(owner, workspace, body.expected_workspace_revision)
        self.console._validate_settings_revision(owner, body.expected_settings_revision)
        self.reap()
        key = (owner, body.console_id)
        with self.lock:
            maximum = self.console._maximum_live_read_sessions
            per_owner = self.console._maximum_live_read_sessions_per_identity
            owner_count = sum(s["owner"] == owner for s in self.sessions.values()) + sum(k[0] == owner for k in self.opening)
            if len(self.sessions) + len(self.opening) >= maximum or owner_count >= per_owner:
                raise ApiProblem(429, "console_session_capacity", "Close an unused console database session before opening another")
            if key in self.opening or any(s["owner"] == owner and s["consoleId"] == body.console_id for s in self.sessions.values()):
                raise ApiProblem(409, "console_session_exists", "Close the existing database session before opening another for this console")
            self.opening.add(key)
        try:
            with self.connections.use(owner, target.connection_id) as resolved:
                if (getattr(resolved, "owner_id", None) or owner) != (target.connection_owner_id or owner) or resolved.revision != target.connection_revision or resolved.database != target.database:
                    raise ApiProblem(409, "console_target_changed", "The PostgreSQL connection changed")
                connection = self.postgres._connect(resolved, retained=True)
                try:
                    raw = RawSession(connection, target.namespace, autocommit=True,
                                     row_limit=self.console.settings(owner).row_page_size,
                                     byte_limit=self.console._page_memory_bytes)
                except Exception:
                    connection.close()
                    raise
                session = dict(id="raw_" + secrets.token_hex(16), owner=owner, workspaceId=workspace,
                               consoleId=body.console_id, target=target, raw=raw, status="open",
                               createdAt=now(), lastUsedAt=now(), used=time.monotonic(),
                               executions=OrderedDict(), tickets=OrderedDict(), currentExecutionId=None,
                               revision=1, pendingStatements=[], pendingStatementsTruncated=False, transactionStartedAt=None,
                               operation=threading.Lock(), signalLock=threading.RLock(), cancelled=threading.Event())
                with self.lock:
                    self.sessions[session["id"]] = session
            return self.view(session)
        except ConnectionNotFoundError as error:
            raise ApiProblem(403, "console_connection_revoked", "Access to the PostgreSQL connection was revoked") from error
        finally:
            with self.lock:
                self.opening.discard(key)

    def get(self, owner, workspace, session_id, validate_target=True):
        self.reap()
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None or session["owner"] != owner or session["workspaceId"] != workspace:
                raise ApiProblem(404, "console_session_not_found", "Database session closed or expired; uncommitted work was rolled back. Open a new session.")
        if not validate_target:
            return session
        self.console._require_workspace(owner, workspace)
        current = self.console._workspaces.get(owner, workspace)
        target = session["target"]
        if (current.connection_id, getattr(current, "connection_owner_id", None) or owner, current.database, current.namespace) != (target.connection_id, target.connection_owner_id or owner, target.database, target.namespace):
            raise ApiProblem(409, "console_target_changed", "Workspace target changed; close this database session and reconnect")
        try:
            profile = self.connections.get(owner, session["target"].connection_id)
        except ConnectionNotFoundError as error:
            raise ApiProblem(403, "console_connection_revoked", "Access to the PostgreSQL connection was revoked") from error
        if profile.revision != session["target"].connection_revision or (getattr(profile, "owner_id", None) or owner) != (target.connection_owner_id or owner):
            raise ApiProblem(409, "console_connection_changed", "Connection changed; close this session and reconnect")
        return session

    @staticmethod
    def view(session):
        return {key: session[key] for key in ("id", "consoleId", "workspaceId", "status", "createdAt", "lastUsedAt", "currentExecutionId", "revision")} | {
            "backendPid": session["raw"].backend_pid, "transactionStatus": session["raw"].transaction_status,
            "pendingStatements": list(session["pendingStatements"]),
            "pendingStatementsTruncated": session["pendingStatementsTruncated"],
            "transactionStartedAt": session["transactionStartedAt"],
            "idleTimeoutSeconds": RAW_SESSION_IDLE_SECONDS,
            "expiresAt": (datetime.fromisoformat(session["lastUsedAt"]) + timedelta(seconds=RAW_SESSION_IDLE_SECONDS)).isoformat() if session["status"] == "open" else None}

    def claim(self, session, expected_revision=None):
        with session["signalLock"]:
            if expected_revision is not None and expected_revision != session["revision"]:
                raise ApiProblem(409, "console_session_changed", "Transaction changed after review. Refresh and review its pending SQL before committing or rolling back.")
            if not session["operation"].acquire(blocking=False):
                raise ApiProblem(409, "console_session_busy", "This session is running a command. Stop it or wait before running another.")
            session.setdefault("cancelled", threading.Event()).clear()
            session.update(status="running", used=time.monotonic(), lastUsedAt=now(), revision=session["revision"] + 1)

    def release(self, session):
        with session["signalLock"]:
            session.update(status="open", used=time.monotonic(), lastUsedAt=now(), currentExecutionId=None, revision=session["revision"] + 1)
            session["operation"].release()

    def cancel(self, session):
        with session["signalLock"]:
            session.setdefault("cancelled", threading.Event()).set()
            for ticket in session["tickets"].values():
                if ticket["status"] == "ready":
                    ticket.update(status="failed", errorMessage="COPY transfer cancelled before starting")
            if session["status"] == "running":
                session["raw"].cancel()

    def validate_policy(self, session, body):
        # Commit policies are explicit user conveniences. Manual sends the exact script.
        if body.commit_mode != "manual":
            if session["raw"].transaction_status != "idle":
                raise ApiProblem(409, "console_transaction_open", "Finish the current transaction before choosing an automatic commit policy")
            from pglast import parse_sql
            try:
                nodes = parse_sql(body.sql)
            except Exception:
                nodes = ()  # PostgreSQL supplies authoritative syntax diagnostics.
            if any(type(node.stmt).__name__ == "TransactionStmt" for node in nodes):
                raise ApiProblem(422, "console_commit_policy_conflict", "Choose Manual to run SQL containing transaction commands")
    def prepare_policy(self, session, body):
        self.validate_policy(session, body)
        raw = session["raw"]
        if body.commit_mode == "whole_run":
            begin = True
        elif body.commit_mode == "manual" and raw.transaction_status == "idle":
            from pglast import parse_sql
            try:
                nodes = parse_sql(body.sql)
            except Exception:
                nodes = ()
            begin = not any(type(node.stmt).__name__ == "TransactionStmt" for node in nodes)
        else:
            begin = False
        if begin:
            result = raw.execute("BEGIN", lambda _: None)
            if result["errorMessage"]:
                raise ApiProblem(422, "console_begin_failed", result["errorMessage"])
            session["transactionStartedAt"] = now()

    def reserve(self, session, body):
        self.validate_policy(session, body)
        self.claim(session, body.expected_revision)
        execution = dict(id="rex_" + secrets.token_hex(16), sessionId=session["id"], status="reserved",
                         results=[], errorMessage=None, sqlstate=None, elapsedMs=0,
                         transactionStatus=session["raw"].transaction_status, notices=[],
                         completedStatements=0, createdAt=now())
        while len(session["executions"]) >= 20:
            session["executions"].popitem(last=False)
        session["executions"][execution["id"]] = execution
        session["currentExecutionId"] = execution["id"]
        return execution

    def run(self, session, execution, body, is_authorized=None):
        execution["status"] = "running"
        started = time.monotonic()
        def notice(diagnostic):
            if len(execution["notices"]) < 100:
                execution["notices"].append({"severity": diagnostic.severity, "message": (diagnostic.message_primary or "")[:2048]})
        raw = session["raw"]
        byte_limit = raw.byte_limit
        raw.connection.add_notice_handler(notice)
        try:
            self.console._repository.record_history(session["owner"], session["workspaceId"], body.sql,
                                                     datetime.now(timezone.utc), self.console._query_history_limit)
            if session["cancelled"].is_set() or (is_authorized and not is_authorized()):
                execution.update(status="cancelled", errorMessage="Query cancelled before it started", sqlstate="57014")
                return
            if body.commit_mode == "whole_run":
                self.prepare_policy(session, body)
            statements = split(body.sql, with_parser=False) if body.commit_mode in {"manual", "each_statement"} else (body.sql,)
            for statement in statements:
                if session["cancelled"].is_set() or (is_authorized and not is_authorized()):
                    execution.update(errorMessage="Query cancelled", sqlstate="57014")
                    break
                if body.commit_mode == "manual":
                    self.prepare_policy(session, SqlCreate(sql=statement, commit_mode="manual"))
                result = raw.execute(statement, execution.update)
                if raw.transaction_status in {"intrans", "inerror"}:
                    session["transactionStartedAt"] = session["transactionStartedAt"] or now()
                else:
                    session["transactionStartedAt"] = None
                rows = result.pop("results")
                raw.byte_limit = max(0, raw.byte_limit - sum(64 + sum(32 + len(value.encode("utf-8")) if value is not None else 8 for value in row) for result_row in rows for row in result_row["rows"]))
                execution["results"].extend(rows[:max(0, 1000 - len(execution["results"]))])
                execution.update(result)
                if execution["errorMessage"]:
                    break
            if (session["cancelled"].is_set() or (is_authorized and not is_authorized())) and not execution["errorMessage"]:
                execution.update(errorMessage="Query cancelled", sqlstate="57014")
            if body.commit_mode == "whole_run":
                boundary = raw.execute("ROLLBACK" if execution["errorMessage"] else "COMMIT", lambda _: None)
                if boundary["errorMessage"]:
                    execution.update(errorMessage=boundary["errorMessage"], sqlstate=boundary["sqlstate"])
            for index, result in enumerate(execution["results"]):
                result["statementIndex"] = index
            execution["completedStatements"] = len(execution["results"])
            execution["status"] = "cancelled" if execution["sqlstate"] == "57014" else "failed" if execution["errorMessage"] else "succeeded"
        except Exception as error:
            execution.update(status="uncertain" if isinstance(error, (OperationalError, InterfaceError)) and not getattr(error, "sqlstate", None) else "failed", errorMessage=str(error)[:2048], sqlstate=getattr(error, "sqlstate", None))
            if body.commit_mode == "whole_run" and raw.transaction_status in {"intrans", "inerror"}:
                try:
                    raw.execute("ROLLBACK", lambda _: None)
                except Exception:
                    raw.close()
        finally:
            raw.byte_limit = byte_limit
            raw.connection.remove_notice_handler(notice)
            execution.update(elapsedMs=round((time.monotonic() - started) * 1000), transactionStatus=raw.transaction_status)
            self.track_pending(session, body.sql, execution["status"], execution["results"])
            self.release(session)

    def track_pending(self, session, sql_text, status, results=()):
        if session["raw"].transaction_status not in {"intrans", "inerror"}:
            session.update(pendingStatements=[], pendingStatementsTruncated=False, transactionStartedAt=None)
            return
        from pglast import parse_sql
        from pglast.enums import TransactionStmtKind
        session["transactionStartedAt"] = session["transactionStartedAt"] or now()
        try:
            parts = split(sql_text, with_parser=False)
        except Exception:
            parts = (sql_text,)
        for index, statement in enumerate(parts):
            if results and index >= len(results) and status == "succeeded":
                break
            try:
                node = parse_sql(statement)[0].stmt
                boundary = type(node).__name__ == "TransactionStmt" and node.kind in {
                    TransactionStmtKind.TRANS_STMT_COMMIT, TransactionStmtKind.TRANS_STMT_ROLLBACK,
                    TransactionStmtKind.TRANS_STMT_PREPARE,
                }
            except Exception:
                boundary = False
            if boundary and index < len(results):
                session.update(pendingStatements=[], pendingStatementsTruncated=False, transactionStartedAt=now())
                continue
            pending = session["pendingStatements"]
            if len(pending) >= 100 or sum(len(item["sql"]) for item in pending) + len(statement) > 1024 * 1024:
                session["pendingStatementsTruncated"] = True
                continue
            pending.append({"sql": statement, "status": "succeeded" if index < len(results) else status, "ranAt": now()})
            if status != "succeeded" and index >= len(results):
                break

    def list_sessions(self, owner, workspace):
        self.console._require_workspace(owner, workspace)
        self.reap()
        with self.lock:
            return {"sessions": [self.view(s) for s in self.sessions.values()
                                 if s["owner"] == owner and s["workspaceId"] == workspace]}

    def activity(self, session):
        data = self.view(session)
        try:
            with self.connections.use(session["owner"], session["target"].connection_id) as resolved:
                if (getattr(resolved, "owner_id", None) or session["owner"]) != (session["target"].connection_owner_id or session["owner"]):
                    raise ApiProblem(409, "console_target_changed", "The PostgreSQL connection changed")
                data.update(self.postgres.console_activity(resolved, session["raw"].backend_pid,
                                                          started_before=datetime.fromisoformat(session["createdAt"])))
        except ConnectionNotFoundError as error:
            raise ApiProblem(403, "console_connection_revoked", "Access to the PostgreSQL connection was revoked") from error
        except PostgresGatewayError:
            data.update(monitoringAvailable=False, monitoringMessage="Database activity is temporarily unavailable")
        return data

    def ticket(self, session, body, direction):
        from pglast import parse_sql
        try:
            nodes = parse_sql(body.sql)
            command = nodes[0].stmt if len(nodes) == 1 else None
            valid = type(command).__name__ == "CopyStmt" and command.filename is None and bool(command.is_from) == (direction == "upload")
        except Exception:
            valid = False
        if not valid:
            raise ApiProblem(422, "console_copy_direction", "Enter exactly one COPY FROM STDIN command for upload or COPY TO STDOUT command for download")
        self.validate_policy(session, body)
        while len(session["tickets"]) >= 20:
            session["tickets"].popitem(last=False)
        ticket = dict(id="cpy_" + secrets.token_hex(16), sql=body.sql, direction=direction,
                      status="ready", errorMessage=None, commitMode=body.commit_mode)
        session["tickets"][ticket["id"]] = ticket
        return self.ticket_view(ticket)

    @staticmethod
    def ticket_view(ticket):
        return {k: ticket[k] for k in ("id", "status", "errorMessage")}

    @staticmethod
    def authorize_ticket(ticket):
        if ticket.get("isAuthorized") and not ticket["isAuthorized"]():
            raise ApiProblem(409, "ai_permission_changed", "COPY authorization was revoked or its request stopped")

    def find_ticket(self, session, ticket_id, direction):
        ticket = session["tickets"].get(ticket_id)
        if ticket is None or ticket["direction"] != direction:
            raise ApiProblem(404, "console_copy_not_found", "COPY transfer not found")
        return ticket

    def download(self, session, ticket):
        with session["signalLock"]:
            if ticket["status"] != "ready":
                raise ApiProblem(409, "console_copy_used", "COPY transfer has already started; create another to run it again")
            self.authorize_ticket(ticket)
            self.claim(session, ticket.get("expectedRevision"))
            ticket["status"] = "running"
        return CopyDownload(self, session, ticket)

    def close_session(self, session, expected_revision=None):
        self.claim(session, expected_revision)
        try:
            session["raw"].close()
            with self.lock:
                self.sessions.pop(session["id"], None)
        finally:
            session["operation"].release()

    def reap(self):
        with self.lock:
            stale = [s for s in self.sessions.values() if s["status"] == "open" and time.monotonic() - s["used"] > RAW_SESSION_IDLE_SECONDS]
        for session in stale:
            try:
                self.close_session(session)
            except ApiProblem:
                pass

    def close(self):
        with self.lock:
            sessions = tuple(self.sessions.values())
        for session in sessions:
            if session["status"] == "running":
                session["raw"].cancel()
            session["raw"].close()


router = APIRouter(prefix="/api/v1/schemii/workspaces/{workspace_id}/console/sessions", tags=["schemii-sql-console"])


def service(request: Request) -> RawSessionService:
    return request.app.state.raw_console


def owned(request, principal, workspace_id, session_id, validate_target=True):
    try:
        return service(request).get(principal.user_id, workspace_id, session_id, validate_target)
    except ConsoleServiceError as error:
        raise ApiProblem(error.status, error.code, str(error)) from error


@router.get("")
def list_sessions(workspace_id: str, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    """List this owner's workspace sessions and bounded SQL history in open transactions."""
    return service(request).list_sessions(principal.user_id, workspace_id)


@router.post("", status_code=201)
def create_session(workspace_id: str, body: SessionCreate, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    """Open an owner-bound PostgreSQL session using the connection role's privileges."""
    try:
        return service(request).create(principal.user_id, workspace_id, body)
    except ConsoleServiceError as error:
        raise ApiProblem(error.status, error.code, str(error)) from error
    except PostgresGatewayError as error:
        raise ApiProblem(502, error.code, str(error)) from error


@router.get("/{session_id}")
def get_session(workspace_id: str, session_id: str, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    """Read actual PostgreSQL session and transaction status without running SQL."""
    return service(request).view(owned(request, principal, workspace_id, session_id))


@router.delete("/{session_id}", status_code=204)
def close_session(workspace_id: str, session_id: str, request: Request, principal: Principal = Depends(get_current_principal)) -> Response:
    """Disconnect and roll back any open transaction; running work must be stopped first."""
    service(request).close_session(owned(request, principal, workspace_id, session_id, False))
    return Response(status_code=204)


@router.post("/{session_id}/executions", status_code=201)
def create_execution(workspace_id: str, session_id: str, body: SqlCreate, request: Request, background_tasks: BackgroundTasks, principal: Principal = Depends(get_current_principal)) -> dict:
    """Run exact human SQL; automatic commit policies apply only when explicitly selected."""
    session = owned(request, principal, workspace_id, session_id)
    execution = service(request).reserve(session, body)
    background_tasks.add_task(service(request).run, session, execution, body)
    return execution.copy()


def build_raw_explain_sql(sql, analyze=False):
    statements = split(sql, with_parser=False)
    if len(statements) != 1:
        raise ApiProblem(422, "console_explain_single_statement", "Select one statement to explain")
    return "EXPLAIN (FORMAT JSON, VERBOSE, COSTS" + (", ANALYZE, BUFFERS" if analyze else "") + ") " + statements[0]


@router.post("/{session_id}/explain", status_code=201)
def explain_execution(workspace_id: str, session_id: str, body: ExplainCreate, request: Request, background_tasks: BackgroundTasks, principal: Principal = Depends(get_current_principal)) -> dict:
    """Explain one command in this exact session; ANALYZE executes with its current privileges."""
    sql_text = build_raw_explain_sql(body.sql, body.analyze)
    return create_execution(workspace_id, session_id, SqlCreate(sql=sql_text, commit_mode=body.commit_mode), request, background_tasks, principal)


@router.get("/{session_id}/executions/{execution_id}")
def get_execution(workspace_id: str, session_id: str, execution_id: str, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    """Poll a retained receipt without replaying commands."""
    session = owned(request, principal, workspace_id, session_id)
    execution = session["executions"].get(execution_id)
    if execution is None:
        raise ApiProblem(404, "console_execution_not_found", "Execution receipt expired")
    return execution.copy()


@router.get("/{session_id}/activity")
def get_activity(workspace_id: str, session_id: str, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    """Observe database waits and blockers through the independent monitoring connection lane."""
    return service(request).activity(owned(request, principal, workspace_id, session_id))


@router.post("/{session_id}/cancel")
def cancel_execution(workspace_id: str, session_id: str, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    """Signal this exact retained PostgreSQL connection, including COPY operations."""
    session = owned(request, principal, workspace_id, session_id, False)
    service(request).cancel(session)
    return service(request).view(session)


@router.post("/{session_id}/copy/uploads", status_code=201)
def create_upload(workspace_id: str, session_id: str, body: SqlCreate, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    """Prepare an owner-bound one-use COPY FROM STDIN upload; SQL is not yet executed."""
    return service(request).ticket(owned(request, principal, workspace_id, session_id), body, "upload")


@router.put("/{session_id}/copy/uploads/{ticket_id}")
async def upload_copy(workspace_id: str, session_id: str, ticket_id: str, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    """Stream request bytes directly into PostgreSQL COPY without file or row persistence."""
    session = await anyio.to_thread.run_sync(owned, request, principal, workspace_id, session_id)
    manager = service(request)
    ticket = manager.find_ticket(session, ticket_id, "upload")
    with session["signalLock"]:
        if ticket["status"] != "ready":
            raise ApiProblem(409, "console_copy_used", "COPY transfer has already started")
        manager.authorize_ticket(ticket)
        manager.claim(session, ticket.get("expectedRevision"))
        ticket["status"] = "running"
    cursor = context = None
    entered = False
    try:
        policy = SqlCreate(sql=ticket["sql"], commit_mode=(
            "each_statement" if ticket["commitMode"] == "whole_run" else ticket["commitMode"]))
        await anyio.to_thread.run_sync(manager.prepare_policy, session, policy)
        cursor = session["raw"].connection.cursor()
        context = cursor.copy(ticket["sql"])
        copy = await anyio.to_thread.run_sync(context.__enter__)
        entered = True
        async for chunk in request.stream():
            # Cancellation must wait for this worker before COPY cleanup/release.
            manager.authorize_ticket(ticket)
            await anyio.to_thread.run_sync(copy.write, chunk)
        manager.authorize_ticket(ticket)
        entered = False
        await anyio.to_thread.run_sync(context.__exit__, None, None, None)
        ticket["status"] = "succeeded"
        return manager.ticket_view(ticket) | {"command": cursor.statusmessage, "transactionStatus": session["raw"].transaction_status}
    except BaseException as error:
        ticket.update(status="failed", errorMessage=str(error)[:2048] or "COPY upload interrupted")
        with anyio.CancelScope(shield=True):
            if entered:
                try:
                    await anyio.to_thread.run_sync(context.__exit__, type(error), error, error.__traceback__)
                except BaseException:
                    # The failed COPY must never leave its protocol attached to
                    # a connection that another request can reuse.
                    await anyio.to_thread.run_sync(session["raw"].close)
        if isinstance(error, Exception):
            raise ApiProblem(422, "console_copy_failed", ticket["errorMessage"]) from error
        raise
    finally:
        with anyio.CancelScope(shield=True):
            try:
                if cursor is not None:
                    await anyio.to_thread.run_sync(cursor.close)
                manager.track_pending(session, ticket["sql"], ticket["status"])
            finally:
                manager.release(session)


@router.post("/{session_id}/copy/downloads", status_code=201)
def create_download(workspace_id: str, session_id: str, body: SqlCreate, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    """Prepare a one-use COPY TO STDOUT ticket for native browser streaming downloads."""
    return service(request).ticket(owned(request, principal, workspace_id, session_id), body, "download")


@router.get("/{session_id}/copy/downloads/{ticket_id}/status")
def download_status(workspace_id: str, session_id: str, ticket_id: str, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    """Read native COPY transfer completion or PostgreSQL errors."""
    session = owned(request, principal, workspace_id, session_id)
    return service(request).ticket_view(service(request).find_ticket(session, ticket_id, "download"))


@router.get("/{session_id}/copy/downloads/{ticket_id}")
def download_copy(workspace_id: str, session_id: str, ticket_id: str, request: Request, principal: Principal = Depends(get_current_principal)) -> StreamingResponse:
    """Stream COPY bytes directly to the browser download manager."""
    session = owned(request, principal, workspace_id, session_id)
    ticket = service(request).find_ticket(session, ticket_id, "download")
    return CopyStreamingResponse(service(request).download(session, ticket), media_type="application/octet-stream",
                             headers={"Content-Disposition": 'attachment; filename="query-copy.dat"', "Cache-Control": "no-store"})


@router.post("/{session_id}/copy/download")
def download_copy_direct(workspace_id: str, session_id: str, body: SqlCreate, request: Request, principal: Principal = Depends(get_current_principal)) -> StreamingResponse:
    """Stream COPY output for browsers supporting a writable file picker."""
    session = owned(request, principal, workspace_id, session_id)
    ticket_view = service(request).ticket(session, body, "download")
    return download_copy(workspace_id, session_id, ticket_view["id"], request, principal)
