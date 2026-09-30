import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdtemp, cp, readFile, writeFile, access, rm } from 'node:fs/promises';
import { join } from 'node:path';
import os from 'node:os';
import { root, privateJSON, writeJSON } from '../harness/store.mjs';
import { reserveAccounts, releaseAccounts } from '../harness/leases.mjs';
import { deploymentLockPath, sourceIdentity } from '../harness/deployment.mjs';
import { observeChild, stopChild, createRuntime, disposeRuntime } from './children.mjs';
import { birthTick } from './engine.mjs';
import { plan } from './plan.mjs';
import { randomBytes } from 'node:crypto';
import { rpc } from './cli.mjs';

const command = args => spawnSync('./test.sh', args, { cwd: root, encoding: 'utf8', timeout: 10000 });
async function copiedCheckout(t) {
  const checkout = await mkdtemp(join(os.tmpdir(), 'schemii-load-cli-test-'));
  const processes = [], runtimes = [];
  t.after(async () => {
    for (const [child, done] of processes) await stopChild(child, done, { graceMs: 100 });
    // Planted unresolved fixtures are disposed only after preservation assertions.
    for (const [id, receipt] of runtimes) await disposeRuntime(id, receipt);
    await rm(checkout, { recursive: true, force: true });
  });
  for (const directory of ['testing/load', 'testing/harness']) await cp(join(root, directory), join(checkout, directory),
    { recursive: true, filter: path => !path.includes('/__pycache__') });
  await writeFile(join(checkout, '.gitignore'), await readFile(join(root, '.gitignore'), 'utf8') + '\ninputs/\n');
  const git = args => {
    const result = spawnSync('git', ['-C', checkout, ...args], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
  };
  git(['init', '--quiet']); git(['add', '.']);
  git(['-c', 'user.name=Load Fixture', '-c', 'user.email=load-fixture@example.invalid', 'commit', '--quiet', '-m', 'fixture']);
  return { checkout, processes, runtimes };
}

test('supported test.sh entry dispatches load help and preserves ordinary UI help', () => {
  const load = command(['load', 'help']);
  assert.equal(load.status, 0, load.stderr);
  assert.match(load.stdout, /Usage: \.\/test\.sh load plan\|prepare\|run\|status\|report\|cleanup/);
  const ui = command(['help']);
  assert.equal(ui.status, 0, ui.stderr);
  assert.match(ui.stdout, /claim\s+Bind a lane to an agent/);
});

test('test.sh load produces the bounded plan without preparation or application startup', () => {
  const result = command(['load', 'plan', '--recipe', 'smoke', '--workload', 'report-1', '--active', '2']);
  assert.equal(result.status, 0, result.stderr);
  const plan = JSON.parse(result.stdout);
  assert.equal(plan.workload, 'report-1');
  assert.equal(plan.engine, 'incremental-node');
  assert.equal(plan.capacityEligible, false);
  assert.deepEqual(plan.stages, [{ name: 'smoke', callsPerMinute: 100, seconds: 6, measured: true }]);
});

test('load entry rejects an unknown option and a traversal run before any resource action', () => {
  for (const args of [['load', 'prepare', '--bypass-lease'], ['load', 'run', '--run', '../peer']]) {
    const result = command(args);
    assert.equal(result.status, 1);
    assert.equal(result.stdout, '');
    assert.match(JSON.parse(result.stderr).error, /^(invalid_load_option|invalid_load_run)$/);
  }
});

test('actual CLI owner-write failure joins its controller, disposes runtime and permits safe stopped recovery', { timeout: 15000 }, async t => {
  const { checkout, processes, runtimes } = await copiedCheckout(t);
  await writeJSON(join(checkout, 'inputs/plan.json'), plan({ active: 1 }));
  await writeJSON(join(checkout, 'inputs/credentials.json'), { qa_load_owned_001: 'fixture-only-private' });
  await writeJSON(join(checkout, 'inputs/registry.json'), { slots: [{ username: 'qa_load_owned_001' }] });
  const ownAccount = join(checkout, '.git/qa-account-leases/qa_load_owned_001.json');
  const peerAccount = join(checkout, '.git/qa-account-leases/qa_load_peer_001.json');
  await reserveAccounts({ runId: 'load-live-peer', runDir: checkout, accounts: ['qa_load_peer_001'], root: checkout });
  const peer = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { detached: true, stdio: 'ignore' });
  processes.push([peer, observeChild(peer)]);
  const peerBirth = await birthTick(peer.pid);
  assert.ok(peerBirth);
  const lease = deploymentLockPath(checkout);
  const held = () => spawnSync('flock', ['--exclusive', '--nonblock', lease, 'true']).status === 1;
  // Only the owned copy's controller and journal boundary are replaced. The
  // real CLI, flock acquisition, reservations, child join and disposal run.
  await writeFile(join(checkout, 'testing/load/controller.mjs'), `
    import net from 'node:net';
    import {readFileSync,writeFileSync,existsSync,readlinkSync} from 'node:fs';
    import {join} from 'node:path';
    const dir=join(${JSON.stringify(checkout)},'artifacts/load',process.argv[2]);
    const control=JSON.parse(readFileSync(join(dir,'control-private.json')));
    const server=net.createServer();
    let stopping=false;
    process.on('SIGTERM',()=>{
      if(stopping)return; stopping=true;
      writeFileSync(join(dir,'fixture-shutdown.json'),JSON.stringify({pid:process.pid,
        accountHeld:existsSync(${JSON.stringify(ownAccount)}),peerHeld:existsSync(${JSON.stringify(peerAccount)}),
        lease:readlinkSync('/proc/self/fd/3')}),{mode:0o600});
      server.close(()=>setTimeout(()=>process.exit(0),100));
    });
    server.listen(control.socket,()=>writeFileSync(join(dir,'fixture-ready.json'),JSON.stringify({pid:process.pid}),{mode:0o600}));
  `);
  const store = join(checkout, 'testing/harness/store.mjs'), source = await readFile(store, 'utf8');
  await writeFile(store, source.replace('export async function writeJSON(path, data) {', `export async function writeJSON(path, data) {
    if(path.endsWith('/controller-owner.json')) {
      for(let attempt=0;attempt<200;attempt++) {
        try { await stat(join(dirname(path),'fixture-ready.json')); break; }
        catch(error) { if(error.code!=='ENOENT')throw error; await new Promise(resolve=>setTimeout(resolve,5)); }
      }
      throw new Error('fixture_controller_owner_write_failed');
    }
  `));
  const launch = spawn(process.execPath, ['testing/load/cli.mjs', 'prepare', '--plan', 'inputs/plan.json',
    '--accounts', 'qa_load_owned_001', '--credentials-file', 'inputs/credentials.json', '--state-dir', 'inputs',
    '--allow-unobserved'], { cwd: checkout, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const done = observeChild(launch); processes.push([launch, done]);
  let stderr = '', reservation, ready, shutdown, leaseHeldBefore = false;
  launch.stderr.on('data', chunk => { stderr += chunk; }); launch.stdout.resume();
  for (let attempt = 0; attempt < 400; attempt++) {
    try {
      reservation ||= await privateJSON(ownAccount);
      ready = await privateJSON(join(reservation.runDir, 'fixture-ready.json'));
      leaseHeldBefore ||= held();
      shutdown = await privateJSON(join(reservation.runDir, 'fixture-shutdown.json'));
      break;
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.ok(ready); assert.ok(shutdown);
  assert.equal(shutdown.accountHeld, true); assert.equal(shutdown.peerHeld, true);
  assert.equal(shutdown.lease, lease); assert.equal(leaseHeldBefore, true);
  assert.equal((await done).code, 1); assert.match(stderr, /fixture_controller_owner_write_failed/);
  assert.equal(await birthTick(ready.pid), null);
  await assert.rejects(access(ownAccount), { code: 'ENOENT' });
  const launchReceipt = await privateJSON(join(reservation.runDir, 'controller-launch.json'));
  runtimes.push([reservation.runId, launchReceipt.runtime]);
  assert.equal(launchReceipt.status, 'stopped');
  await assert.rejects(access(launchReceipt.runtime.path), { code: 'ENOENT' });
  await assert.rejects(access(join(reservation.runDir, 'controller-owner.json')), { code: 'ENOENT' });
  assert.equal(held(), false);
  assert.equal(await birthTick(peer.pid), peerBirth); await access(peerAccount);
  const cleanup = () => spawnSync(process.execPath, ['testing/load/cli.mjs', 'cleanup', '--run', reservation.runId],
    { cwd: checkout, encoding: 'utf8', timeout: 5000 });
  for (let repeat = 0; repeat < 2; repeat++) {
    const result = cleanup(); assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).objectsRemaining, 0);
    assert.equal((await privateJSON(join(reservation.runDir, 'manifest-private.json'))).status, 'cleaned');
  }
  // Missing owner on an unresolved launch must not authorize process signaling,
  // deletion or account release, even when its socket is already absent.
  const unresolved = await createRuntime(reservation.runId); runtimes[0] = [reservation.runId, unresolved];
  await writeJSON(join(reservation.runDir, 'controller-launch.json'), { version: 1, runId: reservation.runId,
    status: 'pending', runtime: unresolved });
  await reserveAccounts({ runId: reservation.runId, runDir: reservation.runDir, accounts: ['qa_load_owned_001'], root: checkout });
  const unknown = cleanup(); assert.equal(unknown.status, 1);
  assert.equal(JSON.parse(unknown.stderr).error, 'unknown_controller_owner');
  await access(unresolved.path); await access(ownAccount); await access(peerAccount);
  assert.equal(await birthTick(peer.pid), peerBirth);
  await releaseAccounts({ runId: reservation.runId, root: checkout });
  await stopChild(peer, processes[0][1], { graceMs: 100 });
  await releaseAccounts({ runId: 'load-live-peer', root: checkout });
});

test('actual controller prepares before observation exists and admits no arrivals until fresh live qualification', { timeout: 15000 }, async t => {
  const { checkout, processes, runtimes } = await copiedCheckout(t);
  const id = `load-observer-${randomBytes(6).toString('hex')}`, dir = join(checkout, 'artifacts/load', id);
  const runtime = await createRuntime(id); runtimes.push([id, runtime]);
  const observerFile = join(checkout, 'inputs/observer.json'), arrivalFile = join(dir, 'fixture-arrivals.txt');
  const credentialPath = join(checkout, 'inputs/credentials.json');
  await writeJSON(credentialPath, {});
  // Exercise the real controller/state/RPC code with bounded dependency seams.
  // These owned stubs cannot start a deployment, write application fixtures,
  // or offer real HTTP work.
  await writeFile(join(checkout, 'testing/harness/deployment.mjs'), `
    export {sourceIdentity,deploymentLockPath} from ${JSON.stringify(join(root, 'testing/harness/deployment.mjs'))};
    export async function startDeployment(){return {fixtureOnly:true};}
  `);
  await writeFile(join(checkout, 'testing/load/fixtures.mjs'), `
    export async function prepareFixtures(){return {accounts:[]};}
    export async function cleanupFixtures(){return {objectsRemaining:0};}
    export function ordinaryRequest(){throw new Error('fixture_no_http');}
    export function streamRequest(){throw new Error('fixture_no_http');}
  `);
  const engine = join(checkout, 'testing/load/engine.mjs'), source = await readFile(engine, 'utf8');
  const marker = '  const results = [], before = await observation(observerFile);';
  assert.ok(source.includes(marker));
  await writeFile(engine, "import {appendFile as fixtureAppendFile} from 'node:fs/promises';\n" +
    source.replace(marker, `  await fixtureAppendFile(${JSON.stringify(arrivalFile)}, 'started\\n'); return {failures:[]};\n${marker}`));
  const spec = plan({ active: 1, workload: 'report-1' });
  await writeJSON(join(dir, 'manifest-private.json'), { version: 1, id, status: 'preparing', accounts: [],
    plan: spec, source: sourceIdentity(checkout) });
  await writeJSON(join(dir, 'config-private.json'), { credentialPath, observerFile, allowUnobserved: false });
  const control = { socket: join(runtime.path, 'controller.sock'), token: randomBytes(32).toString('hex') };
  await writeJSON(join(dir, 'control-private.json'), control);
  await writeJSON(join(dir, 'controller-launch.json'), { version: 1, runId: id, status: 'pending', runtime });
  const child = spawn(process.execPath, ['testing/load/controller.mjs', id], { cwd: checkout, detached: true,
    stdio: ['ignore', 'pipe', 'pipe'] });
  const done = observeChild(child); processes.push([child, done]);
  let stderr = ''; child.stdout.resume(); child.stderr.on('data', chunk => { stderr += chunk; });
  const owner = { pid: child.pid, birthTick: await birthTick(child.pid) };
  await writeJSON(join(dir, 'controller-owner.json'), owner);
  await writeJSON(join(dir, 'controller-launch.json'), { version: 1, runId: id, status: 'started', runtime, owner });
  let state;
  for (let attempt = 0; attempt < 400; attempt++) {
    state = await privateJSON(join(dir, 'manifest-private.json'));
    if (state.status !== 'preparing') break;
    assert.ok(await birthTick(child.pid), stderr);
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.equal(state.status, 'ready', stderr);
  await assert.rejects(access(observerFile), { code: 'ENOENT' });
  const values = [null,
    { at: new Date(Date.now() - 60000).toISOString(), appRssBytes: 1, appMemoryLimitBytes: 100 },
    { at: new Date().toISOString(), appRssBytes: 1, appMemoryLimitBytes: 0 },
    { at: new Date().toISOString(), appRssBytes: 90, appMemoryLimitBytes: 100 }];
  for (const value of values) {
    if (value) await writeJSON(observerFile, value);
    await assert.rejects(rpc(control, 'run'), value?.appRssBytes === 90 ? /memory_stop_threshold/ : /observer_unavailable/);
    assert.equal((await privateJSON(join(dir, 'manifest-private.json'))).status, 'ready');
    await assert.rejects(access(arrivalFile), { code: 'ENOENT' });
  }
  await writeJSON(observerFile, { at: new Date().toISOString(), appRssBytes: 1, appMemoryLimitBytes: 100 });
  const starts = await Promise.allSettled([rpc(control, 'run'), rpc(control, 'run')]);
  assert.equal(starts.filter(result => result.status === 'fulfilled').length, 1);
  assert.match(starts.find(result => result.status === 'rejected').reason.message, /run_not_ready/);
  assert.equal(await readFile(arrivalFile, 'utf8'), 'started\n');
  const result = await rpc(control, 'cleanup'); assert.equal(result.status, 'cleaned');
  assert.equal((await done).code, 0, stderr);
  await assert.rejects(access(runtime.path), { code: 'ENOENT' });
});
