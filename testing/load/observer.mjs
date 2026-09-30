// Read-only host observer. Container control stays in ./start.sh.
import { readFile, appendFile, stat, lstat, unlink, readdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { processIdentity, cgroupIdentity, readPrivateJSON, writePrivateJSON } from './runtime.mjs';
import { credentials } from '../harness/store.mjs';
import { HTTPClient } from './http.mjs';

const finite = value => Number.isFinite(value) && value >= 0;
const failure = error => ['ENOENT', 'ESRCH'].includes(error.code) || error.message === 'process_exited' ? 'process_exited' :
  ['EACCES', 'EPERM'].includes(error.code) ? 'permission_denied' : 'invalid_or_changed_identity';
const counter = text => {
  if (typeof text !== 'string' || !/^\d+$/.test(text.trim()) || !Number.isSafeInteger(Number(text))) throw new Error('invalid_counter');
  return Number(text);
};
async function effectiveBudgets(path, root) {
  let memory = null, cpu = null, levels = 0;
  for (let current = path; current !== resolve(root); current = dirname(current)) {
    if (++levels > 128 || !current.startsWith(`${resolve(root)}/`)) throw new Error('invalid_cgroup_hierarchy');
    const [maximum, quota] = await Promise.all([readFile(`${current}/memory.max`, 'utf8'), readFile(`${current}/cpu.max`, 'utf8')]);
    if (maximum.trim() !== 'max') {
      const limit = counter(maximum); if (!limit) throw new Error('invalid_memory_limit');
      memory = memory === null ? limit : Math.min(memory, limit);
    }
    const [allowed, period] = quota.trim().split(/\s+/);
    if (!counter(period)) throw new Error('invalid_cpu_period');
    if (allowed !== 'max') {
      const limit = counter(allowed) / counter(period); if (!limit) throw new Error('invalid_cpu_quota');
      cpu = cpu === null ? limit : Math.min(cpu, limit);
    }
  }
  return { memoryLimitBytes: memory, quotaCpus: cpu, cgroupLimitLevels: levels };
}
export async function sampleContainer(container, { procRoot = '/proc', cgroupRoot = '/sys/fs/cgroup' } = {}) {
  try {
    const identity = await processIdentity(container.rootProcess?.pid, procRoot);
    if (identity.birthTick !== container.rootProcess.birthTick) throw new Error('process_replaced');
    const cgroup = await cgroupIdentity(identity.pid, { procRoot, cgroupRoot });
    if (JSON.stringify(cgroup) !== JSON.stringify(container.cgroup)) throw new Error('cgroup_replaced');
    const path = resolve(cgroupRoot, `.${cgroup.path}`);
    const [memory, cpu, members, budgets] = await Promise.all([
      readFile(`${path}/memory.current`, 'utf8'), readFile(`${path}/cpu.stat`, 'utf8').then(text => ({ text, observedMonotonicMs: performance.now() })),
      readFile(`${path}/cgroup.procs`, 'utf8'), effectiveBudgets(path, cgroupRoot),
    ]);
    const cpuValues = Object.fromEntries(cpu.text.trim().split('\n').map(line => line.trim().split(/\s+/)));
    const processes = [], exited = [];
    const pids = members.trim() ? members.trim().split(/\s+/).map(counter) : [];
    if (pids.length > 2048 || !pids.includes(identity.pid)) throw new Error('invalid_cgroup_members');
    for (const pid of pids) {
      try {
        const owner = await processIdentity(pid, procRoot);
        const [status, membership] = await Promise.all([readFile(`${procRoot}/${pid}/status`, 'utf8'), cgroupIdentity(pid, { procRoot, cgroupRoot })]);
        if (JSON.stringify(membership) !== JSON.stringify(cgroup)) throw new Error('process_moved');
        const rss = status.match(/^VmRSS:\s+(\d+)\s+kB$/m);
        if (!rss) throw new Error('rss_unavailable');
        const nsPid = status.match(/^NSpid:\s+([\d\s]+)$/m)?.[1].trim().split(/\s+/).map(Number).at(-1);
        const process = { ...owner, rssBytes: counter(rss[1]) * 1024, namespacePid: nsPid ?? null };
        const threads = status.match(/^Threads:\s+(\d+)$/m);
        if (threads) process.threads = counter(threads[1]);
        else process.threadStatus = 'not_available';
        try {
          const files = await readdir(`${procRoot}/${pid}/fd`);
          if (files.some(file => !/^\d+$/.test(file))) throw new Error('invalid_fd_counter');
          process.fds = files.length;
        } catch (error) { process.fdStatus = failure(error) === 'permission_denied' ? 'permission_denied' : 'not_available'; }
        if ((await processIdentity(pid, procRoot)).birthTick !== owner.birthTick) throw new Error('process_replaced');
        processes.push(process);
      } catch (error) {
        if (failure(error) !== 'process_exited') throw error;
        exited.push(pid);
      }
    }
    if ((await processIdentity(identity.pid, procRoot)).birthTick !== identity.birthTick) throw new Error('process_replaced');
    return { status: 'available', memoryCurrentBytes: counter(memory), ...budgets,
      cpuUsageUsec: counter(cpuValues.usage_usec || ''), cpuObservedMonotonicMs: cpu.observedMonotonicMs,
      throttledUsec: counter(cpuValues.throttled_usec || ''),
      rssBytes: processes.reduce((total, process) => total + process.rssBytes, 0), processes, exitedDuringSample: exited };
  } catch (error) {
    let reason = failure(error);
    if (['ENOENT', 'ESRCH'].includes(error.code)) {
      try {
        if ((await processIdentity(container.rootProcess.pid, procRoot)).birthTick === container.rootProcess.birthTick)
          reason = 'resource_counter_unavailable';
      } catch { /* The recorded process really exited. */ }
    }
    return { status: 'unavailable', reason };
  }
}
export function combineObservation(receipt, containers, internal, { previous = null, now = Date.now(), monotonicMs = performance.now() } = {}) {
  const app = containers.schemii;
  const output = { at: new Date(now).toISOString(), monotonicMs, version: 1, status: app?.status === 'available' ? 'available' : 'unavailable',
    qualification: { scope: 'deployment_aggregate', method: 'launcher_PID_birth_cgroup_inode_and_authenticated_admin_snapshot',
      generatorPlacement: receipt.topology.generatorPlacement, generatorHeadroomVerified: false,
      capacityEligible: false, runOwnedBackends: 'unavailable', activeJobs: 'unavailable',
      ingressConnections: 'not_sampled', databaseActivity: 'not_sampled', eventLoopLagMs: 'not_sampled' },
    containers, internal: internal || { status: 'unavailable' } };
  if (app?.status !== 'available') return output;
  if (finite(app.memoryLimitBytes) && app.memoryLimitBytes > 0 && app.memoryCurrentBytes >= app.memoryLimitBytes * 0.85) {
    output.status = 'unavailable'; output.reason = 'cgroup_memory_guard';
    return output; // Existing publicObservation rejects this sample and stops arrivals.
  }
  output.appRssBytes = app.rssBytes;
  for (const metric of ['threads', 'fds'])
    if (app.processes.every(process => finite(process[metric]))) output[metric] = app.processes.reduce((total, process) => total + process[metric], 0);
  if (finite(app.memoryLimitBytes) && app.memoryLimitBytes > 0) output.appMemoryLimitBytes = app.memoryLimitBytes;
  const before = previous?.containers?.schemii;
  const elapsed = app.cpuObservedMonotonicMs - before?.cpuObservedMonotonicMs;
  if (before?.status === 'available' && elapsed > 0 && app.cpuUsageUsec >= before.cpuUsageUsec)
    output.appCpuPercent = (app.cpuUsageUsec - before.cpuUsageUsec) / (elapsed * 1000) * 100;
  output.qualification.cpuPercentBasis = 'one_core_100_percent_monotonic_elapsed';
  if (!internal || internal.version !== 1 || internal.scope !== 'process_aggregate' ||
      !Number.isFinite(Date.parse(internal.at)) || Math.abs(now - Date.parse(internal.at)) > 15000 ||
      !app.processes.some(process => process.namespacePid === internal.processPid)) {
    output.qualification.internalStatus = 'unavailable_or_wrong_process';
    return output;
  }
  output.qualification.internalStatus = 'available';
  if (app.processes.length === 1) output.apiProcesses = 1;
  output.qualification.apiProcessScope = app.processes.length === 1 ? 'single_cgroup_process_matches_endpoint' : 'worker_count_unverified';
  const source = internal.sources?.source;
  if (source?.status === 'available') {
    const lanes = ['ordinary', 'monitoring', 'control'].map(key => source.counts?.[key]);
    if (lanes.every(lane => finite(lane?.connections))) output.sourceConnections = lanes.reduce((total, lane) => total + lane.connections, 0);
    const ordinary = lanes[0];
    if (finite(ordinary?.ordinaryPermits)) output.ordinaryPermits = ordinary.ordinaryPermits;
    if (finite(ordinary?.retainedPermits)) output.retainedPermits = ordinary.retainedPermits;
  }
  const managed = internal.sources?.console, raw = internal.sources?.raw;
  if (managed?.status === 'available' && raw?.status === 'available' &&
      finite(managed.counts?.readSessionHandles) && finite(managed.counts?.manualTransactionHandles) && finite(raw.counts?.registeredSessions)) {
    output.retainedSessions = managed.counts.readSessionHandles + managed.counts.manualTransactionHandles + raw.counts.registeredSessions;
    output.qualification.retainedSessionsScope = 'registered_managed_read_manual_transaction_and_raw_handles';
  }
  if (managed?.status === 'available' && finite(managed.counts?.nativeCursors)) output.openCursors = managed.counts.nativeCursors;
  const metadata = internal.sources?.metadata;
  if (metadata?.status === 'available') {
    if (finite(metadata.counts?.active)) output.metadataActive = metadata.counts.active;
    if (finite(metadata.counts?.rejected)) output.metadataRejected = metadata.counts.rejected;
  }
  return output;
}
export async function collect(receipt, { internal = null, previous = null, procRoot = '/proc', cgroupRoot = '/sys/fs/cgroup', now = Date.now() } = {}) {
  const boot = (await readFile(`${procRoot}/sys/kernel/random/boot_id`, 'utf8')).trim();
  if (receipt.version !== 1 || boot !== receipt.bootId || !Array.isArray(receipt.containers) || receipt.containers.length > 16) throw new Error('runtime_receipt_wrong_boot_or_version');
  const entries = await Promise.all(receipt.containers.map(async container => [container.service, await sampleContainer(container, { procRoot, cgroupRoot })]));
  return combineObservation(receipt, Object.fromEntries(entries), internal, { previous, now });
}
export function parseObserver(args) {
  if (args.length === 2 && args[0] === '--recover') return { recover: resolve(args[1]) };
  const allowed = new Set(['runtime-receipt', 'output', 'credentials-file', 'account', 'seconds']);
  const options = {};
  for (let index = 0; index < args.length; index += 2) {
    const name = args[index]?.slice(2);
    if (!args[index]?.startsWith('--') || !allowed.has(name) || options[name] || !args[index + 1] || args[index + 1].startsWith('--')) throw new Error('invalid_observer_option');
    options[name] = args[index + 1];
  }
  if (!options['runtime-receipt'] || !options.output || (!!options.account !== !!options['credentials-file'])) throw new Error('observer_paths_and_optional_admin_pair_required');
  const seconds = Number(options.seconds || 10800);
  if (!Number.isSafeInteger(seconds) || seconds < 1 || seconds > 10800) throw new Error('observer_duration_limit');
  return { receiptPath: resolve(options['runtime-receipt']), output: resolve(options.output),
    credentialPath: options['credentials-file'] ? resolve(options['credentials-file']) : null, account: options.account || null, seconds };
}
export async function observe(options, { signal, clientFactory = args => new HTTPClient(args), collector = collect } = {}) {
  const receipt = await readPrivateJSON(options.receiptPath);
  if (receipt.version !== 1 || receipt.bootId !== (await readFile('/proc/sys/kernel/random/boot_id', 'utf8')).trim())
    throw new Error('runtime_receipt_wrong_boot_or_version');
  for (const file of [options.output, `${options.output}.jsonl`, `${options.output}.owner.json`, `${options.output}.session-private.json`]) {
    try { await lstat(file); throw new Error('observer_output_already_exists'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  const owner = { ...(await processIdentity(process.pid)), bootId: receipt.bootId, version: 1,
    output: options.output, method: 'read_only_procfs_cgroup_v2', status: 'running', at: new Date().toISOString() };
  await writePrivateJSON(`${options.output}.owner.json`, owner, { exclusive: true });
  // Refuse to adopt another task's logs, outputs or recorded observer owner.
  await writePrivateJSON(options.output, { at: new Date().toISOString(), status: 'initializing' }, { exclusive: true });
  const client = options.credentialPath ? clientFactory({ origin: receipt.origin, timeoutMs: 4000, maxBytes: 65536 }) : null;
  let previous = null, sampleNumber = 0;
  const started = performance.now(), log = `${options.output}.jsonl`;
  try {
    if (client) {
      const account = (await credentials(options.credentialPath)).get(options.account);
      if (!account) throw new Error('observer_account_missing');
      const identity = await client.login(account);
      await writePrivateJSON(`${options.output}.session-private.json`, { version: 1, owner,
        origin: receipt.origin, username: account.username, userId: identity.user.id, cookie: client.cookie }, { exclusive: true });
    }
    while (!signal?.aborted && performance.now() - started < options.seconds * 1000) {
      let internal;
      if (client) {
        try { internal = await client.json('GET', '/api/v1/admin/runtime-observation'); }
        catch { internal = { status: 'unavailable', reason: 'admin_snapshot_request_failed' }; }
      }
      const current = await collector(receipt, { previous, internal });
      // Unknown counters stay absent. This log keeps qualifications stripped by public reports.
      const line = `${JSON.stringify(current)}\n`;
      const size = await stat(log).then(info => info.size).catch(error => { if (error.code !== 'ENOENT') throw error; return 0; });
      if (size + Buffer.byteLength(line) > 20 * 1024 * 1024) throw new Error('observer_evidence_budget');
      await appendFile(log, line, { mode: 0o600 }); await writePrivateJSON(options.output, current);
      previous = current;
      if (current.status !== 'available') throw new Error('observed_app_exited_or_unavailable');
      const nextSample = started + (++sampleNumber * 5000);
      await delay(Math.max(1, Math.min(nextSample - performance.now(), options.seconds * 1000 - (performance.now() - started))), undefined, { signal });
    }
  } catch (error) {
    if (error.name !== 'AbortError') {
      await writePrivateJSON(options.output, { at: new Date().toISOString(), status: 'unavailable', reason: 'observer_stopped_with_failure' });
      throw error;
    }
  } finally {
    let cleanupFailure;
    try {
      await client?.logout();
      await unlink(`${options.output}.session-private.json`).catch(error => { if (error.code !== 'ENOENT') throw error; });
    } catch (error) { cleanupFailure = error; }
    finally {
      client?.close();
      await writePrivateJSON(`${options.output}.owner.json`, { ...owner, stoppedAt: new Date().toISOString(),
        status: cleanupFailure ? 'authentication_cleanup_pending' : 'stopped' });
    }
    if (cleanupFailure) throw new Error('observer_authentication_cleanup_pending');
  }
}
export async function recoverObserver(output, { processReader = processIdentity, clientFactory = args => new HTTPClient(args) } = {}) {
  const owner = await readPrivateJSON(`${output}.owner.json`);
  if (owner.version !== 1 || owner.output !== output || !Number.isSafeInteger(owner.pid) || owner.pid < 1 ||
      !/^\d+$/.test(owner.birthTick || '') || !/^[0-9a-f-]{36}$/.test(owner.bootId || ''))
    throw new Error('unknown_observer_owner');
  const bootId = (await readFile('/proc/sys/kernel/random/boot_id', 'utf8')).trim();
  if (owner.bootId === bootId) {
    try {
      if ((await processReader(owner.pid)).birthTick === owner.birthTick) throw new Error('observer_still_live');
    } catch (error) { if (!['ENOENT', 'ESRCH'].includes(error.code) && error.message !== 'process_exited') throw error; }
  }
  let session;
  try { session = await readPrivateJSON(`${output}.session-private.json`); }
  catch (error) { if (error.code === 'ENOENT') return { status: 'already_cleaned' }; throw error; }
  if (session.version !== 1 || session.owner?.pid !== owner.pid || session.owner?.birthTick !== owner.birthTick ||
      session.owner?.bootId !== owner.bootId || session.owner?.output !== output || typeof session.cookie !== 'string' ||
      !session.cookie || session.cookie.length > 4096 || /[\r\n]/.test(session.cookie) || !session.username || !session.userId)
    throw new Error('unknown_observer_session');
  const client = clientFactory({ origin: session.origin, cookie: session.cookie, timeoutMs: 4000, maxBytes: 65536 });
  try {
    const identity = await client.request({ path: '/api/v1/auth/me' });
    if (identity.status === 200) {
      if (identity.data?.user?.id !== session.userId || identity.data.user.username !== session.username) throw new Error('observer_session_identity_changed');
      await client.logout();
    } else if (identity.status !== 401) throw new Error('observer_session_state_unavailable');
    await unlink(`${output}.session-private.json`);
    await writePrivateJSON(`${output}.owner.json`, { ...owner, status: 'recovered', recoveredAt: new Date().toISOString() });
    return { status: 'recovered', authenticationClosed: true, evidencePreserved: true };
  } finally { client.close(); }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const aborter = new AbortController();
  for (const event of ['SIGINT', 'SIGTERM']) process.once(event, () => aborter.abort());
  try {
    const options = parseObserver(process.argv.slice(2));
    if (options.recover) process.stdout.write(`${JSON.stringify(await recoverObserver(options.recover))}\n`);
    else await observe(options, { signal: aborter.signal });
  }
  catch (error) { process.stderr.write(`Load observer failed: ${error.message}\n`); process.exitCode = 1; }
}
