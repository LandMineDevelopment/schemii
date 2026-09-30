import { spawn } from 'node:child_process';
import net from 'node:net';
import { open, rm } from 'node:fs/promises';
import { join, dirname, resolve } from 'node:path';
import { randomBytes } from 'node:crypto';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { root, privateJSON, privateDir, writeJSON, credentials } from '../harness/store.mjs';
import { deploymentLockPath, sourceIdentity } from '../harness/deployment.mjs';
import { reserveAccounts, releaseAccounts, withFileLock } from '../harness/leases.mjs';
import { cleanupFixtures } from './fixtures.mjs';
import { plan, WORKLOADS, validatePlan } from './plan.mjs';
import { birthTick, requireStopped } from './engine.mjs';
import { LOCAL, PREVIEW } from './http.mjs';

const COMMANDS = {
  plan: ['recipe', 'workload', 'rate', 'active', 'output'],
  prepare: ['plan', 'accounts', 'credentials-file', 'state-dir', 'observer-file', 'k6-bin', 'origin', 'allow-unobserved'],
  run: ['run'], status: ['run'], report: ['run'], cleanup: ['run'],
};
export function parse(argv) {
  const [command = 'help', ...args] = argv;
  if (command === 'help' || command === '--help') return { command: 'help', options: {} };
  if (!Object.hasOwn(COMMANDS, command)) throw new Error('unknown_load_command');
  const options = {};
  for (let i = 0; i < args.length; i++) {
    const key = args[i].replace(/^--/, '');
    if (!args[i].startsWith('--') || !COMMANDS[command].includes(key) || key in options) throw new Error('invalid_load_option');
    if (key === 'allow-unobserved') options[key] = true;
    else { if (!args[i + 1] || args[i + 1].startsWith('--')) throw new Error('missing_load_option_value'); options[key] = args[++i]; }
  }
  return { command, options };
}
export function loadPath(id) {
  if (!/^load-[a-z0-9-]{6,80}$/.test(id || '')) throw new Error('invalid_load_run');
  return join(root, 'artifacts/load', id);
}
export async function rpc(control, command) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(control.socket); let text = '';
    socket.setTimeout(60000);
    socket.once('connect', () => socket.write(JSON.stringify({ command, token: control.token }) + '\n'));
    socket.on('data', chunk => {
      text += chunk;
      if (text.length > 1024 * 1024) socket.destroy(new Error('controller_response_budget'));
      if (text.includes('\n')) {
        socket.end();
        try { const reply = JSON.parse(text.split('\n')[0]); reply.ok ? resolve(reply.result) : reject(new Error(reply.error)); } catch (error) { reject(error); }
      }
    });
    socket.on('error', reject); socket.once('timeout', () => socket.destroy(new Error('controller_timeout')));
    socket.once('end', () => { if (!text.includes('\n')) reject(new Error('controller_disconnected')); });
  });
}
function ownedSocket(runId) { return join('/tmp', `schemii-load-${process.getuid()}`, runId, 'controller.sock'); }
function summary(run) { return { run: run.id, status: run.status, accounts: run.accounts.length,
  workload: run.plan.workload, recipe: run.plan.recipe, report: join(loadPath(run.id), 'report.json'),
  failures: run.result?.failures || [], capacityEligible: run.result?.capacityEligible || false }; }

export async function loadMain(argv) {
  process.umask(0o077);
  const { command, options: o } = parse(argv);
  if (command === 'help') { console.log(`Usage: ./test.sh load plan|prepare|run|status|report|cleanup [options]
plan --recipe smoke|ramp|confirm|spike|soak --workload ${WORKLOADS.join('|')} --rate 100 --active 2 --output FILE
prepare --plan FILE --accounts USER1,USER2 --credentials-file PRIVATE_FILE [--state-dir PRIVATE_DIR]
        --observer-file PRIVATE_FILE [--k6-bin HOST_BINARY] [--origin ${LOCAL}|${PREVIEW}]
        [--allow-unobserved: smoke protocol checks only; never capacity evidence]
run|status|report|cleanup --run LOAD_ID
No stress run is part of default tests. prepare uses ./start.sh and holds the existing deployment/account leases.
cleanup deletes only private receipt-owned report fixtures, revokes load sessions and preserves reports.`); return; }
  if (command === 'plan') {
    const value = plan({ recipe: o.recipe, workload: o.workload, rate: Number(o.rate || 100), active: Number(o.active || 2) });
    if (o.output) await writeJSON(resolve(o.output), value);
    console.log(JSON.stringify(value, null, 2)); return value;
  }
  if (command === 'prepare') {
    if (!o.plan || !o.accounts || !o['credentials-file']) throw new Error('plan_accounts_credentials_required');
    const spec = validatePlan(await privateJSON(resolve(o.plan)));
    const accounts = o.accounts.split(',');
    if (accounts.length !== spec.activeIdentities || new Set(accounts).size !== accounts.length ||
        accounts.some(name => !/^qa_[a-z_]+_[0-9]{3}$/.test(name))) throw new Error('registered_accounts_must_match_active');
    const credentialPath = resolve(o['credentials-file']), credentialMap = await credentials(credentialPath);
    if (accounts.some(name => !credentialMap.has(name))) throw new Error('missing_account_credential');
    if (o['allow-unobserved'] && spec.recipe !== 'smoke') throw new Error('unobserved_only_smoke');
    if (!o['observer-file'] && !o['allow-unobserved']) throw new Error('observer_required');
    const id = `load-${Date.now().toString(36)}-${randomBytes(4).toString('hex')}`, dir = loadPath(id);
    const origin = o.origin || LOCAL;
    if (![LOCAL, PREVIEW].includes(origin)) throw new Error('invalid_origin');
    await privateDir(dir);
    const run = { version: 1, id, accounts, plan: spec, status: 'preparing', createdAt: new Date().toISOString(),
      source: sourceIdentity(root), generator: { location: 'cohosted', node: process.version,
        platform: os.platform(), release: os.release(), architecture: os.arch(), cpuCount: os.availableParallelism(),
        totalMemoryBytes: os.totalmem(), cpuModel: os.cpus()[0]?.model }, topology: { apiProcesses: 'unverified', source: 'qa-postgres',
        registeredAccounts: (await privateJSON(join(resolve(o['state-dir'] || join(root, '.schemii/testing')), 'registry.json'))).slots.length,
        activeIdentities: accounts.length, profileSharing: 'distinct managed profiles' } };
    await writeJSON(join(dir, 'manifest-private.json'), run);
    const runtime = join('/tmp', `schemii-load-${process.getuid()}`, id); await privateDir(runtime);
    await writeJSON(join(dir, 'control-private.json'), { socket: join(runtime, 'controller.sock'), token: randomBytes(32).toString('hex') });
    await writeJSON(join(dir, 'config-private.json'), { credentialPath, stateDir: resolve(o['state-dir'] || join(root, '.schemii/testing')),
      observerFile: o['observer-file'] ? resolve(o['observer-file']) : null, allowUnobserved: !!o['allow-unobserved'],
      binary: o['k6-bin'] ? resolve(o['k6-bin']) : 'k6', origin });
    await reserveAccounts({ runId: id, runDir: dir, accounts, root });
    const lock = deploymentLockPath(root), gate = join(dirname(lock), 'qa-startup.lock');
    const log = await open(join(dir, 'controller-private.log'), 'a', 0o600);
    let child;
    try {
      child = spawn('bash', ['-c', 'exec 4> "$1"; flock --wait 180 4 || exit 4; exec 3> "$2"; if flock --nonblock 3; then export SCHEMII_QA_LEASE_MODE=exclusive; else flock --shared --nonblock 3 || exit 4; export SCHEMII_QA_LEASE_MODE=shared; fi; export SCHEMII_QA_LEASE_FD=3 SCHEMII_QA_GATE_FD=4; exec node "$3" "$4"', 'load-controller', gate, lock,
        join(root, 'testing/load/controller.mjs'), id], { cwd: root, detached: true, stdio: ['ignore', log.fd, log.fd] });
      child.unref(); await writeJSON(join(dir, 'controller-owner.json'), { pid: child.pid, birthTick: await birthTick(child.pid) });
    } catch (error) { await releaseAccounts({ runId: id, root }); throw error; }
    finally { await log.close(); }
    for (let i = 0; i < 360; i++) {
      const state = await privateJSON(join(dir, 'manifest-private.json'));
      if (state.status !== 'preparing') { console.log(JSON.stringify(summary(state))); return summary(state); }
      if (!await birthTick(child.pid)) throw new Error(`controller_stopped_prepare_${id}`);
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw new Error(`prepare_pending_${id}`);
  }
  const dir = loadPath(o.run);
  const control = await privateJSON(join(dir, 'control-private.json'));
  if (command === 'cleanup') {
    return withFileLock(join(dir, 'lifecycle.lock'), async () => {
      if (control.socket !== ownedSocket(o.run)) throw new Error('controller_socket_ownership_changed');
      try { const result = await rpc(control, 'cleanup'); console.log(JSON.stringify(result)); return result; }
      catch (error) { if (!['ENOENT', 'ECONNREFUSED'].includes(error.code)) throw error; }
      const run = await privateJSON(join(dir, 'manifest-private.json'));
      await requireStopped(await privateJSON(join(dir, 'controller-owner.json')));
      if (run.generatorOwner?.pending) throw new Error('unresolved_generator_launch');
      if (run.generatorOwner?.pid) await requireStopped(run.generatorOwner);
      const config = await privateJSON(join(dir, 'config-private.json'));
      // Reacquire the shared deployment lease while doing supported API cleanup.
      // An exclusive rebuild may finish first; cleanup never starts the app.
      const lease = await open(deploymentLockPath(root), 'a', 0o600);
      try {
        await new Promise((resolve, reject) => { const lock = spawn('flock', ['--shared', '3'], { stdio: ['ignore', 'ignore', 'ignore', lease.fd] }); lock.once('error', reject); lock.once('close', code => code === 0 ? resolve() : reject(new Error('cleanup_lease_failed'))); });
        const result = await cleanupFixtures({ dir, credentialMap: await credentials(config.credentialPath), origin: config.origin });
        await releaseAccounts({ runId: o.run, root }); run.status = 'cleaned'; run.cleanup = result;
        await writeJSON(join(dir, 'manifest-private.json'), run);
        await rm(control.socket, { force: true }); await rm(dirname(control.socket), { recursive: true, force: true });
        console.log(JSON.stringify(result)); return result;
      } finally { await lease.close(); }
    });
  }
  if (command === 'run') { const result = await rpc(control, 'run'); console.log(JSON.stringify(result)); return result; }
  const run = await privateJSON(join(dir, 'manifest-private.json'));
  if (command === 'report') {
    const report = { version: 1, run: run.id, status: run.status, source: run.source, plan: run.plan,
      generator: run.generator, topology: run.topology, result: run.result || null, cleanup: run.cleanup || null,
      deploymentVerified: !!run.deployment, applicationAcceptance: false };
    await writeJSON(join(dir, 'report.json'), report);
  }
  console.log(JSON.stringify(summary(run))); return summary(run);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  loadMain(process.argv.slice(2)).catch(error => { console.error(JSON.stringify({ error: /^[a-z0-9_-]+$/.test(error.message) ? error.message : 'load_command_failed' })); process.exitCode = 1; });
}
