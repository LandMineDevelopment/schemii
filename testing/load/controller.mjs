import net from 'node:net';
import { chmod } from 'node:fs/promises';
import { join } from 'node:path';
import { timingSafeEqual } from 'node:crypto';
import { isDeepStrictEqual as same } from 'node:util';
import { root, privateJSON, writeJSON, credentials } from '../harness/store.mjs';
import { startDeployment, sourceIdentity } from '../harness/deployment.mjs';
import { releaseAccounts } from '../harness/leases.mjs';
import { prepareFixtures, cleanupFixtures, prepareOwnedFixtures, heldFixture, fixtureCommand } from './fixtures.mjs';
import { executePlan, verifyK6, observation, requireStopped } from './engine.mjs';
import { loadPath } from './cli.mjs';
import { disposeRuntime, runtimeSocket, awaitControllerHandoff } from './children.mjs';
import { openJournal, journalBudget, writeSnapshot } from './journal.mjs';
import { sampleSource } from './ownership.mjs';
import { readPrivateJSON, processIdentity } from './runtime.mjs';

process.umask(0o077);
const dir = loadPath(process.argv[2]), run = await privateJSON(join(dir, 'manifest-private.json'));
const config = await privateJSON(join(dir, 'config-private.json'));
const control = await privateJSON(join(dir, 'control-private.json'));
const launch = await privateJSON(join(dir, 'controller-launch.json'));
if (launch.version !== 1 || launch.runId !== run.id || control.socket !== runtimeSocket(run.id)) throw new Error('unknown_controller_launch');
// No deployment or fixture write may escape an unrecorded controller launch.
await awaitControllerHandoff(dir, run.id);
const credentialMap = config.fixtureAuthority ? null : await credentials(config.credentialPath);
let receipt, task, stopping = false, server, fixture, attachment, heartbeat, journal, heartbeatPending = false;
const aborter = new AbortController();
let manifestWrites = Promise.resolve(), receiptWrites = Promise.resolve();
const save = () => manifestWrites = manifestWrites.then(() => writeJSON(join(dir, 'manifest-private.json'), run));
const saveReceipt = () => receiptWrites = receiptWrites.then(() => receipt?.kind === 'disposable' ? writeSnapshot(join(dir, 'fixtures-private.json'), receipt) : writeJSON(join(dir, 'fixtures-private.json'), receipt));
const safe = error => /^[a-z0-9_-]{1,100}$/.test(error.message) ? error.message : 'load_operation_failed';
const close = async () => {
  clearInterval(heartbeat);
  if (server) await new Promise(resolve => server.close(resolve));
  await disposeRuntime(run.id, launch.runtime);
  if (journal) await journal.close();
  if (attachment) await fixtureCommand(fixture, 'detach', attachment);
};
async function cleanup() {
  if (stopping) throw new Error('cleanup_already_running');
  stopping = true; aborter.abort(); if (task) await task;
  if (run.generatorOwner?.pending) throw new Error('unresolved_generator_launch');
  if (run.generatorOwner?.pid) await requireStopped(run.generatorOwner);
  if (config.fixtureAuthority) await heldFixture(config.fixtureAuthority);
  const result = await cleanupFixtures({ dir, credentialMap, origin: config.origin });
  if (result.objectsRemaining) throw new Error('owned_objects_remaining');
  await releaseAccounts({ runId: run.id, root }); run.status = 'cleaned'; run.cleanup = result; await save();
  return { run: run.id, status: run.status, ...result, evidencePreserved: true };
}
function authorize(token) {
  if (typeof token !== 'string' || !/^[a-f0-9]{64}$/.test(token) || !timingSafeEqual(Buffer.from(token), Buffer.from(control.token))) throw new Error('invalid_controller_token');
}
async function dispatch(command) {
  if (command === 'cleanup') return cleanup();
  if (command !== 'run') throw new Error('invalid_controller_command');
  if (run.status !== 'ready' || task) throw new Error('run_not_ready');
  if (sourceIdentity(root).fingerprint !== run.source.fingerprint) throw new Error('source_changed_prepare_new_run');
  // The collector starts after startDeployment publishes the ready runtime.
  // Qualify that fresh observation before changing state or offering work.
  if (config.observerFile) {
    try {
      const observed = await observation(config.observerFile);
      if (fixture) {
        const privateObservation = await readPrivateJSON(config.observerFile);
        if (privateObservation.runtimeReceiptSha256 !== fixture.osRuntimeReceiptObjectSha256) throw new Error('fixture_observer_runtime_changed');
      }
      if (observed.appRssBytes / observed.appMemoryLimitBytes >= run.plan.stopRssRatio) throw new Error('memory_stop_threshold');
    }
    catch (error) { if (error.code === 'ENOENT') throw new Error('observer_unavailable'); throw error; }
  } else if (!config.allowUnobserved) throw new Error('observer_required');
  // Another dispatch may have qualified while the observer file was read.
  if (run.status !== 'ready' || task) throw new Error('run_not_ready');
  if (fixture && Date.parse(fixture.expiresAt) - Date.now() < (run.plan.stages.reduce((sum, stage) => sum + stage.seconds, 0) + (run.plan.idleSeconds || 0) + run.plan.recoverySeconds + 60) * 1000) throw new Error('fixture_hold_too_short');
  run.status = 'running'; await save();
  task = executePlan({ spec: run.plan, receipt, dir, origin: config.origin, binary: config.binary,
    expectedRuntimeReceiptSha256: fixture?.osRuntimeReceiptObjectSha256,
    observerFile: config.observerFile, allowUnobserved: config.allowUnobserved, signal: aborter.signal, abort: () => aborter.abort(),
    onStage: async stages => { run.result = { stages }; await save(); },
    onGenerator: async owner => { run.generatorOwner = { ...run.generatorOwner, ...owner }; await save(); },
    saveReceipt,
    onIntent: async value => {
      if (!journal) throw new Error('durable_journey_journal_required');
      return journal.append({ type: 'intent', ...value, status: 'pending', at: new Date().toISOString() });
    },
    finishIntent: async (intentSequence, value) => { await journal.append({ type: 'intent-update', intentSequence, ...value, at: new Date().toISOString() }); },
    onExecution: async (username, id, metadata = {}) => {
      if (journal) {
        await journal.append({ type: 'execution', action: metadata.terminalAt ? 'terminal' : 'created', username, id, ...metadata, at: new Date().toISOString() });
        return;
      }
      receipt.executions ||= [];
      if (!receipt.executions.some(item => item.id === id)) { receipt.executions.push({ username, id }); await saveReceipt(); }
    } }).then(async result => { run.result = result; run.status = result.failures.length ? 'failed' : 'completed'; await save(); },
      async error => { run.status = 'failed'; run.result ||= {}; run.result.failures = [safe(error)]; await save(); });
  return { run: run.id, status: 'running', work: 'opt-in generator scheduled; inspect status/report before cleanup' };
}
try {
  if (config.fixtureAuthority) {
    if (run.plan.fixtureMode !== 'disposable' || run.plan.engine !== 'incremental-node' || JSON.stringify(config.journalBudget) !== JSON.stringify(journalBudget(run.plan))) throw new Error('constrained_fixture_consumer_required');
    fixture = await heldFixture(config.fixtureAuthority);
    attachment = (await fixtureCommand(fixture, 'attach')).attachmentFile;
    const attached = await readSnapshot(attachment, 1024 * 1024), identity = await processIdentity(process.pid);
    if (attached.status !== 'attached' || attached.consumer.pid !== identity.pid || attached.consumer.start !== identity.birthTick || attached.consumer.uid !== process.getuid()) throw new Error('fixture_consumer_binding_changed');
    await writeJSON(join(dir, 'fixture-attachment-private.json'), { authorityFile: config.fixtureAuthority, attachmentFile: attachment, run: fixture.run, nonce: fixture.nonce, consumer: attached.consumer });
    heartbeat = setInterval(() => {
      if (heartbeatPending) return; heartbeatPending = true;
      void fixtureCommand(fixture, 'heartbeat', attachment).then(() => heldFixture(config.fixtureAuthority)).then(current => {
        for (const field of ['run', 'nonce', 'owner', 'profile', 'runtimeReceipt', 'osRuntimeReceiptSha256', 'osRuntimeReceiptObjectSha256', 'sourceWitnessConfig', 'credentialsFile', 'application', 'source'])
          if (!same(current[field], fixture[field])) throw new Error('held_fixture_identity_changed');
        fixture = current;
      })
        .catch(() => { run.status = 'fixture-observation-lost'; aborter.abort(); }).finally(() => { heartbeatPending = false; });
    }, 3000);
    journal = await openJournal(join(dir, 'journey-journal'), { tag: fixture.tag, budget: config.journalBudget });
    receipt = await prepareOwnedFixtures({ dir, spec: run.plan, authority: fixture });
    receipt.executionJournal = join(dir, 'journey-journal'); await saveReceipt();
    const actor = receipt.recoveryAccount || receipt.accounts[0];
    const ownership = { version: 1, runId: fixture.tag, workload: run.plan.workload, fixtureReceipt: join(dir, 'fixtures-private.json'),
      sourceConfig: fixture.sourceWitnessConfig, sourceOutput: join(dir, 'source-observation-private.json'),
      recovery: actor.workspace ? { username: actor.username, workspaceId: actor.workspace.id, consoleId: `con_${'0'.repeat(32)}`,
        workspaceRevision: actor.workspace.revision, settingsRevision: actor.settings.revision } : null };
    await writeJSON(join(dir, 'ownership-private.json'), ownership);
    await sampleSource(ownership.sourceConfig, ownership.sourceOutput);
    run.deployment = { fixtureOnly: true, profile: fixture.profile, instrumentation: fixture.instrumentation, stockCapacityEligible: false };
  } else {
    if (run.plan.engine === 'k6') await verifyK6(config.binary);
    run.deployment = await startDeployment({ root, runDir: dir });
    receipt = await prepareFixtures({ dir, accounts: run.accounts, credentialMap, stateDir: config.stateDir,
      kind: run.plan.workload === 'cheap-read' ? 'cheap' : 'reports', origin: config.origin });
  }
  run.status = 'ready';
} catch (error) { run.status = 'blocked'; run.result = { failures: [safe(error)] }; await save(); }
server = net.createServer(socket => {
  let input = ''; socket.setTimeout(60000, () => socket.destroy());
  socket.on('data', chunk => {
    input += chunk;
    if (input.length > 4096) { socket.destroy(); return; }
    if (!input.includes('\n')) return;
    socket.removeAllListeners('data');
    void (async () => {
      let request;
      try {
        request = JSON.parse(input.split('\n')[0]); authorize(request.token);
      const result = await dispatch(request.command);
        if (request.command === 'cleanup') {
          // Reply is flushed before the controller exits and closes its lease.
          socket.end(JSON.stringify({ ok: true, result }) + '\n', async () => { await close(); process.exit(0); });
        } else socket.end(JSON.stringify({ ok: true, result }) + '\n');
      } catch (error) { socket.end(JSON.stringify({ ok: false, error: safe(error) }) + '\n'); }
    })();
  });
});
await new Promise((resolve, reject) => { server.once('error', reject); server.listen(control.socket, resolve); });
await chmod(control.socket, 0o600);
await save();
const shutdown = () => { void cleanup().then(close).then(() => process.exit(0)).catch(async error => {
  run.status = 'cleanup-blocked'; run.result ||= {}; run.result.failures = [safe(error)]; await save(); await close(); process.exit(1);
}); };
process.once('SIGINT', shutdown); process.once('SIGTERM', shutdown);
