import test from 'node:test';
import assert from 'node:assert/strict';
import { assertLaneReadyToClaim, assertProductNavigationAllowed, openProductsAfterIsolation, recoverFleetFailClosed } from './readiness.mjs';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile, symlink } from 'node:fs/promises';
import https from 'node:https';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { checkHTTPS, DESKTOP_PREVIEW, disposableGuestBinding, readPrivateDeploymentJSON, startDeployment } from './deployment.mjs';


test('a wave starts product streams only after every session passes logout isolation', async () => {
  const events = [];
  const lanes = [{ id: 'author' }, { id: 'reader' }];
  const loggedBackIn = new Set();
  let parked = new Set();

  await openProductsAfterIsolation(lanes, {
    parkLanes: async () => {
      for (const lane of lanes) { events.push(`park:${lane.id}`); parked.add(lane.id); }
    },
    proveIsolation: async () => {
      assert.equal(parked.size, lanes.length, 'all product pages must be parked before logout begins');
      for (const lane of lanes) {
        events.push(`logout:${lane.id}`);
        events.push(`logout-verified:${lane.id}`);
        events.push(`relogin:${lane.id}`);
        loggedBackIn.add(lane.id);
      }
      events.push('isolation-complete');
    },
    navigateLane: async id => {
      assert.equal(loggedBackIn.size, lanes.length, 'all separate sessions must be restored before product navigation');
      events.push(`product-stream:${id}`);
    },
  });

  assert.deepEqual(events, [
    'park:author', 'park:reader',
    'logout:author', 'logout-verified:author', 'relogin:author',
    'logout:reader', 'logout-verified:reader', 'relogin:reader',
    'isolation-complete', 'product-stream:author', 'product-stream:reader',
  ]);
});

test('failed isolation never opens a product page or starts its stream', async () => {
  const events = [];
  await assert.rejects(openProductsAfterIsolation([{ id: 'author' }], {
    parkLanes: async () => { events.push('park'); },
    proveIsolation: async () => { events.push('logout-failed'); throw new Error('logout could not be verified'); },
    navigateLane: async id => events.push(`product-stream:${id}`),
  }), /logout could not be verified/);
  assert.deepEqual(events, ['park', 'logout-failed']);
});

test('a lane is never opened when parking fails before isolation', async () => {
  const events = [];
  await assert.rejects(openProductsAfterIsolation([{ id: 'author' }], {
    parkLanes: async () => { events.push('park-failed'); throw new Error('could not park product page'); },
    proveIsolation: async () => events.push('logout'),
    navigateLane: async id => events.push(`product-stream:${id}`),
  }), /could not park product page/);
  assert.deepEqual(events, ['park-failed']);
});

test('failed fleet recovery closes and invalidates every active lane before persisting blocked status', async () => {
  const lanes = [
    { id: 'recovering', status: 'ready', generation: 3, agent: 'old-agent', heartbeatAt: 'old-heartbeat' },
    { id: 'peer', status: 'ready', generation: 8, agent: null, heartbeatAt: null },
    { id: 'finished', status: 'complete', generation: 2 },
  ];
  const tokens = new Map([['recovering', 'old-token'], ['peer', 'peer-token']]);
  const browserHandles = new Set(['recovering', 'peer']);
  const run = { status: 'recovering' };
  let persisted = false;
  let cleanupReported = false;
  assert.doesNotThrow(() => assertLaneReadyToClaim(lanes[1]));

  const recoveryEvents = [];
  await assert.rejects(recoverFleetFailClosed({
    recover: async () => {
      recoveryEvents.push('worker-stop');
      recoveryEvents.push('wait-for-operation');
      recoveryEvents.push('close-requested-lane');
      throw new Error('requested lane close failed before isolation');
    },
    lanes,
    affectedLaneIds: () => ['recovering', 'peer'],
    messageFor: error => error.message,
    closeFleet: async () => {
      recoveryEvents.push('close-entire-fleet');
      browserHandles.clear();
      throw new Error('one browser close reported an error');
    },
    invalidateLane: lane => {
      tokens.delete(lane.id);
      lane.generation++;
      lane.agent = null;
      lane.heartbeatAt = null;
    },
    onFailure: ({ message, cleanupError }) => {
      run.status = 'blocked';
      run.error = message;
      cleanupReported = Boolean(cleanupError);
    },
    persist: async () => { persisted = true; },
  }), /requested lane close failed before isolation/);

  assert.deepEqual(recoveryEvents, ['worker-stop', 'wait-for-operation', 'close-requested-lane', 'close-entire-fleet']);
  assert.equal(browserHandles.size, 0, 'all sessions in the failed proof wave are closed');
  assert.equal(tokens.has('recovering'), false);
  assert.equal(tokens.has('peer'), false, 'a ready peer token cannot survive failed fleet proof');
  assert.deepEqual(lanes.slice(0, 2).map(lane => lane.status), ['blocked', 'blocked']);
  assert.deepEqual(lanes.slice(0, 2).map(lane => lane.generation), [4, 9]);
  assert.throws(() => assertLaneReadyToClaim(lanes[1]), /Only a preflight-ready lane can be claimed/);
  assert.equal(lanes[0].agent, null);
  assert.equal(lanes[0].heartbeatAt, null);
  assert.equal(lanes[2].status, 'complete', 'unaffected completed lanes remain unchanged');
  assert.equal(run.status, 'blocked');
  assert.equal(run.error, 'requested lane close failed before isolation');
  assert.equal(cleanupReported, true, 'cleanup failure is exposed to the run summary');
  assert.equal(persisted, true, 'blocked peer state is persisted despite a cleanup error');
});

test('product navigation is rejected until the fleet isolation proof succeeds', () => {
  const baseURL = 'https://localhost:8001';
  assert.throws(() => assertProductNavigationAllowed('/', false, baseURL), /completed isolation proof/);
  assert.throws(() => assertProductNavigationAllowed('/schemoo/projects', false, baseURL), /completed isolation proof/);
  assert.throws(() => assertProductNavigationAllowed('/schemer?report=starter', false, baseURL), /completed isolation proof/);
  assert.throws(() => assertProductNavigationAllowed('/%73chemer', false, baseURL), /completed isolation proof/);
  assert.doesNotThrow(() => assertProductNavigationAllowed('/account', false, baseURL));
  assert.doesNotThrow(() => assertProductNavigationAllowed('/schemer', true, baseURL));
});

test('guest HTTPS verifies the certificate and requires a non-redirected request identity', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'qa-preview-tls-'));
  t.after(() => rm(dir, {recursive:true, force:true}));
  const key = join(dir, 'key.pem'), cert = join(dir, 'cert.pem');
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert,
    '-days', '1', '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1'], {stdio:'ignore'});
  const ca = await readFile(cert);
  const server = https.createServer({key:await readFile(key), cert:ca}, (req, res) => {
    if (req.url !== '/no-id') res.setHeader('x-request-id', 'a'.repeat(32));
    if (req.url === '/api-map') res.statusCode = 401;
    if (req.url === '/wrong-route') {
      res.statusCode = 302;
      res.setHeader('location', DESKTOP_PREVIEW);
    }
    res.end();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const origin = `https://127.0.0.1:${server.address().port}`;
  await assert.rejects(checkHTTPS(origin), {code:'DEPTH_ZERO_SELF_SIGNED_CERT'});
  await assert.rejects(checkHTTPS(`${origin}/no-id`, {ca, requestIdentity:true}), /request ID/);
  await assert.rejects(checkHTTPS(`${origin}/wrong-route`, {ca, requestIdentity:true}), /Unexpected redirect/);
  assert.equal((await checkHTTPS(`${origin}/api-map`, {ca, requestIdentity:true, allowAuthChallenge:true})).status, 401);
});

test('disposable deployment rejects undeclared origins and requires held lease descriptors', async t => {
  const root = await mkdtemp(join(tmpdir(), 'qa-preview-lease-'));
  t.after(() => rm(root, {recursive:true, force:true}));
  execFileSync('git', ['init', '-q', root]);
  const guestBinding = disposableGuestBinding({
    bootId:'12345678-1234-1234-1234-123456789012', commit:'a'.repeat(40), certificateSha256:'b'.repeat(64),
  });
  await assert.rejects(startDeployment({root, runDir:root, guestBinding}), /declared preview origin/);
  await assert.rejects(startDeployment({root, runDir:root, previewURL:null}), /declared preview origin/);
  await assert.rejects(startDeployment({root, runDir:root, previewURL:null, guestBinding}), /held coordinator lease/);
  const module = new URL('./deployment.mjs', import.meta.url).href;
  const leaseProbe = (lockCommands, mode) => spawnSync('bash', ['-c',
    `exec 3>"$1/.git/qa-deployment.lock"; exec 4>"$1/.git/qa-startup.lock"; ${lockCommands}
     exec node --input-type=module -e "import {assertDeploymentLease} from '$2'; assertDeploymentLease(process.argv[1]);" "$1"`,
    'probe', root, module], {encoding:'utf8', env:{...process.env, SCHEMII_QA_LEASE_FD:'3', SCHEMII_QA_LEASE_MODE:mode}});
  assert.match(leaseProbe(':', 'exclusive').stderr, /held coordinator lease/);
  assert.match(leaseProbe('flock -s 3; flock -x 4', 'exclusive').stderr, /held coordinator lease/);
  assert.equal(leaseProbe('flock -x 3; flock -x 4', 'exclusive').status, 0);
  assert.equal(leaseProbe('flock -s 3; flock -x 4', 'shared').status, 0);
});

test('manual CLI snapshots only an explicitly supplied private guest binding', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'qa-preview-plan-'));
  t.after(() => rm(dir, {recursive:true, force:true}));
  const binding = {
    bootId:'12345678-1234-1234-1234-123456789012', commit:'a'.repeat(40), certificateSha256:'b'.repeat(64),
  };
  const file = join(dir, 'binding.json');
  await writeFile(file, JSON.stringify(binding), {mode:0o600});
  const cli = new URL('./cli.mjs', import.meta.url).pathname;
  const plan = (...args) => JSON.parse(execFileSync(process.execPath, [cli, 'plan', ...args], {encoding:'utf8'}));
  const normal = plan();
  assert.equal(normal.previewURL, DESKTOP_PREVIEW);
  assert.equal(normal.guestBinding, undefined);
  const disposable = plan('--disposable-guest-binding', file);
  assert.equal(disposable.baseURL, 'https://localhost:8001');
  assert.equal(disposable.previewURL, null);
  assert.deepEqual(disposable.guestBinding, binding);
});

test('deployment input refuses public files, symlinks, FIFOs and oversized JSON without opening them as streams', {timeout:1000}, async t => {
  const dir = await mkdtemp(join(tmpdir(), 'qa-private-input-'));
  t.after(() => rm(dir, {recursive:true, force:true}));
  const file = join(dir, 'binding'), link = join(dir, 'link'), fifo = join(dir, 'fifo'), publicFile = join(dir, 'public');
  await writeFile(file, '{"ok":true}', {mode:0o600});
  await writeFile(publicFile, '{"ok":true}', {mode:0o600});
  execFileSync('chmod', ['644',publicFile]);
  await symlink(file, link);
  execFileSync('mkfifo', ['-m','600', fifo]);
  assert.deepEqual(await readPrivateDeploymentJSON(file), {ok:true});
  for (const candidate of [publicFile, link, fifo, dir]) {
    await assert.rejects(readPrivateDeploymentJSON(candidate), /bounded private regular file/);
  }
  await assert.rejects(readPrivateDeploymentJSON(file, 4), /bounded private regular file/);
});
