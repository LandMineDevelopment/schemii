// Private run attribution. Never export accounts, SQL or opaque IDs as metrics.
import { spawn } from 'node:child_process';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { HTTPClient } from './http.mjs';
import { readPrivateJSON, writePrivateJSON } from './runtime.mjs';

const noJobLanes = new Set(['cheap-read', 'catalog', 'compile', 'report-1', 'report-5', 'report-20', 'csv', 'slow-reader', 'disconnect', 'cancel']);
const executionStates = new Set(['reserved', 'running', 'succeeded', 'failed', 'cancelled', 'partial_committed']);
const jobStates = new Set(['queued', 'running', 'cancelling', 'completed', 'paused', 'failed']);
const id = (value, prefix) => typeof value === 'string' && new RegExp(`^${prefix}_[a-f0-9]{32}$`).test(value);
export function fresh(value, now = Date.now()) {
  return Number.isFinite(Date.parse(value?.at)) && now - Date.parse(value.at) <= 15000 && Date.parse(value.at) <= now + 5000;
}
export function sourceEvidence(value, runId, now = Date.now()) {
  if (value?.version !== 1 || value.status !== 'available' || !fresh(value, now) || !/^load_[a-f0-9]{16}$/.test(runId || '') || value.authority?.runId !== runId ||
      value.authority.kind !== 'exclusive_source_role' || value.authority.exclusiveToRun !== true ||
      typeof value.authority.database !== 'string' || !value.authority.database || typeof value.authority.role !== 'string' || !value.authority.role ||
      value.authority.applicationName !== 'schemii' || typeof value.authority.provisioningReceipt !== 'string' || !value.authority.provisioningReceipt.startsWith('/') ||
      value.method !== 'independent_pg_stat_activity_exact_role_database_application_pid_start' ||
      !Array.isArray(value.captured) || !Array.isArray(value.live) || value.captured.length > 10000 ||
      value.ownedBackends !== value.live.length) throw new Error('owned_source_observation_unavailable');
  const keys = new Set();
  for (const backend of value.captured) {
    if (!Number.isSafeInteger(backend.pid) || backend.pid < 1 || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$/.test(backend.backendStart) || !Number.isFinite(Date.parse(backend.backendStart)) ||
        backend.database !== value.authority.database || backend.role !== value.authority.role ||
        backend.applicationName !== value.authority.applicationName || keys.has(backend.pid)) throw new Error('owned_source_identity_invalid');
    keys.add(backend.pid);
  }
  if (value.live.some(backend => !value.captured.some(captured => JSON.stringify(captured) === JSON.stringify(backend))) ||
      new Set(value.live.map(backend => backend.pid)).size !== value.live.length) throw new Error('uncaptured_source_identity');
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
  return sourceEvidence(await readPrivateJSON(outputPath), config.runId);
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

export const recoverySQL = "SELECT 1 AS value, pg_backend_pid() AS pid, to_char(backend_start AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US') || '+00:00' AS backend_start, current_database() AS database, current_user AS role, current_setting('application_name') AS application_name FROM pg_stat_activity WHERE pid = pg_backend_pid()";
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
    const columns = ['value', 'pid', 'backend_start', 'database', 'role', 'application_name'];
    if (page.executionId !== execution.id || page.resultId !== result || page.nextCursor !== null || page.truncated !== false ||
        JSON.stringify(page.columns?.map(value => value.name)) !== JSON.stringify(columns) || page.rows?.length !== 1 ||
        page.rows[0]?.length !== 6 || page.rows[0][0] !== 1) throw new Error('recovery_exact_result_failed');
    const [, pid, backendStart, database, role, applicationName] = page.rows[0];
    const captured = sourceEvidence(await monitor(), receipt.tag);
    const backend = captured.live.find(value => value.pid === pid && value.backendStart === backendStart &&
      value.database === database && value.role === role && value.applicationName === applicationName);
    if (!backend) throw new Error('recovery_source_acquisition_unwitnessed');
    await writePrivateJSON(journal, { ...intent, executionId: execution.id, resultId: result, backend, status: 'witnessed' });
    await client.json('DELETE', `/api/v1/common/query-executions/${execution.id}/results/${result}`, undefined, 204); closed = true;
    let released;
    do {
      released = sourceEvidence(await monitor(), receipt.tag);
      if (!released.live.length) break;
      await sleep(100);
    } while (clock() - started < 10000);
    if (released.live.length || !released.captured.some(value => JSON.stringify(value) === JSON.stringify(backend))) throw new Error('recovery_source_not_released');
    await writePrivateJSON(journal, { ...intent, executionId: execution.id, resultId: result, backend, status: 'verified', at: new Date().toISOString() });
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
