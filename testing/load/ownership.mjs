// Private run attribution. Never export accounts, SQL or opaque IDs as metrics.
import { spawn } from 'node:child_process';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { isDeepStrictEqual as same } from 'node:util';
import { HTTPClient } from './http.mjs';
import { readPrivateJSON, writePrivateJSON } from './runtime.mjs';
import { readSnapshot, writeSnapshot, journalRecords } from './journal.mjs';
import { Accounting } from './accounting.mjs';
import { fixtureExecutions } from './fixtures.mjs';

const noJobLanes = new Set(['cheap-read', 'catalog', 'compile', 'report-1', 'report-5', 'report-20', 'csv', 'slow-reader', 'disconnect', 'cancel',
  'sql-page', 'wide-rows', 'idle-expiry', 'shared-report', 'login-churn', 'metadata-write', 'mix']);
const executionStates = new Set(['reserved', 'running', 'succeeded', 'failed', 'cancelled', 'partial_committed']);
const jobStates = new Set(['queued', 'running', 'cancelling', 'completed', 'paused', 'failed']);
const id = (value, prefix) => typeof value === 'string' && new RegExp(`^${prefix}_[a-f0-9]{32}$`).test(value);
export function fresh(value, now = Date.now()) {
  return Number.isFinite(Date.parse(value?.at)) && now - Date.parse(value.at) <= 15000 && Date.parse(value.at) <= now + 5000;
}

export async function observeCohortWork(receipt, workload, { source, state = null, origin, now = Date.now(), clientFactory = options => new HTTPClient(options), fixtureObservation = null } = {}) {
  sourceEvidence(source, receipt.tag, now);
  if (!noJobLanes.has(workload) || !Array.isArray(receipt.accounts) || !receipt.accounts.length || receipt.accounts.length > 1000 || (receipt.jobs || []).length) throw new Error('cohort_job_policy_unknown');
  if (state && (state.runId !== receipt.tag || !same(state.authority, source.authority))) throw new Error('cohort_observation_authority_changed');
  state ||= { version: 1, runId: receipt.tag, authority: source.authority, recordCursor: 0, ownerCursor: 0, workCursor: 0, owners: {}, pending: {}, pendingIntents: {}, retired: 0, retiredLegacy: {}, serverManagedStreamsRetired: 0 };
  state.pendingIntents ||= {};
  state.fixtureOperations ||= {};
  state.serverManagedStreamsRetired ||= 0;
  const observedAccounts = [...receipt.accounts, ...(receipt.recoveryAccount ? [receipt.recoveryAccount] : [])];
  const accounts = new Map(observedAccounts.map(account => [account.username, account]));
  if (accounts.size !== observedAccounts.length || [...accounts.values()].some(account => !account.userId || !account.cookie)) throw new Error('cohort_owner_invalid');
  if (!state.recordCursor && !Object.keys(state.owners).length) for (const account of observedAccounts) {
    if (Number.isFinite(Date.parse(account.identityVerifiedAt)) && Date.parse(account.identityVerifiedAt) <= now + 5000)
      state.owners[account.username] = { userId: account.userId, at: account.identityVerifiedAt };
  }
  let backlog = false, ingested = 0;
  if (receipt.executionJournal) for await (const record of journalRecords(receipt.executionJournal, { from: state.recordCursor + 1 })) {
    if (++ingested > 4096) { backlog = true; break; }
    const event = record.value;
    if (event.type === 'intent' && ['execution', 'stream'].includes(event.kind)) {
      if (!accounts.has(event.username)) throw new Error('cohort_intent_owner_invalid');
      state.pendingIntents[record.sequence] = { ...event, status: 'pending' };
      if (event.fixtureObserved) {
        if (!/^[a-f0-9]{32}$/.test(event.operationNonce || '') || state.fixtureOperations[event.operationNonce]) throw new Error('cohort_fixture_intent_reused');
        state.fixtureOperations[event.operationNonce] = { username: event.username, resource: event.dashboardId || event.modelId };
        if (Object.keys(state.fixtureOperations).length > 4096) throw new Error('cohort_fixture_intent_budget');
      }
    } else if (event.type === 'intent-update' && state.pendingIntents[event.intentSequence]) {
      const intent = state.pendingIntents[event.intentSequence];
      Object.assign(intent, event, { observedAt: new Date(now).toISOString() });
      if (intent.executionId) {
        if (!id(intent.executionId, 'cex') || state.pending[intent.executionId]?.username !== intent.username) throw new Error('cohort_intent_execution_unbound');
        delete state.pendingIntents[event.intentSequence];
      }
    }
    if (event.type === 'execution') {
      if (!id(event.id, 'cex') || !accounts.has(event.username)) throw new Error('cohort_execution_owner_invalid');
      const current = state.pending[event.id];
      if (!current && event.action === 'created') state.pending[event.id] = { id: event.id, username: event.username, kind: event.kind || 'query',
        dashboardId: event.dashboardId, dashboardRevision: event.dashboardRevision, modelId: event.modelId, modelRevision: event.modelRevision, operationNonce: event.operationNonce, status: 'unknown' };
      else if (current && current.username !== event.username) throw new Error('cohort_execution_owner_changed');
      if (current && event.action === 'terminal') {
        if (!Number.isFinite(Date.parse(event.terminalAt)) || event.terminalAt > new Date(now + 5000).toISOString()) throw new Error('cohort_terminal_invalid');
        current.streamTerminalAt = event.terminalAt; current.streamTerminal = event.terminal;
        if (event.operationNonce) Object.assign(current, { fixtureStatus: event.fixtureStatus, fixtureObservedAt: event.fixtureObservedAt,
          fixtureRequest: event.fixtureRequest, observerProcess: event.observerProcess });
      }
    }
    state.recordCursor = record.sequence;
  }
  // A reservation may finish after its HTTP adapter aborts. Keep that exact ID
  // attributable via the still-unacknowledged private operation; cleanup also
  // consumes this fixed artifact. Never infer an empty execution from a miss.
  if (fixtureObservation && receipt.fixtureObserver) for (const [key, intent] of Object.entries(state.pendingIntents)) {
    if (!intent.fixtureObserved || !intent.operationNonce) continue;
    const entry = fixtureObservation.operations.find(value => value.operation === intent.operationNonce), captured = entry?.receipts?.[0];
    if (!captured) continue;
    const account = accounts.get(intent.username);
    if (entry.actor !== account.userId || captured.owner !== account.userId || entry.resource !== (intent.dashboardId || intent.modelId) || !id(captured.id, 'cex')) throw new Error('cohort_fixture_capture_changed');
    state.pending[captured.id] ||= { id: captured.id, username: intent.username, kind: 'report-stream', operationNonce: intent.operationNonce,
      dashboardId: intent.dashboardId, dashboardRevision: intent.dashboardRevision, modelId: intent.modelId, modelRevision: intent.modelRevision, status: 'unknown' };
    Object.assign(state.pending[captured.id], { observerProcess: fixtureObservation.process, fixtureStatus: entry.unknown ? 'unknown' : captured.status,
      fixtureObservedAt: captured.observedAt, fixtureRequest: entry.request, streamTerminalAt: fixtureObservation.at });
    delete state.pendingIntents[key];
  }
  if (fixtureObservation && receipt.fixtureObserver) for (const pending of Object.values(state.pending)) {
    if (!pending.operationNonce) continue;
    const entry = fixtureObservation.operations.find(value => value.operation === pending.operationNonce), captured = entry?.receipts?.[0];
    if (!entry || !captured) continue; // An acknowledged ID keeps its durable exact status.
    if (entry.actor !== accounts.get(pending.username).userId || captured.owner !== entry.actor || captured.id !== pending.id || entry.resource !== (pending.dashboardId || pending.modelId)) throw new Error('cohort_fixture_capture_changed');
    Object.assign(pending, { observerProcess: fixtureObservation.process, fixtureStatus: entry.unknown ? 'unknown' : captured.status,
      fixtureObservedAt: captured.observedAt, fixtureRequest: entry.request, streamTerminalAt: fixtureObservation.at });
  }
  for (const [key, intent] of Object.entries(state.pendingIntents)) {
    if (!receipt.fixtureObserver && intent.kind === 'stream' && intent.status === 'closed' && intent.idExposure === 'server_managed_not_exposed' && source.releaseVerified && Date.parse(source.at) >= Date.parse(intent.observedAt)) {
      delete state.pendingIntents[key]; state.serverManagedStreamsRetired++;
    }
  }
  // Legacy captured IDs remain auditable; no closed flag grants retirement.
  for (const entry of receipt.executions || []) {
    if (!id(entry.id, 'cex') || !accounts.has(entry.username)) throw new Error('cohort_execution_owner_invalid');
    if (!state.retiredLegacy[entry.id]) state.pending[entry.id] ||= { ...entry, terminalAt: undefined, observedAt: undefined, legacy: true, status: 'unknown' };
  }
  if (Object.keys(state.pending).length + Object.keys(state.pendingIntents).length > 100000) throw new Error('cohort_pending_observation_budget');
  const fixtureGap = !!receipt.fixtureObserver && (!fixtureObservation || fixtureObservation.losses > 0 ||
    !/^[a-f0-9]{32}$/.test(fixtureObservation.process?.incarnation || '') ||
    fixtureObservation.operations.some(entry => {
      const known = state.fixtureOperations[entry.operation];
      return !known || accounts.get(known.username)?.userId !== entry.actor || known.resource !== entry.resource || entry.unknown || !entry.receipts.length;
    }));
  if (receipt.fixtureObserver && fixtureObservation) {
    if (state.fixtureProcess && !same(state.fixtureProcess, fixtureObservation.process)) throw new Error('cohort_fixture_process_changed');
    state.fixtureProcess ||= fixtureObservation.process;
  }
  const freshnessMs = Math.max(120000, Math.ceil(accounts.size / 16) * 5000 + 15000), cutoff = now - freshnessMs;
  const retiredProofs = [];
  const retire = () => {
    for (const entry of Object.values(state.pending)) {
      if (!fixtureGap && ['succeeded', 'failed', 'cancelled', 'partial_committed'].includes(entry.status) && entry.terminalAt && Date.parse(entry.observedAt) >= cutoff && source.releaseVerified && Date.parse(source.at) >= Date.parse(entry.terminalAt)) {
        retiredProofs.push({ id: entry.id, status: entry.status, terminalAt: entry.terminalAt, observedAt: entry.observedAt });
        delete state.pending[entry.id]; if (entry.legacy) state.retiredLegacy[entry.id] = true; state.retired++;
      }
    }
  };
  retire(); // A witnessed released terminal needs no unsupported expired-receipt GET.
  const pending = Object.values(state.pending), selected = [];
  for (let index = 0; index < Math.min(80, pending.length); index++) selected.push(pending[(state.workCursor + index) % pending.length]);
  state.workCursor = pending.length ? (state.workCursor + selected.length) % pending.length : 0;
  const owners = new Set(selected.map(value => value.username));
  const names = [...accounts.keys()];
  for (let index = 0; index < Math.min(16, names.length); index++) owners.add(names[(state.ownerCursor + index) % names.length]);
  state.ownerCursor = (state.ownerCursor + Math.min(16, names.length)) % names.length;
  const clients = new Map();
  const started = performance.now();
  const get = async (client, path) => {
    if (performance.now() - started > 4000) throw new Error('cohort_observation_deadline');
    const value = await client.json('GET', path);
    if (performance.now() - started > 4000) throw new Error('cohort_observation_deadline');
    return value;
  };
  try {
    for (const username of owners) {
      const account = accounts.get(username), client = clientFactory({ origin, cookie: account.cookie, timeoutMs: 3000, maxBytes: 65536 }); clients.set(username, client);
      const current = await get(client, '/api/v1/auth/me');
      if (current.user?.id !== account.userId || current.user.username !== username) throw new Error('cohort_owner_changed');
      state.owners[username] = { userId: account.userId, at: new Date(now).toISOString() };
    }
    for (const entry of selected) {
      if (['report-stream', 'shared-report'].includes(entry.kind)) {
        // Raw receipt GET is forbidden for shared streams. The authenticated
        // exact terminal is a supported boundary; release still needs witness.
        const account = accounts.get(entry.username), variants = [account, ...(account.variants || [])];
        if (entry.dashboardId) {
          const saved = [account.sharedDashboard, ...variants.flatMap(value => Object.values(value.dashboards || {}))].find(value => value?.id === entry.dashboardId);
          if (!saved || saved.revision !== entry.dashboardRevision) throw new Error('cohort_report_binding_changed');
          const current = await get(clients.get(entry.username), `/api/v1/schemer/dashboards/${entry.dashboardId}`);
          if (current.id !== saved.id || current.revision !== saved.revision) throw new Error('cohort_report_unavailable');
        } else {
          const saved = variants.map(value => value.model).find(value => value?.id === entry.modelId);
          if (!saved || saved.revision !== entry.modelRevision) throw new Error('cohort_report_binding_changed');
          const current = await get(clients.get(entry.username), `/api/v1/schemoo/models/${entry.modelId}`);
          if (current.id !== saved.id || current.revision !== saved.revision) throw new Error('cohort_report_unavailable');
        }
        if (receipt.fixtureObserver) {
          if (!entry.operationNonce || !entry.observerProcess?.incarnation || !executionStates.has(entry.fixtureStatus) ||
              !Number.isFinite(Date.parse(entry.fixtureObservedAt)) || Date.parse(entry.fixtureObservedAt) > now + 5000 || !['complete', 'failed'].includes(entry.fixtureRequest)) entry.status = 'unknown';
          else { entry.status = entry.fixtureStatus; entry.terminalAt = entry.streamTerminalAt; }
        } else if (entry.streamTerminal === 'drained' || ['succeeded', 'failed', 'cancelled', 'partial_committed'].includes(entry.streamTerminal)) {
          entry.status = entry.streamTerminal === 'drained' ? 'succeeded' : entry.streamTerminal; entry.terminalAt = entry.streamTerminalAt;
        }
        else entry.status = 'unknown';
      } else {
        const current = await get(clients.get(entry.username), `/api/v1/common/query-executions/${entry.id}`);
        if (current.id !== entry.id || !executionStates.has(current.status)) throw new Error('cohort_execution_unavailable');
        entry.status = current.status;
        if (!['reserved', 'running'].includes(current.status)) entry.terminalAt ||= new Date(now).toISOString();
      }
      entry.observedAt = new Date(now).toISOString();
    }
  } finally { for (const client of clients.values()) client.close(); }
  retire();
  const outstanding = Object.values(state.pending);
  const unknown = outstanding.filter(entry => !entry.observedAt || Date.parse(entry.observedAt) < cutoff || entry.status === 'unknown').length + Object.keys(state.pendingIntents).length + Number(fixtureGap);
  const uncoveredOwners = names.filter(name => state.owners[name]?.userId !== accounts.get(name).userId || Date.parse(state.owners[name]?.at) < cutoff || !state.owners[name]).length;
  return { state, value: { version: 1, at: new Date(now).toISOString(), runId: receipt.tag, status: 'available', activeJobs: fixtureGap ? null : 0,
    activeExecutions: unknown || uncoveredOwners || backlog ? null : outstanding.filter(entry => ['reserved', 'running'].includes(entry.status)).length,
    unreleasedExecutions: outstanding.length, unknownExecutions: unknown + Number(backlog), unboundIntentCount: Object.keys(state.pendingIntents).length,
    serverManagedStreamsRetired: state.serverManagedStreamsRetired,
    jobScope: 'immutable_adapter_creates_no_jobs', executionScope: 'bounded_fair_owner_reads_and_post_terminal_cohort_witness', retiredProofs,
    coverage: { pending: outstanding.length, unknown, backlog, uncoveredOwners, complete: !unknown && !backlog && !uncoveredOwners && !outstanding.length,
      retired: state.retired, inspectedThisBatch: selected.length,
      ownerReadsThisBatch: owners.size, maximumBatchReads: 176, freshnessMs }, executions: outstanding, jobs: [] } };
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
  const account = [...receipt.accounts, ...(receipt.recoveryAccount ? [receipt.recoveryAccount] : [])].find(value => value.username === config.username);
  if (!account || config.runId !== receipt.tag || !id(config.workspaceId, 'ws') || !id(config.consoleId, 'con') ||
      !Number.isSafeInteger(config.workspaceRevision) || config.workspaceRevision < 1 ||
      !Number.isSafeInteger(config.settingsRevision) || config.settingsRevision < 1 || typeof monitor !== 'function') throw new Error('owned_recovery_authority_required');
  const accounting = new Accounting();
  const client = clientFactory({ origin, cookie: account.cookie, accounting, timeoutMs: 3000, maxBytes: 65536 });
  const intent = { version: 1, at: new Date().toISOString(), runId: receipt.tag, username: account.username,
    workspaceId: config.workspaceId, consoleId: config.consoleId, status: 'create_pending' };
  let execution, result, closed = false;
  try {
    await writePrivateJSON(journal, intent, { exclusive: true });
    const identity = await client.json('GET', '/api/v1/auth/me', undefined, 200, { operation: 'auth_read' });
    if (identity?.user?.id !== account.userId || identity.user.username !== account.username) throw new Error('recovery_authentication_failed');
    execution = await client.json('POST', `/api/v1/schemii/workspaces/${config.workspaceId}/console/executions`, {
      consoleId: config.consoleId, expectedWorkspaceRevision: config.workspaceRevision,
      expectedSettingsRevision: config.settingsRevision, mode: 'managed_read', statements: [recoverySQL],
    }, 201, { operation: 'execution_create' });
    if (!id(execution?.id, 'cex')) throw new Error('recovery_execution_invalid');
    await writePrivateJSON(journal, { ...intent, executionId: execution.id, status: 'acquired' });
    await onExecution(account.username, execution.id);
    const started = clock();
    while (['reserved', 'running'].includes(execution.status) && clock() - started < 10000) {
      await sleep(100);
      const value = await client.json('GET', `/api/v1/common/query-executions/${execution.id}`, undefined, 200, { operation: 'execution_read' });
      if (value?.id !== execution.id) throw new Error('recovery_execution_changed');
      execution = value;
    }
    if (execution.status !== 'succeeded' || JSON.stringify(execution.completedStatementIndexes) !== '[0]' || execution.results?.length !== 1 ||
        !id(execution.results[0].id, 'res')) throw new Error('recovery_terminal_failed');
    result = execution.results[0].id;
    const page = await client.json('GET', `/api/v1/common/query-executions/${execution.id}/results/${result}`, undefined, 200, { operation: 'result_page' });
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
    await client.json('DELETE', `/api/v1/common/query-executions/${execution.id}/results/${result}`, undefined, 204, { operation: 'result_close' }); closed = true;
    let released;
    do {
      released = sourceEvidence(await monitor(), receipt.tag, Date.now(), captured.authority);
      if (!released.live.length) break;
      await sleep(100);
    } while (clock() - started < 10000);
    if (!released.releaseVerified || !released.captured.some(value => same(value, backend))) throw new Error('recovery_source_not_released');
    await writePrivateJSON(journal, { ...intent, executionId: execution.id, resultId: result, backend, authority: captured.authority, status: 'verified', at: new Date().toISOString() });
    const measured = accounting.snapshot();
    return { verified: true, authenticatedRead: true, exactSelectTerminal: true, capturedBackendReleased: true,
      instrumentation: { scope: 'recovery_probe_outside_workload_population', http: measured.http, operations: measured.operations } };
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
  const config = await readOwnershipConfig(configPath), receipt = await readSnapshot(config.fixtureReceipt);
  if (receipt.tag !== config.runId) throw new Error('owned_run_changed');
  const source = sourceEvidence(await monitor(config.sourceConfig, config.sourceOutput), config.runId);
  let jobs;
  if (receipt.kind === 'disposable') {
    const statePath = `${configPath}.work-state.json`; let previous = null;
    try { previous = await readSnapshot(statePath); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    const fixtureObservation = receipt.fixtureObserver ? await fixtureExecutions(receipt.fixtureObserver) : null;
    const batch = await observeCohortWork(receipt, config.workload, { source, state: previous, origin, fixtureObservation });
    await writeSnapshot(statePath, batch.state); jobs = batch.value;
  } else jobs = await work(receipt, config.workload, { origin });
  return { version: 1, at: new Date().toISOString(), runId: config.runId, status: 'available', source, work: jobs };
}

export function ownershipPaths(dir) {
  return { config: join(dir, 'ownership-private.json'), recoveryJournal: join(dir, 'recovery-private.json') };
}
