import test from 'node:test';
import assert from 'node:assert/strict';
import { workerAssignment } from './workers.mjs';

const brief = {
  lane: 'lane-1',
  scenarios: [{ id: 'design-schemii-desktop', instructions: 'Save a design and reload it.' }],
  resources: { workspace: 'scratch-design-1' },
};

test('worker stays read-only without explicit write authorization', () => {
  const prompt = workerAssignment('/repo', '/private/session.json', brief);
  assert.match(prompt, /This lane is read-only/);
  assert.match(prompt, /Record write-dependent steps blocked/);
  assert.match(prompt, /finding .* --scenario EXACT_SCENARIO_ID/);
  assert.match(prompt, /Execute all feasible substeps/);
});

test('worker may write only exact disposable resources and operations', () => {
  const prompt = workerAssignment('/repo', '/private/session.json', {
    ...brief,
    writeAuthorization: {
      enabled: true,
      resources: ['scratch-design-1'],
      operations: ['save design', 'approve chat proposal'],
    },
  });
  assert.match(prompt, /may perform application writes through its owned browser session ONLY/);
  assert.match(prompt, /scratch-design-1/);
  assert.match(prompt, /approve chat proposal/);
  assert.match(prompt, /Do not modify retained data, other accounts/);
  assert.doesNotMatch(prompt, /This lane is read-only/);
});

test('empty or incomplete authorization does not enable writes', () => {
  for (const writeAuthorization of [
    { enabled: true, resources: [], operations: ['save design'] },
    { enabled: true, resources: ['scratch-design-1'], operations: [] },
    { enabled: false, resources: ['scratch-design-1'], operations: ['save design'] },
  ]) {
    assert.match(workerAssignment('/repo', '/private/session.json', { ...brief, writeAuthorization }), /This lane is read-only/);
  }
});

async function probeWorkers(t, { ignoreTermination = false, onEvent = () => {} } = {}) {
  const { spawn } = await import('node:child_process');
  const { mkdtemp, rm } = await import('node:fs/promises');
  const { join } = await import('node:path');
  const { tmpdir } = await import('node:os');
  const { CodexWorkers } = await import('./workers.mjs');
  const directory = await mkdtemp(join(tmpdir(),'schemii-owned-worker-'));
  const script = ignoreTermination
    ? `const fs=require('node:fs');process.stdin.resume();process.on('SIGTERM',()=>{});fs.writeFileSync('probe.json',JSON.stringify({pid:process.pid}));setInterval(()=>{},50);`
    : `const fs=require('node:fs'),{spawn}=require('node:child_process');
const child=spawn(process.execPath,['-e','process.on("SIGTERM",()=>process.exit(0));setInterval(()=>{},50)'],{stdio:'ignore'});
let stopping=false,ticks=0;process.stdin.resume();
process.on('SIGTERM',()=>{stopping=true;child.kill('SIGTERM');});
child.once('exit',()=>{if(stopping)process.exit(0);});
fs.writeFileSync('probe.json',JSON.stringify({pid:process.pid,child:child.pid}));
setInterval(()=>{fs.writeFileSync('pulse.json',JSON.stringify({ticks:++ticks}));process.stderr.write('noise\\n');},50);`;
  const workers = new CodexWorkers({root:directory,runDir:directory,timeoutSeconds:60,onEvent,
    spawnProcess:(_command,_args,options)=>spawn(process.execPath,['-e',script],options)});
  t.after(async()=>{await workers.close();await rm(directory,{recursive:true,force:true});});
  return {workers,directory};
}
async function readProbe(directory,lane) {
  const {readFile}=await import('node:fs/promises');const {join}=await import('node:path');
  for(let i=0;i<100;i++) {
    try{return JSON.parse(await readFile(join(directory,lane,'probe.json'),'utf8'));}catch{}
    await new Promise(resolve=>setTimeout(resolve,10));
  }
  throw new Error('Owned process probe never became ready.');
}

// This is the original counterexample: a blocked lane operation must not defer
// inference termination until the browser queue or the 60-second worker timeout.
test('lease expiry fences immediately and reaps only owned worker before stalled UI queue drains',async t=>{
  const {expireWorkerLease}=await import('./leases.mjs');const {processIdentity}=await import('./native.mjs');
  const {workers,directory}=await probeWorkers(t);
  const stale={id:'lane-1',status:'claimed',agent:'actual-stale-agent',generation:1,heartbeatAt:new Date(0).toISOString()};
  const peer={id:'lane-2',status:'claimed',agent:'actual-live-peer',generation:1,heartbeatAt:new Date().toISOString()};
  stale.worker=await workers.start({lane:stale,sessionFile:'/private/stale-session',brief});
  peer.worker=await workers.start({lane:peer,sessionFile:'/private/peer-session',brief});
  const old=await readProbe(directory,stale.id),other=await readProbe(directory,peer.id);
  const childOwner=await processIdentity(old.child),peerOwner=await processIdentity(other.pid);
  let releaseUI;const blockedUI=new Promise(resolve=>{releaseUI=resolve;});let invalidated=false,closed=false,persists=0;
  const started=Date.now();
  const expired=expireWorkerLease(stale,{leaseMs:100,now:Date.now(),invalidate:()=>{invalidated=true;},
    stopWorker:(id,reason)=>workers.stop(id,reason),closeBrowser:async()=>{await blockedUI;closed=true;},persist:async()=>{persists++;}});
  assert.equal(invalidated,true);assert.equal(stale.status,'paused');assert.equal(stale.generation,2);
  for(let i=0;i<100&&workers.handles.has(stale.id);i++)await new Promise(resolve=>setTimeout(resolve,20));
  assert.equal(workers.handles.has(stale.id),false,'stale inference worker must exit even while UI remains stalled');
  assert.equal(closed,false);assert.equal((await processIdentity(other.pid))?.birthTick,peerOwner.birthTick);
  assert.equal(await processIdentity(old.pid),null);assert.equal(await processIdentity(childOwner.pid),null);
  releaseUI();const result=await expired;
  assert.equal(result.status,'stopped');assert.equal(result.exit.reason,'lease-expired');assert.equal(result.reservation,'retained-for-recovery');
  assert.ok(Date.now()-started<3000,'lease stop must finish far before 60-second overall timeout');assert.ok(persists>=2);
  assert.equal(peer.status,'claimed');assert.equal(peer.generation,1);assert.equal(workers.handles.size,1);
});

test('ignoring owned worker is escalated and reaped within bounded termination',async t=>{
  const {processIdentity}=await import('./native.mjs');const {workers,directory}=await probeWorkers(t,{ignoreTermination:true});
  const lane={id:'lane-1',generation:1};await workers.start({lane,sessionFile:'/private/session',brief});
  const owner=await readProbe(directory,lane.id),started=Date.now();const result=await workers.stop(lane.id,'lease-expired');
  assert.equal(result.reason,'lease-expired');assert.equal(result.signal,'SIGKILL');assert.equal(await processIdentity(owner.pid),null);
  assert.ok(Date.now()-started>=4900);assert.ok(Date.now()-started<7000);assert.equal(workers.handles.size,0);
});

test('stale process identity cannot kill a live worker or peer and retains cleanup ownership',async t=>{
  const {processIdentity}=await import('./native.mjs');const {workers,directory}=await probeWorkers(t);
  const lane={id:'lane-1',generation:1},peer={id:'lane-2',generation:1};
  await workers.start({lane,sessionFile:'/private/session',brief});await workers.start({lane:peer,sessionFile:'/private/peer',brief});
  const owner=await readProbe(directory,lane.id),other=await readProbe(directory,peer.id),handle=workers.handles.get(lane.id),original=handle.birthTick;
  try {
    handle.birthTick='0';await assert.rejects(workers.stop(lane.id,'lease-expired'),/ownership changed/);
    assert.equal((await processIdentity(owner.pid))?.birthTick,original);assert.ok(await processIdentity(other.pid));
    assert.equal(handle.cleanupPending,true);assert.equal(workers.handles.size,2);
  } finally {handle.birthTick=original;await workers.stop(lane.id);}
  assert.ok(await processIdentity(other.pid));assert.equal(workers.handles.size,1);
});
