"""AI adapters for the same owner-bound database sessions used by the console."""
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator
from schemii.common.api.errors import ApiProblem

OPERATIONS = ('create', 'list', 'inspect', 'execute', 'explain', 'begin', 'commit', 'rollback', 'cancel', 'close', 'copy_upload', 'copy_download')

class RawConsoleAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation: Literal['create', 'list', 'inspect', 'execute', 'explain', 'begin', 'commit', 'rollback', 'cancel', 'close', 'copy_upload', 'copy_download']
    sessionId: str | None = Field(default=None, pattern=r'^raw_[0-9a-f]{32}$')
    expectedRevision: int | None = Field(default=None, ge=1, strict=True)
    executionId: str | None = Field(default=None, pattern=r'^rex_[0-9a-f]{32}$')
    sql: str | None = Field(default=None, min_length=1, max_length=1024 * 1024)
    commitMode: Literal['manual', 'each_statement', 'whole_run'] = 'manual'
    analyze: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def required_fields(self):
        if self.operation not in {'create', 'list'} and not self.sessionId:
            raise ValueError('Use a discovered sessionId')
        if self.operation in {'execute', 'explain', 'copy_upload', 'copy_download'} and not self.sql:
            raise ValueError('Supply the exact SQL')
        if self.operation not in {'create', 'list', 'inspect'} and self.expectedRevision is None:
            raise ValueError('Supply the reviewed session revision')
        return self


def required_actions(action):
    operation = action['operation']
    ids = [f'console.{operation}']
    if operation == 'explain':
        ids.append('query.analyze' if action.get('analyze') else 'query.explain')
        if action.get('analyze'):
            ids.append('console.execute')
    executes = operation in {'execute', 'copy_upload', 'copy_download'} or operation == 'explain' and action.get('analyze')
    if executes and action.get('commitMode', 'manual') != 'manual':
        ids.append('console.commit')
    if operation == 'execute':
        from schemii.common.postgres.query_plans import query_authorities
        ids.extend('query.' + authority for authority in query_authorities(action['sql']) if authority in {'explain', 'analyze'})
        from pglast import parse_sql
        from pglast.enums import TransactionStmtKind as Kind
        try:
            nodes = parse_sql(action['sql'])
        except Exception:
            # PostgreSQL may accept syntax newer than our parser. Unknown SQL
            # cannot silently acquire transaction or diagnostic authority.
            ids.extend(("console.begin", "console.commit", "console.rollback", "query.explain", "query.analyze"))
            nodes = ()
        for node in nodes:
            if type(node.stmt).__name__ == 'TransactionStmt':
                kind = node.stmt.kind
                ids.append('console.commit' if kind in {Kind.TRANS_STMT_COMMIT, Kind.TRANS_STMT_COMMIT_PREPARED, Kind.TRANS_STMT_PREPARE} else 'console.rollback' if kind in {Kind.TRANS_STMT_ROLLBACK, Kind.TRANS_STMT_ROLLBACK_PREPARED, Kind.TRANS_STMT_ROLLBACK_TO} else 'console.begin')
    return tuple(dict.fromkeys(ids))


def execute(ai, owner, chat, action, authorized):
    from schemii.schemii.console.raw_session import SessionCreate, SqlCreate
    from schemii.schemii.console.raw_session import build_raw_explain_sql
    request = RawConsoleAction.model_validate(action)
    manager = ai.raw_console
    def check():
        if not authorized():
            raise ApiProblem(409, 'ai_permission_changed', 'The request was stopped or its permissions changed')
    check()
    operation = request.operation
    if operation == 'create':
        workspace = ai.services.workspaces.get(owner, chat.workspace_id)
        return manager.create(owner, chat.workspace_id, SessionCreate(console_id='con_' + uuid4().hex,
            expected_workspace_revision=workspace.revision, expected_settings_revision=ai.services.console.settings(owner).revision))
    if operation == 'list':
        return manager.list_sessions(owner, chat.workspace_id)
    session = manager.get(owner, chat.workspace_id, request.sessionId, validate_target=operation not in {'close', 'cancel'})
    if operation == 'inspect':
        result = {'session': manager.view(session), 'activity': manager.activity(session)}
        if request.executionId:
            receipt = session['executions'].get(request.executionId)
            if receipt is None:
                raise ApiProblem(404, 'console_execution_not_found', 'Execution is not in this session')
            result['execution'] = {key: value for key, value in receipt.items() if key not in {'results', 'notices', 'errorMessage'}}

        return result
    if operation == 'cancel':
        with session['signalLock']:
            if session['revision'] != request.expectedRevision or (request.executionId and session['currentExecutionId'] != request.executionId):
                raise ApiProblem(409, 'console_session_changed', 'The running command changed after review')
            check()
            manager.cancel(session)
        return {'sessionId': session['id'], 'cancelRequested': True}
    if operation == 'close':
        manager.close_session(session, expected_revision=request.expectedRevision)
        return {'sessionId': session['id'], 'closed': True, 'pendingWorkRolledBack': True}
    sql = {'begin': 'BEGIN', 'commit': 'COMMIT', 'rollback': 'ROLLBACK'}.get(operation, request.sql)
    if operation == 'explain':
        sql = build_raw_explain_sql(sql, analyze=request.analyze)
    body = SqlCreate(sql=sql, commit_mode=request.commitMode, expected_revision=request.expectedRevision)
    if operation.startswith('copy_'):
        # A browser chooses/uploads a file or downloads the stream. Models never
        # receive file bytes or an arbitrary filesystem/network capability.
        with session['signalLock']:
            if session['revision'] != request.expectedRevision:
                raise ApiProblem(409, 'console_session_changed', 'Review the current session revision')
            check()
            direction = 'upload' if operation == 'copy_upload' else 'download'
            ticket = manager.ticket(session, body, direction)
            session['tickets'][ticket['id']]['isAuthorized'] = authorized
            session['tickets'][ticket['id']]['expectedRevision'] = request.expectedRevision
        base = f'/api/v1/schemii/workspaces/{chat.workspace_id}/console/sessions/{session["id"]}/copy/{direction}s/{ticket["id"]}'
        return {'effect': 'browser_copy_handoff', 'direction': direction, 'sessionId': session['id'], 'ticketId': ticket['id'], 'url': base, 'method': 'PUT' if direction == 'upload' else 'GET', 'sql': sql, 'sqlExecuted': False}
    check()
    execution = manager.reserve(session, body)
    from schemii.common.query_executions.cancellation import cancellable_connection
    try:
        with cancellable_connection(session["raw"].connection):
            manager.run(session, execution, body, is_authorized=authorized)
    finally:
        if execution["status"] == "reserved":
            execution.update(status="cancelled")
            manager.release(session)
    # Durable operation receipts contain metadata only. Row values are inspected
    # separately under structured_data_read and remain in the volatile session.
    return {key: value for key, value in execution.items() if key not in {'results', 'notices', 'errorMessage'}}

class RawResults(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sessionId: str = Field(pattern=r'^raw_[0-9a-f]{32}$')
    executionId: str = Field(pattern=r'^rex_[0-9a-f]{32}$')


def results(ai, owner, chat, arguments):
    request = RawResults.model_validate(arguments)
    if not chat.capabilities.structured_data_read:
        raise ApiProblem(403, 'ai_permission_required', 'Enable Analyze query results')
    session = ai.raw_console.get(owner, chat.workspace_id, request.sessionId)
    receipt = session['executions'].get(request.executionId)
    if receipt is None:
        raise ApiProblem(404, 'console_execution_not_found', 'The result expired; SQL was not replayed')
    import json
    response = {'sessionId': request.sessionId, 'executionId': request.executionId,
                'status': receipt['status'], 'results': [], 'transientData': True,
                'sampled': False, 'omittedResults': 0, 'notices': [],
                'sampleNotice': 'Values are bounded samples from this session. SQL was not replayed.'}
    budget = ai.policy.result_context_bytes
    remaining = ai.policy.result_context_rows
    def fits():
        return len(json.dumps(response, ensure_ascii=False, default=str).encode()) <= budget
    for source in receipt['results']:
        result = {key: value for key, value in source.items() if key != 'rows'}
        result.update(rows=[], returnedRows=0, sampled=bool(source.get('truncated')))
        response['results'].append(result)
        if not fits():
            response['results'].pop()
            response['omittedResults'] += 1
            response['sampled'] = True
            continue
        for row in source.get('rows', []):
            if remaining == 0:
                break
            result['rows'].append(row)
            if not fits():
                result['rows'].pop()
                break
            remaining -= 1
        result['returnedRows'] = len(result['rows'])
        result['sampled'] |= result['returnedRows'] != len(source.get('rows', []))
        response['sampled'] |= result['sampled']
    if receipt.get('errorMessage'):
        response['errorMessage'] = receipt['errorMessage']
        if not fits():
            response.pop('errorMessage')
            response['sampled'] = True
    for notice in receipt.get('notices', []):
        response['notices'].append(notice)
        if not fits():
            response['notices'].pop()
            response['sampled'] = True
            break
    # Counters and sampling flags can grow after the last row was considered.
    while not fits() and response['results']:
        response['results'].pop()
        response['omittedResults'] += 1
        response['sampled'] = True
    return response
