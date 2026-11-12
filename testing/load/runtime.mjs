// Launcher-only resource receipt. Never invoke Docker from an observer.
import { readFile, stat, lstat, open, rename, unlink, mkdir, link } from 'node:fs/promises';
import { dirname, resolve, posix, basename } from 'node:path';
import { hostname, cpus, totalmem, freemem, availableParallelism } from 'node:os';
import { randomUUID, createHash } from 'node:crypto';
import { constants as fsConstants } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';

const services = new Set(['schemii', 'ingress', 'metadata-postgres', 'demo-postgres', 'qa-postgres', 'ai-prototype-runtime']);
export function processRole(command) {
  const argv = command.split('\0').filter(Boolean), name = basename(argv[0] || '');
  const exactFixtureArgv = !command.replace(/\0$/, '').split('\0').includes('');
  const fixtureArgs = ['pressure_app:observed_application', '--factory', '--app-dir', '/qualification', '--host', '0.0.0.0', '--port', '8000', '--workers', '1', '--no-access-log'];
  if (exactFixtureArgv && argv[0] === '/sbin/docker-init' && argv[1] === '--' && basename(argv[2] || '') === 'uvicorn' &&
      JSON.stringify(argv.slice(3)) === JSON.stringify(fixtureArgs)) return 'container_init';
  const python = /^python(3(\.\d+)?)?$/.test(name);
  const target = name === 'uvicorn' ? 1 : python && argv[1] === '-m' && argv[2] === 'uvicorn' ? 3 :
    python && basename(argv[1] || '') === 'uvicorn' ? 2 : null;
  if (exactFixtureArgv && target !== null && JSON.stringify(argv.slice(target)) === JSON.stringify(fixtureArgs)) return 'api';
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
const fixtureServices = ['schemii', 'ingress', 'metadata', 'source'];
const fixtureAssets = ['start.sh', 'dev/pressure/launcher.sh', 'dev/pressure/controller.py', 'dev/pressure/probe.py',
  'dev/pressure/app.py', 'dev/pressure/compose.yaml', 'dev/ingress/nginx.conf', 'dev/schemii.toml', 'testing/load/runtime.mjs'];
const moduleRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const canonical = value => JSON.stringify(value && typeof value === 'object' ?
  Array.isArray(value) ? value.map(item => JSON.parse(canonical(item))) :
    Object.fromEntries(Object.keys(value).sort().map(key => [key, JSON.parse(canonical(value[key]))])) : value);
const digest = value => createHash('sha256').update(value).digest('hex');
const fixtureProfileDigests = {"pressure-v1": "4be8ad3e008fca63d10b2d80593d798b39ca630c2312c09dd41f25262c1ffe80", "representative-load-v1": "ba07302c0307538fcc3146fa7f4dba51d436297e264ad27987c89f7082379d82"};
async function boundedText(path, maximum = 8192) {
  const file = await open(path, fsConstants.O_RDONLY | fsConstants.O_NONBLOCK);
  try {
    const data = Buffer.alloc(maximum + 1), { bytesRead } = await file.read(data, 0, data.length, 0);
    if (bytesRead > maximum) throw new Error('fixture_read_bound');
    return data.subarray(0, bytesRead).toString('utf8');
  } finally { await file.close(); }
}
async function fixtureApiProcesses(container, namespacePid, options) {
  const { procRoot, cgroupRoot } = options;
  const members = (await boundedText(`${cgroupRoot}${container.cgroup.path}/cgroup.procs`)).trim().split(/\s+/).map(Number);
  if (members.length !== 2 || new Set(members).size !== 2 || !members.includes(container.rootProcess.pid) ||
      members.some(pid => !Number.isSafeInteger(pid) || pid < 1)) throw new Error('fixture_api_topology_unknown');
  let apiProcess, initProcess;
  for (const pid of members) {
    const identity = await processIdentity(pid, procRoot);
    if (canonical(await cgroupIdentity(pid, options)) !== canonical(container.cgroup)) throw new Error('fixture_process_cgroup_changed');
    const status = await boundedText(`${procRoot}/${pid}/status`, 16384);
    const ns = status.match(/^NSpid:\s+([\d\s]+)$/m)?.[1].trim().split(/\s+/).map(Number).at(-1);
    const role = processRole(await boundedText(`${procRoot}/${pid}/cmdline`));
    if (ns === namespacePid && role === 'api' && !apiProcess) apiProcess = { ...identity, namespacePid: ns };
    else if (pid === container.rootProcess.pid && ns === 1 && role === 'container_init' && !initProcess)
      initProcess = { ...identity, namespacePid: ns };
    else throw new Error('fixture_api_member_unknown');
    if ((await processIdentity(pid, procRoot)).birthTick !== identity.birthTick) throw new Error('fixture_process_reused');
  }
  if (!apiProcess || !initProcess) throw new Error('fixture_api_identity_missing');
  return { apiProcess, initProcess };
}
export async function recheckFixtureReceipt(receipt, { procRoot = '/proc', cgroupRoot = '/sys/fs/cgroup' } = {}) {
  if ((await boundedText(`${procRoot}/sys/kernel/random/boot_id`)).trim() !== receipt.bootId) throw new Error('fixture_boot_changed');
  for (const container of receipt.containers) {
    for (const identity of [container.rootProcess, container.apiProcess, container.initProcess].filter(Boolean)) {
      if ((await processIdentity(identity.pid, procRoot)).birthTick !== identity.birthTick) throw new Error('fixture_process_reused');
      if (canonical(await cgroupIdentity(identity.pid, { procRoot, cgroupRoot })) !== canonical(container.cgroup))
        throw new Error('fixture_cgroup_changed');
    }
    if (container.service === 'schemii') {
      const current = await fixtureApiProcesses(container, container.apiProcess.namespacePid, { procRoot, cgroupRoot });
      if (canonical(current) !== canonical({ apiProcess: container.apiProcess, initProcess: container.initProcess }))
        throw new Error('fixture_api_identity_changed');
    }
  }
}
export async function buildFixtureReceipt(journal, { root, revision, procRoot = '/proc', cgroupRoot = '/sys/fs/cgroup' } = {}) {
  const intent = journal?.osExport, records = intent?.records, profile = journal?.profile;
  if (journal?.version !== 1 || journal.stage !== 'os-export-intent' || journal.root !== root || journal.directory !== `${journal.primary}/.schemii/metadata-pressure/${journal.run}` ||
      !/^pressure_[0-9a-f]{32}$/.test(journal.run || '') || journal.project !== `schemii-pressure-${journal.run.slice(9)}` ||
      !/^[0-9a-f]{64}$/.test(journal.nonce || '') || intent?.version !== 1 || intent.status !== 'pending' ||
      intent.nonce !== journal.nonce || intent.output !== 'os-runtime.json' || intent.revision !== revision || !/^[0-9a-f]{40}$/.test(revision || '') ||
      !Object.hasOwn(fixtureProfileDigests, profile?.name || '') || profile.version !== 1 || digest(canonical(profile)) !== fixtureProfileDigests[profile.name] ||
      canonical(profile) !== canonical(intent.profile) || canonical(profile) !== canonical(journal.runtimeReceipt?.profile) ||
      journal.runtimeReceipt?.run !== journal.run || !Array.isArray(records) || records.length !== 4)
    throw new Error('fixture_export_provenance');
  const bootId = (await boundedText(`${procRoot}/sys/kernel/random/boot_id`)).trim();
  if (!/^[0-9a-f-]{36}$/.test(bootId)) throw new Error('invalid_boot_identity');
  const containers = [], seen = new Set(), pids = new Set(), groups = new Set();
  const recordKeys = ['id', 'image', 'memoryBytes', 'nanoCpus', 'pid', 'pids', 'project', 'running', 'service', 'startedAt'].sort().join(',');
  for (const record of records) {
    const original = journal.containers?.[record.service], allocation = profile.allocations?.[record.service];
    if (Object.keys(record).sort().join(',') !== recordKeys || record.project !== journal.project ||
        !fixtureServices.includes(record.service) || seen.has(record.service) || pids.has(record.pid) ||
        !/^[0-9a-f]{64}$/.test(record.id || '') || record.id !== original?.id || record.image !== original.image ||
        original.state !== 'running' || record.running !== true || record.startedAt !== original.startedAt ||
        record.memoryBytes !== allocation?.memoryBytes || record.nanoCpus !== allocation?.nanoCpus || record.pids !== allocation?.pids ||
        record.memoryBytes !== original.memory || record.nanoCpus !== original.nanoCpus || record.pids !== original.pids)
      throw new Error('fixture_container_provenance');
    seen.add(record.service); pids.add(record.pid);
    const rootProcess = await processIdentity(record.pid, procRoot), cgroup = await cgroupIdentity(record.pid, { procRoot, cgroupRoot });
    if (groups.has(canonical(cgroup))) throw new Error('fixture_shared_cgroup');
    groups.add(canonical(cgroup));
    const container = { service: record.service, id: record.id, image: record.image, rootProcess, cgroup,
      configuredMemoryBytes: record.memoryBytes, configuredCpus: record.nanoCpus / 1e9, configuredPids: record.pids };
    if (record.service === 'schemii') {
      const observed = journal.runtimeReceipt.application;
      if (observed?.containerId !== record.id || observed.containerStartedAt !== record.startedAt ||
          observed.observation?.run !== journal.run || observed.observation?.stage !== 'running' ||
          observed.observation?.startupComplete !== true || observed.observation?.observationFailed !== false ||
          !Number.isSafeInteger(observed.observation.process?.pid) || observed.observation.process.pid < 2)
        throw new Error('fixture_api_provenance');
      Object.assign(container, await fixtureApiProcesses(container, observed.observation.process.pid, { procRoot, cgroupRoot }));
      if (container.apiProcess.birthTick !== observed.observation.process.startTicks || observed.observation.process.uid !== 10001 ||
          !/^[0-9a-f]{32}$/.test(observed.observation.process.incarnation || '')) throw new Error('fixture_api_birth_mismatch');
    }
    containers.push(container);
  }
  if (fixtureServices.some(service => !seen.has(service))) throw new Error('fixture_topology_incomplete');
  const receipt = { version: 1, at: new Date().toISOString(), revision, origin: 'https://localhost:8001', bootId,
    topology: { host: hostname(), logicalCpus: cpus().length, hostMemoryBytes: totalmem(), generatorPlacement: 'cohosted_unqualified',
      apiProcesses: 1, apiProcessScope: 'exact_namespace_API_and_distinct_container_init', instrumentationOverheadMeasured: false },
    fixture: { version: 1, run: journal.run, nonce: journal.nonce, project: journal.project, profile,
      owner: journal.owner, sourceAssetsSha256: digest(canonical(journal.assets)), stockCapacityEligible: false }, containers };
  await recheckFixtureReceipt(receipt, { procRoot, cgroupRoot });
  return receipt;
}
async function gitOutput(root, args) {
  return new Promise((done, reject) => execFile('git', ['--no-optional-locks', '-C', root, ...args],
    { timeout: 2000, maxBuffer: 16384, env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_'))) },
    (error, output) => error ? reject(new Error('fixture_source_unavailable')) : done(output.trim())));
}
export async function fixtureLauncherFence(journal, root, primary, common, { procRoot = '/proc', parentPid = process.ppid, leaseMode = process.env.SCHEMII_QA_LEASE_MODE } = {}) {
  const owner = journal.owner;
  if (!owner || owner.pid !== parentPid || journal.root !== root || journal.primary !== primary ||
      (await processIdentity(owner.pid, procRoot)).birthTick !== owner.start || leaseMode !== 'exclusive')
    throw new Error('fixture_launcher_owner_required');
  const command = (await boundedText(`${procRoot}/${owner.pid}/cmdline`)).split('\0').filter(Boolean);
  if (!/^python(3(\.\d+)?)?$/.test(basename(command[0] || '')) || command[1] !== `${root}/dev/pressure/controller.py` ||
      canonical(command.slice(2, 9)) !== canonical(['--root', root, '--primary', primary, '--common', common, '--']) ||
      !['--qualify-metadata-pressure', '--hold-metadata-pressure', '--hold-load-fixture'].includes(command[9]) || command.length > 11)
    throw new Error('fixture_launcher_command_required');
  if ((command[9] === '--hold-load-fixture') !== (journal.profile?.name === 'representative-load-v1') ||
      (command[9] !== '--qualify-metadata-pressure' && (command.length !== 11 || !/^\d+$/.test(command[10]) ||
        Number(command[10]) < 60 || Number(command[10]) > 3600)) ||
      (command[9] === '--qualify-metadata-pressure' && command.length === 11 && command[10] !== journal.directory))
    throw new Error('fixture_launcher_profile_command');
  for (const [fd, name] of [[3, 'qa-deployment.lock'], [4, 'qa-startup.lock']]) {
    const expected = await lstat(`${common}/${name}`), actual = await stat(`${procRoot}/self/fd/${fd}`);
    const info = await boundedText(`${procRoot}/self/fdinfo/${fd}`);
    if (!expected.isFile() || expected.isSymbolicLink() || expected.dev !== actual.dev || expected.ino !== actual.ino ||
        !/^lock:\s+\d+:\s+FLOCK\s+ADVISORY\s+WRITE\b/m.test(info)) throw new Error('fixture_exclusive_lease_required');
  }
  if (canonical(Object.keys(journal.assets || {}).sort()) !== canonical([...fixtureAssets].sort())) throw new Error('fixture_source_inventory');
  for (const asset of fixtureAssets) {
    const entry = await lstat(`${root}/${asset}`);
    if (!entry.isFile() || entry.isSymbolicLink() || digest(await boundedText(`${root}/${asset}`, 262144)) !== journal.assets[asset])
      throw new Error('fixture_source_changed');
  }
}
export async function exportFixtureReceipt(directory) {
  const root = moduleRoot, common = await gitOutput(root, ['rev-parse', '--path-format=absolute', '--git-common-dir']);
  const primary = (await gitOutput(root, ['worktree', 'list', '--porcelain'])).split('\n')[0].slice('worktree '.length);
  if (directory !== resolve(directory) || dirname(directory) !== `${primary}/.schemii/metadata-pressure` ||
      !/^pressure_[0-9a-f]{32}$/.test(basename(directory))) throw new Error('fixture_directory_required');
  const info = await lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || (info.mode & 0o777) !== 0o700 || info.uid !== process.getuid()) throw new Error('fixture_private_directory');
  const journal = await readPrivateJSON(`${directory}/journal.json`);
  await fixtureLauncherFence(journal, root, primary, common);
  const revision = await gitOutput(root, ['rev-parse', 'HEAD']);
  if (await gitOutput(root, ['status', '--porcelain', '--untracked-files=normal'])) throw new Error('fixture_clean_source_required');
  const receipt = await buildFixtureReceipt(journal, { root, revision });
  const latest = await readPrivateJSON(`${directory}/journal.json`);
  await fixtureLauncherFence(latest, root, primary, common);
  if (latest.stage !== 'os-export-intent' || canonical(latest.osExport) !== canonical(journal.osExport) ||
      canonical(latest.profile) !== canonical(journal.profile) || canonical(latest.runtimeReceipt) !== canonical(journal.runtimeReceipt))
    throw new Error('fixture_journal_changed');
  await recheckFixtureReceipt(receipt);
  await writePrivateJSON(`${directory}/os-runtime.json`, receipt, { exclusive: true });
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
  const before = await lstat(path);
  if (!before.isFile() || before.isSymbolicLink() || (before.mode & 0o077) || before.uid !== process.getuid() || before.size > 1024 * 1024)
    throw new Error('invalid_private_receipt');
  const file = await open(path, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW | fsConstants.O_NONBLOCK);
  try {
    const information = await file.stat();
    if (!information.isFile() || information.dev !== before.dev || information.ino !== before.ino ||
        information.uid !== process.getuid() || (information.mode & 0o077) || information.size > 1024 * 1024) throw new Error('invalid_private_receipt');
    const raw = Buffer.alloc(1024 * 1024 + 1), { bytesRead } = await file.read(raw, 0, raw.length, 0);
    if (bytesRead > 1024 * 1024) throw new Error('invalid_private_receipt');
    return JSON.parse(raw.subarray(0, bytesRead).toString('utf8'));
  } finally { await file.close(); }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length === 4 && process.argv[2] === 'export-fixture') {
      await exportFixtureReceipt(process.argv[3]);
      process.stdout.write('{"status":"exported"}\n');
    } else {
    if (process.argv.length !== 5 || process.argv[2] !== 'export') throw new Error('Usage: runtime.mjs export PRIVATE_RECEIPT REVISION');
    let input = ''; for await (const chunk of process.stdin) { input += chunk; if (input.length > 65536) throw new Error('runtime_export_budget'); }
    const records = input.trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
    const receipt = await buildReceipt(records, { revision: process.argv[4], origin: `https://localhost:${process.env.SCHEMII_TEST_APP_PORT || '8001'}` });
    await writePrivateJSON(resolve(process.argv[3]), receipt, { exclusive: true });
    }
  } catch (error) { process.stderr.write(process.argv[2] === 'export-fixture' ? 'Fixture runtime observation receipt unavailable\n' : `Runtime observation receipt unavailable: ${error.message}\n`); process.exitCode = 1; }
}
