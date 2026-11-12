import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, stat, rm, symlink } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { buildReceipt, processIdentity, processRole, sampleGenerator, generatorHeadroom, writePrivateJSON, readPrivateJSON } from './runtime.mjs';

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
