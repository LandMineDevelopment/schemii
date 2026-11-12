import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, stat, rm, symlink, rename, chmod } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { buildReceipt, buildFixtureReceipt, recheckFixtureReceipt, fixtureLauncherFence, processIdentity, processRole, sampleGenerator, generatorHeadroom, writePrivateJSON, readPrivateJSON } from './runtime.mjs';

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'schemii-runtime-contract-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const procRoot = join(root, 'proc'), cgroupRoot = join(root, 'cgroup');
  await mkdir(join(procRoot, 'sys/kernel/random'), { recursive: true });
  await writeFile(join(procRoot, 'sys/kernel/random/boot_id'), '12345678-1234-1234-1234-123456789abc\n');
  const records = [];
  for (const [index, service] of ['schemii', 'ingress', 'metadata-postgres'].entries()) {
    const pid = 100 + index, path = `/system.slice/owned-${pid}.scope`;
    const fields = Array(22).fill('0'); fields[0] = 'S'; fields[19] = `${1000 + index}`;
    await mkdir(join(procRoot, `${pid}`)); await mkdir(`${cgroupRoot}${path}`, { recursive: true });
    await writeFile(join(procRoot, `${pid}/stat`), `${pid} (a command) ${fields.join(' ')}\n`);
    await writeFile(join(procRoot, `${pid}/cgroup`), `0::${path}\n`);
    records.push({ project: 'schemii-test', service, pid, id: `${index + 1}`.repeat(64), image: `sha256:${'f'.repeat(64)}`,
      running: true, memoryBytes: 1024, nanoCpus: 2e9, Env: ['PASSWORD=must-not-export'], Args: ['secret-query'] });
  }
  return { root, procRoot, cgroupRoot, records, revision: 'a'.repeat(40), origin: 'https://localhost:8001' };
}
test('runtime receipt exports only verified launcher identities and selected budgets', async t => {
  const options = await fixture(t), receipt = await buildReceipt(options.records, options);
  assert.equal(receipt.containers.length, 3);
  assert.deepEqual(receipt.containers[0].rootProcess, { pid: 100, birthTick: '1000' });
  assert.equal(receipt.containers[0].configuredCpus, 2);
  assert.equal(receipt.topology.generatorPlacement, 'cohosted_unqualified');
  assert(!JSON.stringify(receipt).includes('PASSWORD'));
  assert(!JSON.stringify(receipt).includes('secret-query'));
  const actual = await stat(`${options.cgroupRoot}${receipt.containers[0].cgroup.path}`);
  assert.equal(receipt.containers[0].cgroup.inode, actual.ino);
});
test('foreign, duplicate, incomplete and stopped runtime identities are rejected', async t => {
  const o = await fixture(t);
  await assert.rejects(buildReceipt([{ ...o.records[0], project: 'peer' }, ...o.records.slice(1)], o), /container_identity/);
  await assert.rejects(buildReceipt([o.records[0], o.records[0], o.records[2]], o), /container_identity/);
  await assert.rejects(buildReceipt(o.records.slice(1), o), /incomplete_runtime_topology/);
  await assert.rejects(buildReceipt([{ ...o.records[0], running: false }, ...o.records.slice(1)], o), /container_identity/);
  await writeFile(join(o.procRoot, '100/stat'), `100 (gone) Z ${Array(21).fill('0').join(' ')}`);
  await assert.rejects(processIdentity(100, o.procRoot), /process_exited/);
});
test('private receipts are atomic, do not overwrite peer files and reject symlink reads', async t => {
  const o = await fixture(t), path = join(o.root, 'receipt.json');
  await writePrivateJSON(path, { value: 1 }, { exclusive: true });
  assert.equal((await stat(path)).mode & 0o777, 0o600);
  await assert.rejects(writePrivateJSON(path, { value: 2 }, { exclusive: true }), /EEXIST/);
  assert.deepEqual(await readPrivateJSON(path), { value: 1 });
  await symlink(path, join(o.root, 'link.json'));
  await assert.rejects(readPrivateJSON(join(o.root, 'link.json')), /invalid_private_receipt/);
  assert.equal((await readFile(path, 'utf8')).trim(), '{\n  "value": 1\n}');
});
test('only supported single API commands qualify; argv content is not retained', () => {
  for (const argv of [['uvicorn', 'schemii.main:app'], ['/opt/venv/bin/python', '-m', 'uvicorn', 'schemii.main:app', '--workers', '1'],
    ['/opt/venv/bin/python', '/opt/venv/bin/uvicorn', 'schemii.main:app']])
    assert.equal(processRole(argv.join('\0')), 'api');
  for (const argv of [['uvicorn', 'schemii.main:app', '--workers', '2'], ['uvicorn', 'schemii.main:app', '--reload'],
    ['python', 'some-sidecar.py'], ['uvicorn', 'foreign:app'], ['uvicorn', 'foreign:app', '--log-config', 'schemii.main:app'],
    ['uvicorn', 'schemii.main:app', '--workers=2', '1']]) assert.equal(processRole(argv.join('\0')), 'unknown');
  assert.equal(processRole('/bin/sh\0-c\0exec uvicorn schemii.main:app'), 'launcher_shell');
});
test('generator resources and network namespace counters retain qualification and replacement fences', async t => {
  const f = await fixture(t), owner = { pid: 100, birthTick: '1000' };
  await mkdir(join(f.procRoot, '100/fd')); await mkdir(join(f.procRoot, '100/net'));
  await writeFile(join(f.procRoot, '100/status'), 'VmRSS:\t80 kB\nThreads:\t3\n');
  await writeFile(join(f.procRoot, '100/net/dev'), 'Inter-| Receive | Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n eth0: 100 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0\n');
  const value = await sampleGenerator(owner, { procRoot: f.procRoot, ownPid: 100 });
  assert.equal(value.status, 'available'); assert.equal(value.rssBytes, 81920); assert.equal(value.threads, 3);
  assert.equal(value.headroomVerified, false); assert.equal(value.networkCapacity, 'unavailable');
  assert.equal(value.networkScope, 'network_namespace_aggregate_not_run_attributed');
  assert.equal((await sampleGenerator({ ...owner, birthTick: 'foreign' }, f)).status, 'unavailable');
  const external = await sampleGenerator(owner, { ...f, ticksPerSecond: 100 });
  assert.equal(external.cpuTickFrequency, 100); assert.equal(external.cpuScope, 'proc_stat_with_host_CLK_TCK');
  assert.equal(external.cpuUsec, 0);
  const summary = { arrivals: { dropped: 0 }, latency: { schedulingLagMs: { samples: 10, p95: 2 } } };
  const before = { ...value, cpuUsec: 1000, monotonicMs: 1000 }, after = { ...value, cpuUsec: 501000, monotonicMs: 2000,
    interfaces: value.interfaces.map(row => ({ ...row, sentBytes: row.sentBytes + 1000 })) };
  const result = generatorHeadroom(before, after, summary);
  assert.equal(result.cpuPercentOneCore, 50); assert.equal(result.networkNamespace[0].sentBytesPerSecond, 1000);
  assert.equal(result.headroomVerified, false); assert.equal(result.schedulingLagMs.p95, 2);
  assert.equal(generatorHeadroom(before, { ...after, owner: { ...owner, birthTick: 'replacement' } }, summary).status, 'unavailable');
  const reset = { ...after, interfaces: [{ ...after.interfaces[0], sentBytes: 0 }] };
  assert.equal(generatorHeadroom(before, reset, summary).status, 'unavailable');
});

const fixtureLoadProfile = {"name":"representative-load-v1","version":1,"purpose":"representative-one-api-load-fixture","allocations":{"schemii":{"memoryBytes":2147483648,"nanoCpus":2000000000,"pids":256},"metadata":{"memoryBytes":1073741824,"nanoCpus":1000000000,"pids":256},"source":{"memoryBytes":2147483648,"nanoCpus":2000000000,"pids":256},"ingress":{"memoryBytes":134217728,"nanoCpus":500000000,"pids":64},"observer-init":{"memoryBytes":67108864,"nanoCpus":250000000,"pids":16}},"metadata":{"normalMaximum":16,"readinessMaximum":2,"statementTimeoutMs":5000,"lockTimeoutMs":1000,"idleTransactionTimeoutMs":5000,"shutdownTimeoutSeconds":15},"sourceAdmission":{"ordinaryTotal":20,"ordinaryPerIdentity":4,"retainedTotal":19,"retainedPerIdentity":3},"database":{"maximumConnections":100,"sharedBuffers":"128MB"},"apiProcesses":1,"instrumentation":"fixture_only_stock_asgi_lifespan_and_owner_sampler","instrumentationOverheadMeasured":false,"stockCapacityEligible":false};
const wrappedArgs = ['uvicorn', 'pressure_app:observed_application', '--factory', '--app-dir', '/qualification', '--host',
  '0.0.0.0', '--port', '8000', '--workers', '1', '--no-access-log'];
async function disposableFixture(t) {
  const f = await fixture(t), run = `pressure_${'a'.repeat(32)}`, project = `schemii-pressure-${'a'.repeat(32)}`;
  const profile = structuredClone(fixtureLoadProfile), records = [], containers = {};
  for (const [index, service] of ['schemii', 'ingress', 'metadata', 'source'].entries()) {
    const pid = 100 + index, path = `/system.slice/owned-${pid}.scope`;
    if (index === 3) {
      const fields = Array(22).fill('0'); fields[0] = 'S'; fields[19] = `${1000 + index}`;
      await mkdir(join(f.procRoot, `${pid}`)); await mkdir(`${f.cgroupRoot}${path}`, { recursive: true });
      await writeFile(join(f.procRoot, `${pid}/stat`), `${pid} (owned source) ${fields.join(' ')}\n`);
      await writeFile(join(f.procRoot, `${pid}/cgroup`), `0::${path}\n`);
    }
    const allocation = profile.allocations[service], id = `${index + 1}`.repeat(64), image = `sha256:${'f'.repeat(64)}`;
    containers[service] = { id, image, state: 'running', startedAt: '2026-11-12T00:00:00Z',
      memory: allocation.memoryBytes, nanoCpus: allocation.nanoCpus, pids: allocation.pids };
    records.push({ service, id, image, project, pid, running: true, startedAt: containers[service].startedAt,
      memoryBytes: allocation.memoryBytes, nanoCpus: allocation.nanoCpus, pids: allocation.pids });
  }
  const apiFields = Array(22).fill('0'); apiFields[0] = 'S'; apiFields[19] = '2000';
  await mkdir(join(f.procRoot, '200'));
  await writeFile(join(f.procRoot, '200/stat'), `200 (uvicorn) ${apiFields.join(' ')}\n`);
  await writeFile(join(f.procRoot, '200/cgroup'), '0::/system.slice/owned-100.scope\n');
  await writeFile(join(f.procRoot, '200/status'), 'NSpid:\t200\t7\n');
  await writeFile(join(f.procRoot, '100/status'), 'NSpid:\t100\t1\n');
  await writeFile(join(f.procRoot, '100/cmdline'), ['/sbin/docker-init', '--', ...wrappedArgs].join('\0'));
  await writeFile(join(f.procRoot, '200/cmdline'), ['/usr/local/bin/python', '/usr/local/bin/uvicorn', ...wrappedArgs.slice(1)].join('\0'));
  await writeFile(`${f.cgroupRoot}/system.slice/owned-100.scope/cgroup.procs`, '100\n200\n');
  const primary = `${f.root}/primary`, nonce = 'b'.repeat(64);
  const journal = { version: 1, stage: 'os-export-intent', root: f.root, primary,
    directory: `${primary}/.schemii/metadata-pressure/${run}`, run, nonce, project, profile, containers,
    owner: { pid: 555, start: '5550' }, assets: { 'testing/load/runtime.mjs': 'c'.repeat(64) },
    runtimeReceipt: { run, profile, application: { containerId: records[0].id, containerStartedAt: containers.schemii.startedAt,
      observation: { run, stage: 'running', startupComplete: true, observationFailed: false,
        process: { pid: 7, uid: 10001, startTicks: '2000', incarnation: 'd'.repeat(32) } } } },
    osExport: { version: 1, status: 'pending', nonce, revision: f.revision, profile, records, output: 'os-runtime.json' } };
  return { ...f, journal };
}
test('fixture OS export binds exact journal IDs/source/profile and distinguishes API from container init', async t => {
  const f = await disposableFixture(t), receipt = await buildFixtureReceipt(f.journal, f);
  assert.equal(receipt.containers.length, 4);
  assert.equal(receipt.origin, 'https://localhost:8001');
  assert.equal(receipt.fixture.nonce, f.journal.nonce);
  assert.deepEqual(receipt.fixture.profile, fixtureLoadProfile);
  assert.equal(receipt.fixture.stockCapacityEligible, false);
  const app = receipt.containers.find(item => item.service === 'schemii');
  assert.deepEqual(app.rootProcess, { pid: 100, birthTick: '1000' });
  assert.deepEqual(app.initProcess, { pid: 100, birthTick: '1000', namespacePid: 1 });
  assert.deepEqual(app.apiProcess, { pid: 200, birthTick: '2000', namespacePid: 7 });
  assert.equal(app.configuredMemoryBytes, 2147483648);
  assert.equal(receipt.topology.apiProcesses, 1);
  assert(!JSON.stringify(receipt).includes('cmdline'));
  await assert.rejects(buildReceipt(f.journal.osExport.records, f), /invalid_container_identity/);
});
test('fixture exporter rejects forged project PID IDs service profile source and ambiguous membership', async t => {
  const f = await disposableFixture(t);
  const mutate = action => { const journal = structuredClone(f.journal); action(journal); return journal; };
  for (const journal of [mutate(j => j.osExport.records[0].project = 'schemii-test'),
    mutate(j => j.osExport.records[0].id = '9'.repeat(64)), mutate(j => j.osExport.records[0].pid = 999),
    mutate(j => j.osExport.records[2].service = 'metadata-postgres'), mutate(j => j.osExport.revision = '0'.repeat(40)),
    mutate(j => j.osExport.profile.metadata.normalMaximum = 2), mutate(j => j.osExport.nonce = '0'.repeat(64)),
    mutate(j => j.osExport.records[0].Env = ['SECRET=must-not-adopt']), mutate(j => j.runtimeReceipt.application.observation.process.startTicks = 'wrong')])
    await assert.rejects(buildFixtureReceipt(journal, f));
  await writeFile(`${f.cgroupRoot}/system.slice/owned-100.scope/cgroup.procs`, '100\n200\n300\n');
  await assert.rejects(buildFixtureReceipt(f.journal, f), /fixture_api_topology_unknown/);
});
test('fixture OS proof fails if PID incarnation boot cgroup or API role changes after capture', async t => {
  const f = await disposableFixture(t), receipt = await buildFixtureReceipt(f.journal, f);
  const path = join(f.procRoot, '200/stat'), original = await readFile(path, 'utf8');
  await writeFile(path, original.replace('2000', '2001'));
  await assert.rejects(recheckFixtureReceipt(receipt, f), /fixture_process_reused/);
  await writeFile(path, original);
  const group = `${f.cgroupRoot}/system.slice/owned-100.scope`;
  await rename(group, `${group}.old`); await mkdir(group);
  await assert.rejects(recheckFixtureReceipt(receipt, f), /fixture_cgroup_changed/);
  await rm(group, { recursive: true }); await rename(`${group}.old`, group);
  await writeFile(join(f.procRoot, '200/cmdline'), 'python\0foreign.py\0');
  await assert.rejects(recheckFixtureReceipt(receipt, f), /fixture_api_member_unknown/);
  await writeFile(join(f.procRoot, 'sys/kernel/random/boot_id'), '87654321-1234-1234-1234-123456789abc\n');
  await assert.rejects(recheckFixtureReceipt(receipt, f), /fixture_boot_changed/);
});
test('only exact fixed wrapper/init argv roles qualify without exposing argv content', () => {
  assert.equal(processRole(wrappedArgs.join('\0')), 'api');
  assert.equal(processRole(['/sbin/docker-init', '--', ...wrappedArgs].join('\0')), 'container_init');
  for (const args of [['/tmp/docker-init', '--', ...wrappedArgs], ['/sbin/docker-init', ...wrappedArgs],
    ['/sbin/docker-init', '--', 'uvicorn', 'foreign:app'], [...wrappedArgs, '--workers', '2'], [...wrappedArgs, '--reload']])
    assert.equal(processRole(args.join('\0')), 'unknown');
});
test('private OS receipts never overwrite peers and unsafe reads/outputs fail before adoption', async t => {
  const f = await disposableFixture(t), receipt = await buildFixtureReceipt(f.journal, f), target = join(f.root, 'os-runtime.json');
  await writePrivateJSON(target, receipt, { exclusive: true });
  assert.equal((await stat(target)).mode & 0o777, 0o600);
  await assert.rejects(writePrivateJSON(target, receipt, { exclusive: true }), /EEXIST/);
  await symlink(target, join(f.root, 'os-link.json'));
  await assert.rejects(readPrivateJSON(join(f.root, 'os-link.json')), /invalid_private_receipt/);
  await chmod(target, 0o644);
  await assert.rejects(readPrivateJSON(target), /invalid_private_receipt/);
  const publicDir = join(f.root, 'public'); await mkdir(publicDir, { mode: 0o755 });
  await assert.rejects(writePrivateJSON(join(publicDir, 'output.json'), receipt, { exclusive: true }), /private_parent_required/);
});

test('fixture launcher fence requires exact parent source profile and inherited exclusive canonical descriptors', async t => {
  const f = await disposableFixture(t), primary = f.journal.primary, common = join(f.root, 'common');
  await mkdir(common); await mkdir(join(f.procRoot, '555')); await mkdir(join(f.procRoot, 'self/fd'), { recursive: true });
  await mkdir(join(f.procRoot, 'self/fdinfo'));
  const fields = Array(22).fill('0'); fields[0] = 'S'; fields[19] = '5550';
  await writeFile(join(f.procRoot, '555/stat'), `555 (controller) ${fields.join(' ')}\n`);
  await writeFile(join(f.procRoot, '555/cmdline'), ['python3', `${f.root}/dev/pressure/controller.py`, '--root', f.root,
    '--primary', primary, '--common', common, '--', '--hold-load-fixture', '3600'].join('\0'));
  for (const [fd, name] of [[3, 'qa-deployment.lock'], [4, 'qa-startup.lock']]) {
    await writeFile(join(common, name), ''); await symlink(join(common, name), join(f.procRoot, `self/fd/${fd}`));
    await writeFile(join(f.procRoot, `self/fdinfo/${fd}`), 'lock:\t1: FLOCK ADVISORY WRITE 555 00:01:999 0 EOF\n');
  }
  f.journal.assets = {};
  for (const path of ['start.sh', 'dev/pressure/launcher.sh', 'dev/pressure/controller.py', 'dev/pressure/probe.py',
    'dev/pressure/app.py', 'dev/pressure/compose.yaml', 'dev/ingress/nginx.conf', 'dev/schemii.toml', 'testing/load/runtime.mjs']) {
    const target = join(f.root, path); await mkdir(join(target, '..'), { recursive: true }); await writeFile(target, `source:${path}`);
    f.journal.assets[path] = createHash('sha256').update(`source:${path}`).digest('hex');
  }
  const options = { procRoot: f.procRoot, parentPid: 555, leaseMode: 'exclusive' };
  await fixtureLauncherFence(f.journal, f.root, primary, common, options);
  await assert.rejects(fixtureLauncherFence(f.journal, f.root, primary, common, { ...options, parentPid: 556 }), /fixture_launcher_owner_required/);
  await assert.rejects(fixtureLauncherFence(f.journal, f.root, primary, common, { ...options, leaseMode: 'shared' }), /fixture_launcher_owner_required/);
  await writeFile(join(f.procRoot, 'self/fdinfo/3'), 'pos:0\n');
  await assert.rejects(fixtureLauncherFence(f.journal, f.root, primary, common, options), /fixture_exclusive_lease_required/);
  await writeFile(join(f.procRoot, 'self/fdinfo/3'), 'lock:\t1: FLOCK ADVISORY WRITE 555 00:01:999 0 EOF\n');
  await writeFile(join(f.root, 'dev/pressure/controller.py'), 'changed source');
  await assert.rejects(fixtureLauncherFence(f.journal, f.root, primary, common, options), /fixture_source_changed/);
});
test('private FIFO inputs are rejected before an open can block and output symlinks preserve peers', async t => {
  const { execFileSync } = await import('node:child_process');
  const f = await fixture(t), fifo = join(f.root, 'private.fifo');
  execFileSync('mkfifo', ['-m', '600', '--', fifo]);
  await assert.rejects(readPrivateJSON(fifo), /invalid_private_receipt/);
  const peer = join(f.root, 'peer.json'), output = join(f.root, 'unsafe-output.json');
  await writePrivateJSON(peer, { preserved: true }, { exclusive: true });
  await symlink(peer, output);
  await assert.rejects(writePrivateJSON(output, { preserved: false }, { exclusive: true }), /EEXIST/);
  assert.deepEqual(await readPrivateJSON(peer), { preserved: true });
  assert.equal(processRole(['/sbin/docker-init', '--', '', ...wrappedArgs].join('\0')), 'unknown');
});
