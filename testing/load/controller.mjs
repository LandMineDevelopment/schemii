import net from 'node:net';
import { rm, chmod } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { timingSafeEqual } from 'node:crypto';
import { root, privateJSON, writeJSON, credentials } from '../harness/store.mjs';
import { startDeployment, sourceIdentity } from '../harness/deployment.mjs';
import { releaseAccounts } from '../harness/leases.mjs';
import { prepareFixtures, cleanupFixtures } from './fixtures.mjs';
import { executePlan, verifyK6, observation, requireStopped } from './engine.mjs';
import { loadPath } from './cli.mjs';

process.umask(0o077);
const dir = loadPath(process.argv[2]), run = await privateJSON(join(dir, 'manifest-private.json'));
const config = await privateJSON(join(dir, 'config-private.json'));
const control = await privateJSON(join(dir, 'control-private.json'));
const credentialMap = await credentials(config.credentialPath);
let receipt, task, stopping = false, server;
const aborter = new AbortController();
const save = () => writeJSON(join(dir, 'manifest-private.json'), run);
const safe = error => /^[a-z0-9_-]{1,100}$/.test(error.message) ? error.message : 'load_operation_failed';
const close = async () => { if (server) await new Promise(resolve => server.close(resolve)); await rm(control.socket, { force: true }); await rm(dirname(control.socket), { recursive: true, force: true }); };
async function cleanup() {
  if (stopping) throw new Error('cleanup_already_running');
  stopping = true; aborter.abort(); if (task) await task;
  if (run.generatorOwner?.pending) throw new Error('unresolved_generator_launch');
  if (run.generatorOwner?.pid) await requireStopped(run.generatorOwner);
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
  run.status = 'running'; await save();
  task = executePlan({ spec: run.plan, receipt, dir, origin: config.origin, binary: config.binary,
    observerFile: config.observerFile, allowUnobserved: config.allowUnobserved, signal: aborter.signal, abort: () => aborter.abort(),
    onStage: async stages => { run.result = { stages }; await save(); },
    onGenerator: async owner => { run.generatorOwner = { ...run.generatorOwner, ...owner }; await save(); },
    onExecution: async (username, id) => {
      receipt.executions ||= [];
      if (!receipt.executions.some(item => item.id === id)) { receipt.executions.push({ username, id }); await writeJSON(join(dir, 'fixtures-private.json'), receipt); }
    } }).then(async result => { run.result = result; run.status = result.failures.length ? 'failed' : 'completed'; await save(); },
      async error => { run.status = 'failed'; run.result ||= {}; run.result.failures = [safe(error)]; await save(); });
  return { run: run.id, status: 'running', work: 'opt-in generator scheduled; inspect status/report before cleanup' };
}
try {
  if (run.plan.engine === 'k6') await verifyK6(config.binary);
  if (config.observerFile) await observation(config.observerFile);
  run.deployment = await startDeployment({ root, runDir: dir });
  receipt = await prepareFixtures({ dir, accounts: run.accounts, credentialMap, stateDir: config.stateDir,
    kind: run.plan.workload === 'cheap-read' ? 'cheap' : 'reports', origin: config.origin });
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
