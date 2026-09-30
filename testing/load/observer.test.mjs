import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm, readFile, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { processIdentity, cgroupIdentity, writePrivateJSON, readPrivateJSON } from './runtime.mjs';
import { sampleContainer, combineObservation, collect, parseObserver, observe, recoverObserver } from './observer.mjs';

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'schemii-observer-contract-')); t.after(() => rm(root, { recursive: true, force: true }));
  const procRoot = join(root, 'proc'), cgroupRoot = join(root, 'cg');
  await mkdir(join(procRoot, '100'), { recursive: true }); await mkdir(join(procRoot, 'sys/kernel/random'), { recursive: true });
  await writeFile(join(procRoot, 'sys/kernel/random/boot_id'), '12345678-1234-1234-1234-123456789abc\n');
  const fields = Array(22).fill('0'); fields[0] = 'S'; fields[19] = '999';
  await writeFile(join(procRoot, '100/stat'), `100 (uvicorn command) ${fields.join(' ')}\n`);
  await writeFile(join(procRoot, '100/status'), 'VmRSS:\t80 kB\nNSpid:\t100\t1\n');
  await writeFile(join(procRoot, '100/cgroup'), '0::/owned.scope\n'); await mkdir(join(cgroupRoot, 'owned.scope'), { recursive: true });
  for (const [file, value] of Object.entries({ 'memory.current': '100000', 'memory.max': '1000000', 'cpu.stat': 'usage_usec 100000\nthrottled_usec 2000\n', 'cpu.max': '200000 100000', 'cgroup.procs': '100\n' }))
    await writeFile(join(cgroupRoot, 'owned.scope', file), value);
  const options = { procRoot, cgroupRoot };
  const container = { service: 'schemii', rootProcess: await processIdentity(100, procRoot), cgroup: await cgroupIdentity(100, options) };
  return { root, container, ...options };
}
const internal = now => ({ version: 1, at: new Date(now).toISOString(), scope: 'process_aggregate', processPid: 1,
  sources: { source: { status: 'available', counts: { ordinary: { connections: 3, ordinaryPermits: 1, retainedPermits: 2 }, monitoring: { connections: 1 }, control: { connections: 0 } } },
    console: { status: 'available', counts: { readSessionHandles: 2, manualTransactionHandles: 1, nativeCursors: 5, resultPageTokens: 7 } },
    raw: { status: 'available', counts: { registeredSessions: 3 } }, metadata: { status: 'available', counts: { active: 2, rejected: 4 } } } });
const receipt = { topology: { generatorPlacement: 'cohosted_unqualified' } };
test('cgroup snapshot measures actual budgets/counters and aggregates RSS', async t => {
  const f = await fixture(t), sample = await sampleContainer(f.container, f);
  assert.equal(sample.status, 'available'); assert.equal(sample.rssBytes, 81920);
  assert.equal(sample.memoryCurrentBytes, 100000); assert.equal(sample.memoryLimitBytes, 1000000);
  assert.equal(sample.cpuUsageUsec, 100000); assert.equal(sample.quotaCpus, 2);
  assert.deepEqual(sample.processes, [{ pid: 100, birthTick: '999', rssBytes: 81920, namespacePid: 1 }]);
});
test('stale PID, replaced cgroup and missing counters fail unavailable without zeros', async t => {
  const f = await fixture(t);
  assert.equal((await sampleContainer({ ...f.container, rootProcess: { pid: 100, birthTick: 'old' } }, f)).status, 'unavailable');
  assert.equal((await sampleContainer({ ...f.container, cgroup: { ...f.container.cgroup, inode: 0 } }, f)).status, 'unavailable');
  await rm(join(f.cgroupRoot, 'owned.scope/cpu.stat'));
  const sample = await sampleContainer(f.container, f); assert.equal(sample.status, 'unavailable'); assert(!('rssBytes' in sample));
  assert.equal(sample.reason, 'resource_counter_unavailable');
});
test('known internal counters match endpoint process and remain distinct from unmeasured attribution', async t => {
  const f = await fixture(t), app = await sampleContainer(f.container, f), now = Date.now();
  const output = combineObservation(receipt, { schemii: app }, internal(now), { now });
  assert.equal(output.sourceConnections, 4); assert.equal(output.ordinaryPermits, 1); assert.equal(output.retainedPermits, 2);
  assert.equal(output.retainedSessions, 6); assert.equal(output.openCursors, 5);
  assert.equal(output.metadataActive, 2); assert.equal(output.metadataRejected, 4); assert.equal(output.apiProcesses, 1);
  assert(!('ownedBackends' in output)); assert(!('activeJobs' in output)); assert(!('ingressConnections' in output));
  assert.equal(output.qualification.generatorHeadroomVerified, false); assert.equal(output.qualification.capacityEligible, false);
});
test('busy cursor, wrong endpoint and stale internal observations never manufacture gauges', async t => {
  const f = await fixture(t), app = await sampleContainer(f.container, f), now = Date.now(), value = internal(now);
  delete value.sources.console.counts.nativeCursors;
  assert(!('openCursors' in combineObservation(receipt, { schemii: app }, value, { now })));
  value.processPid = 99;
  assert(!('retainedSessions' in combineObservation(receipt, { schemii: app }, value, { now })));
  assert(!('metadataActive' in combineObservation(receipt, { schemii: app }, internal(now - 20000), { now })));
  const unavailable = combineObservation(receipt, { schemii: { status: 'unavailable' } }, internal(now), { now });
  assert(!('appRssBytes' in unavailable));
});
test('CPU percent uses cumulative usec across sample time; first/reset samples stay unknown', async t => {
  const f = await fixture(t), app = await sampleContainer(f.container, f), now = Date.now();
  const first = combineObservation(receipt, { schemii: app }, null, { now });
  assert(!('appCpuPercent' in first));
  const next = combineObservation(receipt, { schemii: { ...app, cpuUsageUsec: 2600000 } }, null, { now: now + 5000, previous: first });
  assert.equal(next.appCpuPercent, 50);
  assert(!('appCpuPercent' in combineObservation(receipt, { schemii: { ...app, cpuUsageUsec: 0 } }, null, { now: now + 5000, previous: first })));
});
test('memory guard uses cgroup charge even when RSS is below the limit', async t => {
  const f = await fixture(t), app = await sampleContainer(f.container, f), now = Date.now();
  const output = combineObservation(receipt, { schemii: { ...app, memoryCurrentBytes: 850000 } }, internal(now), { now });
  assert.equal(output.reason, 'cgroup_memory_guard'); assert(!('appRssBytes' in output));
});
test('effective budgets include stricter ancestors and disclosed unlimited quota', async t => {
  const f = await fixture(t), parent = join(f.cgroupRoot, 'parent.scope'), child = join(parent, 'owned.scope');
  await mkdir(child, { recursive: true });
  for (const file of ['memory.current', 'memory.max', 'cpu.stat', 'cpu.max', 'cgroup.procs'])
    await writeFile(join(child, file), await readFile(join(f.cgroupRoot, 'owned.scope', file)));
  await writeFile(join(parent, 'memory.max'), '400000'); await writeFile(join(parent, 'cpu.max'), '50000 100000');
  await writeFile(join(f.procRoot, '100/cgroup'), '0::/parent.scope/owned.scope\n');
  const container = { ...f.container, cgroup: await cgroupIdentity(100, f) };
  const sample = await sampleContainer(container, f);
  assert.equal(sample.memoryLimitBytes, 400000); assert.equal(sample.quotaCpus, 0.5); assert.equal(sample.cgroupLimitLevels, 2);
  await writeFile(join(parent, 'cpu.max'), 'max 100000'); await writeFile(join(child, 'cpu.max'), 'max 100000');
  assert.equal((await sampleContainer(container, f)).quotaCpus, null);
});
test('exited members are disclosed without attributing a replacement process', async t => {
  const f = await fixture(t);
  await writeFile(join(f.cgroupRoot, 'owned.scope/cgroup.procs'), '100\n101\n');
  const sample = await sampleContainer(f.container, f);
  assert.deepEqual(sample.exitedDuringSample, [101]); assert.equal(sample.processes.length, 1);
});
test('boot ownership and options are bounded before observing any process', async t => {
  const f = await fixture(t);
  await assert.rejects(collect({ version: 1, bootId: 'wrong', containers: [f.container] }, f), /wrong_boot/);
  assert.throws(() => parseObserver(['--runtime-receipt', 'x', '--output', 'y', '--account', 'admin']), /optional_admin_pair/);
  assert.throws(() => parseObserver(['--runtime-receipt', 'x', '--output', 'y', '--seconds', '999999']), /duration_limit/);
  assert.equal(parseObserver(['--runtime-receipt', 'x', '--output', 'y', '--seconds', '5']).seconds, 5);
  assert.equal((await readFile(join(f.procRoot, '100/stat'), 'utf8')).includes('uvicorn'), true);
});
async function observationOptions(t) {
  const f = await fixture(t), receiptPath = join(f.root, 'receipt.json'), credentialPath = join(f.root, 'credentials.json');
  const bootId = (await readFile('/proc/sys/kernel/random/boot_id', 'utf8')).trim();
  await writePrivateJSON(receiptPath, { version: 1, bootId, origin: 'https://localhost:8001' });
  await writePrivateJSON(credentialPath, { accounts: { admin: { password: 'unit-contract-password' } } });
  return { ...f, receiptPath, credentialPath, output: join(f.root, 'observation.json'), account: 'admin', seconds: 1 };
}
function observerClient({ failLogout = false } = {}) {
  const actions = [], client = { cookie: '', actions,
    login: async account => { actions.push('login'); client.cookie = 'unit-owned-cookie'; return { user: { id: 'unit-admin', username: account.username } }; },
    json: async () => { actions.push('sample'); return {}; },
    logout: async () => { actions.push('logout'); if (failLogout) throw new Error('unit-logout-failure'); client.cookie = ''; },
    close: () => actions.push('close') };
  return client;
}
test('controlled cancellation closes and revokes only the observer session while retaining audit evidence', async t => {
  const o = await observationOptions(t), client = observerClient(), aborter = new AbortController();
  await observe(o, { signal: aborter.signal, clientFactory: () => client,
    collector: async () => { aborter.abort(); return { status: 'available', at: new Date().toISOString() }; } });
  assert.deepEqual(client.actions, ['login', 'sample', 'logout', 'close']);
  assert.equal((await readPrivateJSON(`${o.output}.owner.json`)).status, 'stopped');
  await assert.rejects(stat(`${o.output}.session-private.json`), /ENOENT/);
  assert.equal((await readFile(`${o.output}.jsonl`, 'utf8')).trim().split('\n').length, 1);
});
test('failed logout retains the exact private session and records cleanup pending', async t => {
  const o = await observationOptions(t), client = observerClient({ failLogout: true }), aborter = new AbortController();
  await assert.rejects(observe(o, { signal: aborter.signal, clientFactory: () => client,
    collector: async () => { aborter.abort(); return { status: 'available', at: new Date().toISOString() }; } }), /authentication_cleanup_pending/);
  const session = await readPrivateJSON(`${o.output}.session-private.json`);
  assert.equal(session.cookie, 'unit-owned-cookie'); assert.equal((await stat(`${o.output}.session-private.json`)).mode & 0o777, 0o600);
  assert.equal((await readPrivateJSON(`${o.output}.owner.json`)).status, 'authentication_cleanup_pending');
  assert.equal(client.actions.at(-1), 'close');
});
test('orphan recovery refuses a live owner and wrong identity; converges after exact authentication cleanup', async t => {
  const o = await observationOptions(t), owner = { ...(await processIdentity(process.pid)), bootId: (await readFile('/proc/sys/kernel/random/boot_id', 'utf8')).trim(), version: 1, output: o.output };
  await writePrivateJSON(`${o.output}.owner.json`, owner);
  await writePrivateJSON(`${o.output}.session-private.json`, { version: 1, owner, cookie: 'unit-owned-cookie', origin: 'https://localhost:8001', username: 'admin', userId: 'unit-admin' });
  let calls = 0;
  await assert.rejects(recoverObserver(o.output, { clientFactory: () => { calls++; } }), /observer_still_live/);
  assert.equal(calls, 0);
  const dead = async () => { const error = new Error('gone'); error.code = 'ENOENT'; throw error; };
  const actions = [], client = { request: async () => ({ status: 200, data: { user: { id: 'wrong', username: 'admin' } } }), logout: async () => actions.push('logout'), close: () => actions.push('close') };
  await assert.rejects(recoverObserver(o.output, { processReader: dead, clientFactory: () => client }), /identity_changed/);
  assert.deepEqual(actions, ['close']); assert.equal((await readPrivateJSON(`${o.output}.session-private.json`)).cookie, 'unit-owned-cookie');
  client.request = async () => ({ status: 200, data: { user: { id: 'unit-admin', username: 'admin' } } });
  assert.equal((await recoverObserver(o.output, { processReader: dead, clientFactory: () => client })).authenticationClosed, true);
  assert.equal((await recoverObserver(o.output, { processReader: dead, clientFactory: () => client })).status, 'already_cleaned');
  assert.deepEqual(actions, ['close', 'logout', 'close']);
});
async function controlledCollector(t, options) {
  const module = new URL('./observer.mjs', import.meta.url).href;
  const source = `import { observe } from ${JSON.stringify(module)};
    const aborter = new AbortController();
    process.once('SIGTERM', () => aborter.abort());
    const client = { cookie: '', login: async () => {
      client.cookie = 'unit-owned-cookie'; return { user: { id: 'unit-admin', username: 'admin' } };
    }, json: async () => ({}), logout: async () => { client.cookie = ''; }, close: () => {} };
    let ready = false;
    await observe(JSON.parse(process.argv[1]), { signal: aborter.signal, clientFactory: () => client,
      collector: async () => { if (!ready) { ready = true; process.stdout.write('ready\\n'); }
        return { status: 'available', at: new Date().toISOString() }; } });`;
  const child = spawn(process.execPath, ['--input-type=module', '-e', source, JSON.stringify({ ...options, seconds: 30 })], { stdio: ['ignore', 'pipe', 'pipe'] });
  const completion = once(child, 'exit');
  t.after(async () => { if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL'); await completion; });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('controlled_collector_not_ready')), 5000);
    child.stdout.once('data', data => { clearTimeout(timer); data.toString().includes('ready') ? resolve() : reject(new Error('unexpected_collector_output')); });
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', () => { clearTimeout(timer); reject(new Error('collector_exited_before_ready')); });
  });
  return { child, completion };
}
test('real controlled SIGTERM reaps the collector and automatically clears only its authentication receipt', async t => {
  const o = await observationOptions(t), { child, completion } = await controlledCollector(t, o);
  const owner = await readPrivateJSON(`${o.output}.owner.json`);
  assert.equal(owner.pid, child.pid); assert.equal((await processIdentity(child.pid)).birthTick, owner.birthTick);
  assert.equal(child.kill('SIGTERM'), true);
  assert.deepEqual(await completion, [0, null]);
  assert.equal((await readPrivateJSON(`${o.output}.owner.json`)).status, 'stopped');
  await assert.rejects(stat(`${o.output}.session-private.json`), /ENOENT/);
  await assert.rejects(processIdentity(owner.pid), /ENOENT/);
});
test('real controlled SIGKILL leaves exact orphan receipts which recovery reconciles after owner exit', async t => {
  const o = await observationOptions(t), { child, completion } = await controlledCollector(t, o);
  assert.equal(child.kill('SIGKILL'), true); assert.deepEqual(await completion, [null, 'SIGKILL']);
  assert.equal((await readPrivateJSON(`${o.output}.owner.json`)).status, 'running');
  let cookie, loggedOut = false, closed = false;
  const result = await recoverObserver(o.output, { clientFactory: options => {
    cookie = options.cookie;
    return { request: async () => ({ status: 200, data: { user: { id: 'unit-admin', username: 'admin' } } }),
      logout: async () => { loggedOut = true; }, close: () => { closed = true; } };
  } });
  assert.equal(cookie, 'unit-owned-cookie'); assert.equal(result.authenticationClosed, true);
  assert.equal(loggedOut && closed, true); await assert.rejects(stat(`${o.output}.session-private.json`), /ENOENT/);
  assert.equal((await readPrivateJSON(`${o.output}.owner.json`)).status, 'recovered');
});
