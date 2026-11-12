import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { sourceEvidence, observeOwnedWork, ownedRecovery, recoverySQL, readOwnershipConfig, sampleSource } from './ownership.mjs';
import { writePrivateJSON, readPrivateJSON } from './runtime.mjs';
import { verifyRecovery } from './accounting.mjs';
import { observation } from './engine.mjs';

const runId = 'load_0123456789abcdef', executionId = `cex_${'a'.repeat(32)}`, resultId = `res_${'b'.repeat(32)}`;
const workspaceId = `ws_${'c'.repeat(32)}`, consoleId = `con_${'d'.repeat(32)}`;
const account = { username: 'private-account', userId: 'private-user', cookie: 'private-cookie' };
const receipt = () => ({ version: 1, tag: runId, accounts: [{ ...account }], executions: [], jobs: [] });
const backend = { pid: 101, backendStart: '2026-11-11T10:00:00.000000+00:00', database: 'owned-db', role: 'owned-role', applicationName: 'schemii' };
const source = (live = [backend]) => ({ version: 1, at: new Date().toISOString(), status: 'available',
  method: 'independent_pg_stat_activity_exact_role_database_application_pid_start',
  authority: { kind: 'exclusive_source_role', exclusiveToRun: true, runId, database: backend.database, role: backend.role, applicationName: 'schemii', provisioningReceipt: '/private/provisioned.json' },
  captured: [backend], live, ownedBackends: live.length });
async function directory(t) {
  const dir = await mkdtemp(join(tmpdir(), 'schemii-owned-observation-')); t.after(() => rm(dir, { recursive: true, force: true })); return dir;
}
test('source identities are fresh, exact, captured and bounded; no aggregate/activity substitution', () => {
  assert.equal(sourceEvidence(source(), runId).ownedBackends, 1);
  for (const value of [{ ...source(), at: '2000-01-01' }, { ...source(), status: 'unavailable' },
    { ...source(), captured: [] }, { ...source(), ownedBackends: 0 }, { ...source(), live: [backend, backend] },
    { ...source(), live: [{ ...backend, backendStart: '2026-11-11T11:00:00+00:00' }] },
    { ...source(), captured: [{ ...backend, role: 'foreign' }] }]) assert.throws(() => sourceEvidence(value, runId));
  assert.throws(() => sourceEvidence(source(), 'foreign-run'));
  assert.throws(() => sourceEvidence({ ...source(), monitoringAvailable: false, live: undefined }, runId));
});

function workClient(overrides = {}) {
  const calls = [], closed = [];
  return { calls, closed, factory: () => ({
    json: async (method, path) => {
      calls.push({ method, path });
      if (path.endsWith('/auth/me')) return overrides.identity || { user: { id: account.userId, username: account.username } };
      if (path.includes('bulk-jobs')) return overrides.job;
      if (overrides.error) throw new Error('unexpected_http_404');
      return overrides.execution || { id: executionId, status: 'succeeded' };
    }, close: () => closed.push(true),
  }) };
}
test('immutable no-job lane has a meaningful authenticated zero; owned executions are separate', async () => {
  const f = workClient(), r = receipt(); r.executions.push({ username: account.username, id: executionId });
  const value = await observeOwnedWork(r, 'report-1', { origin: 'https://localhost:8001', clientFactory: f.factory });
  assert.equal(value.activeJobs, 0); assert.equal(value.activeExecutions, 0);
  assert.equal(value.jobScope, 'immutable_adapter_creates_no_jobs'); assert.equal(value.executions[0].id, executionId);
  assert.equal(f.closed.length, 1);
  const baseline = await observeOwnedWork(receipt(), 'cheap-read', { clientFactory: workClient().factory });
  assert.equal(baseline.activeJobs, 0); assert.equal(baseline.executions.length, 0);
});
test('missing, foreign, uncertain, expired and duplicate execution records never prove release', async () => {
  const r = receipt(); r.executions.push({ username: account.username, id: executionId, closed: true });
  for (const overrides of [{ error: true }, { execution: { id: executionId, status: 'uncertain' } },
    { execution: { id: 'foreign', status: 'succeeded' } }, { identity: { user: { id: 'foreign' } } }]) {
    const f = workClient(overrides);
    await assert.rejects(observeOwnedWork(r, 'report-1', { clientFactory: f.factory })); assert.equal(f.closed.length, 1);
  }
  const foreign = receipt(); foreign.executions.push({ username: 'other-owner', id: executionId });
  const f = workClient(); await assert.rejects(observeOwnedWork(foreign, 'report-1', { clientFactory: f.factory }));
  assert.equal(f.calls.length, 1); // only authenticate the explicitly recorded account
  r.executions.push(r.executions[0]); await assert.rejects(observeOwnedWork(r, 'report-1', { clientFactory: workClient().factory }));
});
test('work observation enforces aggregate call/deadline limits without manufacturing zero', async () => {
  const r = receipt(); r.executions = Array.from({ length: 256 }, () => ({ username: account.username, id: executionId }));
  const f = workClient(); await assert.rejects(observeOwnedWork(r, 'report-1', { clientFactory: f.factory }), /receipt_invalid/);
  assert.equal(f.calls.length, 0);
  let time = 0;
  await assert.rejects(observeOwnedWork(receipt(), 'report-1', { clientFactory: workClient().factory, clock: () => { time += 3000; return time; } }), /deadline/);
});
test('job observation uses exact receipt owner/workspace/id, and rejects unknown/uncertain policies', async () => {
  const r = receipt(), job = { id: `bjob_${'e'.repeat(32)}`, workspaceId, username: account.username };
  r.jobs.push(job);
  for (const status of ['queued', 'running', 'cancelling', 'completed', 'paused', 'failed']) {
    const f = workClient({ job: { ...job, status } });
    const value = await observeOwnedWork(r, 'owned-bulk-job', { clientFactory: f.factory });
    assert.equal(value.activeJobs, ['queued', 'running', 'cancelling'].includes(status) ? 1 : 0);
    assert(f.calls[1].path.endsWith(`/workspaces/${workspaceId}/console/bulk-jobs/${job.id}`));
  }
  for (const value of [{ ...job, status: 'reconciliation_required' }, { ...job, workspaceId: 'foreign', status: 'completed' }])
    await assert.rejects(observeOwnedWork(r, 'owned-bulk-job', { clientFactory: workClient({ job: value }).factory }));
  await assert.rejects(observeOwnedWork(receipt(), 'unimplemented-lane', { clientFactory: workClient().factory }));
  await assert.rejects(observeOwnedWork(receipt(), 'owned-bulk-job', { clientFactory: workClient().factory }));
  await assert.rejects(observeOwnedWork(r, 'report-1', { clientFactory: workClient({ job: { ...job, status: 'completed' } }).factory }));
});

function recoveryFixture({ wrongRow = false, wrongStart = false, leak = false, missingCapture = false, terminal = 'succeeded' } = {}) {
  const calls = []; let closed = false, time = 0;
  const execution = { id: executionId, status: terminal, completedStatementIndexes: [0], results: [{ id: resultId }] };
  return { calls, closed: () => closed, clock: () => time, sleep: async ms => { time += ms; },
    monitor: async () => source(missingCapture ? [] : closed && !leak ? [] : [backend]),
    factory: () => ({ close() {}, json: async (method, path, body, status) => {
      calls.push({ method, path, body, status });
      if (path.endsWith('/auth/me')) return { user: { id: account.userId, username: account.username } };
      if (method === 'POST') { assert.equal(status, 201); return execution; }
      if (method === 'DELETE') { closed = true; return {}; }
      if (path.endsWith(resultId)) return { executionId, resultId, nextCursor: null, truncated: false,
        columns: ['value', 'pid', 'backend_start', 'database', 'role', 'application_name'].map(name => ({ name })),
        rows: [[wrongRow ? 2 : 1, backend.pid, wrongStart ? backend.backendStart.replace('.000000', '.000001') : backend.backendStart, backend.database, backend.role, backend.applicationName]] };
      return execution;
    } }) };
}
const recoveryConfig = { runId, username: account.username, workspaceId, consoleId, workspaceRevision: 1, settingsRevision: 1 };
test('fresh owner read + exact SELECT terminal captures actual backend then proves source release', async t => {
  const dir = await directory(t), f = recoveryFixture(), recorded = [], journal = join(dir, 'recovery-private.json');
  const value = await ownedRecovery({ receipt: receipt(), config: recoveryConfig, journal, monitor: f.monitor,
    clientFactory: f.factory, onExecution: async (...args) => recorded.push(args) });
  assert.equal(value.verified, true); assert.equal(value.capturedBackendReleased, true);
  assert.deepEqual(recorded, [[account.username, executionId]]);
  assert.equal(f.calls[1].body.statements[0], recoverySQL);
  assert.equal((await readPrivateJSON(journal)).status, 'verified');
  await assert.rejects(ownedRecovery({ receipt: receipt(), config: recoveryConfig, journal, monitor: f.monitor, clientFactory: f.factory }), /EEXIST/);
});
test('wrong rows, wrong terminal, unobserved acquisition and stranded backend fail active recovery', async t => {
  const dir = await directory(t);
  for (const [index, options] of [{ wrongRow: true }, { wrongStart: true }, { terminal: 'failed' }, { missingCapture: true }, { leak: true }].entries()) {
    const f = recoveryFixture(options), journal = join(dir, `failed-${index}.json`);
    await assert.rejects(ownedRecovery({ receipt: receipt(), config: recoveryConfig, journal, monitor: f.monitor,
      clientFactory: f.factory, clock: f.clock, sleep: f.sleep }));
    assert.notEqual((await readPrivateJSON(journal)).status, 'verified'); assert(f.closed());
  }
});
test('passive idle and aggregate handles cannot pass the engine recovery gate', () => {
  const zero = Object.fromEntries(['retainedSessions', 'openCursors', 'ownedBackends', 'activeJobs', 'ordinaryPermits', 'retainedPermits'].map(key => [key, 0]));
  assert.equal(verifyRecovery(zero, zero, { requireOwnedProbe: true }).verified, false);
  const probe = { verified: true, authenticatedRead: true, exactSelectTerminal: true, capturedBackendReleased: true };
  assert.equal(verifyRecovery(zero, zero, { requireOwnedProbe: true, probe, ownedWork: { status: 'available', activeJobs: 0, activeExecutions: 0 } }).verified, true);
  assert.equal(verifyRecovery(zero, zero, { requireOwnedProbe: true, probe, ownedWork: { status: 'available', activeJobs: 0, activeExecutions: 1 } }).verified, false);
});
test('engine rejects stale attribution and gauges with no private ownership qualification', async t => {
  const dir = await directory(t), file = join(dir, 'observer.json');
  const base = { at: new Date().toISOString(), status: 'available', appRssBytes: 1, appMemoryLimitBytes: 100, ownedBackends: 0, activeJobs: 0 };
  await writePrivateJSON(file, base); await assert.rejects(observation(file), /unattributed/);
  await writePrivateJSON(file, { ...base, ownership: { runId, source: source([]), work: { runId, status: 'available', at: '2000-01-01' } } });
  await assert.rejects(observation(file), /owned_work/);
});
test('ownership configuration is private, disjoint and explicit', async t => {
  const dir = await directory(t), file = join(dir, 'ownership-private.json');
  const config = { version: 1, runId, workload: 'report-1', fixtureReceipt: join(dir, 'fixtures-private.json'),
    sourceConfig: join(dir, 'source-config-private.json'), sourceOutput: join(dir, 'source-private.json') };
  await writePrivateJSON(file, config); assert.equal((await readOwnershipConfig(file)).runId, runId);
  await writePrivateJSON(file, { ...config, sourceOutput: config.fixtureReceipt }); await assert.rejects(readOwnershipConfig(file), /overlap/);
});
test('monitor unavailability never supplies a successful or stale source snapshot', async () => {
  const { EventEmitter } = await import('node:events');
  await assert.rejects(sampleSource('/unused/private/config', '/unused/private/output', { launch: () => {
    const child = new EventEmitter(); child.kill = () => {}; queueMicrotask(() => child.emit('close', 1)); return child;
  } }), /source_monitor_unavailable/);
});

test('opt-in exact HTTPS SELECT/release oracle', { skip: process.env.SCHEMII_LOAD_OWNERSHIP_LIVE !== '1' }, async () => {
  // Coordinator supplies an exclusively owned disposable role/workspace/Console,
  // live observer and prepared fixture receipt. No provisioner or launcher here.
  const config = await readOwnershipConfig(process.env.SCHEMII_LOAD_OWNERSHIP_CONFIG);
  const r = await readPrivateJSON(config.fixtureReceipt);
  const value = await ownedRecovery({ receipt: r, config: { ...config.recovery, runId: config.runId }, origin: 'https://localhost:8001',
    journal: join(process.env.SCHEMII_LOAD_OWNERSHIP_JOURNAL_DIR, 'recovery-private.json'),
    monitor: () => sampleSource(config.sourceConfig, config.sourceOutput),
    onExecution: async (username, identifier) => {
      r.executions ||= []; r.executions.push({ username, id: identifier }); await writePrivateJSON(config.fixtureReceipt, r);
    } });
  assert(value.verified);
});
