import { randomBytes } from 'node:crypto';
import { setTimeout as sleep } from 'node:timers/promises';
import { HTTPClient } from './http.mjs';
import { RowOracle, NDJSONOracle, CSVOracle, ProtocolFailure } from './protocol.mjs';
import { ordinaryRequest, streamRequest } from './fixtures.mjs';

const fail = code => { throw new ProtocolFailure(code); };
const identifier = value => {
  if (typeof value !== 'string' || !/^load_[a-f0-9]{16}_u\d{4}$/.test(value)) fail('unowned_source_schema');
  return `"${value}"`;
};
const exactId = (value, prefix) => new RegExp(`^${prefix}_[a-f0-9]{32}$`).test(value || '');
const json = (client, method, path, body, expected, operation, signal) => client.json(method, path, body, expected, { operation, signal });

export async function runJourney({ workload, account, client, signal, sequence = 0,
  onIntent = async () => { throw new Error('durable_intent_required'); }, onExecution = async () => {},
  finishIntent = async () => {}, save = async () => {}, origin, Client = HTTPClient,
  captureSource = async () => { throw new Error('independent_source_capture_required'); },
  idleWait = ms => sleep(ms, undefined, { signal }) }) {
  if (['cheap-read', 'catalog', 'compile'].includes(workload)) {
    const request = ordinaryRequest(workload, account);
    const result = await json(client, request.method, request.path, request.body, request.status, workload === 'cheap-read' ? 'auth_read' : workload, signal);
    if (workload === 'cheap-read' && (result.user?.id !== account.userId || result.user.username !== account.username)) fail('cross_owner_data');
    if (workload === 'catalog' && !result.tables?.length) fail('empty_catalog');
    if (workload === 'compile' && (typeof result.sql !== 'string' || !result.sql.trim())) fail('missing_compiled_sql');
    return;
  }
  if (workload === 'login-churn') {
    // Never mutate a shared stage client's cookie during concurrent journeys.
    const session = new Client({ origin, accounting: client.accounting, timeoutMs: 30000, maxBytes: 65536 });
    const intent = await onIntent({ kind: 'session', username: account.username });
    let loggedOut = false;
    try {
      await session.login({ username: account.username, password: account.password }, account.userId);
      await finishIntent(intent, { cookie: session.cookie, status: 'acquired' });
      const current = await json(session, 'GET', '/api/v1/auth/me', undefined, 200, 'auth_read', signal);
      if (current.user?.id !== account.userId || current.user.username !== account.username) fail('cross_owner_data');
      await session.logout(); loggedOut = true; await finishIntent(intent, { status: 'closed', cookie: null });
    } finally {
      try {
        if (!loggedOut && session.cookie) { await session.logout(); await finishIntent(intent, { status: 'closed', cookie: null }); }
      } finally { session.close(); }
    }
    return;
  }
  if (workload === 'metadata-write') {
    // One revisioned label write per identity at a time. Parallel readers are
    // unaffected; a conflict is reported rather than retried or overwritten.
    const control = account.control ||= {};
    if (control.metadataBusy) fail('metadata_journey_overlap');
    control.metadataBusy = true;
    try {
      const current = await json(client, 'GET', `/api/v1/schemii/workspaces/${account.workspace.id}`, undefined, 200, 'metadata_read', signal);
      if (current.ownerId !== account.userId || current.id !== account.workspace.id) fail('metadata_owner_changed');
      const name = `${account.fixtureTag}_metadata_${sequence}`;
      const intent = await onIntent({ kind: 'metadata', username: account.username, id: current.id, expectedRevision: current.revision, name });
      const updated = await json(client, 'PATCH', `/api/v1/schemii/workspaces/${current.id}`, { expectedRevision: current.revision, name }, 200, 'metadata_write', signal);
      if (updated.id !== current.id || updated.ownerId !== account.userId || updated.name !== name || updated.revision !== current.revision + 1) fail('metadata_revision_failed');
      account.workspace.revision = updated.revision; account.workspace.name = name; await save();
      await finishIntent(intent, { status: 'closed', revision: updated.revision });
    } finally { control.metadataBusy = false; }
    return;
  }
  if (['sql-page', 'wide-rows', 'idle-expiry'].includes(workload)) {
    const wide = workload === 'wide-rows', columns = wide ? ['id', 'owner_nonce', 'c1', 'c2', 'c3', 'c4'] : ['id', 'owner_nonce'];
    const sql = `SELECT ${columns.map(value => `"${value}"`).join(', ')} FROM ${identifier(account.source.schema)}."${wide ? 'wide_rows' : 'orders'}" ORDER BY "id"`;
    const consoleId = `con_${randomBytes(16).toString('hex')}`;
    const intent = await onIntent({ kind: 'execution', username: account.username, consoleId, workspaceId: account.workspace.id });
    let execution, resultId, closed = false;
    try {
      execution = await json(client, 'POST', `/api/v1/schemii/workspaces/${account.workspace.id}/console/executions`, {
        consoleId, expectedWorkspaceRevision: account.workspace.revision, expectedSettingsRevision: account.settings.revision,
        mode: 'managed_read', statements: [sql],
      }, 201, 'execution_create', signal);
      if (!exactId(execution.id, 'cex')) fail('invalid_execution');
      await onExecution(account.username, execution.id); await finishIntent(intent, { status: 'acquired', executionId: execution.id });
      const started = performance.now();
      while (['reserved', 'running'].includes(execution.status) && performance.now() - started < 30000) {
        await sleep(25, undefined, { signal });
        const next = await json(client, 'GET', `/api/v1/common/query-executions/${execution.id}`, undefined, 200, 'execution_read', signal);
        if (next.id !== execution.id) fail('execution_identity_changed'); execution = next;
      }
      if (execution.status !== 'succeeded' || execution.results?.length !== 1 || !exactId(execution.results[0].id, 'res') ||
          JSON.stringify(execution.completedStatementIndexes) !== '[0]') fail('sql_terminal_failed');
      await captureSource(execution.id); // Retained source connection is still owned here, before any final-page close.
      resultId = execution.results[0].id;
      const path = `/api/v1/common/query-executions/${execution.id}/results/${resultId}`;
      const oracle = new RowOracle(account.oracles[wide ? 'wide' : 'sql']);
      let cursor = null, pageNumber = 0, expectedId = 0;
      do {
        const page = await json(client, 'GET', path + (cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''), undefined, 200, 'result_page', signal);
        if (page.executionId !== execution.id || page.resultId !== resultId || page.truncated !== false || !Array.isArray(page.rows)) fail('sql_page_invalid');
        oracle.columns(page.columns?.map(value => value.name));
        for (const row of page.rows) { if (row[0] !== ++expectedId) fail('sql_row_order_invalid'); oracle.row(row); }
        if (page.nextCursor !== null && (typeof page.nextCursor !== 'string' || !page.nextCursor || page.nextCursor === cursor)) fail('sql_cursor_invalid');
        cursor = page.nextCursor; pageNumber++;
        if (pageNumber > 10000) fail('sql_page_budget');
        if (workload === 'idle-expiry') {
          if (pageNumber !== 1 || !cursor || !Number.isFinite(Date.parse(page.expiresAt)) || Date.parse(page.expiresAt) > Date.now() + 1800000) fail('idle_contract_unqualified');
          const idleStarted = performance.now();
          await idleWait(1800000);
          // Tests may interrupt/inspect the wait, never replace elapsed time.
          if (performance.now() - idleStarted < 1800000) fail('real_idle_window_not_elapsed');
          const expired = await client.request({ path: `${path}?cursor=${encodeURIComponent(cursor)}`, operation: 'result_page', signal });
          if (expired.status !== 410 || !['console_result_gone', 'console_result_replay_required'].includes(expired.data?.error?.code)) fail('idle_expiry_not_observed');
          await finishIntent(intent, { status: 'expired', idleSeconds: 1800, independentRelease: 'pending_witness' });
          closed = true; return;
        }
      } while (cursor);
      oracle.finish();
      if (account.oracles[wide ? 'wide' : 'sql'].rows > account.settings.rowPageSize && pageNumber < 2) fail('paging_not_exercised');
      await json(client, 'DELETE', path, undefined, 204, 'result_close', signal); closed = true;
      await finishIntent(intent, { status: 'closed', independentRelease: 'pending_witness' });
    } finally {
      if (execution?.id && !closed) {
        await json(client, 'DELETE', `/api/v1/common/query-executions/${execution.id}`, undefined, 200, 'control');
        if (resultId) await json(client, 'DELETE', `/api/v1/common/query-executions/${execution.id}/results/${resultId}`, undefined, 204, 'result_close');
      }
    }
    return;
  }
  const shared = workload === 'shared-report';
  const request = shared ? { method: 'POST', path: `/api/v1/schemer/dashboards/${account.sharedDashboard.id}/executions/stream`,
    body: { expectedRevision: account.sharedDashboard.revision }, tiles: account.sharedDashboard.tiles } : streamRequest(workload, account);
  const dashboard = shared ? account.sharedDashboard : workload.startsWith('report-') ? account.dashboards[Number(workload.slice(7))] : null;
  const binding = dashboard ? { dashboardId: dashboard.id, dashboardRevision: dashboard.revision } : { modelId: account.model.id, modelRevision: account.model.revision };
  const reportMetadata = { kind: 'report-stream', ...binding };
  const intent = await onIntent({ kind: 'stream', username: account.username, workload });
  let recording = Promise.resolve(), executionId;
  const oracle = workload === 'csv' ? new CSVOracle(account.oracles.csv) : new NDJSONOracle(request.tiles, { onExecution: id => {
    executionId = id; recording = recording.then(async () => {
      await onExecution(account.username, id, reportMetadata);
      await finishIntent(intent, { status: 'acquired', executionId: id });
    });
    void recording.catch(() => {});
  } });
  let cancellation;
  if (workload === 'cancel') {
    const push = oracle.push.bind(oracle);
    oracle.push = chunk => {
      push(chunk);
      if (executionId && !cancellation) cancellation = recording.then(async () => {
        const value = await json(client, 'DELETE', `/api/v1/common/query-executions/${executionId}`, undefined, 200, 'control', signal);
        if (value.id !== executionId) fail('cancel_execution_changed');
        if (['succeeded', 'failed', 'cancelled', 'partial_committed'].includes(value.status))
          await onExecution(account.username, executionId, { ...reportMetadata, terminalAt: new Date().toISOString(), terminal: value.status });
      });
    };
  }
  try {
    const result = await client.request({ ...request, oracle, signal, operation: 'report_stream', slowMs: workload === 'slow-reader' ? 100 : 0,
      disconnectAfterBytes: workload === 'disconnect' ? 1024 : 0 });
    if (result.status !== 200) fail('report_http_failed');
    if (executionId) await recording.then(() => onExecution(account.username, executionId, { ...reportMetadata, terminalAt: new Date().toISOString(), terminal: 'drained' }));
    await finishIntent(intent, { status: 'closed', ...(executionId ? { executionId } : { idExposure: 'server_managed_not_exposed' }) });
  } catch (error) {
    if (!(workload === 'disconnect' && error.code === 'intentional_disconnect') && !(workload === 'cancel' && cancellation && error.code === 'stream_error')) throw error;
  } finally { await recording; if (cancellation) await cancellation; }
}
