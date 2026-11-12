import { spawn } from 'node:child_process';
import { open, readFile, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { Accounting, assertHealthy, verifyRecovery } from './accounting.mjs';
import { HTTPClient } from './http.mjs';
import { CSVOracle, NDJSONOracle, ProtocolFailure } from './protocol.mjs';
import { ordinaryRequest, streamRequest } from './fixtures.mjs';
import { observeChild, stopChild } from './children.mjs';
import { publicObservation, K6_VERSION } from './plan.mjs';
import { privateJSON, writeJSON } from '../harness/store.mjs';
import { processIdentity, sampleGenerator, generatorHeadroom, clockTickFrequency, writePrivateJSON, readPrivateJSON } from './runtime.mjs';
import { sourceEvidence, fresh, readOwnershipConfig, ownershipPaths, ownedRecovery, observeOwnedWork, sampleSource } from './ownership.mjs';

export async function birthTick(pid) {
  try {
    const text = await readFile(`/proc/${pid}/stat`, 'utf8');
    const fields = text.slice(text.lastIndexOf(')') + 2).split(' ');
    return fields[0] === 'Z' ? null : fields[19];
  } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}
export async function requireStopped(owner) {
  if (!owner || !Number.isSafeInteger(owner.pid) || typeof owner.birthTick !== 'string') throw new Error('unknown_process_owner');
  if (await birthTick(owner.pid) === owner.birthTick) throw new Error('owned_process_still_live');
}
export async function observation(file, expectedRunId = null) {
  if (!file) return null;
  const value = await privateJSON(file);
  // Preserve numeric-only external smoke observations, which remain unqualified
  // for owned release. An explicit failure or owned witness must fail closed.
  if ((value.status && value.status !== 'available') || (value.ownership && value.status !== 'available')) throw new Error('observer_unavailable');
  if (expectedRunId && value.ownership?.runId !== expectedRunId) throw new Error('owned_run_observation_mismatch');
  if (value.ownership) {
    sourceEvidence(value.ownership.source, value.ownership.runId);
    if (!fresh(value.ownership.work) || value.ownership.work.runId !== value.ownership.runId || value.ownership.work.status !== 'available') throw new Error('owned_work_observation_unavailable');
  } else if ('ownedBackends' in value || 'activeJobs' in value) throw new Error('unattributed_resource_observation');
  return publicObservation(value);
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

// Scheduling is based on absolute arrival deadlines, independent of response
// completion. A late/full generator drops work rather than queueing it silently.
export async function openLoop({ rate, seconds, maxConcurrent, task, signal,
  clock = () => performance.now(), sleep = pause }) {
  const accounting = new Accounting(), active = new Set(), started = clock();
  const offered = Math.floor(rate * seconds / 60), interval = 60000 / rate;
  for (let index = 0; index < offered; index++) {
    const due = started + index * interval;
    if (signal?.aborted) break;
    if (clock() < due) await sleep(due - clock());
    if (signal?.aborted) break;
    const lag = Math.max(0, clock() - due);
    if (active.size >= maxConcurrent || lag > Math.max(100, interval)) { accounting.offered(false, lag); continue; }
    accounting.offered(true, lag);
    const promise = Promise.resolve().then(() => task(index, accounting)).then(
      () => accounting.journey(true), () => accounting.journey(false)).finally(() => active.delete(promise));
    active.add(promise);
  }
  await Promise.allSettled(active);
  const result = accounting.snapshot();
  // Cancelled schedules still disclose all originally offered demand.
  result.arrivals.notOfferedAfterStop = offered - result.arrivals.offered;
  result.scheduledArrivals = offered;
  return result;
}

export async function runStreams({ spec, stage, receipt, origin, signal, onExecution = async () => {}, onFatal = () => {} }) {
  const clients = receipt.accounts.slice(0, spec.activeIdentities).map(account => new HTTPClient({ origin, cookie: account.cookie,
    timeoutMs: spec.requestDeadlineMs, maxBytes: spec.maxBodyBytes }));
  try {
    return await openLoop({ rate: stage.callsPerMinute, seconds: stage.seconds,
      maxConcurrent: spec.maxConcurrent, signal, task: async (index, accounting) => {
        const slot = index % clients.length, client = clients[slot], account = receipt.accounts[slot];
        client.accounting = accounting;
        const request = streamRequest(spec.workload, account);
        const executions = []; let recording = Promise.resolve();
        const oracle = spec.workload === 'csv' ? new CSVOracle(account.oracles.csv, { maxBytes: spec.maxBodyBytes }) :
          new NDJSONOracle(request.tiles, { maxBytes: spec.maxBodyBytes, onExecution: id => {
            executions.push(id); recording = recording.then(() => onExecution(account.username, id));
            // Observe a failed private ownership write immediately and stop
            // offered work; still join/rethrow it at the response boundary.
            void recording.catch(onFatal);
          } });
        let stream;
        const onRecord = async () => recording;
        try {
          if (spec.workload === 'cancel') {
            // The first execution event is captured incrementally while the
            // response is open; control calls use the same owner's cookie.
            let cancelSent = false;
            const push = oracle.push.bind(oracle);
            oracle.push = chunk => {
              push(chunk);
              if (executions.length && !cancelSent) {
                cancelSent = true;
                stream = onRecord().then(() => client.json('DELETE', `/api/v1/common/query-executions/${executions[0]}`));
              }
            };
          }
          const result = await client.request({ ...request, oracle, signal,
            slowMs: spec.workload === 'slow-reader' ? 100 : 0,
            disconnectAfterBytes: spec.workload === 'disconnect' ? 1024 : 0 });
          if (result.status !== 200) throw new ProtocolFailure(`unexpected_http_${result.status}`);
        } catch (error) {
          await onRecord();
          if (stream) await stream;
          if (spec.workload === 'disconnect' && error.code === 'intentional_disconnect') return;
          // An intentional cancellation may emit an error terminal; its result
          // remains a failed/incomplete HTTP stream in the reconciled totals.
          if (spec.workload === 'cancel' && error.code === 'stream_error' && stream) return;
          if (!/^unexpected_http_(409|429|502|503)$/.test(error.code || '')) onFatal(error);
          throw error;
        } finally { await onRecord(); }
        if (stream) await stream;
      } });
  } finally { for (const client of clients) client.close(); }
}

const count = (metrics, name) => metrics[name]?.count || 0;
export function reconcileK6(raw, stage) {
  const m = raw.metrics, sent = count(m, 'schemii_sent'), dropped = count(m, 'dropped_iterations');
  const summary = { scheduledArrivals: Math.floor(stage.callsPerMinute * stage.seconds / 60),
    http: { sent, admitted: count(m, 'schemii_admitted'), completed: count(m, 'schemii_completed'),
      rejected: count(m, 'schemii_rejected'), failed: count(m, 'schemii_failed'), incomplete: count(m, 'schemii_incomplete') },
    arrivals: { offered: sent + dropped, started: sent, dropped,
      completed: count(m, 'schemii_completed'), failed: sent - count(m, 'schemii_completed') },
    latency: { httpMs: { samples: sent, p95: m.schemii_http_ms?.['p(95)'] ?? null,
      p99: m.schemii_http_ms?.['p(99)'] ?? null, p50: m.schemii_http_ms?.med ?? null, max: m.schemii_http_ms?.max ?? null } },
    generator: { maxVUs: m.vus_max?.max ?? null, dataReceived: count(m, 'data_received'), dataSent: count(m, 'data_sent') } };
  summary.accountingFailures = [];
  if (sent !== count(m, 'http_reqs') || sent !== count(m, 'iterations') ||
      sent !== summary.http.completed + summary.http.rejected + summary.http.failed + summary.http.incomplete ||
      summary.arrivals.offered !== summary.scheduledArrivals) summary.accountingFailures.push('k6_underdelivery_or_unreconciled');
  return summary;
}

export async function runK6({ spec, stage, receipt, dir, origin, binary, signal, onGenerator }) {
  const input = join(dir, 'k6-input-private.json'), result = join(dir, 'k6-result.json');
  const log = await open(join(dir, 'k6-private.log'), 'a', 0o600);
  const script = new URL('./exact-rate.k6.js', import.meta.url).pathname;
  let child, done, abort;
  try {
    await writeJSON(input, { workload: spec.workload, rate: stage.callsPerMinute, seconds: stage.seconds, vus: spec.maxConcurrent, origin,
      accounts: receipt.accounts.slice(0, spec.activeIdentities).map(account => ({ cookie: account.cookie, request: ordinaryRequest(spec.workload, account) })) });
    await onGenerator({ pending: true });
    child = spawn(binary, ['run', '--quiet', '--summary-mode=full', script], {
      env: { ...process.env, SCHEMII_LOAD_INPUT: input, SCHEMII_LOAD_RESULT: result }, detached: true, stdio: ['ignore', log.fd, log.fd] });
    done = observeChild(child);
    let stopping;
    abort = () => { stopping ||= stopChild(child, done); void stopping.catch(() => {}); };
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
    await onGenerator({ pid: child.pid, birthTick: await birthTick(child.pid), pending: false });
    const { code, error } = await done;
    if (error) throw new Error('k6_generator_unavailable');
    await onGenerator({ pending: false, exited: true });
    if (![0, 99].includes(code)) throw new Error('k6_generator_failed');
    const summary = reconcileK6(JSON.parse(await readFile(result, 'utf8')), stage);
    summary.thresholdsPassed = code === 0;
    return summary;
  } finally {
    if (abort) signal.removeEventListener('abort', abort);
    try { if (child) await stopChild(child, done); }
    finally { await log.close(); await unlink(input).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
  }
}

export async function verifyK6(binary) {
  const output = await new Promise((resolve, reject) => {
    const child = spawn(binary, ['version'], { stdio: ['ignore', 'pipe', 'ignore'] }); let value = '';
    child.stdout.on('data', chunk => value += chunk); child.once('error', reject);
    child.once('close', code => code === 0 ? resolve(value) : reject(new Error('k6_unavailable')));
  });
  if (!output.startsWith(`k6 v${K6_VERSION} `)) throw new Error('k6_version_mismatch');
}

export async function executePlan({ spec, receipt, dir, origin, binary, observerFile, allowUnobserved,
  signal, abort, onStage = async () => {}, onGenerator = async () => {}, onExecution = async () => {},
  runStage = options => options.spec.engine === 'k6' ? runK6(options) : runStreams(options),
  generatorSampler = sampleGenerator, tickFrequencyReader = clockTickFrequency, sleep = pause }) {
  const results = [], before = await observation(observerFile);
  let monitorError = null, timer;
  let ownershipConfig = null;
  const paths = ownershipPaths(dir);
  try { ownershipConfig = await readOwnershipConfig(paths.config); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (ownershipConfig && (ownershipConfig.runId !== receipt.tag || ownershipConfig.workload !== spec.workload ||
      ownershipConfig.fixtureReceipt !== join(dir, 'fixtures-private.json'))) throw new Error('ownership_run_mismatch');
  if (ownershipConfig) await observation(observerFile, ownershipConfig.runId);
  if (!before && !allowUnobserved) throw new Error('observer_required');
  if (observerFile) timer = setInterval(async () => {
    try {
      const observed = await observation(observerFile, ownershipConfig?.runId);
      if (observed.appRssBytes / observed.appMemoryLimitBytes >= spec.stopRssRatio) throw new Error('memory_stop_threshold');
    } catch (error) { monitorError = error.message; abort(); }
  }, 2000);
  try {
    for (const stage of spec.stages) {
      if (signal.aborted) break;
      const startedAt = new Date().toISOString();
      const ticksPerSecond = await tickFrequencyReader();
      const generatorOwner = spec.engine === 'incremental-node' ? await processIdentity(process.pid) : null;
      let generatorBefore = generatorOwner ? await generatorSampler(generatorOwner, { ticksPerSecond }) : null, generatorAfter = generatorBefore;
      let sampleCount = generatorBefore?.status === 'available' ? 1 : 0, peakRssBytes = generatorBefore?.rssBytes || 0;
      let currentGenerator = generatorOwner, resourceFailure = null, samplerBusy = false;
      if (generatorBefore && generatorBefore.status !== 'available') { resourceFailure = 'generator_observation_unavailable'; abort(); }
      const generatorFile = join(dir, 'generator-private.json');
      const sample = async () => {
        if (!currentGenerator || samplerBusy) return;
        const sampledOwner = currentGenerator;
        samplerBusy = true;
        try {
          const value = await generatorSampler(sampledOwner, { ticksPerSecond });
          if (currentGenerator !== sampledOwner) return; // A joined generator ended during this sample.
          await writePrivateJSON(generatorFile, { ...value, runId: receipt.tag });
          if (value.status !== 'available') { resourceFailure = 'generator_observation_unavailable'; abort(); }
          else { generatorBefore ||= value; generatorAfter = value; sampleCount++; peakRssBytes = Math.max(peakRssBytes, value.rssBytes); }
        } catch { resourceFailure = 'generator_observation_unavailable'; abort(); }
        finally { samplerBusy = false; }
      };
      const resources = setInterval(() => { void sample(); }, 1000);
      const options = { spec, stage, receipt, dir, origin, binary, signal,
        onGenerator: async owner => {
          await onGenerator(owner);
          if (owner.exited) currentGenerator = null;
          else if (owner.pid && owner.birthTick) { currentGenerator = owner; await sample(); }
        }, onExecution, onFatal: abort };
      let summary;
      try { await sample(); summary = await runStage(options); }
      finally { clearInterval(resources); while (samplerBusy) await pause(1); }
      if (generatorOwner) { generatorAfter = await generatorSampler(generatorOwner, { ticksPerSecond }); sampleCount++; peakRssBytes = Math.max(peakRssBytes, generatorAfter.rssBytes || 0); }
      summary.generatorObservation = generatorHeadroom(generatorBefore, generatorAfter, summary);
      summary.generatorObservation.sampleCount = sampleCount;
      summary.generatorObservation.peakSampledRssBytes = peakRssBytes;
      const intentional = ['disconnect', 'cancel'].includes(spec.workload);
      const failures = intentional ? (summary.arrivals.failed ? ['unexpected_control_failure'] : []) : assertHealthy(summary, spec.budgets);
      failures.push(...(summary.accountingFailures || []));
      if (summary.arrivals.dropped || summary.arrivals.notOfferedAfterStop) failures.push('generator_underdelivery');
      if (summary.thresholdsPassed === false) failures.push('k6_thresholds');
      if (resourceFailure) failures.push(resourceFailure);
      const result = { ...stage, startedAt, endedAt: new Date().toISOString(), summary, failures };
      results.push(result); await onStage(results);
      // Capacity knees are a stopping point, not a reason to retry stages.
      if (failures.length || monitorError) break;
    }
    await sleep(spec.recoverySeconds * 1000);
    let probe = null, ownedWork = null;
    if (ownershipConfig?.recovery) {
      try {
        probe = await ownedRecovery({ receipt, config: { ...ownershipConfig.recovery, runId: receipt.tag }, origin,
          journal: paths.recoveryJournal, onExecution,
          monitor: () => sampleSource(ownershipConfig.sourceConfig, ownershipConfig.sourceOutput) });
        ownedWork = await observeOwnedWork(await readPrivateJSON(ownershipConfig.fixtureReceipt), spec.workload, { origin });
      } catch { probe = { verified: false }; }
    }
    const after = await observation(observerFile, ownershipConfig?.runId);
    const recovery = verifyRecovery(before, after, { requireOwnedProbe: true, probe, ownedWork });
    const failures = [...new Set(results.flatMap(result => result.failures))];
    if (monitorError) failures.push(monitorError);
    if (signal.aborted) failures.push('run_stopped');
    if (!recovery.verified) failures.push(...recovery.failures);
    return { stages: results, observations: { before, after }, recovery, failures,
      // The foundation has not proven topology/budgets, generator headroom,
      // control recovery or independent campaign cleanup acceptance.
      capacityEligible: false, capacityLimitations: ['independent_campaign_acceptance_pending'],
      conclusion: 'No active-user sizing is inferred from this harness report.' };
  } finally { clearInterval(timer); }
}
