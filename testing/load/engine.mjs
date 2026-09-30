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
export async function observation(file) {
  if (!file) return null;
  return publicObservation(await privateJSON(file));
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
  signal, abort, onStage = async () => {}, onGenerator = async () => {}, onExecution = async () => {} }) {
  const results = [], before = await observation(observerFile);
  if (!before && !allowUnobserved) throw new Error('observer_required');
  let monitorError = null, timer;
  if (observerFile) timer = setInterval(async () => {
    try {
      const observed = await observation(observerFile);
      if (observed.appRssBytes / observed.appMemoryLimitBytes >= spec.stopRssRatio) throw new Error('memory_stop_threshold');
    } catch (error) { monitorError = error.message; abort(); }
  }, 2000);
  try {
    for (const stage of spec.stages) {
      if (signal.aborted) break;
      const startedAt = new Date().toISOString();
      const options = { spec, stage, receipt, dir, origin, binary, signal, onGenerator, onExecution, onFatal: abort };
      const summary = await (spec.engine === 'k6' ? runK6(options) : runStreams(options));
      const intentional = ['disconnect', 'cancel'].includes(spec.workload);
      const failures = intentional ? (summary.arrivals.failed ? ['unexpected_control_failure'] : []) : assertHealthy(summary, spec.budgets);
      failures.push(...(summary.accountingFailures || []));
      if (summary.arrivals.dropped || summary.arrivals.notOfferedAfterStop) failures.push('generator_underdelivery');
      if (summary.thresholdsPassed === false) failures.push('k6_thresholds');
      const result = { ...stage, startedAt, endedAt: new Date().toISOString(), summary, failures };
      results.push(result); await onStage(results);
      // Capacity knees are a stopping point, not a reason to retry stages.
      if (failures.length || monitorError) break;
    }
    await pause(spec.recoverySeconds * 1000);
    const after = await observation(observerFile);
    const recovery = verifyRecovery(before, after);
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
