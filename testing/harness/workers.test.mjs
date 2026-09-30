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

async function probeWorkers(t, { ignoreTermination = false, crashable = false, holdChildReadiness = false, onEvent = () => {}, onExit = () => {} } = {}) {
  const { spawn } = await import('node:child_process');
  const { mkdtemp, rm, readFile, access } = await import('node:fs/promises');
  const { join } = await import('node:path');
  const { tmpdir } = await import('node:os');
  const { CodexWorkers } = await import('./workers.mjs');
  const directory = await mkdtemp(join(tmpdir(),'schemii-owned-worker-'));
  // A spawned PID is not readiness: publish only after descendant handlers are
  // installed. Keep initialization controllable without adding a timing sleep.
  const descendant = `process.on('message',message=>{
  if(message!=='initialize')return;
  process.on('SIGTERM',()=>process.exit(0));process.send('ready');
});process.send('starting');setInterval(()=>{},50);`;
  const script = ignoreTermination
    ? `const fs=require('node:fs');
const publish=(file,value)=>{fs.writeFileSync(file+'.tmp',JSON.stringify(value));fs.renameSync(file+'.tmp',file);};
process.stdin.resume();
for(const signal of ['SIGTERM','SIGINT'])process.on(signal,()=>publish(signal+'.json',{pid:process.pid,signal}));
publish('probe.json',{pid:process.pid});setInterval(()=>{},50);`
    : `const fs=require('node:fs'),{spawn}=require('node:child_process');
const child=spawn(process.execPath,['-e',${JSON.stringify(descendant)}],{stdio:['ignore',${crashable ? "'inherit','inherit'" : "'ignore','ignore'"},'ipc']});
const publish=(file,value)=>{fs.writeFileSync(file+'.tmp',JSON.stringify(value));fs.renameSync(file+'.tmp',file);};
let stopping=false,childExited=false,childStarting=false,initialized=false,ticks=0;process.stdin.resume();
// Group SIGTERM can deliver the child exit before the leader's signal callback.
process.on('SIGTERM',()=>{stopping=true;if(childExited)process.exit(0);else child.kill('SIGTERM');});
child.once('exit',()=>{childExited=true;publish('child-exited.json',{pid:child.pid});if(stopping)process.exit(0);});
child.on('message',message=>{
  if(message==='starting'){childStarting=true;publish('child-starting.json',{pid:child.pid});}
  if(message==='ready')publish('probe.json',{pid:process.pid,child:child.pid});
});
setInterval(()=>{
  if(childStarting&&!initialized&&(!${holdChildReadiness}||fs.existsSync('release-child'))){initialized=true;child.send('initialize');}
  if(fs.existsSync('exit-child')&&!childExited)child.kill('SIGTERM');
  if(${crashable}&&fs.existsSync('crash'))process.exit(7);
},10);
if(!${crashable})setInterval(()=>{publish('pulse.json',{ticks:++ticks});process.stderr.write('noise\\n');},50);`;
  const workers = new CodexWorkers({root:directory,runDir:directory,timeoutSeconds:60,onEvent,onExit,
    spawnProcess:(_command,_args,options)=>spawn(process.execPath,['-e',script],options)});
  t.after(async()=>{
    const {processIdentity}=await import('./native.mjs');const owners=[];
    for(const handle of workers.handles.values()){
      const owner=await processIdentity(handle.pid);if(owner)owners.push(owner);
      for(const file of ['probe.json','child-starting.json']){
        try{
          const probe=JSON.parse(await readFile(join(directory,handle.laneId,file),'utf8'));
          const descendant=await processIdentity(probe.child||probe.pid);
          if(descendant?.parent===handle.pid)owners.push(descendant);
        }catch(error){if(error.code!=='ENOENT')throw error;}
      }
    }
    await workers.close();assert.equal(workers.handles.size,0);
    for(const owner of owners)assert.notEqual((await processIdentity(owner.pid))?.birthTick,owner.birthTick,'automatic pool closure must reap owned birth identities');
    await rm(directory,{recursive:true,force:true});await assert.rejects(access(directory),{code:'ENOENT'});
  });
  return {workers,directory};
}
async function readProbe(directory,lane,file='probe.json') {
  const {readFile}=await import('node:fs/promises');const {join}=await import('node:path');
  for(let i=0;i<100;i++) {
    try{return JSON.parse(await readFile(join(directory,lane,file),'utf8'));}catch(error){if(error.code!=='ENOENT')throw error;}
    await new Promise(resolve=>setTimeout(resolve,10));
  }
  throw new Error('Owned process probe never became ready.');
}

test('owned fixture readiness waits for descendant signal handlers rather than its PID',async t=>{
  const {access,writeFile}=await import('node:fs/promises');const {join}=await import('node:path');
  const {processIdentity}=await import('./native.mjs');
  const {workers,directory}=await probeWorkers(t,{holdChildReadiness:true});
  const lane={id:'lane-1',generation:1};const worker=await workers.start({lane,sessionFile:'/private/session',brief});
  const descendant=await readProbe(directory,lane.id,'child-starting.json');
  assert.equal((await processIdentity(worker.pid))?.birthTick,worker.birthTick);
  assert.equal((await processIdentity(descendant.pid))?.parent,worker.pid);
  await assert.rejects(access(join(directory,lane.id,'probe.json')),{code:'ENOENT'});
  await writeFile(join(directory,lane.id,'release-child'),'initialize');
  const owner=await readProbe(directory,lane.id);
  assert.equal(owner.pid,worker.pid);assert.equal(owner.child,descendant.pid);
  const started=Date.now();await workers.stop(lane.id,'fixture-ready-cleanup');
  assert.ok(Date.now()-started<2000,'ready graceful fixture must not require escalation');
  assert.equal(await processIdentity(owner.pid),null);assert.equal(await processIdentity(owner.child),null);
  assert.equal(workers.handles.size,0);
});

test('owned fixture exits gracefully when descendant exit precedes leader termination',async t=>{
  const {writeFile}=await import('node:fs/promises');const {join}=await import('node:path');
  const {processIdentity}=await import('./native.mjs');const {workers,directory}=await probeWorkers(t);
  const lane={id:'lane-1',generation:1},peer={id:'lane-2',generation:1};
  await workers.start({lane,sessionFile:'/private/session',brief});await workers.start({lane:peer,sessionFile:'/private/peer',brief});
  const owner=await readProbe(directory,lane.id),other=await readProbe(directory,peer.id);
  const peerOwner=await processIdentity(other.pid);
  await writeFile(join(directory,lane.id,'exit-child'),'terminate descendant first');
  assert.deepEqual(await readProbe(directory,lane.id,'child-exited.json'),{pid:owner.child});
  assert.equal(await processIdentity(owner.child),null);assert.ok(await processIdentity(owner.pid));
  const started=Date.now(),result=await workers.stop(lane.id,'fixture-child-first-cleanup');
  assert.equal(result.code,0);assert.equal(result.signal,null);
  assert.ok(Date.now()-started<2000,'child-first graceful fixture must not require escalation');
  assert.equal(await processIdentity(owner.pid),null);assert.equal(workers.handles.has(lane.id),false);
  assert.equal((await processIdentity(other.pid))?.birthTick,peerOwner.birthTick);assert.equal(workers.handles.size,1);
});

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
  const owner=await readProbe(directory,lane.id),handle=workers.handles.get(lane.id),birth=handle.birthTick;
  const peerPool=await probeWorkers(t),peer={id:'lane-peer',generation:1};
  await peerPool.workers.start({lane:peer,sessionFile:'/private/peer',brief});
  const other=await readProbe(peerPool.directory,peer.id),peerOwner=await processIdentity(other.pid);
  process.kill(-owner.pid,'SIGINT');
  assert.deepEqual(await readProbe(directory,lane.id,'SIGINT.json'),{pid:owner.pid,signal:'SIGINT'});
  assert.equal((await processIdentity(owner.pid))?.birthTick,birth,'fixture must actually ignore SIGINT');

  // Advance only the deadline clock. OS signals, polling timers, /proc birth
  // checks and ChildProcess exit/recording events remain real. No production
  // timeout is configurable or shortened for this test.
  let now=0,killAttempts=0,recordingClosed=false,activeStop;
  const clock=t.mock.method(Date,'now',()=>now),actualKill=process.kill.bind(process);
  const signal=t.mock.method(process,'kill',(pid,name)=>{
    if(pid===-owner.pid&&name==='SIGKILL'){killAttempts++;return true;}
    return actualKill(pid,name);
  });
  void handle.closed.then(()=>{recordingClosed=true;});
  const observed=async predicate=>{
    for(let i=0;i<100&&!predicate();i++)await new Promise(resolve=>setTimeout(resolve,10));
    assert.ok(predicate(),'real drain loop must observe the controlled deadline');
  };
  const atDeadline=async (value,reads=1)=>{
    const calls=clock.mock.callCount();now=value;
    await observed(()=>clock.mock.callCount()>=calls+reads);
  };
  try {
    const stalled=activeStop=workers.stop(lane.id,'lease-expired');void stalled.catch(()=>{});
    assert.deepEqual(await readProbe(directory,lane.id,'SIGTERM.json'),{pid:owner.pid,signal:'SIGTERM'});
    // Wait for the real loop to establish its grace deadline before advancing.
    await observed(()=>clock.mock.callCount()>=3);
    // The final-deadline read follows any awaited escalation ownership scan.
    // Observe both reads before proving that no early SIGKILL was attempted.
    await atDeadline(4999,2);
    assert.equal(killAttempts,0,'production must grant the full 5000ms grace');
    assert.equal((await processIdentity(owner.pid))?.birthTick,birth);
    await atDeadline(5000);await observed(()=>killAttempts===1);
    assert.equal((await processIdentity(owner.pid))?.birthTick,birth,'omitted escalation must leave the real fixture alive');
    assert.equal(recordingClosed,false);assert.equal(workers.handles.has(lane.id),true);
    await atDeadline(5999);
    assert.equal(handle.cleanupPending,false,'production must retain the full 1000ms final deadline');
    await atDeadline(6000);
    await assert.rejects(stalled,/Owned worker processes remain live after termination/);
    assert.equal(handle.cleanupPending,true);assert.equal(workers.handles.has(lane.id),true);
    assert.equal((await processIdentity(other.pid))?.birthTick,peerOwner.birthTick);

    // Restore the real escalation and retry the retained owner. This must reap
    // the actual leader and drain its recording before releasing its handle.
    signal.mock.restore();now=0;
    const calls=clock.mock.callCount(),stopped=activeStop=workers.stop(lane.id,'fixture-owned-cleanup');void stopped.catch(()=>{});
    await observed(()=>clock.mock.callCount()>=calls+3);
    await atDeadline(5000);
    await observed(()=>!workers.handles.has(lane.id));
    const result=await stopped;
    assert.equal(result.reason,'lease-expired');assert.equal(result.signal,'SIGKILL');
    assert.equal(handle.child.signalCode,'SIGKILL');assert.equal(recordingClosed,true);
    assert.equal(await processIdentity(owner.pid),null);assert.equal(workers.handles.size,0);
    assert.equal((await processIdentity(other.pid))?.birthTick,peerOwner.birthTick);
    assert.equal((await processIdentity(other.child))?.parent,other.pid);
    assert.equal(peerPool.workers.handles.size,1);
  } finally {
    signal.mock.restore();
    // A failed assertion must not switch an in-flight logical deadline to wall
    // time. Let either captured drain settle before restoring its clock; the
    // registered fixture close then cleans the restored pool normally.
    const advance=setInterval(()=>{now+=1000;},10);
    try { await activeStop?.catch(()=>{t.diagnostic('Controlled drain settled cleanup-pending; restored fixture teardown retains ownership.');}); }
    finally { clearInterval(advance);clock.mock.restore(); }
  }
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

test('unexpected leader exit fences promptly and drains recorded children/streams before releasing ownership',async t=>{
  const {writeFile}=await import('node:fs/promises');const {join}=await import('node:path');const {processIdentity}=await import('./native.mjs');
  const {recordWorkerExit,workerCleanupReasons}=await import('./leases.mjs');
  const lane={id:'lane-1',status:'claimed',generation:1},peer={id:'lane-2',status:'claimed',generation:1};
  let token='old-owned-token',workers,firstExit;const exits=[];
  const pool=await probeWorkers(t,{crashable:true,onExit:result=>{
    exits.push(result);
    if(result.laneId===lane.id)recordWorkerExit(lane,result);
    if(result.laneId===lane.id&&result.generation===lane.generation&&lane.status==='claimed') {
      assert.match(workerCleanupReasons(lane).join(),/cleanup pending/);
      firstExit={retained:workers.handles.has(lane.id),cleanup:result.cleanup};lane.status='paused';lane.generation++;token=null;
    }
  }});workers=pool.workers;const {directory}=pool;
  lane.worker=await workers.start({lane,sessionFile:'/private/session',brief});peer.worker=await workers.start({lane:peer,sessionFile:'/private/peer',brief});
  const owner=await readProbe(directory,lane.id),other=await readProbe(directory,peer.id),handle=workers.handles.get(lane.id);
  const childOwner=await processIdentity(owner.child),peerOwner=await processIdentity(other.pid);
  for(let i=0;i<150&&!handle.ownedGroup?.some(item=>item.pid===childOwner.pid&&item.birthTick===childOwner.birthTick);i++)await new Promise(resolve=>setTimeout(resolve,20));
  assert.ok(handle.ownedGroup.some(item=>item.pid===childOwner.pid),'child must be observed while the original leader proves group ownership');
  let recordingClosed=false;void handle.closed.then(()=>{recordingClosed=true;});const started=Date.now();
  await writeFile(join(directory,lane.id,'crash'),'exit 7');
  const result=await handle.done;
  assert.deepEqual(firstExit,{retained:true,cleanup:'pending'});assert.equal(token,null);assert.equal(lane.generation,2);
  assert.equal(result.code,7);assert.equal(result.reason,'agent-failed');assert.equal(result.cleanup,'stopped');
  assert.equal(lane.workerLifecycle.workerCleanup,'stopped');assert.equal(lane.workerLifecycle.browserCleanup,'pending');
  assert.match(workerCleanupReasons(lane).join(),/cleanup pending/);assert.match(workerCleanupReasons(lane).join(),/worker execution failed.*exit 7/);
  assert.equal(recordingClosed,true);assert.equal(handle.child.exitCode,7);assert.equal(workers.handles.has(lane.id),false);
  assert.equal(await processIdentity(owner.pid),null);assert.equal(await processIdentity(childOwner.pid),null);
  assert.equal((await processIdentity(other.pid))?.birthTick,peerOwner.birthTick);assert.equal(peer.status,'claimed');assert.equal(peer.generation,1);
  assert.equal(exits.filter(item=>item.laneId===lane.id).length,2);assert.ok(Date.now()-started<3000);
});

test('crash with unobserved group ownership stays cleanup-pending without signaling child or peer',async t=>{
  const {writeFile}=await import('node:fs/promises');const {join}=await import('node:path');const {processIdentity}=await import('./native.mjs');
  const exits=[],{workers,directory}=await probeWorkers(t,{crashable:true,onExit:result=>exits.push(result)});
  const lane={id:'lane-1',generation:1},peer={id:'lane-2',generation:1};
  await workers.start({lane,sessionFile:'/private/session',brief});await workers.start({lane:peer,sessionFile:'/private/peer',brief});
  const owner=await readProbe(directory,lane.id),other=await readProbe(directory,peer.id),handle=workers.handles.get(lane.id);
  const childOwner=await processIdentity(owner.child);assert.equal(childOwner.parent,owner.pid);
  // Model a crash before child observation. Keep a separately verified fixture
  // identity only to restore its record and use automatic pool teardown later.
  clearInterval(handle.groupTimer);await handle.groupScan;handle.ownedGroup=[];
  try {
    await writeFile(join(directory,lane.id,'crash'),'exit 7 before observation');
    for(let i=0;i<100&&!handle.cleanupPending;i++)await new Promise(resolve=>setTimeout(resolve,20));
    assert.equal(handle.cleanupPending,true);assert.equal(workers.handles.has(lane.id),true);
    assert.equal(handle.leaderExit.code,7);assert.equal(handle.reason,'agent-failed');
    assert.equal((await processIdentity(childOwner.pid))?.birthTick,childOwner.birthTick);assert.ok(await processIdentity(other.pid));
    await assert.rejects(workers.stop(lane.id,'crash-cleanup'),/ownership changed/);
    assert.equal(exits.filter(item=>item.laneId===lane.id).length,1);assert.equal(exits[0].cleanup,'pending');
  } finally {
    handle.ownedGroup=[childOwner];const result=await workers.stop(lane.id,'fixture-owned-cleanup');
    assert.equal(result.reason,'agent-failed');assert.equal(result.code,7);assert.equal(await processIdentity(childOwner.pid),null);
  }
  assert.ok(await processIdentity(other.pid));assert.equal(workers.handles.size,1);
});

test('recording closure failure retains stopped worker ownership and its original exit',async t=>{
  const {processIdentity}=await import('./native.mjs');const {workers,directory}=await probeWorkers(t);
  const lane={id:'lane-1',generation:1},peer={id:'lane-2',generation:1};
  await workers.start({lane,sessionFile:'/private/session',brief});await workers.start({lane:peer,sessionFile:'/private/peer',brief});
  const owner=await readProbe(directory,lane.id),other=await readProbe(directory,peer.id),handle=workers.handles.get(lane.id),actualClosure=handle.closed;
  const unavailable=Promise.reject(new Error('Worker recording closure is unavailable.'));void unavailable.catch(()=>{});handle.closed=unavailable;
  try {
    await assert.rejects(workers.stop(lane.id,'lease-expired'),/recording closure is unavailable/);
    assert.equal(handle.cleanupPending,true);assert.equal(workers.handles.has(lane.id),true);
    assert.equal(await processIdentity(owner.pid),null);assert.equal(await processIdentity(owner.child),null);assert.ok(await processIdentity(other.pid));
    assert.equal(handle.reason,'lease-expired');assert.match(handle.cleanupError,/recording closure/);
  } finally {
    // Only remove the injected failure after the real process streams/file close.
    await actualClosure;handle.closed=actualClosure;await workers.stop(lane.id,'fixture-owned-cleanup');
  }
  assert.ok(await processIdentity(other.pid));assert.equal(workers.handles.size,1);
});
