import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, rm, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { reserveAccounts, releaseAccounts, withAvailableAccounts } from './leases.mjs';

test('fixture cleanup holds the reservation lock and refuses an account owned by a run', async () => {
  const root=await mkdtemp(join(tmpdir(),'schemii-qa-lease-'));
  const runDir=join(root,'run');
  await mkdir(runDir);
  execFileSync('git',['init','--quiet',root]);
  const account='qa_report_author_001';
  const runId='qa-test-reservation';
  try {
    await reserveAccounts({runId,runDir,accounts:[account],root});
    let called=false;
    await assert.rejects(withAvailableAccounts([account],async()=>{called=true;},{root}),
      error=>error.code==='QA_ACCOUNT_BUSY');
    assert.equal(called,false);

    await releaseAccounts({runId,accounts:[account],root});
    assert.equal(await withAvailableAccounts([account],async()=>true,{root}),true);
  } finally {
    await releaseAccounts({runId,accounts:[account],root});
    await rm(root,{recursive:true,force:true});
  }
});

test('lease renewal accepts current structured activity, never noise or previous owner events',async()=>{
  const {renewWorkerLease}=await import('./leases.mjs');
  const lane={status:'claimed',generation:3,worker:{pid:123,birthTick:'456'},heartbeatAt:'original'};
  const current={generation:3,pid:123,birthTick:'456',kind:'worker-progress'};
  for(const event of [{...current,kind:'worker-stderr'},{...current,kind:'worker-heartbeat'},{...current,generation:2},{...current,pid:987},{...current,birthTick:'111'}])assert.equal(renewWorkerLease(lane,event,'new'),false);
  assert.equal(lane.heartbeatAt,'original');assert.equal(renewWorkerLease(lane,current,'new'),true);assert.equal(lane.heartbeatAt,'new');
  assert.deepEqual(lane.workerActivity,{providerEvents:1,toolEvents:0});
});

test('expiry closure failure remains cleanup pending and retains fenced ownership',async()=>{
  const {expireWorkerLease}=await import('./leases.mjs');const lane={id:'lane-1',status:'claimed',agent:'owned',generation:1,heartbeatAt:new Date(0).toISOString()};
  const result=await expireWorkerLease(lane,{leaseMs:1,invalidate:()=>{},stopWorker:async()=>{throw new Error('controlled stop failure');},closeBrowser:async()=>{throw new Error('must not close before worker drain');},persist:async()=>{}});
  assert.equal(result.status,'cleanup-pending');assert.equal(lane.generation,2);assert.equal(lane.status,'paused');assert.equal(result.reservation,'retained-for-recovery');
  assert.deepEqual(await expireWorkerLease(lane,{leaseMs:1}),{expired:false});
});
