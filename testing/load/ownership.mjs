// Private run attribution. Never export accounts, SQL or opaque IDs as metrics.
import { spawn } from 'node:child_process';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { isDeepStrictEqual as same } from 'node:util';
import { HTTPClient } from './http.mjs';
import { readPrivateJSON, writePrivateJSON } from './runtime.mjs';

const noJobLanes = new Set(['cheap-read', 'catalog', 'compile', 'report-1', 'report-5', 'report-20', 'csv', 'slow-reader', 'disconnect', 'cancel']);
const executionStates = new Set(['reserved', 'running', 'succeeded', 'failed', 'cancelled', 'partial_committed']);
const jobStates = new Set(['queued', 'running', 'cancelling', 'completed', 'paused', 'failed']);
const id = (value, prefix) => typeof value === 'string' && new RegExp(`^${prefix}_[a-f0-9]{32}$`).test(value);
export function fresh(value, now = Date.now()) {
  return Number.isFinite(Date.parse(value?.at)) && now - Date.parse(value.at) <= 15000 && Date.parse(value.at) <= now + 5000;
}
const timestamp = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$/.test(value) && Number.isFinite(Date.parse(value));
const backendKey = value => `${value.pid}/${value.backendStart}`;
const backendIdentity = value => ({ pid: value.pid, backendStart: value.backendStart, database: value.database, role: value.role, applicationName: value.applicationName });
export function sourceEvidence(value, runId, now = Date.now(), expectedAuthority = null) {
  const endpoint = value?.authority?.endpoint, server = value?.serverIdentity;
  if (value?.version !== 2 || value.status !== 'available' || !fresh(value, now) || !/^load_[a-f0-9]{16}$/.test(runId || '') || value.authority?.runId !== runId ||
      value.authority.kind !== 'exclusive_source_role' || value.authority.exclusiveToRun !== true ||
      typeof value.authority.database !== 'string' || !value.authority.database || typeof value.authority.role !== 'string' || !value.authority.role ||
      value.authority.applicationName !== 'schemii' || typeof value.authority.provisioningReceipt !== 'string' || !value.authority.provisioningReceipt.startsWith('/') ||
      !endpoint || typeof endpoint.host !== 'string' || !endpoint.host || !Number.isSafeInteger(endpoint.port) || endpoint.port < 1 || endpoint.port > 65535 ||
      !['require', 'verify-ca', 'verify-full'].includes(endpoint.sslmode) ||
      !server || typeof server.address !== 'string' || !server.address || !Number.isSafeInteger(server.port) || server.port < 1 || server.port > 65535 ||
      !Number.isSafeInteger(server.databaseOid) || server.databaseOid < 1 || !timestamp(server.postmasterStart) || !/^[a-f0-9]{64}$/.test(server.fixtureId || '') ||
      !same(server, value.authority.server) || (expectedAuthority && !same(expectedAuthority, value.authority)) ||
      value.method !== 'bound_server_discovery_and_captured_pid_start_recheck' ||
      !Array.isArray(value.captured) || !Array.isArray(value.live) || value.captured.length > 10000 ||
      !Array.isArray(value.capturedChecks) || value.capturedChecks.length !== value.captured.length ||
      value.ownedBackends !== value.live.length || value.releaseVerified !== (value.captured.length > 0 && value.live.length === 0)) throw new Error('owned_source_observation_unavailable');
  const keys = new Map();
  for (const backend of value.captured) {
    if (!Number.isSafeInteger(backend.pid) || backend.pid < 1 || !timestamp(backend.backendStart) ||
        backend.database !== value.authority.database || backend.role !== value.authority.role ||
        backend.applicationName !== value.authority.applicationName || keys.has(backendKey(backend)) || !same(backend, backendIdentity(backend))) throw new Error('owned_source_identity_invalid');
    keys.set(backendKey(backend), backend);
  }
  if (value.live.some(backend => !same(keys.get(backendKey(backend)), backend)) ||
      new Set(value.live.map(backend => backend.pid)).size !== value.live.length) throw new Error('uncaptured_source_identity');
  const live = new Set(value.live.map(backendKey)), checked = new Set();
  for (const check of value.capturedChecks) {
    const key = backendKey(check);
    if (!same(keys.get(key), backendIdentity(check)) || checked.has(key) || !['present', 'absent', 'pid_reused'].includes(check.status) ||
        (check.status === 'present') !== live.has(key) ||
        (check.status === 'pid_reused' && (!timestamp(check.observedBackendStart) || check.observedBackendStart <= check.backendStart))) throw new Error('owned_source_recheck_invalid');
    checked.add(key);
  }
  return value;
}
export async function sampleSource(configPath, outputPath, { launch = spawn, python = resolve('.venv/bin/python') } = {}) {
  const script = fileURLToPath(new URL('./source_monitor.py', import.meta.url));
  // Prevent ambient libpq service/hostaddr/password defaults from changing the
  // explicitly provisioned source authority. The monitor receives no secrets on argv.
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('PG')));
  await new Promise((accept, reject) => {
    const child = launch(python, [script, '--config', configPath, '--output', outputPath], { env, stdio: 'ignore' });
    const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
    child.once('error', () => { clearTimeout(timer); reject(new Error('source_monitor_unavailable')); });
    child.once('close', code => { clearTimeout(timer); code === 0 ? accept() : reject(new Error('source_monitor_unavailable')); });
  });
  const config = await readPrivateJSON(configPath);
  if (config.version !== 2 || !same(config.authority.endpoint, Object.fromEntries(['host', 'port', 'sslmode'].map(key => [key, config.connection[key]])))) throw new Error('source_endpoint_changed');
  return sourceEvidence(await readPrivateJSON(outputPath), config.runId, Date.now(), { runId: config.runId, ...config.authority });
}

export async function observeOwnedWork(receipt, workload, { origin, clientFactory = options => new HTTPClient(options), clock = () => performance.now() } = {}) {
  if (receipt?.version !== 1 || !/^load_[a-f0-9]{16}$/.test(receipt.tag || '') ||
      !Array.isArray(receipt.accounts) || !receipt.accounts.length || receipt.accounts.length > 1000 ||
      !Array.isArray(receipt.executions || []) || (receipt.executions || []).length > 10000 ||
      !Array.isArray(receipt.jobs || []) || (receipt.jobs || []).length > 1000 ||
      receipt.accounts.length + (receipt.executions || []).length + (receipt.jobs || []).length > 256) throw new Error('owned_work_receipt_invalid');
  const clients = new Map(), executions = [], jobs = [], seen = new Set();
  const started = clock();
  const read = async (client, path) => {
    if (clock() - started > 4000) throw new Error('owned_work_deadline');
    const result = await client.json('GET', path);
    if (clock() - started > 4000) throw new Error('owned_work_deadline');
    return result;
  };
  try {
    for (const account of receipt.accounts) {
      if (!account.username || !account.userId || !account.cookie || clients.has(account.username)) throw new Error('owned_account_invalid');
      const client = clientFactory({ origin, cookie: account.cookie, timeoutMs: 3000, maxBytes: 65536 });
      clients.set(account.username, client);
      const current = await read(client, '/api/v1/auth/me');
      if (current?.user?.id !== account.userId || current.user.username !== account.username) throw new Error('owned_account_changed');
    }
    for (const entry of receipt.executions || []) {
      if (!id(entry.id, 'cex') || !clients.has(entry.username) || seen.has(entry.id)) throw new Error('owned_execution_invalid');
      seen.add(entry.id);
      const value = await read(clients.get(entry.username), `/api/v1/common/query-executions/${entry.id}`);
      // 404, uncertain state, ended /activity, or a cleanup flag never prove release.
      if (value?.id !== entry.id || !executionStates.has(value.status)) throw new Error('owned_execution_unavailable');
      executions.push({ id: entry.id, status: value.status });
    }
    for (const entry of receipt.jobs || []) {
      if (!id(entry.id, 'bjob') || !id(entry.workspaceId, 'ws') || !clients.has(entry.username) || seen.has(entry.id)) throw new Error('owned_job_invalid');
      seen.add(entry.id);
      const value = await read(clients.get(entry.username), `/api/v1/schemii/workspaces/${entry.workspaceId}/console/bulk-jobs/${entry.id}`);
      if (value?.id !== entry.id || value.workspaceId !== entry.workspaceId || !jobStates.has(value.status)) throw new Error('owned_job_unavailable');
      jobs.push({ id: entry.id, status: value.status });
    }
    if (noJobLanes.has(workload) && jobs.length) throw new Error('no_job_lane_contradiction');
    if (!noJobLanes.has(workload) && workload !== 'owned-bulk-job') throw new Error('owned_job_policy_unknown');
    if (workload === 'owned-bulk-job' && !jobs.length) throw new Error('job_journal_required');
    return { version: 1, at: new Date().toISOString(), status: 'available', runId: receipt.tag,
      activeJobs: jobs.filter(value => ['queued', 'running', 'cancelling'].includes(value.status)).length,
      activeExecutions: executions.filter(value => ['reserved', 'running'].includes(value.status)).length,
      jobScope: noJobLanes.has(workload) ? 'immutable_adapter_creates_no_jobs' : 'receipt_owned_ids',
      executionScope: 'receipt_owned_ids_only_not_backend_release', executions, jobs };
  } finally { for (const client of clients.values()) client.close(); }
}

const utcSQL = expression => `pg_catalog.to_char(${expression} AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '+00:00'`;
export const recoverySQL = `SELECT 1 AS value, a.pid AS pid, ${utcSQL('a.backend_start')} AS backend_start, pg_catalog.current_database() AS database, current_user AS role, a.application_name AS application_name, pg_catalog.inet_server_addr()::text AS server_address, pg_catalog.inet_server_port() AS server_port, ${utcSQL('pg_catalog.pg_postmaster_start_time()')} AS postmaster_start, d.oid::bigint AS database_oid, pg_catalog.shobj_description(d.oid, 'pg_database') AS fixture_marker FROM pg_catalog.pg_stat_activity AS a CROSS JOIN pg_catalog.pg_database AS d WHERE a.pid = pg_catalog.pg_backend_pid() AND d.datname = pg_catalog.current_database()`;
export async function ownedRecovery({ receipt, config, origin, journal, monitor, onExecution = async () => {},
  clientFactory = options => new HTTPClient(options), sleep = delay, clock = () => performance.now() }) {
  const account = receipt.accounts.find(value => value.username === config.username);
  if (!account || config.runId !== receipt.tag || !id(config.workspaceId, 'ws') || !id(config.consoleId, 'con') ||
      !Number.isSafeInteger(config.workspaceRevision) || config.workspaceRevision < 1 ||
      !Number.isSafeInteger(config.settingsRevision) || config.settingsRevision < 1 || typeof monitor !== 'function') throw new Error('owned_recovery_authority_required');
  const client = clientFactory({ origin, cookie: account.cookie, timeoutMs: 3000, maxBytes: 65536 });
  const intent = { version: 1, at: new Date().toISOString(), runId: receipt.tag, username: account.username,
    workspaceId: config.workspaceId, consoleId: config.consoleId, status: 'create_pending' };
  let execution, result, closed = false;
  try {
    await writePrivateJSON(journal, intent, { exclusive: true });
    const identity = await client.json('GET', '/api/v1/auth/me');
    if (identity?.user?.id !== account.userId || identity.user.username !== account.username) throw new Error('recovery_authentication_failed');
    execution = await client.json('POST', `/api/v1/schemii/workspaces/${config.workspaceId}/console/executions`, {
      consoleId: config.consoleId, expectedWorkspaceRevision: config.workspaceRevision,
      expectedSettingsRevision: config.settingsRevision, mode: 'managed_read', statements: [recoverySQL],
    }, 201);
    if (!id(execution?.id, 'cex')) throw new Error('recovery_execution_invalid');
    await writePrivateJSON(journal, { ...intent, executionId: execution.id, status: 'acquired' });
    await onExecution(account.username, execution.id);
    const started = clock();
    while (['reserved', 'running'].includes(execution.status) && clock() - started < 10000) {
      await sleep(100);
      const value = await client.json('GET', `/api/v1/common/query-executions/${execution.id}`);
      if (value?.id !== execution.id) throw new Error('recovery_execution_changed');
      execution = value;
    }
    if (execution.status !== 'succeeded' || JSON.stringify(execution.completedStatementIndexes) !== '[0]' || execution.results?.length !== 1 ||
        !id(execution.results[0].id, 'res')) throw new Error('recovery_terminal_failed');
    result = execution.results[0].id;
    const page = await client.json('GET', `/api/v1/common/query-executions/${execution.id}/results/${result}`);
    const columns = ['value', 'pid', 'backend_start', 'database', 'role', 'application_name', 'server_address', 'server_port', 'postmaster_start', 'database_oid', 'fixture_marker'];
    if (page.executionId !== execution.id || page.resultId !== result || page.nextCursor !== null || page.truncated !== false ||
        JSON.stringify(page.columns?.map(value => value.name)) !== JSON.stringify(columns) || page.rows?.length !== 1 ||
        page.rows[0]?.length !== 11 || page.rows[0][0] !== 1) throw new Error('recovery_exact_result_failed');
    const [, pid, backendStart, database, role, applicationName, address, port, postmasterStart, databaseOid, fixtureMarker] = page.rows[0];
    const captured = sourceEvidence(await monitor(), receipt.tag);
    const fixtureId = typeof fixtureMarker === 'string' && /^schemii-load:[0-9a-f]{64}$/.test(fixtureMarker) ? fixtureMarker.slice('schemii-load:'.length) : null;
    if (!same(captured.serverIdentity, { address, port, postmasterStart, databaseOid, fixtureId })) throw new Error('recovery_source_server_mismatch');
    const backend = captured.live.find(value => value.pid === pid && value.backendStart === backendStart &&
      value.database === database && value.role === role && value.applicationName === applicationName);
    if (!backend) throw new Error('recovery_source_acquisition_unwitnessed');
    await writePrivateJSON(journal, { ...intent, executionId: execution.id, resultId: result, backend, authority: captured.authority, status: 'witnessed' });
    await client.json('DELETE', `/api/v1/common/query-executions/${execution.id}/results/${result}`, undefined, 204); closed = true;
    let released;
    do {
      released = sourceEvidence(await monitor(), receipt.tag, Date.now(), captured.authority);
      if (!released.live.length) break;
      await sleep(100);
    } while (clock() - started < 10000);
    if (!released.releaseVerified || !released.captured.some(value => same(value, backend))) throw new Error('recovery_source_not_released');
    await writePrivateJSON(journal, { ...intent, executionId: execution.id, resultId: result, backend, authority: captured.authority, status: 'verified', at: new Date().toISOString() });
    return { verified: true, authenticatedRead: true, exactSelectTerminal: true, capturedBackendReleased: true };
  } finally {
    try {
      if (execution?.id && !closed) {
        await client.json('DELETE', `/api/v1/common/query-executions/${execution.id}`);
        for (const value of execution.results || []) if (id(value.id, 'res'))
          await client.json('DELETE', `/api/v1/common/query-executions/${execution.id}/results/${value.id}`, undefined, 204);
      }
    } finally { client.close(); }
  }
}

export async function readOwnershipConfig(path) {
  const config = await readPrivateJSON(path);
  if (config.version !== 1 || !/^load_[a-f0-9]{16}$/.test(config.runId || '') ||
      !config.fixtureReceipt || !config.sourceConfig || !config.sourceOutput || !noJobLanes.has(config.workload) ||
      typeof path !== 'string' || resolve(path) !== path) throw new Error('invalid_ownership_config');
  for (const key of ['fixtureReceipt', 'sourceConfig', 'sourceOutput'])
    if (typeof config[key] !== 'string' || resolve(config[key]) !== config[key]) throw new Error('absolute_ownership_paths_required');
  if (new Set([path, config.fixtureReceipt, config.sourceConfig, config.sourceOutput]).size !== 4) throw new Error('ownership_paths_overlap');
  return config;
}

export async function observeOwnership(configPath, { origin, monitor = sampleSource, work = observeOwnedWork } = {}) {
  const config = await readOwnershipConfig(configPath), receipt = await readPrivateJSON(config.fixtureReceipt);
  if (receipt.tag !== config.runId) throw new Error('owned_run_changed');
  const source = sourceEvidence(await monitor(config.sourceConfig, config.sourceOutput), config.runId);
  const jobs = await work(receipt, config.workload, { origin });
  return { version: 1, at: new Date().toISOString(), runId: config.runId, status: 'available', source, work: jobs };
}

export function ownershipPaths(dir) {
  return { config: join(dir, 'ownership-private.json'), recoveryJournal: join(dir, 'recovery-private.json') };
}
