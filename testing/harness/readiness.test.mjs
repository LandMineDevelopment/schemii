import test from 'node:test';
import assert from 'node:assert/strict';
import { assertLaneReadyToClaim, assertProductNavigationAllowed, openProductsAfterIsolation, recoverFleetFailClosed } from './readiness.mjs';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile, symlink } from 'node:fs/promises';
import https from 'node:https';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { checkHTTPS, DESKTOP_PREVIEW, disposableGuestBinding, readPrivateDeploymentJSON, startDeployment, withDisposableLauncher, stageFixtureCA, stageBoundFixtureCA, assertNoAmbientFixtureCA } from './deployment.mjs';
import { verifyOfflineBuildConfig } from './build-inputs.mjs';


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

test('guest launcher sanitizes engine configuration and refuses reset or inherited authority before launch', async t => {
  const root = await mkdtemp(join(tmpdir(), 'qa-guest-engine-'));
  t.after(() => rm(root, {recursive:true, force:true}));
  const client = join(root, '.docker');
  await mkdir(client, {mode:0o700});
  await writeFile(join(client,'config.json'), JSON.stringify({currentContext:'non-guest-context'}), {mode:0o600});
  const ambient = {
    HOME:root, PATH:process.env.PATH, SCHEMII_TARGET_EGRESS_MODE:'external',
    SCHEMII_ALLOWED_TARGET_HOSTS:'demo-postgres,fixture.internal', SCHEMII_TEST_POSTGRES_DB:'fixture_db',
  };
  const binding=disposableGuestBinding({
    bootId:'12345678-1234-1234-1234-123456789012',commit:'a'.repeat(40),certificateSha256:'b'.repeat(64),
    fixture:{mode:'external',hosts:['demo-postgres','fixture.internal']},
  });
  let config;
  await withDisposableLauncher(root, binding, async env => {
    config=env.DOCKER_CONFIG;
    assert.notEqual(config,client);
    assert.deepEqual(await readdir(config),[]);
    assert.equal((await stat(config)).mode & 0o777,0o700);
    assert.equal(env.DOCKER_HOST,'unix:///var/run/docker.sock');
    assert.equal(env.DOCKER_CONTEXT,undefined);
    assert.equal(env.SCHEMII_RESET_MIGRATION_DEMO,'0');
    assert.equal(env.SCHEMII_TEST_APP_PORT,'8001');
    assert.equal(env.SCHEMII_ALLOWED_TARGET_HOSTS,ambient.SCHEMII_ALLOWED_TARGET_HOSTS);
    assert.equal(env.SCHEMII_TARGET_EGRESS_MODE,ambient.SCHEMII_TARGET_EGRESS_MODE);
    assert.equal(env.SCHEMII_TEST_POSTGRES_DB,ambient.SCHEMII_TEST_POSTGRES_DB);
  },ambient);
  await assert.rejects(stat(config),{code:'ENOENT'});
  const neverLaunch=()=>assert.fail('refused input reached the launcher');
  for(const denied of [
    {SCHEMII_RESET_MIGRATION_DEMO:'1'}, {SCHEMII_SECRET_DIRECTORY:'/other'},
    {SCHEMII_LAUNCH_LOCK_FILE:'/other'}, {DOCKER_CONTEXT:'non-guest-context'},
    {DOCKER_HOST:'tcp://other:2376'}, {DOCKER_CONFIG:client}, {COMPOSE_PROJECT_NAME:'other'},
    {SCHEMII_TEST_APP_PORT:'8443'}, {SCHEMII_ALLOWED_TARGET_HOSTS:'*'},
  ]) {
    await assert.rejects(withDisposableLauncher(root,binding,neverLaunch,{...ambient,...denied}),/Disposable QA/);
  }
  await assert.rejects(withDisposableLauncher(root,{},neverLaunch,ambient),/outside the approved binding/);
  await assert.rejects(withDisposableLauncher(root,binding,neverLaunch,{
    ...ambient,SCHEMII_ALLOWED_TARGET_HOSTS:'another.internal',
  }),/outside the approved binding/);
  await mkdir(join(root,'.schemii'));
  await writeFile(join(root,'.schemii','compose.local.yaml'),'services: {}');
  await assert.rejects(withDisposableLauncher(root,binding,neverLaunch,ambient),/Compose override/);
});

test('guest CLI reserves a separate isolated reviewer without dropping any lane cases', async t => {
  const dir=await mkdtemp(join(tmpdir(),'qa-guest-review-plan-'));
  t.after(()=>rm(dir,{recursive:true,force:true}));
  const file=join(dir,'binding.json');
  await writeFile(file,JSON.stringify({
    bootId:'12345678-1234-1234-1234-123456789012',commit:'a'.repeat(40),certificateSha256:'b'.repeat(64),
  }),{mode:0o600});
  const cli=new URL('./cli.mjs',import.meta.url).pathname;
  const plan=(...args)=>spawnSync(process.execPath,[cli,'plan','--accounts','tester,verifier',
    '--products','schemii,schemoo,schemer','--reviewer-account','verifier','--runtime-slots','3',...args],{encoding:'utf8'});
  const r=plan('--disposable-guest-binding',file);
  assert.equal(r.status,0,r.stderr);
  const result=JSON.parse(r.stdout);
  assert.equal(result.parallel,1);
  assert.deepEqual(result.lanes.map(l=>[l.role,l.scenarios.length]),[['tester',6],['reviewer',6]]);
  assert.match(plan().stderr,/explicit disposable guest binding/);
  assert.match(plan('--disposable-guest-binding',file,'--parallel','2').stderr,/observed --runtime-slots|counts testers/);
  assert.match(plan('--disposable-guest-binding',file,'--browser','native').stderr,/guest-local isolated/);
  const wavePlan=(parallel,slots)=>spawnSync(process.execPath,[cli,'plan','--disposable-guest-binding',file,
    '--accounts','tester-one,tester-two,verifier','--reviewer-account','verifier',
    '--parallel',parallel,'--runtime-slots',slots],{encoding:'utf8'});
  const multiwave=wavePlan('1','3');
  assert.equal(multiwave.status,2);
  assert.match(multiwave.stderr,/requires one tester wave/);
  const oneWave=wavePlan('2','4');
  assert.equal(oneWave.status,0,oneWave.stderr);
  assert.deepEqual(JSON.parse(oneWave.stdout).lanes.map(l=>l.role),['tester','tester','reviewer']);
});

test('registered QA overlay may only append a host already approved in the guest binding', async t => {
  const root=await mkdtemp(join(tmpdir(),'qa-guest-overlay-'));
  t.after(()=>rm(root,{recursive:true,force:true}));
  const registry=join(root,'.schemii','testing','registry.json');
  await mkdir(join(root,'.schemii','testing'),{recursive:true});
  const base={
    bootId:'12345678-1234-1234-1234-123456789012',commit:'a'.repeat(40),certificateSha256:'b'.repeat(64),
  };
  const missing=disposableGuestBinding({...base,fixture:{mode:'external',hosts:['fixture.internal']}});
  const accepted=disposableGuestBinding({...base,fixture:{mode:'external',hosts:['fixture.internal','qa-postgres']}});
  assert.equal(await withDisposableLauncher(root,missing,env=>env.SCHEMII_ALLOWED_TARGET_HOSTS,{}),'fixture.internal');
  await writeFile(registry,'{}',{mode:0o600});
  const neverLaunch=()=>assert.fail('unapproved overlay reached the launcher');
  for (const binding of [disposableGuestBinding(base),missing]) {
    await assert.rejects(withDisposableLauncher(root,binding,neverLaunch,{}),/exact approval of qa-postgres/);
  }
  await withDisposableLauncher(root,accepted,env=>{
    assert.equal(env.SCHEMII_QA_GUEST_TARGET_HOSTS,accepted.fixture.hosts.join(','));
    assert.equal(env.SCHEMII_ALLOWED_TARGET_HOSTS,env.SCHEMII_QA_GUEST_TARGET_HOSTS);
  },{});
});

test('disposable fixture CA is digest-bound, certificate-only and mounted read-only for the app', async t => {
  const root=await mkdtemp(join(tmpdir(),'qa-fixture-ca-'));
  t.after(()=>rm(root,{recursive:true,force:true}));
  await mkdir(join(root,'.schemii'),{mode:0o700});
  const caPath=join(root,'root.pem'), key=join(root,'private.pem');
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,'-out',caPath,
    '-days','1','-subj','/CN=Disposable QA test CA','-addext','basicConstraints=critical,CA:TRUE'],{stdio:'ignore'});
  execFileSync('chmod',['600',caPath]);
  const digest=async file=>createHash('sha256').update(await readFile(file)).digest('hex');
  const sha256=await digest(caPath);
  const guest={bootId:'12345678-1234-1234-1234-123456789012',commit:'a'.repeat(40),certificateSha256:'b'.repeat(64)};
  const fixture={mode:'external',hosts:['fixture.internal'],ca:{path:caPath,sha256}};
  const binding=disposableGuestBinding({...guest,fixture});
  assert.throws(()=>disposableGuestBinding({...guest,fixture:{...fixture,hosts:['fixture.internal','peer.internal']}}),/one explicit/);
  assert.throws(()=>disposableGuestBinding({...guest,fixture:{...fixture,hosts:['qa-postgres']}}),/one explicit/);
  for(const host of ['demo-postgres.','QA-POSTGRES.','Metadata-Postgres...','LOCALHOST.','::1','127.0.0.1']) {
    assert.throws(()=>disposableGuestBinding({...guest,fixture:{...fixture,hosts:[host]}}),/one explicit/);
  }
  assert.throws(()=>assertNoAmbientFixtureCA({SCHEMII_QA_FIXTURE_CA_BINDING:'/private/binding.json'}),/not ambient/);
  const overlay=JSON.parse(await readFile(await stageFixtureCA(root,caPath,sha256),'utf8'));
  assert.deepEqual(Object.keys(overlay.services),['schemii']);
  const service=overlay.services.schemii, [mount]=service.volumes;
  assert.equal(service.environment.PGSSLROOTCERT,mount.target);
  assert.equal(mount.read_only,true);
  assert.equal(mount.bind.create_host_path,false);
  assert.equal((await stat(mount.source)).mode & 0o777,0o444);
  assert.equal(await digest(mount.source),sha256);
  await assert.rejects(stageFixtureCA(root,caPath,'0'.repeat(64)),/approved digest/);
  await assert.rejects(stageFixtureCA(root,key,await digest(key)),/no other material/);
  await assert.rejects(stageFixtureCA(root,join(root,'absent.pem'),sha256),{code:'ENOENT'});
  const link=join(root,'link.pem'), oversized=join(root,'oversized.pem'), leaf=join(root,'leaf.pem');
  await symlink(caPath,link);
  await assert.rejects(stageFixtureCA(root,link,sha256),/canonical private path/);
  await writeFile(oversized,Buffer.alloc(17*1024),{mode:0o600});
  await assert.rejects(stageFixtureCA(root,oversized,await digest(oversized)),/bounded private/);
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',join(root,'leaf.key'),'-out',leaf,
    '-days','1','-subj','/CN=fixture.internal','-addext','basicConstraints=critical,CA:FALSE'],{stdio:'ignore'});
  execFileSync('chmod',['600',leaf]);
  await assert.rejects(stageFixtureCA(root,leaf,await digest(leaf)),/self-signed CA/);
  const csr=join(root,'fixture.csr'), server=join(root,'fixture.pem');
  execFileSync('openssl',['req','-new','-key',join(root,'leaf.key'),'-out',csr,'-subj','/CN=fixture.internal'],{stdio:'ignore'});
  execFileSync('openssl',['x509','-req','-in',csr,'-CA',caPath,'-CAkey',key,'-CAcreateserial',
    '-days','1','-out',server],{stdio:'ignore'});
  const verify=(trust,host)=>spawnSync('openssl',['verify','-CAfile',trust,'-verify_hostname',host,server],{encoding:'utf8'});
  assert.equal(verify(mount.source,'fixture.internal').status,0);
  assert.notEqual(verify(mount.source,'foreign.internal').status,0);
  assert.notEqual(verify(leaf,'fixture.internal').status,0);
  const neverLaunch=()=>assert.fail('invalid CA reached launcher');
  await assert.rejects(withDisposableLauncher(root,{...binding,fixture:{...fixture,ca:{path:caPath,sha256:'0'.repeat(64)}}},neverLaunch,{}),/approved digest/);
  await assert.rejects(withDisposableLauncher(root,{},neverLaunch,{SCHEMII_QA_FIXTURE_CA_PATH:caPath}),/inherited launcher setting/);
  const checkout=join(root,'checkout');
  execFileSync('git',['init','-q',checkout]);
  await mkdir(join(checkout,'.schemii'),{mode:0o700});
  await writeFile(join(checkout,'.gitignore'),'.schemii/\n');
  execFileSync('git',['-C',checkout,'add','.gitignore']);
  execFileSync('git',['-C',checkout,'-c','user.name=QA','-c','user.email=qa@example.invalid','commit','-qm','fixture']);
  const commit=execFileSync('git',['-C',checkout,'rev-parse','HEAD'],{encoding:'utf8'}).trim();
  const bootId=(await readFile('/proc/sys/kernel/random/boot_id','utf8')).trim();
  const bound=join(root,'binding.json');
  const env={DOCKER_HOST:'unix:///var/run/docker.sock',SCHEMII_QA_GUEST_TARGET_HOSTS:'fixture.internal',
    SCHEMII_ALLOWED_TARGET_HOSTS:'fixture.internal',SCHEMII_TARGET_EGRESS_MODE:'external'};
  const writeBinding = changes=>writeFile(bound,JSON.stringify({...binding,commit,bootId,...changes}),{mode:0o600});
  for (const changes of [{commit:'f'.repeat(40)},{bootId:guest.bootId}]) {
    await writeBinding(changes);
    await assert.rejects(stageBoundFixtureCA(checkout,bound,env),/bound VM boot and clean checkout/);
  }
  await writeBinding({});
  await assert.rejects(stageBoundFixtureCA(checkout,bound,{...env,SCHEMII_ALLOWED_TARGET_HOSTS:'foreign.internal'}),/differ from the approved/);
  await assert.rejects(stageBoundFixtureCA(checkout,bound,{...env,DOCKER_HOST:'unix:///var/run/other.sock'}),/differ from the approved/);
  assert.match(await stageBoundFixtureCA(checkout,bound,env),/compose\.json$/);

});

test('launcher rejects fixture CA from default, shared and incomplete guest launch inputs before engine preflight', () => {
  const marker={SCHEMII_QA_FIXTURE_CA_BINDING:'/private/binding.json'};
  const shared={SCHEMII_QA_LEASE_MODE:'shared',SCHEMII_QA_LEASE_FD:'3',SCHEMII_QA_GATE_FD:'4',
    SCHEMII_QA_GUEST_TARGET_HOSTS:'fixture.internal',SCHEMII_TARGET_EGRESS_MODE:'external',SCHEMII_ALLOWED_TARGET_HOSTS:'fixture.internal'};
  for(const env of [marker,{...marker,...shared}]) {
    const result=spawnSync('bash',['./start.sh'],{env:{PATH:process.env.PATH,...env},encoding:'utf8'});
    assert.equal(result.status,1);
    assert.match(result.stderr,/fixture CA requires an exclusive disposable guest launch/);
  }
  const raw=spawnSync('bash',['./start.sh'],{env:{PATH:process.env.PATH,
    SCHEMII_QA_FIXTURE_CA_PATH:'/private/fixture.pem',SCHEMII_QA_FIXTURE_CA_SHA256:'a'.repeat(64)},encoding:'utf8'});
  assert.equal(raw.status,1);
  assert.match(raw.stderr,/requires a private disposable guest binding/);

});

test('offline build stages only the approved package bytes under the exact guest launch locks', async t => {
  const scratch = await mkdtemp(join(tmpdir(), 'qa-offline-inputs-'));
  t.after(() => rm(scratch, {recursive:true,force:true}));
  const root = join(scratch,'checkout'), bundle = join(scratch,'bundle');
  const config = join(scratch,'docker'), bin = join(scratch,'bin');
  for (const dir of [root,bundle,config,bin]) await mkdir(dir,{mode:0o700});
  await writeFile(join(bin,'systemd-detect-virt'),'#!/bin/sh\nexit 0\n',{mode:0o700});
  const sha = bytes => createHash('sha256').update(bytes).digest('hex');
  const sampleNpm = Buffer.from('synthetic package bytes');
  const npmDigest = createHash('sha512').update(sampleNpm).digest();
  const hex = npmDigest.toString('hex');
  const npmPath = `npm-cache/_cacache/content-v2/sha512/${hex.slice(0,2)}/${hex.slice(2,4)}/${hex.slice(4)}`;
  const lock = JSON.stringify({lockfileVersion:3,packages:{'':{},'node_modules/synthetic':{
    resolved:'https://registry.npmjs.org/synthetic/-/synthetic-1.0.0.tgz',integrity:'sha512-'+npmDigest.toString('base64'),
  }}});
  const constraints = 'synthetic==1.0\n';
  const pyproject = '[build-system]\nrequires = ["setuptools>=80,<85"]\n';
  const images = Object.fromEntries(['node','python','postgres','ingress'].map(name=>[name,name+':1@sha256:'+'a'.repeat(64)]));
  const inputFiles = {
    'ai/prototype/package-lock.json':lock, 'constraints.docker.txt':constraints, 'pyproject.toml':pyproject,
  };
  for (const [file, bytes] of Object.entries({
    ...inputFiles, 'dev/runtime-contract.json':JSON.stringify({images}),
    'Dockerfile':`FROM ${images.python}\nFROM ${images.python}\n`,
    'ai/prototype/Dockerfile':`FROM ${images.node}\n`,
    'compose.test.yaml':`    image: ${images.postgres}\n    image: ${images.ingress}\n`,
    '.gitignore':'.schemii/\n',
  })) {
    const filePath = join(root,file);
    await mkdir(join(filePath,'..'),{recursive:true});
    await writeFile(filePath,bytes);
  }
  execFileSync('git',['init','-q',root]);
  execFileSync('git',['-C',root,'add','.']);
  execFileSync('git',['-C',root,'-c','user.name=QA','-c','user.email=qa@example.invalid','commit','-qm','fixture']);
  await mkdir(join(root,'.schemii'));
  await writeFile(join(root,'.schemii','secret'),'not part of the build context');
  const commit = execFileSync('git',['-C',root,'rev-parse','HEAD'],{encoding:'utf8'}).trim();
  const boots = (await readFile('/proc/sys/kernel/random/boot_id','utf8')).trim();
  // These four-byte markers test staging and custody only. No package install is attempted.
  const zip = Buffer.from([0x50,0x4b,0x03,0x04]);
  const wheel = 'wheelhouse/synthetic-1.0-py3-none-any.whl';
  const files = {[npmPath]:sampleNpm,[wheel]:zip,'wheelhouse/setuptools-84.2.1-py3-none-any.whl':zip};
  for (const [name, bytes] of Object.entries(files)) {
    await mkdir(join(bundle,name,'..'),{recursive:true,mode:0o700});
    await writeFile(join(bundle,name),bytes,{mode:0o600});
  }
  const manifest = {
    version:1, images, locks:Object.fromEntries(Object.entries(inputFiles).map(([name,bytes])=>[name,sha(bytes)])),
    members:Object.entries(files).map(([path,bytes])=>({path,bytes:bytes.length,sha256:sha(bytes)})),
  };
  const run = async ({document=manifest, changes={}, held=true, mode='exclusive', wrongDigest=false, env={}}={}) => {
    const manifestPath = join(bundle,'manifest.json'), bound = join(scratch,'binding.json');
    const data = JSON.stringify(document);
    await writeFile(manifestPath,data,{mode:0o600});
    const offlineBuild = {manifest:manifestPath,sha256:wrongDigest ? '0'.repeat(64) : sha(data)};
    const binding = {bootId:boots,commit,certificateSha256:'a'.repeat(64),offlineBuild,...changes};
    await writeFile(bound,JSON.stringify(binding),{mode:0o600});
    const stage = await mkdtemp(join(scratch,'stage-'));
    const result = spawnSync('bash',['-ec',
      'umask 077; exec 3>.git/qa-deployment.lock; exec 4>.git/qa-startup.lock; ' +
      (held ? 'flock -x 3; flock -x 4; ' : '') + 'exec "$@"',
      'test',process.execPath,new URL('./deployment.mjs',import.meta.url).pathname,'stage-offline-build',root,stage],{
      cwd:root, env:{PATH:bin+':'+process.env.PATH,DOCKER_CONFIG:config,DOCKER_HOST:'unix:///var/run/docker.sock',
        SCHEMII_QA_LEASE_MODE:mode, SCHEMII_QA_LEASE_FD:'3',SCHEMII_QA_GATE_FD:'4',
        SCHEMII_QA_OFFLINE_BUILD_BINDING:bound,...env}, encoding:'utf8',timeout:7000,
    });
    return {stage,result};
  };
  const passed = await run();
  assert.equal(passed.result.status,0,passed.result.stderr);
  const context = join(passed.stage,'context');
  assert.deepEqual((await readdir(context)).sort(),['.gitignore','Dockerfile','ai','compose.test.yaml','constraints.docker.txt','dev','pyproject.toml','qa-build-inputs']);
  assert.equal((await stat(join(context,'Dockerfile'))).mode & 0o777,0o644);
  assert.deepEqual(await readFile(join(context,'qa-build-inputs',npmPath)),sampleNpm);
  assert.equal((await stat(join(context,'qa-build-inputs',wheel))).mode & 0o777,0o444);
  const overlay=JSON.parse(await readFile(join(passed.stage,'compose.json'),'utf8'));
  const services=overlay.services;
  assert.deepEqual(Object.keys(services),['schemii','ai-prototype-runtime']);
  for (const [name, dockerfile] of [['schemii','Dockerfile'],['ai-prototype-runtime','ai/prototype/Dockerfile']]) {
    services[name].build.dockerfile = dockerfile;
    services[name].build.args.SCHEMII_SOURCE_COMMIT = commit;
  }
  verifyOfflineBuildConfig(overlay,passed.stage,commit);
  services.schemii.build.network='default';
  assert.throws(()=>verifyOfflineBuildConfig(overlay,passed.stage,commit),/approved offline configuration/);
  services.schemii.build.network='none';
  delete services.schemii.build.args.SCHEMII_BUILD_MODE;
  assert.throws(()=>verifyOfflineBuildConfig(overlay,passed.stage,commit),/approved offline configuration/);
  const missing = {...manifest,members:manifest.members.slice(1)};
  const foreign = structuredClone(manifest);
  foreign.members[1].path='wheelhouse/../private.pem';
  for (const options of [
    {held:false}, {mode:'shared'}, {changes:{bootId:'a'.repeat(8)+'-0000-0000-0000-000000000000'}},
    {changes:{commit:'b'.repeat(40)}}, {wrongDigest:true}, {document:missing}, {document:foreign},
    {env:{SCHEMII_SECRET_DIRECTORY:'/private/foreign-secrets'}},
    {document:{...manifest,locks:{...manifest.locks,'constraints.docker.txt':'0'.repeat(64)}}},
    {document:{...manifest,images:{...manifest.images,node:images.python}}},
  ]) {
    const {stage,result}=await run(options);
    assert.equal(result.status,1,result.stderr);
    await assert.rejects(readFile(join(stage,'compose.json')),{code:'ENOENT'});
  }
  await rm(join(bundle,wheel));
  await symlink(join(bundle,npmPath),join(bundle,wheel));
  assert.equal((await run()).result.status,1);
  await rm(join(bundle,wheel));
  await writeFile(join(bundle,wheel),Buffer.from('wrong package bytes'),{mode:0o600});
  assert.equal((await run()).result.status,1);
  assert.throws(()=>assertNoAmbientFixtureCA({SCHEMII_QA_OFFLINE_BUILD_BINDING:'/private/offline.json'}),/not ambient/);
});
