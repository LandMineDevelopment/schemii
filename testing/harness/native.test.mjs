import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, utimes, rm, symlink, rename, lstat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { workspaceNavigationHref } from '../../src/schemii/schemii/web/assets/workspace-navigation.js';
import { recordWorkerExit, recordWorkerBrowserClosure } from './leases.mjs';
import { writeJSON } from './store.mjs';
import { bindNative, readNativeSession, processIdentity, nativeBrowserProcesses, closeNativeReceipt, releaseNativeTransport, beginScenario, assertCaptureState, importNativeFile, currentCapture, inspectionReceipt, inspectDownload, pendingScenarios, recordReview, recordNativeResource, nativeBrowserRoots, recoverNativeLane, acceptance } from './native.mjs';

const fingerprint = 'a'.repeat(64);
function fixture() {
  const lane = { id:'lane-1', agent:'actual-tester-id', username:'qa_designer_005', role:'tester', status:'claimed', generation:2,
    url:'/?workspace=owned-5', resources:{ workspace:'owned-5' }, deniedProducts:[], captures:[],
    scenarios:[{id:'roundtrip-schemii-desktop',product:'schemii',viewport:{width:1280,height:800},functional:'not-run',visual:'not-run'}] };
  const reviewer = { ...lane, id:'lane-2', agent:'actual-reviewer-id', username:'qa_designer_007', role:'reviewer', native:{sessionId:'reviewer-session'}, captures:[] };
  const run = { browser:'native', controller:'t3', baseURL:'https://localhost:8001', deployment:{ identity:{fingerprint} }, lanes:[lane,reviewer] };
  return { run, lane, reviewer, scenario:lane.scenarios[0] };
}
async function sessionFixture(t) {
  const directory = await mkdtemp(join(tmpdir(),'schemii-native-test-'));
  t.after(() => rm(directory,{recursive:true,force:true}));
  const browserRoot = join(directory,'native-browsers'), session = join(browserRoot,`session-${'b'.repeat(32)}`);
  await mkdir(join(session,'output'),{recursive:true,mode:0o700});
  const identity = await processIdentity(process.pid);
  await writeFile(join(session,'session.json'),JSON.stringify({schema:1,session_id:session.split('/').at(-1),state:'running',pid:process.pid,birth_tick:Number(identity.birthTick),child_pid:process.pid,child_birth_tick:Number(identity.birthTick)}),{mode:0o600});
  return { directory, browserRoot, session };
}
function png(width=1280,height=800) {
  const bytes = Buffer.alloc(24); Buffer.from('89504e470d0a1a0a','hex').copy(bytes); bytes.writeUInt32BE(width,16);bytes.writeUInt32BE(height,20);return bytes;
}
function captureFixture() {
  const f=fixture(); beginScenario(f.run,f.lane,f.scenario.id);
  const capture={...assertCaptureState(f.run,f.lane,{url:f.lane.url,viewport:f.scenario.viewport}),kind:'image',path:'lane-1/fresh.png',sha256:'c'.repeat(64)};
  f.lane.captures.push(capture);return {...f,capture};
}

test('native claims bind live private unique connection ownership; stale and peer bindings fail',async t=>{
  const f=fixture(), fs=await sessionFixture(t);
  const binding=await bindNative(f.run,f.lane,fs.session,fs.browserRoot);
  assert.equal(binding.agent,'actual-tester-id');assert.equal(binding.generation,2);
  delete f.reviewer.native;
  await assert.rejects(bindNative(f.run,f.reviewer,fs.session,fs.browserRoot),/cannot be shared/);
  const path=join(fs.session,'session.json'), metadata=JSON.parse(await readFile(path)); metadata.birth_tick++;
  await writeFile(path,JSON.stringify(metadata));
  await assert.rejects(readNativeSession(fs.session,fs.browserRoot),/ownership is stale/);
});

test('wrong resource/route, viewport, unexpected denial and source drift reject evidence',()=>{
  const {run,lane,scenario}=fixture();
  assert.throws(()=>assertCaptureState(run,lane,{url:lane.url,viewport:scenario.viewport}),/scenario-begin/);
  beginScenario(run,lane,scenario.id);
  for(const url of ['/?workspace=someone-else','/account','https://example.test/?workspace=owned-5']) assert.throws(()=>assertCaptureState(run,lane,{url,viewport:scenario.viewport}),/route\/resource/);
  assert.throws(()=>assertCaptureState(run,lane,{url:lane.url,viewport:{width:390,height:844}}),/viewport/);
  assert.throws(()=>beginScenario(run,lane,scenario.id,{expectedState:'denied'}),/explicitly expected/);
  run.deployment.identity.fingerprint='d'.repeat(64);
  assert.throws(()=>assertCaptureState(run,lane,{url:lane.url,viewport:scenario.viewport}),/generation\/source/);
});

test('real UI table selection imports an owned inspector PNG and retains its actual URL',async t=>{
  const f=fixture(), fs=await sessionFixture(t), workspace=`ws_${'6'.repeat(32)}`, tableId=`table_${'9'.repeat(32)}`;
  f.lane.url=`/?workspace=${workspace}`;f.lane.resources={localWorkspaceId:workspace};
  await bindNative(f.run,f.lane,fs.session,fs.browserRoot);
  beginScenario(f.run,f.lane,f.scenario.id,{},new Date(Date.now()-1000).toISOString());
  const url=workspaceNavigationHref(new URL(f.lane.url,f.run.baseURL),{workspaceId:workspace,layer:'tables',tableId,table:'qa_pilot_items'});
  assert.equal(url,`/?workspace=${workspace}&tableId=${tableId}&table=qa_pilot_items`);
  const file=join(fs.session,'output','inspector.png');await writeFile(file,png());
  const capture=await importNativeFile(f.run,f.lane,file,{runDir:join(fs.directory,'qa-test'),browserRoot:fs.browserRoot,url,viewport:f.scenario.viewport});
  assert.equal(capture.url,new URL(url,f.run.baseURL).href);
  assert.equal(f.lane.currentScenario.url,new URL(f.lane.url,f.run.baseURL).href);
  assert.equal(capture.scenarioAttempt,f.lane.currentScenario.id);
});

test('workspace selection parameters stay bounded to the exact assigned product/resource/state',()=>{
  const f=fixture(), workspace=`ws_${'6'.repeat(32)}`, other=`ws_${'7'.repeat(32)}`;
  f.lane.url=`/?workspace=${workspace}`;f.lane.resources={localWorkspaceId:workspace};beginScenario(f.run,f.lane,f.scenario.id);
  const capture=url=>assertCaptureState(f.run,f.lane,{url,viewport:f.scenario.viewport});
  for(const suffix of ['&table=order+items','&layer=tables&tableId=table-1','&layer=views&viewId=view-1&view=monthly_sales&viewKind=materialized_view','&layer=sql']) {
    assert.equal(capture(f.lane.url+suffix).url,new URL(f.lane.url+suffix,f.run.baseURL).href);
  }
  for(const url of [
    `/?workspace=${other}&table=qa_pilot_items`,
    `/schemoo?workspace=${workspace}&table=qa_pilot_items`,
    `/account?workspace=${workspace}&table=qa_pilot_items`,
    `https://example.test/?workspace=${workspace}&table=qa_pilot_items`,
    `https://someone@localhost:8001/?workspace=${workspace}&table=qa_pilot_items`,
    f.lane.url+'&workspace='+other,
    f.lane.url+'&table=a&table=b',
    f.lane.url+'&table=',f.lane.url+'&table='+('x'.repeat(257)),
    f.lane.url+'&layer=unknown',f.lane.url+'&layer=sql&table=qa_pilot_items',
    f.lane.url+'&view=qa_pilot_items',f.lane.url+'&layer=views&viewKind=unknown',
    f.lane.url+'&table=qa_pilot_items&redirect=other',f.lane.url+'#sql=SELECT+1',
  ]) assert.throws(()=>capture(url),/route\/resource/,url);
  assert.throws(()=>beginScenario(f.run,f.lane,f.scenario.id,{url:`/?workspace=${other}`}),/explicitly assigned/);
  const valid=f.lane.url+'&table=qa_pilot_items';
  assert.throws(()=>assertCaptureState(f.run,f.lane,{url:valid,viewport:{width:390,height:844}}),/viewport/);
  f.lane.generation++;assert.throws(()=>capture(valid),/generation\/source/);f.lane.generation--;
  f.run.deployment.identity.fingerprint='b'.repeat(64);assert.throws(()=>capture(valid),/generation\/source/);f.run.deployment.identity.fingerprint=fingerprint;
  f.scenario.product='schemoo';assert.throws(()=>capture(valid),/route\/resource/);f.scenario.product='schemii';
  f.lane.deniedProducts=['schemii'];beginScenario(f.run,f.lane,f.scenario.id,{expectedState:'denied'});
  assert.equal(capture('/account').url,new URL('/account',f.run.baseURL).href);
  assert.throws(()=>capture('/account?table=qa_pilot_items'),/route\/resource/);
  assert.throws(()=>capture(valid),/route\/resource/);
});

test('explicit scenario selection and fixed query values cannot drift during workspace captures',()=>{
  const f=fixture(), workspace=`ws_${'6'.repeat(32)}`;
  f.lane.url=`/?workspace=${workspace}&campaign=preview&tableId=table-1`;beginScenario(f.run,f.lane,f.scenario.id);
  const capture=url=>assertCaptureState(f.run,f.lane,{url,viewport:f.scenario.viewport});
  assert.ok(capture(f.lane.url+'&table=qa_pilot_items'));
  assert.throws(()=>capture(f.lane.url.replace('table-1','table-2')+'&table=qa_pilot_items'),/route\/resource/);
  assert.throws(()=>capture(f.lane.url.replace('preview','other')+'&table=qa_pilot_items'),/route\/resource/);
  assert.throws(()=>capture(f.lane.url.replace('&tableId=table-1','')+'&table=qa_pilot_items'),/route\/resource/);
});

test('explicit expected denied state is current scenario evidence, never a blanket account-page pass',()=>{
  const f=fixture();f.lane.deniedProducts=['schemii'];
  beginScenario(f.run,f.lane,f.scenario.id,{expectedState:'denied'});
  assert.equal(assertCaptureState(f.run,f.lane,{url:'/account',viewport:f.scenario.viewport}).scenario,f.scenario.id);
  assert.throws(()=>assertCaptureState(f.run,f.lane,{url:f.lane.url,viewport:f.scenario.viewport}),/route\/resource/);
});

test('fresh native PNG exports to exact lane with hash; stale output/different viewport and symlinks fail',async t=>{
  const f=fixture(), fs=await sessionFixture(t);
  await bindNative(f.run,f.lane,fs.session,fs.browserRoot);
  beginScenario(f.run,f.lane,f.scenario.id,{},new Date(Date.now()-1000).toISOString());
  const file=join(fs.session,'output','fresh.png');await writeFile(file,png());
  const options={runDir:join(fs.directory,'qa-test'),browserRoot:fs.browserRoot,url:f.lane.url,viewport:f.scenario.viewport};
  const capture=await importNativeFile(f.run,f.lane,file,options);
  assert.match(capture.path,/^lane-1\/image-/);assert.equal(capture.sha256,createHash('sha256').update(png()).digest('hex'));
  assert.deepEqual(await readFile(join(options.runDir,capture.path)),png());
  await utimes(file,new Date(0),new Date(0));
  await assert.rejects(importNativeFile(f.run,f.lane,file,options),/fresh/);
  await writeFile(file,png(390,844));
  await assert.rejects(importNativeFile(f.run,f.lane,file,options),/PNG dimensions/);
});

test('exact old desktop product capture cannot satisfy a new scenario or generation',()=>{
  const f=captureFixture();inspectionReceipt(f.capture,f.lane.agent,{tool:'view_image',invocation:'actual image tool',note:'Expected canvas fully visible; actual no clipping.'});
  assert.equal(currentCapture(f.run,f.lane,f.capture.path,{inspection:true}),f.capture);
  beginScenario(f.run,f.lane,f.scenario.id);
  assert.throws(()=>currentCapture(f.run,f.lane,f.capture.path,{inspection:true}),/current scenario/);
  f.lane.currentScenario.scenario=f.capture.scenario;f.lane.currentScenario.id=f.capture.scenarioAttempt;f.lane.generation++;
  assert.throws(()=>currentCapture(f.run,f.lane,f.capture.path),/current scenario/);
});

test('visual evidence requires image delivery/view invocation and concrete inspection, not arbitrary notes',()=>{
  const f=captureFixture();
  assert.throws(()=>currentCapture(f.run,f.lane,f.capture.path,{inspection:true}),/inspection/);
  assert.throws(()=>inspectionReceipt(f.capture,f.lane.agent,{tool:'notes',invocation:'x',note:'looks good'}),/native inline/);
  assert.throws(()=>inspectionReceipt(f.capture,f.lane.agent,{tool:'view_image',invocation:'',note:'looks good'}),/invocation/);
  const receipt=inspectionReceipt(f.capture,f.lane.agent,{tool:'native-inline-image',invocation:'native screenshot result',note:'Expected readable save dialog; actual readable and no overlap.'});
  assert.equal(receipt.sha256,f.capture.sha256);assert.equal(receipt.assurance,'worker-attestation-requires-independent-review');
});

test('wrong/truncated downloads fail exact byte/value oracles; contents stay private',async t=>{
  const fs=await sessionFixture(t), path='download.json', bytes=Buffer.from('{"rows":[1,2]}');await writeFile(join(fs.directory,path),bytes);
  const capture={kind:'download',path,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
  await assert.rejects(inspectDownload(capture,fs.directory,{expected:{json:{rows:[1,2,3]}}}),/JSON/);
  await assert.rejects(inspectDownload(capture,fs.directory,{expected:{text:'truncated'}}),/text/);
  const result=await inspectDownload(capture,fs.directory,{expected:{json:{rows:[1,2]}}});assert.equal(result.result,'passed');assert.equal(result.expected,undefined);
  await writeFile(join(fs.directory,path),'{"rows":[]}');
  await assert.rejects(inspectDownload(capture,fs.directory,{expected:{json:{rows:[]}}}),/changed/);
});

function attemptedFixture() {
  const f=captureFixture();f.lane.native={sessionId:'tester-session',authentication:{source:fingerprint},cleanup:{transport:'stopped',guardian:'stopped',temporaryOutput:'removed-observed'}};
  f.reviewer.native.cleanup={transport:'stopped',guardian:'stopped',temporaryOutput:'removed-observed'};
  Object.assign(f.scenario,{functional:'passed',visual:'passed',attempts:[{id:'attempt-1',functional:'passed',visual:'passed',generation:2,scenarioAttempt:f.capture.scenarioAttempt,source:fingerprint,reviewer:f.lane.agent,evidence:[f.capture.path]}]});
  return f;
}

test('execution completion stays pending; self-review and stale capture review are rejected',()=>{
  const f=attemptedFixture();assert.equal(acceptance(f.run).status,'review-pending');
  assert.throws(()=>recordReview(f.run,f.lane,f.lane,f.scenario.id,{verdict:'accepted',note:'Self review'}),/distinct/);
  assert.throws(()=>recordReview(f.run,f.reviewer,f.lane,f.scenario.id,{verdict:'accepted',note:'Expected and actual match'}),/inspect the target/);
  inspectionReceipt(f.capture,f.reviewer.agent,{tool:'view_image',invocation:'review tool result',note:'Expected full canvas; actual full canvas.'});
  f.capture.generation=1;
  assert.throws(()=>recordReview(f.run,f.reviewer,f.lane,f.scenario.id,{verdict:'accepted',note:'Expected and actual match'}),/inspect the target/);
});

test('distinct hash-bound reviewer persists acceptance, findings and transport cleanup remain separate gates',()=>{
  const f=attemptedFixture();inspectionReceipt(f.capture,f.reviewer.agent,{tool:'view_image',invocation:'review tool result',note:'Expected full canvas; actual full canvas.'});
  recordReview(f.run,f.reviewer,f.lane,f.scenario.id,{verdict:'accepted',note:'Reopened saved design; correct table and geometry.'});
  assert.equal(acceptance(JSON.parse(JSON.stringify(f.run))).status,'reviewed-acceptance');
  f.run.findings=[{id:'finding-1',lane:f.lane.id,scenario:f.scenario.id,verificationStatus:'unverified'}];
  assert.match(acceptance(f.run).reasons.join(),/adjudication/);
  f.run.findings=[];f.lane.native.cleanup.transport='live';
  assert.match(acceptance(f.run).reasons.join(),/cleanup pending/);
});

function reviewedLegacyFixture() {
  const f=attemptedFixture();f.run.browser='isolated';f.run.controller='codex';
  inspectionReceipt(f.capture,f.reviewer.agent,{tool:'view_image',invocation:'independent recorded image view',note:'Expected owned saved table; actual owned saved table.'});
  recordReview(f.run,f.reviewer,f.lane,f.scenario.id,{verdict:'accepted',note:'Independent owned persistence and visual state match.'});
  return f;
}
test('reviewed legacy and native cases stay pending for owned lifecycle, crash and recording cleanup',()=>{
  const f=reviewedLegacyFixture();f.lane.status='paused';
  assert.equal(acceptance(f.run).status,'reviewed-acceptance');
  for(const status of ['termination-requested','cleanup-pending','controller-intervention-required']) {
    f.lane.workerLifecycle={status,generation:1,reason:'lease-expired',error:'Controlled unresolved closure'};
    assert.equal(acceptance(f.run).status,'review-pending');assert.match(acceptance(f.run).reasons.join(),/owned worker cleanup pending/);
  }
  delete f.lane.workerLifecycle;f.lane.workerExit={cleanup:'pending'};
  assert.match(acceptance(f.run).reasons.join(),/worker\/recording drain pending/);
  f.run.browser='native';assert.equal(acceptance(f.run).status,'review-pending');
  delete f.lane.workerExit;f.lane.workerLifecycle={status:'stopped',recording:'unavailable'};
  assert.match(acceptance(f.run).reasons.join(),/recording unavailable/);
  f.lane.workerLifecycle={status:'stopped',workerCleanup:'pending',browserCleanup:'pending'};
  assert.match(acceptance(f.run).reasons.join(),/worker\/recording stage pending/);assert.match(acceptance(f.run).reasons.join(),/browser closure pending/);
  delete f.lane.workerLifecycle;f.reviewer.workerLifecycle={status:'cleanup-pending'};
  assert.match(acceptance(f.run).reasons.join(),/lane-2: owned worker cleanup pending/);
});
test('acceptance waits for matching worker and browser drain; old generation cannot clear a fresh owner',()=>{
  const f=reviewedLegacyFixture(), owner={pid:123,birthTick:'456',generation:2};f.lane.worker=owner;
  const exit={...owner,laneId:f.lane.id,code:0,reason:'completed',cleanup:'pending'};
  assert.match(acceptance(f.run).reasons.join(),/release unobserved/);
  recordWorkerExit(f.lane,exit);assert.equal(acceptance(f.run).status,'review-pending');
  recordWorkerExit(f.lane,{...exit,cleanup:'stopped'});
  assert.equal(f.lane.workerLifecycle.status,'cleanup-pending');assert.equal(acceptance(f.run).status,'review-pending');
  recordWorkerBrowserClosure(f.lane,owner);assert.equal(acceptance(f.run).status,'reviewed-acceptance');
  f.lane.worker={...owner,generation:3};f.lane.generation=3;
  const fresh={...exit,generation:3,cleanup:'pending'};recordWorkerExit(f.lane,fresh);
  assert.equal(recordWorkerExit(f.lane,{...exit,cleanup:'stopped'}),false);
  assert.equal(recordWorkerBrowserClosure(f.lane,owner),false);
  assert.equal(f.lane.workerExit.generation,3);assert.equal(f.lane.workerExit.cleanup,'pending');assert.equal(acceptance(f.run).status,'review-pending');
  assert.equal(f.lane.workerExitHistory.at(-1).applied,false);
  recordWorkerExit(f.lane,{...fresh,cleanup:'stopped'});recordWorkerBrowserClosure(f.lane,f.lane.worker);
  assert.equal(acceptance(f.run).status,'reviewed-acceptance');
  recordWorkerExit(f.lane,{...fresh,code:7,reason:'agent-failed',cleanup:'stopped'});
  assert.equal(acceptance(f.run).status,'review-pending');assert.match(acceptance(f.run).reasons.join(),/worker execution failed.*exit 7/);
  assert.equal(f.lane.workerExit.code,7);assert.equal(f.scenario.functional,'passed');
  // A new recorded pool owner can crash before start() publishes its return.
  f.lane.generation=4;f.lane.workerLaunchPending=true;
  const startup={...fresh,pid:999,birthTick:'888',generation:4,code:7,reason:'agent-failed',cleanup:'pending'};
  assert.equal(recordWorkerExit(f.lane,startup),true);assert.equal(f.lane.worker.pid,999);assert.equal(f.lane.workerExit.generation,4);
  assert.equal(recordWorkerExit(f.lane,{...fresh,cleanup:'stopped'}),false);assert.equal(f.lane.workerExit.cleanup,'pending');
  const outstanding=f.lane.workerLifecycle;f.lane.worker={...f.lane.worker,generation:5};f.lane.generation=5;
  const replacement={...startup,generation:5,code:0,reason:'completed',cleanup:'stopped'};
  recordWorkerExit(f.lane,replacement);recordWorkerBrowserClosure(f.lane,f.lane.worker);
  assert.ok(f.lane.workerLifecycleHistory.includes(outstanding));assert.equal(outstanding.status,'cleanup-pending');
  assert.match(acceptance(f.run).reasons.join(),/earlier owned worker cleanup pending/);
});

test('recovery briefs include pending scenarios only and preserve failed/completed history',()=>{
  const f=fixture();f.lane.scenarios.push({...f.scenario,id:'completed',functional:'failed',visual:'passed',note:'Initial failure preserved.'});
  assert.deepEqual(pendingScenarios(f.lane).map(s=>s.id),[f.scenario.id]);
  assert.equal(f.lane.scenarios[1].functional,'failed');
});

test('MCP browser argv flags do not identify a live Chromium executable',async t=>{
  const {spawn}=await import('node:child_process');
  const child=spawn('python3',['-u','-c','import sys;print("ready",flush=True);sys.stdin.read()','--browser','chromium'],{stdio:['pipe','pipe','pipe']});
  const exited=new Promise(resolve=>child.once('exit',resolve));
  t.after(async()=>{child.stdin.end();await exited;});
  await new Promise((resolve,reject)=>{child.stdout.once('data',resolve);child.once('error',reject);});
  const owner=await processIdentity(child.pid);
  assert.deepEqual(await nativeBrowserProcesses({childPid:child.pid,childBirthTick:owner.birthTick}),[]);
});

test('present-generation release preserves a backend reopened after validation and a live peer',async t=>{
  const relay=await relayFixture(t),f=fixture(),roots=[relay.owner.slice(0,relay.owner.lastIndexOf('/')),relay.peer.slice(0,relay.peer.lastIndexOf('/'))];
  await bindNative(f.run,f.lane,relay.owner,roots);
  const old={...f.lane.native};let reopened;
  // The persistence boundary is after present-generation validation but before
  // the coordinator receives its result. A supported close/reopen can occur
  // here; historical validation must never authorize terminating that backend.
  await assert.rejects(releaseNativeTransport(f.lane,roots,{persist:async()=>{
    await relay.call('owner','browser_close');reopened=(await relay.call('owner','navigate')).ownership;
  }}),/browser_release/);
  assert.notEqual(reopened.directory,old.directory);
  assert.equal((await processIdentity(old.pid)).birthTick,old.birthTick);
  assert.equal((await processIdentity(reopened.metadata.child_pid)).birthTick,String(reopened.metadata.child_birth_tick));
  assert.ok(await lstat(reopened.directory));assert.equal(f.lane.native.termination,undefined);
  await relay.call('owner','navigate');await relay.call('peer','navigate');
  await assert.rejects(releaseNativeTransport(f.lane,roots),/browser_release/);
  await relay.call('owner','browser_release');await releaseNativeTransport(f.lane,roots);
  await relay.call('peer','navigate');await relay.call('peer','browser_release');
});

async function relayFixture(t) {
  const {spawn} = await import('node:child_process');
  const {createInterface} = await import('node:readline');
  // Reuse the existing disposable backend fixture while exercising the actual
  // production Python Backend/Relay. No Chromium, app, account or inference.
  const script = `import json,sys
from testing.agents.test_browser import RelayProcessTests
fixtures={}
try:
 for name in ('owner','peer'):
  fixture=RelayProcessTests();fixture.setUp();fixtures[name]=fixture
  fixture.launch()
 print(json.dumps({name:{'directory':str(fixture.session()[0]),'metadata':fixture.session()[1]} for name,fixture in fixtures.items()}),flush=True)
 for index,line in enumerate(sys.stdin,2):
  request=json.loads(line);fixture=fixtures[request['name']]
  fixture.call(index,request['tool'])
  response=fixture.receive(lambda message:message.get('id')==index)
  if request['tool']=='browser_release': fixture.process.wait(timeout=5)
  if request['tool'] not in ('browser_close','browser_release'):
   path,metadata=fixture.session();response['ownership']={'directory':str(path),'metadata':metadata}
  print(json.dumps(response),flush=True)
finally:
 for fixture in fixtures.values(): fixture.doCleanups()
`;
  const child = spawn('python3',['-u','-c',script],{cwd:new URL('../..',import.meta.url),stdio:['pipe','pipe','pipe']});
  const lines=[],waiters=[];let failure,stderr='';
  child.stderr.on('data',chunk=>stderr+=chunk);
  const reader=createInterface({input:child.stdout});
  reader.on('line',line=>{if(waiters.length)waiters.shift().resolve(JSON.parse(line));else lines.push(JSON.parse(line));});
  child.once('error',error=>{failure=error;for(const waiter of waiters.splice(0))waiter.reject(error);});
  const exited = new Promise(resolve=>child.once('exit',(code,signal)=>{
    failure=new Error(`Disposable relay driver exited (${code ?? signal}): ${stderr}`);
    for(const waiter of waiters.splice(0))waiter.reject(failure);resolve();
  }));
  t.after(async()=>{child.stdin.end();await exited;reader.close();});
  const receive=()=>lines.length?Promise.resolve(lines.shift()):failure?Promise.reject(failure):new Promise((resolve,reject)=>waiters.push({resolve,reject}));
  const initial=await receive(),captured={owner:[initial.owner],peer:[initial.peer]};
  return {owner:initial.owner.directory,peer:initial.peer.directory,call:async(name,tool)=>{
    child.stdin.write(JSON.stringify({name,tool})+'\n');const response=await receive();
    assert.ok(!response.error && response.result?.isError!==true,JSON.stringify(response));
    if(response.ownership && !captured[name].some(owner=>owner.directory===response.ownership.directory)) captured[name].push(response.ownership);
    if(tool==='browser_release') for(const owner of captured[name]) {
      await assert.rejects(lstat(owner.directory),{code:'ENOENT'});
      for(const [pid,tick] of [['pid','birth_tick'],['child_pid','child_birth_tick'],['guardian_pid','guardian_birth_tick']]) {
        assert.notEqual((await processIdentity(owner.metadata[pid]))?.birthTick,String(owner.metadata[tick]),'Terminal release retained a captured process identity');
      }
    }
    return response;
  }};
}

test('browser-close release requires the owning terminal tool, preserves pending state and is idempotent after exit',async t=>{
  const relay=await relayFixture(t), f=fixture(), roots=[relay.owner.slice(0,relay.owner.lastIndexOf('/')),relay.peer.slice(0,relay.peer.lastIndexOf('/'))];
  await bindNative(f.run,f.lane,relay.owner,roots);
  const captured={...f.lane.native};
  await relay.call('owner','browser_close');
  const finished=await closeNativeReceipt(f.lane);
  assert.equal(finished.context,'closed-observed');assert.equal(finished.transport,'live');
  assert.equal(finished.guardian,'stopped');assert.equal(finished.temporaryOutput,'removed-observed');
  f.lane.status='complete';f.lane.generation++;
  const receipt=join(roots[0],'pending-release.json');
  await assert.rejects(releaseNativeTransport(f.lane,roots,{persist:()=>writeJSON(receipt,f.lane)}),/browser_release/);
  const persisted=JSON.parse(await readFile(receipt));
  assert.equal(persisted.native.cleanup.transport,'live');assert.equal(persisted.native.cleanup.temporaryOutput,'removed-observed');
  assert.equal(persisted.native.releaseAttempts.at(-1).status,'pending');
  assert.match(persisted.native.releaseAttempts.at(-1).error,/browser_release/);
  assert.equal(f.lane.native.termination,undefined);
  assert.equal((await processIdentity(captured.pid)).birthTick,captured.birthTick);
  await relay.call('peer','navigate'); // A separate live connection remains usable.
  await relay.call('owner','browser_release');
  const cleanup=await releaseNativeTransport(f.lane,roots);
  assert.equal(cleanup.transport,'stopped');assert.equal(cleanup.guardian,'stopped');assert.equal(cleanup.temporaryOutput,'removed-observed');
  const repeated=await releaseNativeTransport(f.lane,roots);
  assert.equal(repeated.transport,'stopped');assert.equal(f.lane.native.termination,undefined);
  assert.equal(f.lane.native.releaseAttempts[0].status,'pending');
  assert.ok(f.lane.native.releaseAttempts.slice(1).every(attempt=>attempt.status==='complete'));
  await relay.call('peer','navigate');await relay.call('peer','browser_release');
});

test('removed generation never authorizes killing a reopened endpoint or a reused PID',async t=>{
  const relay=await relayFixture(t), f=fixture(), roots=[relay.owner.slice(0,relay.owner.lastIndexOf('/')),relay.peer.slice(0,relay.peer.lastIndexOf('/'))];
  await bindNative(f.run,f.lane,relay.owner,roots);
  const captured={...f.lane.native};
  await relay.call('owner','browser_close');await relay.call('owner','navigate');
  await assert.rejects(releaseNativeTransport(f.lane,roots),/browser_release/);
  assert.equal(f.lane.native.termination,undefined);
  await relay.call('owner','navigate');await relay.call('peer','navigate');
  // A numeric PID now representing another birth is not our captured endpoint.
  f.lane.native.birthTick=String(BigInt(captured.birthTick)-1n);
  const cleanup=await releaseNativeTransport(f.lane,roots);
  assert.equal(cleanup.transport,'stopped');
  assert.equal((await processIdentity(captured.pid)).birthTick,captured.birthTick);
  await relay.call('owner','navigate');await relay.call('peer','navigate');
  await relay.call('owner','browser_release');await relay.call('peer','browser_release');
});

test('release keeps strict present-generation inode, metadata, symlink and registered-root checks',async t=>{
  const f=fixture(), fs=await sessionFixture(t);
  await bindNative(f.run,f.lane,fs.session,fs.browserRoot);
  const metadata=join(fs.session,'session.json'),original=await readFile(metadata);
  await rm(metadata);
  await assert.rejects(releaseNativeTransport(f.lane,fs.browserRoot),/ENOENT/);
  await writeFile(metadata,original,{mode:0o600});
  await rename(fs.session,fs.session+'-original');await symlink(fs.session+'-original',fs.session);
  await assert.rejects(releaseNativeTransport(f.lane,fs.browserRoot),/symlinks/);
  await rm(fs.session);await rename(fs.session+'-original',fs.session);
  f.lane.native.ino++;
  await assert.rejects(releaseNativeTransport(f.lane,fs.browserRoot),/ownership changed/);
  await assert.rejects(releaseNativeTransport(f.lane,join(fs.directory,'other-root')),/registered project checkout/);
  assert.equal(f.lane.native.termination,undefined);
  // An earlier saved completion cannot clear a newly refused release attempt.
  f.lane.native.cleanup={transport:'stopped',guardian:'stopped',temporaryOutput:'removed-observed'};
  assert.match(acceptance(f.run).reasons.join(),/native transport cleanup pending/);
});


test('workspace selection requires exact declared or fresh owned creation receipt',async t=>{
  const f=fixture(), fs=await sessionFixture(t);f.run.createdAt=new Date(Date.now()-2000).toISOString();
  f.lane.resources={localWorkspaceId:'owned-5',scratchPrefix:'qa_owned_005_'};
  f.lane.writeAuthorization={enabled:true,operations:['create/edit/save own local design']};
  await bindNative(f.run,f.lane,fs.session,fs.browserRoot);
  beginScenario(f.run,f.lane,f.scenario.id,{url:'/?workspace=owned-5'});
  const id=`ws_${'1'.repeat(32)}`,url=`/?workspace=${id}`,file=join(fs.session,'output','created.png');
  await writeFile(file,png());
  const input={kind:'workspace',id,name:'qa_owned_005_import',createdAt:new Date(Date.now()-1000).toISOString(),url,file,viewport:f.scenario.viewport,invocation:'actual creation snapshot',note:'Expected own import workspace; actual own named workspace.'};
  assert.throws(()=>beginScenario(f.run,f.lane,f.scenario.id,{url}),/creation-receipted/);
  const receipt=await recordNativeResource(f.run,f.lane,input,{runDir:join(fs.directory,'qa-test'),browserRoot:fs.browserRoot});
  assert.equal(receipt.id,id);assert.equal(f.lane.resources.cleanupReceipts[0].createdAt,input.createdAt);
  assert.equal(beginScenario(f.run,f.lane,f.scenario.id,{url}).url,new URL(url,f.run.baseURL).href);
  await assert.rejects(recordNativeResource(f.run,f.lane,input,{runDir:join(fs.directory,'qa-test'),browserRoot:fs.browserRoot}),/already recorded/);
  assert.throws(()=>beginScenario(f.run,f.lane,f.scenario.id,{url:'/?workspace=other'}),/explicitly assigned/);
});


test('confirmed defects cannot become accepted merely because adjudication exists',()=>{
  const f=attemptedFixture();inspectionReceipt(f.capture,f.reviewer.agent,{tool:'view_image',invocation:'review image',note:'Expected canvas; actual canvas.'});
  recordReview(f.run,f.reviewer,f.lane,f.scenario.id,{verdict:'accepted',note:'Independent expected/actual match.'});
  f.run.findings=[{id:'finding-material',verificationStatus:'confirmed-defect',reviews:[{verdict:'confirmed-defect',agent:f.reviewer.agent}]}];
  assert.equal(acceptance(f.run).status,'review-pending');assert.match(acceptance(f.run).reasons.join(),/confirmed defect remains unresolved/);
  f.run.findings[0].verificationStatus='not-reproduced';
  assert.match(acceptance(f.run).reasons.join(),/remediation verification/);
});

test('registered linked checkout output roots are accepted; arbitrary roots are rejected',async t=>{
  const fs=await sessionFixture(t);
  const connection=await readNativeSession(fs.session,[join(fs.directory,'unused-primary','artifacts/native-browsers'),fs.browserRoot]);
  assert.equal(connection.directory,fs.session);
  await assert.rejects(readNativeSession(fs.session,[join(fs.directory,'another-checkout','artifacts/native-browsers')]),/registered project checkout/);
  const {root}=await import('./store.mjs');
  assert.ok(nativeBrowserRoots(root).includes(join(root,'artifacts/native-browsers')));
});


test('latest current-attempt review governs acceptance; duplicate receipts never inflate reviewed cases',()=>{
  const f=attemptedFixture();inspectionReceipt(f.capture,f.reviewer.agent,{tool:'view_image',invocation:'review image',note:'Expected canvas; actual canvas.'});
  const input={verdict:'accepted',note:'Independent expected/actual match.'};
  recordReview(f.run,f.reviewer,f.lane,f.scenario.id,input);
  recordReview(f.run,f.reviewer,f.lane,f.scenario.id,input);
  assert.equal(acceptance(f.run).reviewed,1);assert.equal(acceptance(f.run).intended,1);
  recordReview(f.run,f.reviewer,f.lane,f.scenario.id,{verdict:'confirmed-defect',note:'Later independent reproduction reveals a defect.'});
  assert.equal(acceptance(f.run).status,'review-pending');assert.equal(acceptance(f.run).reviewed,0);
  assert.match(acceptance(f.run).reasons.join(),/confirmed-defect/);
});


test('native recovery fences interrupted owner before browser-close prerequisite and preserves peers',async()=>{
  const f=fixture();f.lane.native={sessionId:'owned-connection'};let token='old',persisted=0;
  await assert.rejects(recoverNativeLane(f.run,f.lane,{
    invalidate:()=>{token=null;},persist:async()=>{persisted++;},drain:async()=>{},
    closeOldTransport:async lane=>{assert.equal(lane.status,'paused');assert.equal(lane.generation,3);assert.equal(token,null);throw new Error('browser_close observation required');},
    prepare:async()=>{throw new Error('must not prepare a live context');},
  }),/browser_close/);
  assert.equal(f.lane.status,'paused');assert.equal(f.lane.recovery.status,'blocked');assert.equal(persisted,2);
  assert.equal(f.reviewer.status,'claimed');assert.equal(f.reviewer.generation,2);assert.equal(f.lane.native.sessionId,'owned-connection');
});

test('closed native recovery prepares pending-only reconciliation while retaining completed results',async()=>{
  const f=fixture();f.lane.native={sessionId:'owned-connection'};f.lane.dispatchedAt='previous-owned-claim';
  f.lane.scenarios.push({...f.scenario,id:'completed-case',functional:'passed',visual:'passed',note:'Saved initial evidence remains retained.'});
  let closed=false,prepared=false;
  const result=await recoverNativeLane(f.run,f.lane,{invalidate:()=>{},persist:async()=>{},drain:async()=>{},
    closeOldTransport:async()=>{closed=true;},prepare:async lane=>{assert.equal(closed,true);prepared=true;lane.status='ready';lane.agent=null;},
  });
  assert.equal(prepared,true);assert.deepEqual(result.pending,[f.scenario.id]);assert.equal(result.status,'ready');
  assert.equal(f.lane.scenarios[1].functional,'passed');assert.equal(f.lane.recovery.reconciliationRequired,true);
});
