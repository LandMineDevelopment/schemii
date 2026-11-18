// Launcher-only resource receipt. Never invoke Docker from an observer.
import { readFile, stat, lstat, open, rename, unlink, mkdir, link } from 'node:fs/promises';
import { dirname, resolve, posix, basename } from 'node:path';
import { hostname, cpus, totalmem, freemem, availableParallelism } from 'node:os';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';

const services = new Set(['schemii', 'ingress', 'metadata-postgres', 'demo-postgres', 'qa-postgres', 'ai-prototype-runtime']);
export function processRole(command) {
  const argv = command.split('\0').filter(Boolean), name = basename(argv[0] || '');
  const python = /^python(3(\.\d+)?)?$/.test(name);
  const target = name === 'uvicorn' ? 1 : python && argv[1] === '-m' && argv[2] === 'uvicorn' ? 3 :
    python && basename(argv[1] || '') === 'uvicorn' ? 2 : null;
  if (target !== null && argv[target] === 'schemii.main:app' && !argv.includes('--reload')) {
    const workers = argv.findIndex(value => value === '--workers' || value.startsWith('--workers='));
    if (workers < 0 || (argv[workers] === '--workers' && argv[workers + 1] === '1') || argv[workers] === '--workers=1') return 'api';
  }
  if (['sh', 'bash', 'dash'].includes(name)) return 'launcher_shell';
  return 'unknown';
}
export async function processIdentity(pid, procRoot = '/proc') {
  if (!Number.isSafeInteger(pid) || pid < 1) throw new Error('invalid_process_identity');
  const text = await readFile(`${procRoot}/${pid}/stat`, 'utf8');
  const fields = text.slice(text.lastIndexOf(')') + 2).trim().split(/\s+/);
  if (fields.length < 22 || ['Z', 'X'].includes(fields[0]) || !/^\d+$/.test(fields[19])) throw new Error('process_exited');
  return { pid, birthTick: fields[19] };
}
export async function cgroupIdentity(pid, { procRoot = '/proc', cgroupRoot = '/sys/fs/cgroup' } = {}) {
  const lines = (await readFile(`${procRoot}/${pid}/cgroup`, 'utf8')).trim().split('\n');
  const unified = lines.find(line => line.startsWith('0::'))?.slice(3);
  if (!unified || !unified.startsWith('/') || unified.includes('\0') || posix.normalize(unified) !== unified || unified === '/')
    throw new Error('dedicated_cgroup_v2_unavailable');
  const path = resolve(cgroupRoot, `.${unified}`);
  if (!path.startsWith(`${resolve(cgroupRoot)}/`)) throw new Error('invalid_cgroup_path');
  const information = await stat(path);
  if (!information.isDirectory()) throw new Error('invalid_cgroup_directory');
  return { path: unified, device: information.dev, inode: information.ino };
}
export async function clockTickFrequency() {
  return new Promise(resolve => execFile('getconf', ['CLK_TCK'], { timeout: 1000, maxBuffer: 1024 }, (error, output) => {
    const value = Number(output?.trim());
    resolve(!error && Number.isSafeInteger(value) && value > 0 && value <= 10000 ? value : null);
  }));
}
export async function sampleGenerator(owner, { procRoot = '/proc', ownPid = process.pid, ticksPerSecond = null } = {}) {
  try {
    const identity = await processIdentity(owner?.pid, procRoot);
    if (identity.birthTick !== owner.birthTick) throw new Error('generator_replaced');
    const [status, network, files, rawStat] = await Promise.all([
      readFile(`${procRoot}/${identity.pid}/status`, 'utf8'), readFile(`${procRoot}/${identity.pid}/net/dev`, 'utf8'),
      import('node:fs/promises').then(fs => fs.readdir(`${procRoot}/${identity.pid}/fd`)),
      readFile(`${procRoot}/${identity.pid}/stat`, 'utf8'),
    ]);
    const rss = status.match(/^VmRSS:\s+(\d+)\s+kB$/m), threads = status.match(/^Threads:\s+(\d+)$/m);
    if (!rss || !threads) throw new Error('generator_resources_unavailable');
    const interfaces = network.trim().split('\n').slice(2).map(line => {
      const [name, values] = line.split(':');
      const fields = values?.trim().split(/\s+/).map(Number);
      if (!/^[\w.-]{1,64}$/.test(name.trim()) || fields?.length !== 16 || fields.some(value => !Number.isSafeInteger(value) || value < 0)) throw new Error('generator_network_unavailable');
      return { interface: name.trim(), receivedBytes: fields[0], sentBytes: fields[8], receiveDrops: fields[3], sendDrops: fields[11] };
    });
    if ((await processIdentity(identity.pid, procRoot)).birthTick !== identity.birthTick) throw new Error('generator_replaced');
    const fields = rawStat.slice(rawStat.lastIndexOf(')') + 2).trim().split(/\s+/);
    const cpuTicks = Number(fields[11]) + Number(fields[12]);
    if (!Number.isSafeInteger(cpuTicks) || cpuTicks < 0) throw new Error('generator_cpu_counter_unavailable');
    return { version: 1, at: new Date().toISOString(), status: 'available', owner: identity, monotonicMs: performance.now(),
      rssBytes: Number(rss[1]) * 1024, threads: Number(threads[1]), fds: files.length,
      cpuUsec: ownPid === identity.pid ? Object.values(process.cpuUsage()).reduce((a, b) => a + b, 0) :
        Number.isSafeInteger(ticksPerSecond) && ticksPerSecond > 0 ? cpuTicks / ticksPerSecond * 1e6 : null,
      cpuScope: ownPid === identity.pid ? 'exact_generator_process' : ticksPerSecond ? 'proc_stat_with_host_CLK_TCK' : 'external_generator_cpu_unavailable',
      cpuTicks, cpuTickFrequency: ticksPerSecond || 'unavailable',
      hostAvailableMemoryBytes: freemem(), schedulingCpus: availableParallelism(), interfaces,
      networkScope: 'network_namespace_aggregate_not_run_attributed', networkCapacity: 'unavailable', headroomVerified: false };
  } catch {
    return { version: 1, at: new Date().toISOString(), status: 'unavailable', reason: 'generator_identity_or_resource_unavailable', headroomVerified: false };
  }
}
export function generatorHeadroom(before, after, summary) {
  const result = { status: 'unqualified', headroomVerified: false, networkHeadroom: 'unavailable',
    limitations: ['network_capacity_and_competing_traffic_unqualified', 'generator_cpu_and_memory_budget_unqualified'],
    schedulingLagMs: summary.latency?.schedulingLagMs || { samples: 0 }, droppedArrivals: summary.arrivals.dropped };
  if (before?.status !== 'available' || after?.status !== 'available' ||
      JSON.stringify(before.owner) !== JSON.stringify(after.owner) || after.monotonicMs <= before.monotonicMs) {
    result.status = 'unavailable'; return result;
  }
  result.rssBytes = after.rssBytes; result.threads = after.threads; result.fds = after.fds;
  result.sampledMs = after.monotonicMs - before.monotonicMs;
  if (Number.isFinite(before.cpuUsec) && Number.isFinite(after.cpuUsec) && after.cpuUsec >= before.cpuUsec)
    result.cpuPercentOneCore = (after.cpuUsec - before.cpuUsec) / ((after.monotonicMs - before.monotonicMs) * 1000) * 100;
  const elapsedSeconds = (after.monotonicMs - before.monotonicMs) / 1000;
  if (JSON.stringify(before.interfaces.map(value => value.interface).sort()) !== JSON.stringify(after.interfaces.map(value => value.interface).sort())) {
    result.status = 'unavailable'; result.limitations.push('network_interfaces_changed'); return result;
  }
  result.networkNamespace = [];
  for (const current of after.interfaces) {
    const previous = before.interfaces.find(value => value.interface === current.interface);
    if (!previous || ['receivedBytes', 'sentBytes', 'receiveDrops', 'sendDrops'].some(key => current[key] < previous[key])) {
      result.status = 'unavailable'; result.limitations.push('network_identity_or_counter_changed'); continue;
    }
    result.networkNamespace.push({ interface: current.interface, receivedBytesPerSecond: (current.receivedBytes - previous.receivedBytes) / elapsedSeconds,
      sentBytesPerSecond: (current.sentBytes - previous.sentBytes) / elapsedSeconds,
      drops: current.receiveDrops - previous.receiveDrops + current.sendDrops - previous.sendDrops });
  }
  if (summary.arrivals.dropped || result.networkNamespace.some(value => value.drops)) result.limitations.push('generator_delivery_or_network_drops');
  return result;
}
export async function buildReceipt(records, { procRoot = '/proc', cgroupRoot = '/sys/fs/cgroup', revision, origin } = {}) {
  if (!Array.isArray(records) || records.length > 16 || !/^[0-9a-f]{40}(\+dirty)?$/.test(revision || '') ||
      !/^https:\/\/localhost:\d{4,5}$/.test(origin || '')) throw new Error('invalid_runtime_export');
  const bootId = (await readFile(`${procRoot}/sys/kernel/random/boot_id`, 'utf8')).trim();
  if (!/^[0-9a-f-]{36}$/.test(bootId)) throw new Error('invalid_boot_identity');
  const containers = [], seen = new Set();
  for (const record of records) {
    if (record.project !== 'schemii-test' || !services.has(record.service) || seen.has(record.service) ||
        !/^[0-9a-f]{64}$/.test(record.id || '') || !/^sha256:[0-9a-f]{64}$/.test(record.image || '') || record.running !== true ||
        !Number.isSafeInteger(record.memoryBytes) || record.memoryBytes < 0 || !Number.isSafeInteger(record.nanoCpus) || record.nanoCpus < 0)
      throw new Error('invalid_container_identity');
    seen.add(record.service);
    const rootProcess = await processIdentity(record.pid, procRoot);
    const cgroup = await cgroupIdentity(record.pid, { procRoot, cgroupRoot });
    if ((await processIdentity(record.pid, procRoot)).birthTick !== rootProcess.birthTick) throw new Error('process_replaced_during_export');
    containers.push({ service: record.service, id: record.id, image: record.image, rootProcess, cgroup,
      configuredMemoryBytes: record.memoryBytes, configuredCpus: record.nanoCpus / 1e9 });
  }
  if (!seen.has('schemii') || !seen.has('ingress') || !seen.has('metadata-postgres')) throw new Error('incomplete_runtime_topology');
  return { version: 1, at: new Date().toISOString(), revision, origin, bootId,
    topology: { host: hostname(), logicalCpus: cpus().length, hostMemoryBytes: totalmem(), generatorPlacement: 'cohosted_unqualified' }, containers };
}
export async function writePrivateJSON(path, data, { exclusive = false } = {}) {
  if (!path || !path.startsWith('/')) throw new Error('absolute_private_path_required');
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const parent = await lstat(dirname(path));
  if (!parent.isDirectory() || parent.isSymbolicLink() || (parent.mode & 0o077)) throw new Error('private_parent_required');
  const temporary = `${path}.${randomUUID()}.tmp`;
  let file;
  try {
    file = await open(temporary, 'wx', 0o600);
    await file.writeFile(`${JSON.stringify(data, null, 2)}\n`); await file.close(); file = null;
    if (exclusive) {
      // An existing receipt may belong to another live deployment/campaign.
      await link(temporary, path);
    } else await rename(temporary, path);
  } finally { await file?.close(); await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
}
export async function readPrivateJSON(path) {
  const file = await open(path, 'r');
  try {
    const information = await file.stat(), entry = await lstat(path);
    if (!information.isFile() || (information.mode & 0o077) || entry.isSymbolicLink() ||
        entry.dev !== information.dev || entry.ino !== information.ino || information.size > 1024 * 1024) throw new Error('invalid_private_receipt');
    return JSON.parse(await file.readFile('utf8'));
  } finally { await file.close(); }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length !== 5 || process.argv[2] !== 'export') throw new Error('Usage: runtime.mjs export PRIVATE_RECEIPT REVISION');
    let input = ''; for await (const chunk of process.stdin) { input += chunk; if (input.length > 65536) throw new Error('runtime_export_budget'); }
    const records = input.trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
    const receipt = await buildReceipt(records, { revision: process.argv[4], origin: `https://localhost:${process.env.SCHEMII_TEST_APP_PORT || '8001'}` });
    await writePrivateJSON(resolve(process.argv[3]), receipt, { exclusive: true });
  } catch (error) { process.stderr.write(`Runtime observation receipt unavailable: ${error.message}\n`); process.exitCode = 1; }
}
