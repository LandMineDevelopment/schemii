import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { sourceEvidence, observeOwnedWork, observeCohortWork, ownedRecovery, recoverySQL, readOwnershipConfig, sampleSource } from './ownership.mjs';
import { writePrivateJSON, readPrivateJSON } from './runtime.mjs';
import { verifyRecovery } from './accounting.mjs';
import { observation, executePlan } from './engine.mjs';
import { plan } from './plan.mjs';
import { openJournal } from './journal.mjs';
import { combineObservation } from './observer.mjs';

const runId = 'load_0123456789abcdef', executionId = `cex_${'a'.repeat(32)}`, resultId = `res_${'b'.repeat(32)}`;
const workspaceId = `ws_${'c'.repeat(32)}`, consoleId = `con_${'d'.repeat(32)}`;
const account = { username: 'private-account', userId: 'private-user', cookie: 'private-cookie' };
const receipt = () => ({ version: 1, tag: runId, accounts: [{ ...account }], executions: [], jobs: [] });
const backend = { pid: 101, backendStart: '2026-11-11T10:00:00.000000+00:00', database: 'owned-db', role: 'owned-role', applicationName: 'schemii' };
const server = { address: '127.0.0.1', port: 5432, postmasterStart: '2026-11-11T09:00:00.000000+00:00', databaseOid: 100, fixtureId: 'a'.repeat(64) };
const source = (live = [backend]) => ({ version: 2, at: new Date().toISOString(), status: 'available',
  method: 'bound_server_discovery_and_captured_pid_start_recheck',
  authority: { kind: 'exclusive_source_role', exclusiveToRun: true, runId, database: backend.database, role: backend.role, applicationName: 'schemii', provisioningReceipt: '/private/provisioned.json',
    endpoint: { host: '127.0.0.1', port: 5432, sslmode: 'require' }, server }, serverIdentity: server,
  captured: [backend], capturedChecks: [{ ...backend, status: live.length ? 'present' : 'absent' }],
  live, ownedBackends: live.length, releaseVerified: live.length === 0 });
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
test('more than256 active IDs are fairly inspected, never false zero, and retire only after terminal plus fresh witness', async () => {
  const count = 300, now = Date.now(), inspected = new Set(); let phase = 'running', state = null;
  const r = { version: 1, tag: runId, kind: 'disposable', ready: true,
    accounts: Array.from({ length: count }, (_, index) => ({ username: `owner-${index}`, userId: `user-${index}`, cookie: `cookie-${index}`, identityVerifiedAt: new Date(now).toISOString() })),
    executions: Array.from({ length: count }, (_, index) => ({ username: `owner-${index}`, id: `cex_${index.toString(16).padStart(32, '0')}`, closed: true, terminalAt: new Date(now - 10000).toISOString() })) };
  const clientFactory = ({ cookie }) => { const index = Number(cookie.slice(7)); return { close() {}, json: async (_method, path) => {
    if (path.endsWith('/auth/me')) return { user: { id: `user-${index}`, username: `owner-${index}` } };
    const id = path.split('/').at(-1); inspected.add(id); return { id, status: phase };
  } }; };
  for (let sweep = 0; sweep < 4; sweep++) {
    const at = now + sweep * 5000, observed = { ...source(), at: new Date(at).toISOString() };
    const batch = await observeCohortWork(r, 'sql-page', { source: observed, state, now: at, clientFactory }); state = batch.state;
    assert.notEqual(batch.value.activeExecutions, 0); assert(batch.value.coverage.inspectedThisBatch <= 80); assert(batch.value.coverage.ownerReadsThisBatch <= 96);
  }
  assert.equal(inspected.size, count); phase = 'succeeded';
  for (let sweep = 4; sweep < 8; sweep++) {
    const at = now + sweep * 5000;
    const batch = await observeCohortWork(r, 'sql-page', { source: { ...source(), at: new Date(at).toISOString() }, state, now: at, clientFactory }); state = batch.state;
    assert.equal(batch.value.unreleasedExecutions, count);
  }
  const at = now + 45000, completed = await observeCohortWork(r, 'sql-page', { source: { ...source([]), at: new Date(at).toISOString() }, state, now: at, clientFactory });
  assert.equal(completed.value.activeExecutions, 0); assert.equal(completed.value.coverage.retired, count); assert.equal(completed.value.coverage.complete, true);
  assert.equal(r.executions.length, count); // Cleanup IDs are never pruned.
  const staleAt = now + 300000, stale = await observeCohortWork(r, 'sql-page', { source: { ...source([]), at: new Date(staleAt).toISOString() }, state: completed.state, now: staleAt, clientFactory });
  assert.equal(stale.value.activeExecutions, null); assert(stale.value.coverage.uncoveredOwners > 0);
});
test('shared stream terminal retires through the actual observer without forbidden raw reads or loss of private cleanup IDs', async t => {
  const dir = await directory(t), path = join(dir, 'events'), now = Date.now();
  const journal = await openJournal(path, { tag: runId, budget: { records: 128, bytes: 128 * 2048 + 65536, recordBytes: 2048, chunkRecords: 64 } });
  const r = { ...receipt(), kind: 'disposable', ready: true, executionJournal: path };
  r.accounts[0].identityVerifiedAt = new Date(now).toISOString();
  r.accounts[0].sharedDashboard = { id: 'dashboard_' + 'd'.repeat(32), revision: 3 };
  await journal.append({ type: 'execution', action: 'created', id: executionId, username: account.username, kind: 'report-stream', dashboardId: 'dashboard_' + 'd'.repeat(32), dashboardRevision: 3 });
  const requests = [], clientFactory = () => ({ close() {}, json: async (_method, path) => {
    requests.push(path);
    if (path === '/api/v1/auth/me') return { user: { id: account.userId, username: account.username } };
    assert.equal(path, '/api/v1/schemer/dashboards/' + r.accounts[0].sharedDashboard.id); return r.accounts[0].sharedDashboard;
  } });
  const first = await observeCohortWork(r, 'shared-report', { source: { ...source(), at: new Date(now).toISOString() }, now, clientFactory });
  assert.equal(first.value.activeExecutions, null); assert.equal(first.value.coverage.complete, false);
  const frozenFirst = structuredClone(first);
  await journal.append({ type: 'execution', action: 'terminal', id: executionId, username: account.username, terminal: 'drained', terminalAt: new Date(now + 1).toISOString() });
  const at = now + 100, released = { ...source([]), at: new Date(at).toISOString() };
  const next = await observeCohortWork(r, 'shared-report', { source: released, state: first.state, now: at, clientFactory });
  assert.equal(next.value.coverage.complete, true); assert.equal(next.value.coverage.retired, 1); assert.equal(next.value.retiredProofs[0].id, executionId);
  const app = { status: 'available', processes: [{ role: 'api', pid: 1, birthTick: '1', namespacePid: 1 }], rssBytes: 1, memoryLimitBytes: 100 };
  const wrap = (batch, sample, time) => ({ status: 'available', at: new Date(time).toISOString(), runId, source: sample, work: batch.value });
  const internal = time => ({ version: 1, at: new Date(time).toISOString(), scope: 'process_aggregate', processPid: 1 });
  const previous = combineObservation({ topology: {} }, { schemii: app }, internal(now), { now, ownership: wrap(frozenFirst, { ...source(), at: new Date(now).toISOString() }, now) });
  const qualified = combineObservation({ topology: {} }, { schemii: app }, internal(at), { previous, now: at, ownership: wrap(next, released, at) });
  assert.equal(qualified.status, 'available'); assert.equal(qualified.ownedBackends, 0);
  const unproved = structuredClone(next); unproved.value.retiredProofs = [];
  assert.equal(combineObservation({ topology: {} }, { schemii: app }, internal(at), { previous, now: at, ownership: wrap(unproved, released, at) }).status, 'unavailable');
  const wrongAuthority = structuredClone(released); wrongAuthority.authority.endpoint.host = '192.0.2.11';
  assert.equal(combineObservation({ topology: {} }, { schemii: app }, internal(at), { previous, now: at, ownership: wrap(next, wrongAuthority, at) }).status, 'unavailable');
  assert(!requests.some(value => value.includes('/common/'))); await journal.close();
});
test('unreturned execution and server-managed CSV intents stay unknown until terminal plus a later complete witness', async t => {
  const dir = await directory(t), path = join(dir, 'events'), now = Date.now();
  const journal = await openJournal(path, { tag: runId, budget: { records: 128, bytes: 128 * 2048 + 65536, recordBytes: 2048, chunkRecords: 64 } });
  const r = { ...receipt(), kind: 'disposable', ready: true, executionJournal: path }; r.accounts[0].identityVerifiedAt = new Date(now).toISOString();
  const csv = await journal.append({ type: 'intent', kind: 'stream', username: account.username, workload: 'csv', status: 'pending' });
  const clientFactory = workClient().factory;
  let batch = await observeCohortWork(r, 'csv', { source: source([]), now, clientFactory });
  assert.equal(batch.value.activeExecutions, null); assert.equal(batch.value.unboundIntentCount, 1);
  await journal.append({ type: 'intent-update', intentSequence: csv, status: 'closed', idExposure: 'server_managed_not_exposed' });
  batch = await observeCohortWork(r, 'csv', { source: { ...source([]), at: new Date(now).toISOString() }, state: batch.state, now: now + 100, clientFactory });
  assert.equal(batch.value.coverage.complete, false);
  batch = await observeCohortWork(r, 'csv', { source: { ...source([]), at: new Date(now + 200).toISOString() }, state: batch.state, now: now + 200, clientFactory });
  assert.equal(batch.value.coverage.complete, true); assert.equal(batch.value.serverManagedStreamsRetired, 1);
  await journal.append({ type: 'intent', kind: 'execution', username: account.username, status: 'pending' });
  batch = await observeCohortWork(r, 'sql-page', { source: { ...source([]), at: new Date(now + 300).toISOString() }, state: batch.state, now: now + 300, clientFactory });
  assert.equal(batch.value.activeExecutions, null); assert.equal(batch.value.coverage.complete, false);
  await journal.close();
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

function recoveryFixture({ wrongRow = false, wrongStart = false, leak = false, missingCapture = false, terminal = 'succeeded', releasedSample = null, pageServer = server } = {}) {
  const calls = []; let closed = false, time = 0;
  const execution = { id: executionId, status: terminal, completedStatementIndexes: [0], results: [{ id: resultId }] };
  return { calls, closed: () => closed, clock: () => time, sleep: async ms => { time += ms; },
    monitor: async () => closed && releasedSample ? releasedSample : source(missingCapture ? [] : closed && !leak ? [] : [backend]),
    factory: () => ({ close() {}, json: async (method, path, body, status) => {
      calls.push({ method, path, body, status });
      if (path.endsWith('/auth/me')) return { user: { id: account.userId, username: account.username } };
      if (method === 'POST') { assert.equal(status, 201); return execution; }
      if (method === 'DELETE') { closed = true; return {}; }
      if (path.endsWith(resultId)) return { executionId, resultId, nextCursor: null, truncated: false,
        columns: ['value', 'pid', 'backend_start', 'database', 'role', 'application_name', 'server_address', 'server_port', 'postmaster_start', 'database_oid', 'fixture_marker'].map(name => ({ name })),
        rows: [[wrongRow ? 2 : 1, backend.pid, wrongStart ? backend.backendStart.replace('.000000', '.000001') : backend.backendStart, backend.database, backend.role, backend.applicationName,
          pageServer.address, pageServer.port, pageServer.postmasterStart, pageServer.databaseOid, `schemii-load:${pageServer.fixtureId}`]] };
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
test('review238 counterexamples cannot verify release through sourceEvidence or ownedRecovery', async t => {
  const dir = await directory(t);
  const legacy = { ...source([]), version: 1, method: 'independent_pg_stat_activity_exact_role_database_application_pid_start' };
  const renamed = { ...source([]), capturedChecks: [{ ...backend, status: 'present' }] };
  const missingRechecks = { ...source([]), capturedChecks: [] };
  const retargeted = structuredClone(source([]));
  retargeted.authority.endpoint = { host: '192.0.2.77', port: 6543, sslmode: 'require' };
  retargeted.serverIdentity = { ...server, address: '192.0.2.77', fixtureId: 'b'.repeat(64) };
  retargeted.authority.server = retargeted.serverIdentity;
  // A self-consistent new source cannot replace the acquired source authority.
  assert.equal(sourceEvidence(retargeted, runId).ownedBackends, 0);
  for (const value of [legacy, renamed, missingRechecks, retargeted]) {
    assert.throws(() => sourceEvidence(value, runId, Date.now(), source().authority));
    const f = recoveryFixture({ releasedSample: value }), journal = join(dir, `negative-${[legacy, renamed, missingRechecks, retargeted].indexOf(value)}.json`);
    await assert.rejects(ownedRecovery({ receipt: receipt(), config: recoveryConfig, journal, monitor: f.monitor, clientFactory: f.factory }));
    assert.notEqual((await readPrivateJSON(journal)).status, 'verified');
  }
  const f = recoveryFixture({ pageServer: { ...server, databaseOid: 101 } });
  await assert.rejects(ownedRecovery({ receipt: receipt(), config: recoveryConfig, journal: join(dir, 'wrong-actual-target.json'), monitor: f.monitor, clientFactory: f.factory }), /server_mismatch/);
});
test('complete captured rechecks distinguish a legitimate newer PID incarnation from a live old backend', () => {
  const reused = { ...source([]), capturedChecks: [{ ...backend, status: 'pid_reused', observedBackendStart: '2026-11-11T11:00:00.000000+00:00' }] };
  assert(sourceEvidence(reused, runId).releaseVerified);
  assert.throws(() => sourceEvidence({ ...reused, capturedChecks: [{ ...reused.capturedChecks[0], observedBackendStart: backend.backendStart }] }, runId));
  const next = { ...backend, backendStart: '2026-11-11T11:00:00.000000+00:00' };
  const occupied = { ...reused, captured: [backend, next], live: [next], ownedBackends: 1, releaseVerified: false,
    capturedChecks: [...reused.capturedChecks, { ...next, status: 'present' }] };
  assert.equal(sourceEvidence(occupied, runId).ownedBackends, 1);
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
  await writePrivateJSON(file, { ...base, ownedBackends: undefined, activeJobs: undefined, runtimeReceiptSha256: 'a'.repeat(64) });
  await assert.rejects(observation(file, null, 'b'.repeat(64)), /fixture_observer_runtime_changed/);
  await writePrivateJSON(file, { ...base, ownership: { runId, source: source([]), work: { runId, status: 'available', at: '2000-01-01' } } });
  await assert.rejects(observation(file), /owned_work/);
});
test('engine recovery follows the exact owned workspace revision after metadata journeys and preserves its target', async t => {
  const dir = await directory(t), file = join(dir, 'observer.json'), spec = plan({ fixtureMode: 'disposable', workload: 'metadata-write', active: 1 });
  const r = { ...receipt(), kind: 'disposable', ready: true };
  r.accounts[0].workspace = { id: workspaceId, ownerId: account.userId, revision: 7 };
  const config = { version: 1, runId, workload: spec.workload, fixtureReceipt: join(dir, 'fixtures-private.json'), sourceConfig: join(dir, 'source-config.json'),
    sourceOutput: join(dir, 'source.json'), recovery: { username: account.username, workspaceId, workspaceRevision: 1, settingsRevision: 1, consoleId } };
  await writePrivateJSON(join(dir, 'ownership-private.json'), config);
  const zero = Object.fromEntries(['retainedSessions', 'openCursors', 'ownedBackends', 'activeJobs', 'ordinaryPermits', 'retainedPermits'].map(key => [key, 0]));
  const work = { runId, status: 'available', at: new Date().toISOString(), activeJobs: 0, activeExecutions: 0, coverage: { complete: true } };
  await writePrivateJSON(file, { ...zero, status: 'available', at: new Date().toISOString(), appRssBytes: 1, appMemoryLimitBytes: 100,
    ownership: { runId, source: source([]), work } });
  const aborter = new AbortController(); let calls = 0, recoveryCalls = 0;
  const result = await executePlan({ spec, receipt: r, dir, observerFile: file, signal: aborter.signal, abort: () => aborter.abort(), sleep: async () => {},
    tickFrequencyReader: async () => 100, generatorSampler: async owner => ({ status: 'available', owner, rssBytes: 1, cpuUsec: ++calls, monotonicMs: calls * 1000, interfaces: [] }),
    runStage: async () => ({ http: { incomplete: 0, failed: 0, rejected: 0 }, arrivals: { dropped: 0, failed: 0 }, latency: {} }),
    recover: async options => { recoveryCalls++; assert.equal(options.config.workspaceId, workspaceId); assert.equal(options.config.workspaceRevision, 7); assert.equal(options.config.settingsRevision, 1);
      return { verified: true, authenticatedRead: true, exactSelectTerminal: true, capturedBackendReleased: true }; }, observeRun: async () => ({ work }) });
  assert.equal(recoveryCalls, 1); assert.equal(result.recovery.verified, true); assert.equal(result.capacityEligible, false);
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
